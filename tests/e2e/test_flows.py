"""Every user flow, in a real browser, with the background worker running."""
import io
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jobhq.ai.runner import Result

from .conftest import wait_for

pytestmark = pytest.mark.e2e

POSTING = ("Marketing Operations Manager at Acme Health. You will own HubSpot and Salesforce, lead scoring and attribution. "
           "Looker experience is a plus. Remote, US. $150,000 - $185,000.")


def no_problems(page, expect_4xx=False):
    """No console errors. A test that triggers a validation error expects the browser's own note about the 4xx response."""
    problems = [p for p in page.problems if not (expect_4xx and re.search(r"status of 4\d\d|Error Code 4\d\d", p))]
    assert problems == [], problems


def toast(page):
    page.wait_for_selector(".toast", timeout=8000)
    return page.locator(".toast").last.inner_text()


def script_score(runner, score=(21, 20, 18, 20)):
    runner.script["score"] = lambda *_: Result(ok=True, data={
        "skills": score[0], "experience": score[1], "level": score[2], "culture": score[3], "why": "Strong match on HubSpot and scoring.",
        "unknown_tools": ["Looker"], "gates": {g: {"result": "pass", "why": "fine"} for g in ("employer", "sector", "location", "pay")},
        "pay_min": 150000, "pay_max": 185000, "location": "Remote, US", "work_mode": "remote"})


def connect_claude(page):
    page.goto("/settings")
    page.fill("input[name=api_key]", "sk-ant-e2e-key")
    page.get_by_role("button", name="Save").click()
    assert toast(page) == "Saved."


# ------------------------------------------------------------------ sign-in

def test_sign_in_sign_out_and_wrong_account(browser_page):
    page = browser_page
    page.goto("/roles")
    assert page.url.endswith("/login")
    page.get_by_role("link", name="Sign in with Google").click()
    page.wait_for_url(f"{page.live.base}/")
    assert page.get_by_role("heading", name="Today").is_visible() or page.locator("h1").inner_text().startswith("Hi")
    page.get_by_role("button", name="Sign out").click()
    page.wait_for_url("**/login")
    page.goto("/")
    assert page.url.endswith("/login")
    page.live.google.claims["email"] = "someone.else@example.com"
    page.get_by_role("link", name="Sign in with Google").click()
    page.wait_for_selector(".alert.bad")
    assert "belongs to someone else" in page.locator(".alert.bad").inner_text()
    no_problems(page)


def test_sign_out_everywhere(signed_in):
    page = signed_in
    other, _ = page.live.ctx.sessions.create("phone")
    page.goto("/settings")
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Sign out everywhere").click()
    page.wait_for_url("**/login")
    assert page.live.ctx.sessions.lookup(other) is None
    no_problems(page)


# ------------------------------------------------------------------ setup

def test_setup_checklist_and_claude_settings(signed_in):
    page = signed_in
    page.goto("/")
    assert "0 of 8 done" in page.locator(".card.accent h2").inner_text()
    connect_claude(page)
    page.get_by_role("button", name="Test Claude").click()
    page.wait_for_selector("#test-result .good")
    assert "Claude answered" in page.locator("#test-result").inner_text()
    page.goto("/")
    assert "1 of 8 done" in page.locator(".card.accent h2").inner_text()
    page.goto("/settings")
    assert "sk-ant-e2e-key" not in page.content()  # never shown back
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Forget saved API key").click()
    page.wait_for_load_state("networkidle")
    assert not page.live.ctx.claude_ready()
    no_problems(page)


def test_bad_api_key_is_refused(signed_in):
    page = signed_in
    page.goto("/settings")
    page.fill("input[name=api_key]", "not-a-key")
    page.get_by_role("button", name="Save").click()
    assert "doesn't look like" in toast(page)
    no_problems(page, expect_4xx=True)


def test_gmail_connect_and_disconnect(signed_in):
    page = signed_in
    from jobhq.auth import GMAIL_SCOPE
    page.live.google.token_extra = {"scope": f"openid email {GMAIL_SCOPE}", "refresh_token": "rt-e2e"}
    page.goto("/setup")
    page.get_by_role("link", name="Connect Gmail", exact=True).click()
    page.wait_for_url("**/setup?gmail=ok")
    assert "Gmail is connected" in page.content()
    assert page.live.ctx.settings.get("gmail_refresh_token") == "rt-e2e"
    page.once("dialog", lambda d: d.accept())
    page.get_by_role("button", name="Disconnect Gmail").click()
    page.wait_for_selector("text=Connect Gmail")
    assert not page.live.ctx.settings.has("gmail_refresh_token")
    no_problems(page)


# ------------------------------------------------------------------ profile

def test_profile_build_up(signed_in):
    page = signed_in
    page.goto("/profile")
    page.fill("input[name=name]", "Pat Lee")
    page.fill("input[name=email]", "pat@example.com")
    page.fill("input[name=phone]", "555-0100")
    page.fill("input[name=city]", "Austin")
    page.fill("textarea[name=summary]", "Marketing ops leader.")
    page.fill("textarea[name=target_titles]", "Marketing Operations Manager\nRevOps Lead")
    page.check("input[name=work_modes][value=remote]")
    page.fill("input[name=pay_floor]", "150k")
    page.get_by_role("button", name="Save basic information").click()
    assert toast(page) == "Saved."
    b = page.live.ctx.profile.basics()
    assert b["pay_floor"] == 150000 and b["target_titles"] == ["Marketing Operations Manager", "RevOps Lead"] and b["work_modes"] == ["remote"]

    page.fill("details:has(summary:has-text('Add a job')) input[name=company]", "Acme")
    page.fill("details:has(summary:has-text('Add a job')) input[name=title]", "Ops Lead")
    page.fill("details:has(summary:has-text('Add a job')) input[name=start_date]", "Jan 2020")
    page.get_by_role("button", name="Add job").click()
    page.wait_for_selector("summary:has-text('Ops Lead')")
    page.fill("details.job input[name=text]", "Migrated 75,000 contacts to HubSpot")
    page.get_by_role("button", name="Add fact").click()
    page.wait_for_selector("text=Migrated 75,000 contacts to HubSpot")
    page.fill("input[name=school]", "Penn State")
    page.fill("input[name=degree]", "MBA")
    page.get_by_role("button", name="Add education").click()
    page.wait_for_selector("text=Penn State")
    page.fill("form[hx-post='/profile/skills'] input[name=name]", "Marketo")
    page.select_option("form[hx-post='/profile/skills'] select[name=status]", "never")
    page.get_by_role("button", name="Add skill").click()
    page.wait_for_selector(".tag.never")
    assert page.live.ctx.profile.missing() == []
    assert page.live.ctx.profile.skill_status("Marketo") == "never"
    no_problems(page)


def test_interview_suggestion_then_approve_with_edit(signed_in):
    page = signed_in
    live = page.live
    connect_claude(page)

    def interview(system, prompt, tools, schema):
        tools["propose_fact"].handler({"text": "Led a team of 5 contractors", "topic": "leadership"})
        return Result(ok=True, text="Thanks. I saved that as a suggestion. Next: what was your biggest result at Acme?")

    live.runner.script["interview"] = interview
    page.goto("/interview")
    page.fill("textarea[name=text]", "I managed five contractors at my last job")
    page.keyboard.press("Enter")
    page.wait_for_selector(".msg.assistant:not(.pending) >> text=biggest result", timeout=15000)
    page.reload()
    assert page.locator(".badge").inner_text() == "1"
    page.click("text=1 suggestion waiting for approval")
    page.fill("textarea[name=text]", "Led a team of five contractors")
    page.get_by_role("button", name="Approve").click()
    page.wait_for_selector("section.suggestion", state="detached")
    assert [f["text"] for f in live.ctx.profile.facts()] == ["Led a team of five contractors"]
    no_problems(page)


def test_profile_questions_answer_never_and_skip(signed_in):
    page = signed_in
    ctx = page.live.ctx
    connect_claude(page)
    for topic in ("Looker", "Marketo", "Tableau"):
        ctx.db.run("INSERT INTO profile_questions (topic, question, created) VALUES (?, ?, 0)", (topic, f"Have you used {topic}?"))
    page.goto("/interview")
    page.click("summary:has-text('Open questions')")
    page.locator("li:has-text('Have you used Marketo?')").get_by_role("button", name="Never used it").click()
    page.locator("li:has-text('Have you used Tableau?')").get_by_role("button", name="Skip").click()
    wait_for(lambda: ctx.profile.skill_status("Marketo") == "never")
    row = page.locator("li:has-text('Have you used Looker?')")
    row.locator("input[name=answer]").fill("Yes, two years of dashboards")
    row.get_by_role("button", name="Answer").click()
    page.wait_for_selector(".msg.user >> text=About Looker")
    statuses = {r["topic"]: r["status"] for r in ctx.db.all("SELECT topic, status FROM profile_questions")}
    assert statuses == {"Looker": "answered", "Marketo": "answered", "Tableau": "skipped"}
    no_problems(page)


# -------------------------------------------------------------------- roles

def test_add_role_auto_score_and_lanes(signed_in):
    page = signed_in
    live = page.live
    connect_claude(page)
    script_score(live.runner)
    page.goto("/roles/new")
    page.fill("input[name=company]", "Acme Health")
    page.fill("input[name=title]", "Marketing Operations Manager")
    page.fill("textarea[name=posting]", POSTING)
    page.get_by_role("button", name="Add role").click()
    page.wait_for_url(re.compile(r".*/roles/\d+$"))
    rid = int(page.url.rsplit("/", 1)[1])
    wait_for(lambda: live.ctx.roles.get(rid)["fit_score"] is not None)
    page.reload()
    assert page.locator(".score strong").inner_text() == "79"
    assert "Strong match" in page.content() and "$150,000" in page.content()
    page.goto("/roles")
    assert page.locator("section.lane:has(h2:has-text('Ready to apply')) >> text=Marketing Operations Manager").is_visible()
    assert live.ctx.db.value("SELECT COUNT(*) FROM profile_questions WHERE topic = 'Looker'") == 1
    no_problems(page)


def test_status_timeline_next_step_and_undo(signed_in):
    page = signed_in
    ctx = page.live.ctx
    rid = ctx.roles.create({"company": "Globex", "title": "RevOps Lead"})
    page.goto(f"/roles/{rid}")
    page.get_by_role("button", name="Applied", exact=True).click()
    page.wait_for_selector("p .pill.applied")
    assert ctx.roles.get(rid)["applied_on"] == datetime.now().date().isoformat()
    page.fill("input[name=next_step]", "Email the recruiter")
    page.fill("input[name=next_step_date]", (datetime.now() + timedelta(days=1)).date().isoformat())
    page.locator("form:has(input[name=next_step])").get_by_role("button", name="Save").click()
    page.wait_for_load_state("networkidle")
    assert ctx.roles.get(rid)["next_step"] == "Email the recruiter"
    page.select_option("form[hx-post$='/event'] select[name=kind]", "interview")
    page.fill("form[hx-post$='/event'] input[name=text]", "Phone screen with Dana")
    page.locator("form[hx-post$='/event']").get_by_role("button", name="Add").click()
    page.wait_for_selector(".timeline >> text=Phone screen with Dana")
    page.goto("/")
    assert page.locator("text=Email the recruiter").is_visible()
    page.goto(f"/roles/{rid}")
    page.click("summary:has-text('Change history')")
    page.locator("details li:has-text('next_step')").get_by_role("button", name="Undo").click()
    page.wait_for_selector("text=Undone")
    assert ctx.roles.get(rid)["next_step"] == ""
    no_problems(page)


def test_invalid_role_input_shows_an_error(signed_in):
    page = signed_in
    rid = page.live.ctx.roles.create({"company": "Globex", "title": "RevOps"})
    page.goto(f"/roles/{rid}")
    page.locator("form:has(textarea[name=posting]) input[name=url]").fill("javascript:alert(1)")
    page.get_by_role("button", name="Save details").click()
    assert "https" in toast(page)
    assert page.live.ctx.roles.get(rid)["url"] == ""
    no_problems(page, expect_4xx=True)


# --------------------------------------------------------------------- chat

def test_chat_changes_records_and_undo(signed_in):
    page = signed_in
    live = page.live
    connect_claude(page)
    rid = live.ctx.roles.create({"company": "Globex", "title": "Ops Manager", "status": "applied"})
    when = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%dT09:00")

    def chat(system, prompt, tools, schema):
        tools["update_role"].handler({"role_id": rid, "changes": {"status": "interviewing"}})
        tools["log_event"].handler({"role_id": rid, "kind": "interview", "on_date": when[:10], "text": "Final round"})
        tools["set_reminder"].handler({"text": "Prep for Globex", "when": when, "role_id": rid})
        return Result(ok=True, text="Moved **Globex** to interviewing, logged the final round, and set a reminder.")

    live.runner.script["chat"] = chat
    page.goto("/chat")
    page.fill("textarea[name=text]", "Globex invited me to the final round on Thursday, remind me to prep")
    page.keyboard.press("Enter")
    page.wait_for_selector(".msg.assistant:not(.pending) >> text=Moved", timeout=15000)
    assert page.locator(".msg.assistant strong", has_text="Globex").is_visible()  # markdown rendered
    assert live.ctx.roles.get(rid)["status"] == "interviewing"
    assert len(live.ctx.reminders.pending()) == 1
    page.locator(".actions li:has-text('status')").get_by_role("button", name="Undo").click()
    page.wait_for_selector(".actions >> text=Undone")
    assert live.ctx.roles.get(rid)["status"] == "applied"
    page.locator(".actions").get_by_role("button", name="Cancel reminder").click()
    wait_for(lambda: not live.ctx.reminders.pending())
    page.reload()
    assert page.locator(".msg.user").count() == 1 and page.locator(".msg.assistant").count() == 1  # history persists
    no_problems(page)


def test_chat_failure_is_visible(signed_in):
    page = signed_in
    connect_claude(page)
    page.live.runner.script["chat"] = lambda *_: Result(ok=False, error="Claude is busy right now. Try again in a minute.")
    page.goto("/chat")
    page.fill("textarea[name=text]", "hello")
    page.get_by_role("button", name="Send").click()
    page.wait_for_selector(".msg.failed >> text=busy", timeout=15000)
    no_problems(page)


def test_new_chat_thread(signed_in):
    page = signed_in
    page.goto("/chat")
    page.get_by_role("button", name="New chat").click()
    page.wait_for_url("**/chat?t=*")
    assert page.live.ctx.db.value("SELECT COUNT(*) FROM chat_threads WHERE kind = 'chat'") == 2
    no_problems(page)


# ---------------------------------------------------------------- documents

def setup_profile(ctx):
    from jobhq.profile import clean_basics
    ctx.profile.save_basics(clean_basics({"name": "Pat Lee", "email": "pat@example.com", "phone": "555-0100", "city": "Austin"})[0])
    exp = ctx.db.run("INSERT INTO experience (company, title, start_date) VALUES ('Acme', 'Ops Lead', 'Jan 2020')").lastrowid
    with ctx.db.tx() as conn:
        f1 = ctx.profile.add_fact(conn, "Migrated 75,000 contacts to HubSpot", experience_id=exp)
    return exp, f1


def test_resume_draft_review_edit_approve_download(signed_in):
    page = signed_in
    live = page.live
    connect_claude(page)
    exp, f1 = setup_profile(live.ctx)
    rid = live.ctx.roles.create({"company": "Globex", "title": "Marketing Ops Manager", "posting": POSTING})
    live.runner.script["draft_resume"] = lambda *_: Result(ok=True, data={
        "summary": "Marketing ops leader.", "experience": [{"experience_id": exp, "bullets": [
            {"text": "Migrated 75,000 contacts to HubSpot with zero downtime", "fact_ids": [f1]}]}],
        "skills": ["HubSpot", "Salesforce"], "keywords_missing": ["Looker"]})
    live.runner.script["claims"] = lambda *_: Result(ok=True, data={"problems": []})
    page.goto(f"/roles/{rid}")
    page.get_by_role("button", name="Tailor resume").click()
    page.wait_for_url("**/documents/*", timeout=30000)
    assert page.locator(".checks li.fail").count() == 0
    assert "Looker" in page.content()
    page.fill("textarea[name='b-0-0']", "Moved 75,000 contacts to HubSpot with zero downtime")
    page.get_by_role("button", name="Rebuild PDF and re-check").click()
    page.wait_for_function("document.querySelector(\"textarea[name='b-0-0']\").value.startsWith('Moved')")
    page.get_by_role("button", name="Approve").click()
    page.wait_for_selector("h1 .pill.approved")
    with page.expect_download() as dl:
        page.get_by_role("link", name="Download").click()
    path = dl.value.path()
    assert Path(path).read_bytes()[:4] == b"%PDF" and dl.value.suggested_filename == "Pat_Lee_Resume_Globex.pdf"
    page.goto("/documents")
    assert page.locator("text=all passed").is_visible()
    no_problems(page)


def test_failed_checks_need_confirmation(signed_in):
    page = signed_in
    live = page.live
    connect_claude(page)
    exp, f1 = setup_profile(live.ctx)
    with live.ctx.db.tx() as conn:
        live.ctx.profile.set_skill(conn, "Marketo", "never")
    rid = live.ctx.roles.create({"company": "Globex", "title": "Ops", "posting": POSTING})
    live.runner.script["draft_cover_letter"] = lambda *_: Result(ok=True, data={
        "greeting": "Dear Hiring Manager,", "closing": "Sincerely,",
        "paragraphs": [{"text": "I ran Marketo for years.", "fact_ids": []}, {"text": "Migrated 75,000 contacts.", "fact_ids": [f1]},
                       {"text": "I'd like to talk.", "fact_ids": []}]})
    live.runner.script["claims"] = lambda *_: Result(ok=True, data={"problems": [{"text": "I ran Marketo", "issue": "never used"}]})
    page.goto(f"/roles/{rid}")
    page.get_by_role("button", name="Write cover letter").click()
    page.wait_for_url("**/documents/*", timeout=30000)
    assert page.locator(".checks li.fail").count() >= 2
    dialogs = []
    page.once("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
    page.get_by_role("button", name="Approve").click()
    page.wait_for_timeout(500)
    assert dialogs and "didn't pass" in dialogs[0]
    no_problems(page)


# ----------------------------------------------------------------- practice

def test_practice_typed_answer_graded(signed_in):
    page = signed_in
    live = page.live
    connect_claude(page)
    live.runner.script["practice_question"] = lambda *_: Result(ok=True, data={"question": "Tell me about a migration you led.", "focus": "delivery"})
    live.runner.script["practice_grade"] = lambda *_: Result(ok=True, data={
        "structure": 4, "evidence": 4, "fit": 3, "length": 4, "worked": "Clear story.", "fix": "Name the business result.",
        "follow_up": "What would you do differently?"})
    page.goto("/practice")
    page.get_by_role("button", name="Give me a question").click()
    page.wait_for_url("**/practice/q/*")
    assert "migration you led" in page.locator("h1").inner_text()
    page.fill("textarea[name=answer]", "Um, I led the Iterable to HubSpot migration for 75,000 contacts and we had zero downtime.")
    page.get_by_role("button", name="Grade my answer").click()
    page.wait_for_selector("text=15/20", timeout=20000)
    assert page.locator("text=What would you do differently?").is_visible()
    page.goto("/practice")
    assert page.locator("td >> text=15").is_visible()
    no_problems(page)


# ---------------------------------------------------------------- reminders

def test_reminders_set_cancel_and_fire(signed_in):
    page = signed_in
    live = page.live
    live.ctx.push.subscribe({"endpoint": "https://fcm.googleapis.com/fcm/send/e2e", "keys": {"p256dh": "k", "auth": "a"}})
    page.goto("/reminders")
    page.fill("input[name=text]", "Send the thank-you note")
    page.select_option("select[name=preset]", "tomorrow")
    page.get_by_role("button", name="Set").click()
    page.wait_for_selector("li:has-text('Send the thank-you note')")
    rid = live.ctx.reminders.pending()[0]["id"]
    live.ctx.db.run("UPDATE reminders SET due = 0 WHERE id = ?", (rid,))
    wait_for(lambda: live.pushes, timeout=90)  # the worker checks every minute
    assert live.pushes[0]["body"] == "Send the thank-you note"
    page.reload()
    page.locator("li:has-text('Send the thank-you note')").get_by_role("button", name="snooze 1 hour").click()
    wait_for(lambda: len(live.ctx.reminders.pending()) == 1)
    no_problems(page)


# ------------------------------------------------------------------- intake

def test_inbox_check_creates_roles_and_undo(signed_in):
    from ..conftest import gmail_message
    from ..test_intake import LINKEDIN_ALERT
    page = signed_in
    live = page.live
    connect_claude(page)
    live.ctx.settings.set("gmail_refresh_token", "rt")
    rid = live.ctx.roles.create({"company": "Initech", "title": "Ops", "status": "applied"})
    live.google.messages["alert0001"] = gmail_message("alert0001", "jobalerts-noreply@linkedin.com", "Jobs for you", LINKEDIN_ALERT)
    live.google.messages["employ001"] = gmail_message("employ001", "Recruiting <no-reply@initech.com>", "Update on your application", "Sadly no.")
    live.runner.script["email"] = lambda *_: Result(ok=True, data={"kind": "rejection", "company": "Initech", "role_title": "Ops",
                                                                    "event_date": "", "summary": "Initech declined"})
    page.goto("/intake")
    page.get_by_role("button", name="Check Gmail now").click()
    page.wait_for_selector("text=2 new roles from linkedin", timeout=30000)
    assert live.ctx.roles.get(rid)["status"] == "rejected"
    page.locator("tr:has-text('Initech declined')").get_by_role("button", name="Undo").click()
    page.wait_for_selector("text=(undone)")
    assert live.ctx.roles.get(rid)["status"] == "applied"
    page.goto("/roles")
    assert page.locator("text=Acme Health").count() >= 1
    no_problems(page)


def test_watch_company(signed_in):
    page = signed_in
    page.goto("/intake")
    page.fill("#watch input[name=name]", "Acme")
    page.fill("#watch input[name=slug]", "https://jobs.lever.co/acme")
    page.fill("#watch input[name=keywords]", "operations")
    page.get_by_role("button", name="Watch").click()
    page.wait_for_selector("text=lever/acme")
    page.locator("#watch").get_by_role("button", name="remove").click()
    page.wait_for_selector("text=lever/acme", state="detached")
    no_problems(page)


# ------------------------------------------------------------------- export

def test_export_download(signed_in):
    page = signed_in
    page.live.ctx.roles.create({"company": "Acme", "title": "Ops"})
    page.goto("/settings")
    with page.expect_download() as dl:
        page.get_by_role("link", name="Download my data").click()
    z = zipfile.ZipFile(io.BytesIO(Path(dl.value.path()).read_bytes()))
    assert "roles.csv" in z.namelist() and b"Acme" in z.read("roles.csv")
    no_problems(page)


# ------------------------------------------------------------------- mobile

@pytest.mark.parametrize("path", ["/", "/roles", "/chat", "/profile", "/setup", "/practice"])
def test_phone_width_has_no_sideways_scroll(signed_in, path):
    page = signed_in
    page.set_viewport_size({"width": 360, "height": 740})
    page.goto(path)
    overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
    assert overflow <= 1, f"{path} scrolls sideways by {overflow}px"
    no_problems(page)


def test_service_worker_registers_and_manifest_is_installable(signed_in):
    page = signed_in
    page.goto("/")
    page.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller !== undefined")
    scope = page.evaluate("navigator.serviceWorker.ready.then(r => r.scope)")
    assert scope == f"{page.live.base}/"
    manifest = page.request.get("/manifest.webmanifest").json()
    assert manifest["display"] == "standalone" and {i["sizes"] for i in manifest["icons"]} >= {"192x192", "512x512"}
    for icon in manifest["icons"]:
        assert page.request.get(icon["src"]).ok
    no_problems(page)


# ------------------------------------------------------- outreach and trials

def test_outreach_draft_copy_send_and_follow_up(signed_in):
    page = signed_in
    live = page.live
    connect_claude(page)
    rid = live.ctx.roles.create({"company": "Acme Health", "title": "Marketing Ops Manager", "posting": POSTING})
    note = "Hi Dana, I'd love to hear how your team runs HubSpot."
    live.runner.script["outreach_connection_note"] = lambda *_: Result(ok=True, data={"subject": "", "body": note})
    page.goto(f"/roles/{rid}")
    page.fill("#people input[name=name]", "Dana Ruiz")
    page.select_option("#people select[name=relation]", "warm")
    page.fill("#people input[name=how_known]", "worked together at Initech")
    page.locator("#people").get_by_role("button", name="Add person").click()
    page.wait_for_selector("#people >> text=Dana Ruiz")
    assert live.ctx.roles.get(rid)["warm_path"].startswith("Dana Ruiz")
    page.get_by_role("button", name="Draft connection note").click()
    page.wait_for_selector("textarea[id^=body-]", timeout=20000)
    assert "HubSpot" in page.locator("textarea[id^=body-]").input_value()
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.get_by_role("button", name="Copy").click()
    assert "HubSpot" in page.evaluate("navigator.clipboard.readText()")
    page.get_by_role("button", name="I sent it").click()
    page.wait_for_selector("#people .pill.applied")
    assert any("Follow up with Dana Ruiz" in r["text"] for r in live.ctx.reminders.pending())
    page.goto("/outreach")
    assert page.locator("td >> text=sent").is_visible()
    no_problems(page)


def test_trial_timeline(signed_in):
    page = signed_in
    from datetime import date
    start = (date.today() - timedelta(days=77)).isoformat()  # 13 days left: both warnings still ahead, banner shows
    page.goto("/setup")
    form = page.locator("form.trial:has(input[value=gcp])")
    form.locator("input[name=start]").fill(start)
    form.get_by_role("button", name="Save").click()
    page.wait_for_selector("text=13 days left")
    page.goto("/")
    assert page.locator(".alert >> text=Google Cloud free trial").is_visible()
    assert len(page.live.ctx.reminders.pending()) == 2
    no_problems(page)


def test_claude_plan_sign_in_shown_in_settings(signed_in):
    page = signed_in
    page.goto("/settings")
    assert "sudo jobsctl claude-login" in page.content() and "not signed in" in page.content()
    page.check("input[name=mode][value=claude_login]")
    page.get_by_role("button", name="Save").click()
    assert "isn't signed in" in toast(page)
    page.live.ctx.claude_code.signed_in = True
    page.reload()
    page.check("input[name=mode][value=claude_login]")
    page.get_by_role("button", name="Save").click()
    assert toast(page) == "Saved."
    assert page.live.ctx.claude_ready()
    no_problems(page, expect_4xx=True)
