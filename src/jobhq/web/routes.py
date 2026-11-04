"""Every page and action. All signed-in routes depend on `require`; all POSTs are also
checked by GuardMiddleware for origin and CSRF token before they get here."""
from __future__ import annotations

import csv
import io
import json
import time
import unicodedata
import zipfile
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from .. import documents
from .. import practice as practice_mod
from .. import trials as trials_mod
from ..ai import tasks
from ..ai.runner import MODELS, AIUnavailable
from ..auth import GMAIL_SCOPE, STATE_TTL_S, AuthError, cookie_name, flow_cookie_name
from ..intake import FetchError, fetch_posting, posting_url
from ..notify import PRESETS, ReminderError, preset_due
from ..outreach import KINDS as OUTREACH_KINDS
from ..outreach import Outreach, OutreachError
from ..profile import EDUCATION_LIMITS, EXPERIENCE_LIMITS, WORK_MODES, clean_basics, clean_record, clean_years, pay_notes
from ..proposals import ProposalError
from ..roles import EDITABLE as ROLE_LIMITS
from ..roles import EVENT_KINDS, STATUSES, RoleError, grade, lane
from .common import ctx_of, error, fragment, not_found, page, redirect, refresh, require, same_site_path, session_of, toast

router = APIRouter()
signed_in = [Depends(require)]


async def form(request: Request) -> dict:
    data = await request.form()
    out: dict = {}
    for key, value in data.multi_items():
        if not isinstance(value, str):
            continue  # files are only accepted by the upload routes, which read the body themselves
        if key not in out:
            out[key] = value
        elif isinstance(out[key], list):
            out[key].append(value)
        else:
            out[key] = [out[key], value]
    return out


def _today(ctx) -> str:
    """Today's date where the user lives (the server clock may be in UTC)."""
    return datetime.now(ctx.config.tz).date().isoformat()


def as_list(value) -> list:
    return value if isinstance(value, list) else ([value] if value else [])


def int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


# ===================================================================== sign-in

# /login?error=CODE shows one of these fixed messages; the page never repeats text from the link.
LOGIN_ERRORS = {
    "signed_out": "Your session ended. Sign in again.",
    "expired": "That sign-in link expired. Please try again.",
}


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, error: str = ""):
    if session_of(request):
        return redirect(request, "/")
    return page(request, "login.html", title="Sign in", error_message=LOGIN_ERRORS.get(error, ""))


def _start_google(request: Request, purpose: str) -> Response:
    """Send the browser to Google, with a 10-minute cookie that ties the sign-in to this browser."""
    ctx = ctx_of(request)
    url, binding = ctx.google.begin(purpose)
    response = Response(status_code=303, headers={"Location": url})
    response.set_cookie(flow_cookie_name(ctx.config), binding, max_age=STATE_TTL_S, httponly=True, secure=ctx.config.secure,
                        samesite="lax", path="/")
    return response


@router.get("/auth/google")
def auth_google(request: Request):
    return _start_google(request, "login")


@router.get("/auth/gmail", dependencies=signed_in)
def auth_gmail(request: Request):
    return _start_google(request, "gmail")


@router.get("/auth/callback")
def auth_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    ctx = ctx_of(request)
    try:
        purpose, tokens = ctx.google.finish(state, code, error or None, request.cookies.get(flow_cookie_name(ctx.config)))
    except AuthError as exc:
        return page(request, "login.html", title="Sign in", error_message=str(exc))
    if purpose == "gmail":
        if session_of(request) is None:
            if tokens.get("refresh_token"):
                ctx.google.revoke(tokens["refresh_token"])  # nobody will keep it, so don't leave a live grant behind
            return page(request, "login.html", title="Sign in", error_message="Sign in first, then connect Gmail from the Setup page.")
        granted = set(str(tokens.get("scope", "")).split())
        if GMAIL_SCOPE not in granted:
            if tokens.get("refresh_token"):
                ctx.google.revoke(tokens["refresh_token"])
            return redirect(request, "/setup?gmail=missing")
        if not tokens.get("refresh_token"):
            return redirect(request, "/setup?gmail=norefresh")
        ctx.settings.set("gmail_refresh_token", tokens["refresh_token"])
        ctx.settings.set("gmail_status", "")
        ctx.queue.enqueue("inbox", {"first": True}, dedupe=f"inbox:first:{int(time.time() // 3600)}")
        return redirect(request, "/setup?gmail=ok")
    old = request.cookies.get(cookie_name(ctx.config))
    ctx.sessions.end(old)  # a fresh session id on every sign-in
    token, _ = ctx.sessions.create(request.headers.get("user-agent", ""), (request.client.host if request.client else ""))
    response = Response(status_code=303, headers={"Location": "/"})
    response.set_cookie(cookie_name(ctx.config), token, max_age=30 * 86400, httponly=True, secure=ctx.config.secure, samesite="lax", path="/")
    return response


@router.post("/logout", dependencies=signed_in)
def logout(request: Request):
    ctx = ctx_of(request)
    ctx.sessions.end(request.cookies.get(cookie_name(ctx.config)))
    response = redirect(request, "/login")
    response.delete_cookie(cookie_name(ctx.config), path="/", secure=ctx.config.secure, httponly=True, samesite="lax")
    return response


@router.post("/settings/sessions/end-all", dependencies=signed_in)
def end_all_sessions(request: Request):
    ctx_of(request).sessions.end_all()
    return redirect(request, "/login")


# ======================================================================= today

def setup_steps(ctx) -> list[dict]:
    basics = ctx.profile.basics()
    return [
        {"key": "claude", "done": ctx.claude_ready(), "title": "Connect Claude", "url": "/settings#claude"},
        {"key": "basics", "done": not ctx.profile.missing(), "title": "Fill in your basic information", "url": "/profile"},
        {"key": "history", "done": bool(ctx.profile.experience()), "title": "Add your work history", "url": "/profile#experience"},
        {"key": "interview", "done": len(ctx.profile.facts()) >= 5, "title": "Let the AI interview you", "url": "/interview"},
        {"key": "gmail", "done": ctx.settings.has("gmail_refresh_token"), "title": "Connect Gmail (read only)", "url": "/setup#gmail"},
        {"key": "alerts", "done": bool(ctx.settings.get("alerts_done")), "title": "Set up LinkedIn and Indeed job alerts", "url": "/setup#alerts"},
        {"key": "push", "done": ctx.push.count() > 0, "title": "Turn on notifications", "url": "/setup#push"},
        {"key": "watch", "done": bool(ctx.db.value("SELECT COUNT(*) FROM watch_companies")), "title": "Pick companies to watch", "url": "/intake#watch"},
        {"key": "name", "done": bool(basics.get("name")), "title": "", "url": ""},
    ][:-1]


@router.get("/", response_class=HTMLResponse, dependencies=signed_in)
def today(request: Request):
    ctx = ctx_of(request)
    steps = setup_steps(ctx)
    board = ctx.roles.board()
    return page(request, "today.html", title="Today", steps=steps, setup_left=sum(not s["done"] for s in steps), due=ctx.roles.due(),
                ready=board["ready"][:5], call=board["call"][:5], pipeline=board["pipeline"], reminders=ctx.reminders.pending()[:5],
                review=ctx.db.all("SELECT * FROM inbox_items WHERE status = 'review' ORDER BY received DESC LIMIT 5"),
                questions=ctx.db.all("SELECT * FROM profile_questions WHERE status = 'open' ORDER BY id LIMIT 3"),
                name=(ctx.profile.basics().get("name") or "").split(" ")[0], today_iso=_today(ctx),
                urgent_trials=trials_mod.urgent(ctx), outreach_due=Outreach(ctx).follow_ups_due(),
                gmail_status=ctx.settings.get("gmail_status", ""))


@router.get("/setup", response_class=HTMLResponse, dependencies=signed_in)
def setup(request: Request, gmail: str = ""):
    ctx = ctx_of(request)
    notes = {"ok": "Gmail is connected. The first check is running now.",
             "missing": "Gmail wasn't connected: the box to allow reading email was left unticked. Try again and tick it.",
             "norefresh": "Google didn't give lasting access. Remove the app at myaccount.google.com/permissions, then connect again."}
    return page(request, "setup.html", title="Setup", steps=setup_steps(ctx), gmail_note=notes.get(gmail, ""),
                gmail_status=ctx.settings.get("gmail_status", ""), gmail_last=ctx.settings.get("gmail_last_check"),
                redirect_uri=ctx.google.redirect_uri, vapid=ctx.push.public_key(), trials=trials_mod.get(ctx))


@router.post("/setup/trials", dependencies=signed_in)
async def setup_trials(request: Request):
    ctx, f = ctx_of(request), await form(request)
    try:
        result = trials_mod.save(ctx, str(f.get("name", "")), str(f.get("start", "")).strip(), f.get("days"))
    except trials_mod.TrialError as exc:
        return error(str(exc))
    if result.get("cleared"):
        return refresh(request, "Trial date cleared.")
    return refresh(request, f"Saved. It ends {result['end']}; {result['reminders']} reminders set.")


@router.post("/setup/alerts-done", dependencies=signed_in)
def alerts_done(request: Request):
    ctx_of(request).settings.set("alerts_done", True)
    return refresh(request, "Job alerts marked as set up.")


@router.post("/setup/gmail/disconnect", dependencies=signed_in)
def gmail_disconnect(request: Request):
    ctx = ctx_of(request)
    token = ctx.settings.get("gmail_refresh_token")
    if token:
        ctx.google.revoke(token)
    ctx.settings.delete("gmail_refresh_token")
    return refresh(request, "Gmail disconnected.")


# ==================================================================== settings

@router.get("/settings", response_class=HTMLResponse, dependencies=signed_in)
def settings_page(request: Request):
    ctx = ctx_of(request)
    s = ctx.settings
    return page(request, "settings.html", title="Settings", models=MODELS, model=ctx.model(), mode=ctx.claude_mode(),
                has_key=s.has("claude_api_key"), claude_code=ctx.claude_code.status(fresh=True), instance=ctx.config.instance_name,
                budget=s.get("monthly_budget_usd", 25.0), spent=ctx.spend.this_month(), paper=s.get("paper", "us-letter"),
                sessions=ctx.sessions.list(), runs=ctx.db.all("SELECT * FROM ai_runs ORDER BY id DESC LIMIT 15"),
                current=request.state.session.id_hash)


@router.post("/settings/claude", dependencies=signed_in)
async def settings_claude(request: Request):
    ctx, f = ctx_of(request), await form(request)
    mode = f.get("mode") if f.get("mode") in ("api_key", "claude_login") else ctx.claude_mode()
    key = str(f.get("api_key", "")).strip()
    # Check the whole form before saving any of it, so a rejected form changes nothing.
    if key:
        if not key.startswith("sk-ant-") or len(key) > 300 or not key.isascii() or " " in key:
            return error("That doesn't look like an Anthropic API key (it starts with sk-ant-).")
        if key.startswith("sk-ant-oat"):
            return error("That's a Claude plan token, not an API key. To use your Claude plan, sign in to Claude Code instead (see below).")
    if mode == "claude_login" and not ctx.claude_code.status(fresh=True)["signed_in"]:
        return error("Your Claude plan isn't signed in on the server yet. Whoever runs your server can do it: the steps are under Claude on this page.")
    budget = _dollars(str(f.get("budget", "")))
    if budget is None:
        return error("The budget must be a dollar amount above zero, like 25 or $25.00.")
    if key:
        ctx.settings.set("claude_api_key", key)
    ctx.settings.set("claude_mode", mode)
    if f.get("model") in MODELS:
        ctx.settings.set("model", f["model"])
    ctx.settings.set("monthly_budget_usd", max(1.0, min(budget, 1000.0)))
    if f.get("paper") in ("us-letter", "a4"):
        ctx.settings.set("paper", f["paper"])
    return toast("Saved.")


def _dollars(raw: str) -> float | None:
    """'$25', '25.00' or '1,000' as dollars; blank means the default 25. None for anything else, including
    nan, inf and negative amounts."""
    import math
    text = raw.strip().removeprefix("$").replace(",", "").strip()
    if not text:
        return 25.0
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) and value > 0 else None


@router.post("/settings/claude/forget", dependencies=signed_in)
def settings_forget(request: Request):
    ctx_of(request).settings.delete("claude_api_key")
    return refresh(request, "Saved API key deleted.")


@router.post("/settings/claude/logout", dependencies=signed_in)
def settings_claude_logout(request: Request):
    ctx = ctx_of(request)
    ctx.claude_code.logout()
    ctx.settings.set("claude_mode", "api_key")
    return refresh(request, "Your Claude plan is signed out on this copy.")


@router.post("/settings/claude/test", dependencies=signed_in)
async def settings_test(request: Request):
    ctx = ctx_of(request)
    try:
        result = await ctx.runner.run("test", "Reply with exactly: ready", "Are you there?", max_turns=1, budget_usd=0.05)
    except AIUnavailable as exc:
        return HTMLResponse(f'<span class="bad">{_esc(str(exc))}</span>')
    if not result.ok:
        return HTMLResponse(f'<span class="bad">{_esc(result.error)}</span>')
    return HTMLResponse(f'<span class="good">Claude answered ({_esc(ctx.model())}, ${result.cost_usd:.4f}).</span>')


def _esc(text: str) -> str:
    from markupsafe import escape
    return str(escape(text))


# ===================================================================== profile

@router.get("/profile", response_class=HTMLResponse, dependencies=signed_in)
def profile_page(request: Request):
    ctx = ctx_of(request)
    p = ctx.profile
    jobs = [{**j, "facts": p.facts(j["id"])} for j in p.experience()]
    return page(request, "profile.html", title="Profile", b=p.basics(), jobs=jobs, education=p.education(), skills=p.skills(),
                loose=[f for f in p.facts() if f["experience_id"] is None], modes=WORK_MODES, missing=p.missing(), errors={},
                certifications=p.certifications(), projects=p.projects())


@router.post("/profile/basics", dependencies=signed_in)
async def profile_basics(request: Request):
    ctx, f = ctx_of(request), await form(request)
    f["work_modes"] = as_list(f.get("work_modes"))
    data, errors = clean_basics(f)
    if errors:
        return error("Fix: " + "; ".join(f"{k.replace('_', ' ')}: {v}" for k, v in errors.items()), fields=errors)
    with ctx.db.tx() as conn:
        before = ctx.profile.save_basics(data, conn)
        ctx.audit.log(conn, "user", "update", "profile", "basics", before, data, "Updated basic information")
    return toast(" ".join(["Saved.", *pay_notes(data)]))


@router.post("/profile/experience", dependencies=signed_in)
async def experience_save(request: Request):
    ctx, f = ctx_of(request), await form(request)
    data, problem = clean_record(f, EXPERIENCE_LIMITS, {"company": "Add the company and the job title.", "title": "Add the company and the job title."})
    if problem:
        return error(problem)
    company, title = data["company"], data["title"]
    values = tuple(data[k] for k in ("company", "title", "location", "start_date", "end_date", "summary"))
    exp_id = int_or_none(f.get("id"))
    if exp_id:
        ctx.db.run("UPDATE experience SET company = ?, title = ?, location = ?, start_date = ?, end_date = ?, summary = ? WHERE id = ?", (*values, exp_id))
    else:
        ctx.db.run("INSERT INTO experience (company, title, location, start_date, end_date, summary) VALUES (?, ?, ?, ?, ?, ?)", values)
    return refresh(request, "Job saved." if exp_id else f"Added {title} at {company}.")


@router.post("/profile/experience/{exp_id}/delete", dependencies=signed_in)
def experience_delete(request: Request, exp_id: int):
    ctx_of(request).db.run("DELETE FROM experience WHERE id = ?", (exp_id,))
    return refresh(request, "Job deleted.")


@router.post("/profile/facts", dependencies=signed_in)
async def fact_add(request: Request):
    ctx, f = ctx_of(request), await form(request)
    text = str(f.get("text", "")).strip()
    if not text:
        return error("Write the fact first.")
    exp_id = int_or_none(f.get("experience_id"))
    if exp_id and not ctx.db.one("SELECT 1 FROM experience WHERE id = ?", (exp_id,)):
        return error("That job doesn't exist.")
    with ctx.db.tx() as conn:
        fid = ctx.profile.add_fact(conn, text, str(f.get("topic", "")), exp_id, "user")
        ctx.audit.log(conn, "user", "create", "fact", fid, None, {"text": text}, f"Added fact: {text[:60]}")
    return refresh(request, "Fact added.")


@router.post("/profile/facts/{fact_id}/delete", dependencies=signed_in)
def fact_delete(request: Request, fact_id: int):
    ctx_of(request).db.run("DELETE FROM facts WHERE id = ?", (fact_id,))
    return refresh(request, "Fact deleted.")


@router.post("/profile/education", dependencies=signed_in)
async def education_add(request: Request):
    ctx, f = ctx_of(request), await form(request)
    data, problem = clean_record(f, EDUCATION_LIMITS, {"school": "Add the school."})
    if problem:
        return error(problem)
    school = data["school"]
    ctx.db.run("INSERT INTO education (school, degree, field, start_date, end_date) VALUES (?, ?, ?, ?, ?)",
               tuple(data[k] for k in ("school", "degree", "field", "start_date", "end_date")))
    return refresh(request, f"Added {school}.")


@router.post("/profile/education/{edu_id}/delete", dependencies=signed_in)
def education_delete(request: Request, edu_id: int):
    ctx_of(request).db.run("DELETE FROM education WHERE id = ?", (edu_id,))
    return refresh(request, "Education deleted.")


@router.post("/profile/certifications", dependencies=signed_in)
async def certification_add(request: Request):
    ctx, f = ctx_of(request), await form(request)
    with ctx.db.tx() as conn:
        cert_id, problem = ctx.profile.add_certification(conn, f)
        if problem:
            return error(problem)
        ctx.audit.log(conn, "user", "create", "certification", cert_id, None, {"name": f.get("cert_name")}, f"Added {f.get('cert_name')}")
    return refresh(request, "Certification added.")


@router.post("/profile/certifications/{cert_id}/delete", dependencies=signed_in)
def certification_delete(request: Request, cert_id: int):
    ctx_of(request).db.run("DELETE FROM certifications WHERE id = ?", (cert_id,))
    return refresh(request, "Certification deleted.")


@router.post("/profile/projects", dependencies=signed_in)
async def project_add(request: Request):
    ctx, f = ctx_of(request), await form(request)
    with ctx.db.tx() as conn:
        project_id, problem = ctx.profile.add_project(conn, f)
        if problem:
            return error(problem)
        ctx.audit.log(conn, "user", "create", "project", project_id, None, {"name": f.get("project_name")}, f"Added {f.get('project_name')}")
    return refresh(request, "Project added.")


@router.post("/profile/projects/{project_id}/delete", dependencies=signed_in)
def project_delete(request: Request, project_id: int):
    ctx_of(request).db.run("DELETE FROM projects WHERE id = ?", (project_id,))
    return refresh(request, "Project deleted.")


@router.post("/profile/skills", dependencies=signed_in)
async def skill_add(request: Request):
    ctx, f = ctx_of(request), await form(request)
    name = " ".join(str(f.get("name", "")).split())[:80]
    status = f.get("status") if f.get("status") in ("have", "never") else "have"
    if not name:
        return error("Name the skill or tool.")
    years, problem = clean_years(f.get("years"))
    if problem:
        return error(problem)
    with ctx.db.tx() as conn:
        ctx.profile.set_skill(conn, name, status, str(f.get("level", "")), years, str(f.get("note", "")))
    return refresh(request, f"Saved {name}.")


@router.post("/profile/skills/{skill_id}/delete", dependencies=signed_in)
def skill_delete(request: Request, skill_id: int):
    ctx_of(request).db.run("DELETE FROM skills WHERE id = ?", (skill_id,))
    return refresh(request, "Skill removed.")


# ================================================================= suggestions

@router.get("/suggestions", response_class=HTMLResponse, dependencies=signed_in)
def suggestions(request: Request):
    return page(request, "suggestions.html", title="Suggestions", items=ctx_of(request).proposals.pending())


@router.post("/suggestions/{pid}/{decision}", dependencies=signed_in)
async def suggestion_decide(request: Request, pid: int, decision: str):
    ctx, f = ctx_of(request), await form(request)
    if decision not in ("approve", "reject"):
        raise not_found()
    try:
        message = ctx.proposals.decide(pid, decision == "approve", f.get("text") if "text" in f else None)
    except (ProposalError, ValueError) as exc:
        return error(str(exc))
    return toast(message)


# ================================================================= chat & interview

def _thread(ctx, kind: str, thread_id: int | None = None) -> dict:
    if thread_id:
        row = ctx.db.one("SELECT * FROM chat_threads WHERE id = ? AND kind = ?", (thread_id, kind))
        if row:
            return row
    row = ctx.db.one("SELECT * FROM chat_threads WHERE kind = ? ORDER BY updated DESC LIMIT 1", (kind,))
    if row:
        return row
    now = time.time()
    tid = ctx.db.run("INSERT INTO chat_threads (kind, title, created, updated) VALUES (?, ?, ?, ?)", (kind, kind.title(), now, now)).lastrowid
    if kind == "interview":
        ctx.db.run("INSERT INTO chat_messages (thread_id, role, content, at) VALUES (?, 'assistant', ?, ?)", (tid, (
            "I'll ask you questions one at a time to build your profile: your results, the tools you've used, and what you want next. "
            "Anything worth keeping becomes a suggestion you approve before it's saved. Say **start** when you're ready."), now))
    return ctx.db.one("SELECT * FROM chat_threads WHERE id = ?", (tid,))


def _messages(ctx, thread_id: int) -> list[dict]:
    rows = ctx.db.all("SELECT * FROM chat_messages WHERE thread_id = ? ORDER BY id DESC LIMIT 60", (thread_id,))
    for row in rows:
        row["meta"] = json.loads(row["meta"] or "{}")
    return list(reversed(rows))


@router.get("/chat", response_class=HTMLResponse, dependencies=signed_in)
def chat_page(request: Request, t: int | None = None):
    ctx = ctx_of(request)
    thread = _thread(ctx, "chat", t)
    threads = ctx.db.all("SELECT * FROM chat_threads WHERE kind = 'chat' ORDER BY updated DESC LIMIT 20")
    return page(request, "chat.html", title="Chat", thread=thread, threads=threads, messages=_messages(ctx, thread["id"]), kind="chat")


@router.get("/interview", response_class=HTMLResponse, dependencies=signed_in)
def interview_page(request: Request):
    ctx = ctx_of(request)
    thread = _thread(ctx, "interview")
    return page(request, "chat.html", title="Interview", thread=thread, threads=[], messages=_messages(ctx, thread["id"]), kind="interview",
                questions=ctx.db.all("SELECT * FROM profile_questions WHERE status = 'open' ORDER BY id LIMIT 10"))


@router.post("/chat/new", dependencies=signed_in)
def chat_new(request: Request):
    ctx = ctx_of(request)
    now = time.time()
    tid = ctx.db.run("INSERT INTO chat_threads (kind, title, created, updated) VALUES ('chat', ?, ?, ?)",
                     (datetime.now(ctx.config.tz).strftime("Chat %b %-d"), now, now)).lastrowid
    return redirect(request, f"/chat?t={tid}")


@router.post("/chat/{thread_id}/send", response_class=HTMLResponse, dependencies=signed_in)
async def chat_send(request: Request, thread_id: int):
    ctx, f = ctx_of(request), await form(request)
    text = str(f.get("text", "")).strip()[:6000]
    thread = ctx.db.one("SELECT * FROM chat_threads WHERE id = ?", (thread_id,))
    if not thread:
        raise not_found()
    if not text:
        return error("Write a message first.")
    if not ctx.claude_ready():
        return error("Connect Claude in Settings first.")
    if ctx.db.one("SELECT 1 FROM chat_messages WHERE thread_id = ? AND status = 'pending'", (thread_id,)):
        return error("Wait for the current reply to finish.")
    now = time.time()
    with ctx.db.tx() as conn:
        uid = conn.execute("INSERT INTO chat_messages (thread_id, role, content, at) VALUES (?, 'user', ?, ?)", (thread_id, text, now)).lastrowid
        aid = conn.execute("INSERT INTO chat_messages (thread_id, role, content, status, at) VALUES (?, 'assistant', '', 'pending', ?)",
                           (thread_id, now)).lastrowid
        if thread["title"] in ("Chat", "") or thread["title"].startswith("Chat "):
            conn.execute("UPDATE chat_threads SET title = ? WHERE id = ?", (text[:60], thread_id))
    ctx.queue.enqueue("chat", {"message_id": aid}, max_attempts=1)
    rows = ctx.db.all("SELECT * FROM chat_messages WHERE id IN (?, ?) ORDER BY id", (uid, aid))
    for row in rows:
        row["meta"] = {}
    return fragment(request, "_messages.html", messages=rows)


@router.get("/chat/msg/{message_id}", response_class=HTMLResponse, dependencies=signed_in)
def chat_message(request: Request, message_id: int):
    ctx = ctx_of(request)
    row = ctx.db.one("SELECT * FROM chat_messages WHERE id = ?", (message_id,))
    if not row:
        raise not_found()
    if row["status"] == "pending" and time.time() - row["at"] > 900:  # the job died or was lost; stop the spinner
        ctx.db.run("UPDATE chat_messages SET status = 'failed', content = ? WHERE id = ? AND status = 'pending'",
                   ("Sorry, that took too long and was stopped. Try again.", message_id))
        row = ctx.db.one("SELECT * FROM chat_messages WHERE id = ?", (message_id,))
    row["meta"] = json.loads(row["meta"] or "{}")
    return fragment(request, "_messages.html", messages=[row])


@router.post("/undo/{audit_id}", dependencies=signed_in)
def undo(request: Request, audit_id: int):
    try:
        summary = ctx_of(request).roles.undo(audit_id)
    except RoleError as exc:
        return error(str(exc))
    return toast(f"Undone: {summary}", html='<span class="muted">Undone</span>')


@router.post("/questions/{qid}/{action}", dependencies=signed_in)
async def question_action(request: Request, qid: int, action: str):
    ctx, f = ctx_of(request), await form(request)
    q = ctx.db.one("SELECT * FROM profile_questions WHERE id = ? AND status = 'open'", (qid,))
    if not q:
        return error("That question was already handled.")
    if action == "skip":
        ctx.db.run("UPDATE profile_questions SET status = 'skipped', answered = ? WHERE id = ?", (time.time(), qid))
        return toast("Skipped.", html="")
    if action == "never":
        with ctx.db.tx() as conn:
            ctx.profile.set_skill(conn, q["topic"], "never")
            conn.execute("UPDATE profile_questions SET status = 'answered', answer = 'never used', answered = ? WHERE id = ?", (time.time(), qid))
        return toast(f"Saved: never used {q['topic']}.", html="")
    if action == "answer":
        answer = str(f.get("answer", "")).strip()[:2000]
        if not answer:
            return error("Write an answer first.")
        thread = _thread(ctx, "interview")
        now = time.time()
        with ctx.db.tx() as conn:
            conn.execute("UPDATE profile_questions SET status = 'answered', answer = ?, answered = ? WHERE id = ?", (answer, now, qid))
            conn.execute("INSERT INTO chat_messages (thread_id, role, content, at) VALUES (?, 'user', ?, ?)",
                         (thread["id"], f"About {q['topic']} ({q['question']}): {answer}", now))
            aid = conn.execute("INSERT INTO chat_messages (thread_id, role, content, status, at) VALUES (?, 'assistant', '', 'pending', ?)",
                               (thread["id"], now)).lastrowid
        ctx.queue.enqueue("chat", {"message_id": aid}, max_attempts=1)
        return redirect(request, "/interview")
    raise not_found()


# ======================================================================= roles

def _role_view(role: dict) -> dict:
    name, why = lane(role)
    return {**role, "lane": name, "lane_why": why, "grade": grade(role["fit_score"]),
            "fit_d": json.loads(role["fit"] or "{}"), "gates_d": json.loads(role["gates"] or "{}")}


@router.get("/roles", response_class=HTMLResponse, dependencies=signed_in)
def roles_page(request: Request, show: str = "lanes"):
    ctx = ctx_of(request)
    return page(request, "roles.html", title="Roles", board=ctx.roles.board(), show=show, counts=ctx.roles.counts())


@router.get("/roles/new", response_class=HTMLResponse, dependencies=signed_in)
def role_new(request: Request):
    return page(request, "role_new.html", title="Add a role")


@router.post("/roles", dependencies=signed_in)
async def role_create(request: Request):
    ctx, f = ctx_of(request), await form(request)
    fields = {k: str(f.get(k, "")) for k in ("company", "title", "url", "location", "work_mode", "posting", "notes", "warm_path")}
    for key, label in (("posting", "The posting"), ("notes", "The notes")):
        if len(fields[key].strip()) > ROLE_LIMITS[key]:
            return error(f"{label} is over {ROLE_LIMITS[key]:,} characters ({len(fields[key].strip()):,}). Trim it and save again.")
    fields["url"] = posting_url(fields["url"])
    note = ""
    if fields["url"] and not fields["posting"].strip():
        try:  # a slow site must not hold up the server, and a failed fetch never stops the role being saved
            fields["posting"] = await run_in_threadpool(fetch_posting, ctx, fields["url"])
        except FetchError as exc:
            why = str(exc) if "Paste" in str(exc) else f"{exc} Paste the posting text on the role page."
            note = f"Saved, but the posting couldn't be fetched. {why}"
    try:
        role_id = ctx.roles.create({k: v for k, v in fields.items() if v}, actor="user")
    except RoleError as exc:
        return error(str(exc))
    if ctx.roles.get(role_id)["posting"] and ctx.claude_ready():
        ctx.queue.enqueue("score", {"role_id": role_id}, dedupe=f"score:{role_id}:{int(time.time())}")
    response = redirect(request, f"/roles/{role_id}")
    if note:
        response.headers["HX-Trigger"] = json.dumps({"jobhq:toast": note})
    return response


@router.get("/roles/{role_id}", response_class=HTMLResponse, dependencies=signed_in)
def role_page(request: Request, role_id: int):
    ctx = ctx_of(request)
    role = ctx.roles.get(role_id)
    if not role:
        raise not_found()
    docs = ctx.db.all("SELECT id, kind, status, pages, updated FROM documents WHERE role_id = ? ORDER BY id DESC", (role_id,))
    history = ctx.db.all("SELECT * FROM audit WHERE entity = 'role' AND entity_id = ? ORDER BY id DESC LIMIT 15", (str(role_id),))
    return page(request, "role.html", title=f"{role['title']} at {role['company']}", r=_role_view(role), events=ctx.roles.events(role_id), docs=docs,
                contacts=Outreach(ctx).for_role(role_id), outreach_kinds=OUTREACH_KINDS,
                statuses=STATUSES, event_kinds=EVENT_KINDS, history=history, presets=PRESETS, today_iso=_today(ctx),
                reminders=ctx.db.all("SELECT * FROM reminders WHERE role_id = ? AND status = 'pending' ORDER BY due", (role_id,)))


@router.post("/roles/{role_id}", dependencies=signed_in)
async def role_update(request: Request, role_id: int):
    ctx, f = ctx_of(request), await form(request)
    allowed = ("company", "title", "url", "location", "work_mode", "posting", "posted_on", "status", "warm_path", "next_step",
               "next_step_date", "applied_on", "notes", "pay_min", "pay_max")
    fields = {k: f[k] for k in allowed if k in f}
    try:
        audit_id = ctx.roles.update(role_id, fields, actor="user")
    except RoleError as exc:
        return error(str(exc))
    if "posting" in fields and audit_id and ctx.claude_ready():
        ctx.queue.enqueue("score", {"role_id": role_id}, dedupe=f"score:{role_id}:{int(time.time())}")
    if not audit_id:
        return toast("No changes.")
    return refresh(request, f"Status: {fields['status']}." if set(fields) == {"status"} else "Saved.")


@router.post("/roles/{role_id}/event", dependencies=signed_in)
async def role_event(request: Request, role_id: int):
    ctx, f = ctx_of(request), await form(request)
    try:
        ctx.roles.add_event(role_id, str(f.get("kind", "note")), str(f.get("on_date", "")) or _today(ctx), str(f.get("text", "")))
    except RoleError as exc:
        return error(str(exc))
    return refresh(request, "Added to the timeline.")


@router.post("/roles/{role_id}/event/{event_id}/delete", dependencies=signed_in)
def role_event_delete(request: Request, role_id: int, event_id: int):
    ctx_of(request).db.run("DELETE FROM role_events WHERE id = ? AND role_id = ?", (event_id, role_id))
    return refresh(request, "Timeline entry deleted.")


@router.post("/roles/{role_id}/score", dependencies=signed_in)
def role_score(request: Request, role_id: int):
    ctx = ctx_of(request)
    role = ctx.roles.get(role_id)
    if not role:
        raise not_found()
    if not ctx.claude_ready():
        return error("Connect Claude in Settings first.")
    job_id = ctx.queue.enqueue("score", {"role_id": role_id}, dedupe=f"score:{role_id}:{int(time.time())}")
    return fragment(request, "_job.html", job_id=job_id, label="Scoring", done_url=f"/roles/{role_id}")


@router.post("/roles/{role_id}/fetch", dependencies=signed_in)
def role_fetch(request: Request, role_id: int):
    ctx = ctx_of(request)
    role = ctx.roles.get(role_id)
    if not role or not role["url"]:
        return error("Add the posting's link first.")
    try:
        text = fetch_posting(ctx, role["url"])
    except FetchError as exc:
        return error(str(exc))
    if len(text) < 200:
        return error("That page had almost no text (it may need a login). Paste the posting instead.")
    ctx.roles.update(role_id, {"posting": text}, actor="user", summary="Fetched the posting")
    return refresh(request, "Posting fetched from the link.")


@router.post("/roles/{role_id}/documents/{kind}", dependencies=signed_in)
def role_document(request: Request, role_id: int, kind: str):
    ctx = ctx_of(request)
    if kind not in ("resume", "cover_letter"):
        raise not_found()
    if not ctx.claude_ready():
        return error("Connect Claude in Settings first.")
    job_id = ctx.queue.enqueue("draft", {"role_id": role_id, "kind": kind}, max_attempts=1)
    return fragment(request, "_job.html", job_id=job_id, label="Drafting" if kind == "resume" else "Writing", done_url=None)


@router.get("/jobs/{job_id}", response_class=HTMLResponse, dependencies=signed_in)
def job_status(request: Request, job_id: int, done_url: str = ""):
    ctx = ctx_of(request)
    job = ctx.queue.get(job_id)
    if not job:
        raise not_found()
    if job["status"] == "done":
        result = json.loads(job["result"] or "{}")
        if result.get("ok") is False:
            return HTMLResponse(f'<span class="bad">{_esc(result.get("error") or "It failed.")}</span>')
        target = result.get("url") or done_url or ""
        if same_site_path(target):
            return Response(status_code=204, headers={"HX-Redirect": target})
        return refresh(request, "Done.")
    if job["status"] == "failed":
        return HTMLResponse('<span class="bad">That failed. Try again in a minute.</span>')
    return Response(status_code=204)  # still working: nothing to swap, so the "working" message isn't re-announced


# =================================================================== documents

@router.get("/documents", response_class=HTMLResponse, dependencies=signed_in)
def documents_page(request: Request):
    ctx = ctx_of(request)
    docs = ctx.db.all("SELECT d.id, d.kind, d.status, d.pages, d.updated, d.checks, r.company, r.title, r.id AS role_id FROM documents d "
                      "JOIN roles r ON r.id = d.role_id ORDER BY d.updated DESC LIMIT 100")
    for d in docs:
        d["failed"] = sum(not c["ok"] for c in json.loads(d["checks"] or "[]"))
    return page(request, "documents.html", title="Documents", docs=docs)


@router.get("/documents/{doc_id}", response_class=HTMLResponse, dependencies=signed_in)
def document_page(request: Request, doc_id: int):
    ctx = ctx_of(request)
    doc = documents.get(ctx, doc_id)
    if not doc:
        raise not_found()
    facts = {f["id"]: f["text"] for f in ctx.profile.facts()}
    jobs = {j["id"]: j for j in ctx.profile.experience()}
    role = ctx.roles.get(doc["role_id"]) or {"id": doc["role_id"], "title": "", "company": ""}
    kind = "Resume" if doc["kind"] == "resume" else "Cover letter"
    return page(request, "document.html", title=f"{kind}, {role['title']} at {role['company']}", d=doc, role=role, facts=facts, jobs=jobs)


@router.post("/documents/{doc_id}/edit", dependencies=signed_in)
async def document_edit(request: Request, doc_id: int):
    ctx, f = ctx_of(request), await form(request)
    doc = documents.get(ctx, doc_id)
    if not doc:
        raise not_found()
    content = doc["content"]
    if doc["kind"] == "resume":
        content["summary"] = str(f.get("summary", content.get("summary", "")))[:1500]
        for e_i, entry in enumerate(content.get("experience", [])):
            for b_i, bullet in enumerate(entry.get("bullets", [])):
                key = f"b-{e_i}-{b_i}"
                if key in f:
                    bullet["text"] = str(f[key]).strip()[:600]
            entry["bullets"] = [b for b in entry["bullets"] if b["text"]]
        if "skills" in f:
            content["skills"] = [s.strip()[:60] for s in str(f["skills"]).split(",") if s.strip()][:24]
    else:
        for key in ("greeting", "closing"):
            if key in f:
                content[key] = str(f[key]).strip()[:200]
        for p_i, para in enumerate(content.get("paragraphs", [])):
            if f"p-{p_i}" in f:
                para["text"] = str(f[f"p-{p_i}"]).strip()[:2500]
        content["paragraphs"] = [p for p in content["paragraphs"] if p["text"]]
    try:
        await documents.rebuild(ctx, doc_id, content)
    except documents.DocumentError as exc:
        return error(str(exc))
    return refresh(request, "Rebuilt the PDF and re-ran the checks.")


@router.post("/documents/{doc_id}/approve", dependencies=signed_in)
def document_approve(request: Request, doc_id: int):
    ctx = ctx_of(request)
    if not ctx.db.run("UPDATE documents SET status = 'approved', updated = ? WHERE id = ?", (time.time(), doc_id)).rowcount:
        raise not_found()
    return refresh(request, "Approved.")


@router.post("/documents/{doc_id}/delete", dependencies=signed_in)
def document_delete(request: Request, doc_id: int):
    ctx = ctx_of(request)
    doc = ctx.db.one("SELECT role_id FROM documents WHERE id = ?", (doc_id,))
    documents.delete(ctx, doc_id)
    return redirect(request, f"/roles/{doc['role_id']}" if doc else "/documents")


@router.get("/documents/{doc_id}/pdf", dependencies=signed_in)
def document_pdf(request: Request, doc_id: int, download: int = 0):
    ctx = ctx_of(request)
    doc = ctx.db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    path = documents.pdf_path(ctx, doc) if doc else None
    if not path:
        raise not_found()
    role = ctx.roles.get(doc["role_id"]) or {"company": ""}
    real, fallback = pdf_names(ctx.profile.basics().get("name") or "", doc["kind"], role["company"])
    # HTTP headers are Latin-1 only, so the plain `filename` is ASCII (accents stripped, other scripts dropped) and
    # `filename*` carries the real UTF-8 name, so 王伟 or Nguyễn gets their own name on the file.
    return FileResponse(path, media_type="application/pdf", headers={
        "Content-Disposition": f'{"attachment" if download else "inline"}; filename="{fallback}"; filename*=UTF-8\'\'{quote(real, safe="")}',
        "Content-Security-Policy": "sandbox; default-src 'none'; object-src 'none'"})


def pdf_names(name: str, kind: str, company: str) -> tuple[str, str]:
    """The download name for a document's PDF, as (real UTF-8 name, ASCII-only fallback)."""
    def ascii_only(text: str) -> str:
        return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()

    def build(person: str, firm: str) -> str:
        person = "".join(ch for ch in "_".join(person.split()) if ch.isalnum() or ch in "_-")[:60].strip("_-")
        firm = "".join(ch for ch in firm if ch.isalnum())[:40] or "Company"
        return "_".join(p for p in (person, "Resume" if kind == "resume" else "Cover_Letter", firm) if p) + ".pdf"

    return build(name, company), build(ascii_only(name), ascii_only(company))


# ==================================================================== practice

@router.get("/practice", response_class=HTMLResponse, dependencies=signed_in)
def practice_page(request: Request):
    ctx = ctx_of(request)
    roles = ctx.roles.all(("saved", "applied", "interviewing", "offer"))
    return page(request, "practice.html", title="Practice", roles=roles, history=practice_mod.history(ctx))


@router.post("/practice/question", dependencies=signed_in)
async def practice_new(request: Request):
    ctx, f = ctx_of(request), await form(request)
    role_id = int_or_none(f.get("role_id"))
    if role_id and not ctx.roles.get(role_id):
        return error("That role doesn't exist.")
    own = str(f.get("question", "")).strip()
    if own:
        qid = practice_mod.new_question(ctx, role_id, own, "your question")
        return redirect(request, f"/practice/q/{qid}")
    if not ctx.claude_ready():
        return error("Connect Claude in Settings first, or type your own question.")
    asked = [r["question"] for r in ctx.db.all("SELECT question FROM practice_questions WHERE role_id IS ? ORDER BY id DESC LIMIT 15", (role_id,))]
    try:
        q = await tasks.practice_question(ctx, role_id, asked)
    except AIUnavailable as exc:
        return error(str(exc))
    if not q:
        return error("Couldn't write a question. Try again.")
    qid = practice_mod.new_question(ctx, role_id, q["question"], q["focus"])
    return redirect(request, f"/practice/q/{qid}")


@router.get("/practice/q/{qid}", response_class=HTMLResponse, dependencies=signed_in)
def practice_question_page(request: Request, qid: int):
    ctx = ctx_of(request)
    q = ctx.db.one("SELECT q.*, r.company, r.title FROM practice_questions q LEFT JOIN roles r ON r.id = q.role_id WHERE q.id = ?", (qid,))
    if not q:
        raise not_found()
    attempts = ctx.db.all("SELECT * FROM practice_attempts WHERE question_id = ? ORDER BY id DESC", (qid,))
    for a in attempts:
        a["grade_d"] = json.loads(a["grade"] or "{}")
    start = q["question"] if len(q["question"]) <= 60 else q["question"][:57].rsplit(" ", 1)[0] + "…"
    return page(request, "practice_q.html", title=f"Practice: {start}", q=q, attempts=attempts)


@router.post("/practice/q/{qid}/answer", dependencies=signed_in)
async def practice_answer(request: Request, qid: int):
    ctx, f = ctx_of(request), await form(request)
    if not ctx.db.one("SELECT 1 FROM practice_questions WHERE id = ?", (qid,)):
        raise not_found()
    try:
        seconds = float(f["seconds"]) if f.get("seconds") else None
        aid = practice_mod.add_attempt(ctx, qid, str(f.get("answer", "")), "typed", seconds)
    except (practice_mod.VoiceError, ValueError) as exc:
        return error(str(exc))
    ctx.queue.enqueue("grade", {"attempt_id": aid}, max_attempts=2)
    return refresh(request, "Answer saved. Grading it now.")


@router.post("/practice/q/{qid}/voice", dependencies=signed_in)
async def practice_voice(request: Request, qid: int):
    ctx = ctx_of(request)
    q = ctx.db.one("SELECT q.*, r.company FROM practice_questions q LEFT JOIN roles r ON r.id = q.role_id WHERE q.id = ?", (qid,))
    if not q:
        raise not_found()
    data = await request.body()
    hint = ", ".join(s["name"] for s in ctx.profile.skills() if s["status"] == "have")[:150] + (f", {q['company']}" if q["company"] else "")
    try:
        import asyncio
        text, seconds = await asyncio.to_thread(practice_mod.transcribe, ctx, data, request.headers.get("content-type", ""), hint)
        aid = practice_mod.add_attempt(ctx, qid, text, "voice", seconds)
    except practice_mod.VoiceError as exc:
        return error(str(exc))
    finally:
        del data
    ctx.queue.enqueue("grade", {"attempt_id": aid}, max_attempts=2)
    return refresh(request, "Answer transcribed. Grading it now.")


# =================================================================== reminders

@router.get("/reminders", response_class=HTMLResponse, dependencies=signed_in)
def reminders_page(request: Request):
    ctx = ctx_of(request)
    return page(request, "reminders.html", title="Reminders", pending=ctx.reminders.pending(), sent=ctx.reminders.recent_sent(),
                presets=PRESETS, push_count=ctx.push.count())


@router.post("/reminders", dependencies=signed_in)
async def reminder_add(request: Request):
    ctx, f = ctx_of(request), await form(request)
    preset, when = str(f.get("preset", "")), str(f.get("when", "")).strip()
    if preset in PRESETS:
        due = preset_due(preset, ctx.reminders.now())
        if due is None:
            return error("That time has already passed; pick a later one.")
    else:
        due = ctx.reminders.parse_local(when)
        if due is None:
            return error("Use the date and time picker to choose when." if when else "Pick a time.")
    role_id = int_or_none(f.get("role_id"))
    if f.get("role_id") and (role_id is None or not ctx.roles.get(role_id)):
        return error("That role doesn't exist anymore. Reload the page and try again.")
    try:
        ctx.reminders.set(str(f.get("text", "")), due, role_id)
    except ReminderError as exc:
        return error(str(exc))
    message = f"Reminder set for {ctx.reminders.describe(due)}."
    if not ctx.push.count():
        message += " Turn on notifications on the Setup page so it can reach you."
    return refresh(request, message)


@router.post("/reminders/{rid}/{action}", dependencies=signed_in)
async def reminder_action(request: Request, rid: int, action: str):
    ctx = ctx_of(request)
    try:
        if action == "cancel":
            ctx.reminders.cancel(rid)
        elif action == "done":
            ctx.reminders.done(rid)
        elif action in ("hour", "tomorrow"):
            ctx.reminders.snooze(rid, action)
        else:
            raise not_found()
    except ReminderError as exc:
        return error(str(exc))
    return refresh(request, {"cancel": "Reminder cancelled.", "done": "Marked done.", "hour": "Snoozed for an hour.",
                             "tomorrow": "Snoozed until tomorrow at 9 AM."}[action])


# ======================================================================== push

@router.post("/push/subscribe", dependencies=signed_in)
async def push_subscribe(request: Request):
    ctx = ctx_of(request)
    try:
        sub = json.loads(await request.body())
        ctx.push.subscribe(sub if isinstance(sub, dict) else {}, request.headers.get("user-agent", ""))
    except (ValueError, ReminderError) as exc:
        return error(str(exc) if isinstance(exc, ReminderError) else "That subscription couldn't be read.")
    return toast("Notifications are on for this device.")


@router.post("/push/unsubscribe", dependencies=signed_in)
async def push_unsubscribe(request: Request):
    try:
        endpoint = json.loads(await request.body()).get("endpoint", "")
    except (ValueError, AttributeError):
        endpoint = ""
    ctx_of(request).push.unsubscribe(str(endpoint))
    return toast("Notifications are off for this device.")


@router.post("/push/test", dependencies=signed_in)
def push_test(request: Request):
    sent = ctx_of(request).push.send("Job Search HQ", "Notifications work on this device.", "/", tag="test")
    return toast(f"Sent to {sent} device{'s' if sent != 1 else ''}.") if sent else error("No device accepted it. Turn notifications on first.")


@router.post("/push/action/{token}/{action}")
def push_action(request: Request, token: str, action: str):
    """Buttons on a notification. The service worker can't send the CSRF header, so the signed token proves it came from our notification."""
    ctx = ctx_of(request)
    if session_of(request) is None:
        return Response(status_code=401)
    payload = ctx.box.verify(token)
    if not payload or payload.get("p") != "reminder" or action not in ("done", "snooze"):
        return Response(status_code=403)
    try:
        ctx.reminders.act(int(payload["r"]), action)  # one use: refused once the reminder was handled
    except ReminderError:
        return Response(status_code=409)
    return Response(status_code=204)


# ===================================================================== outreach

@router.get("/outreach", response_class=HTMLResponse, dependencies=signed_in)
def outreach_page(request: Request):
    ctx = ctx_of(request)
    out = Outreach(ctx)
    return page(request, "outreach.html", title="Outreach", contacts=out.all(), due=out.follow_ups_due())


@router.post("/roles/{role_id}/contacts", dependencies=signed_in)
async def contact_add(request: Request, role_id: int):
    ctx, f = ctx_of(request), await form(request)
    try:
        Outreach(ctx).add(role_id, f)
    except (OutreachError, RoleError) as exc:
        return error(str(exc))
    return refresh(request, "Person added.")


@router.post("/contacts/{contact_id}/draft/{kind}", dependencies=signed_in)
def contact_draft(request: Request, contact_id: int, kind: str):
    ctx = ctx_of(request)
    if kind not in OUTREACH_KINDS:
        raise not_found()
    contact = Outreach(ctx).get(contact_id)
    if not contact:
        raise not_found()
    if not ctx.claude_ready():
        return error("Connect Claude in Settings first.")
    job_id = ctx.queue.enqueue("outreach_draft", {"contact_id": contact_id, "kind": kind}, max_attempts=1)
    # No #fragment: a redirect that only changes the fragment doesn't reload the page, so the new draft wouldn't show.
    return fragment(request, "_job.html", job_id=job_id, label="Writing", done_url=f"/roles/{contact['role_id']}" if contact["role_id"] else "/outreach")


@router.post("/outreach/messages/{message_id}", dependencies=signed_in)
async def outreach_edit(request: Request, message_id: int):
    ctx, f = ctx_of(request), await form(request)
    try:
        Outreach(ctx).edit(message_id, str(f.get("subject", "")), str(f.get("body", "")))
    except OutreachError as exc:
        return error(str(exc))
    return toast("Saved.")


@router.post("/outreach/messages/{message_id}/sent", dependencies=signed_in)
def outreach_sent(request: Request, message_id: int):
    ctx = ctx_of(request)
    try:
        result = Outreach(ctx).mark_sent(message_id)
    except OutreachError as exc:
        return error(str(exc))
    return refresh(request, f"Marked sent. Follow-up reminder set for {ctx.reminders.describe(result['due'])}.")


@router.post("/contacts/{contact_id}/status/{status}", dependencies=signed_in)
def contact_status(request: Request, contact_id: int, status: str):
    try:
        Outreach(ctx_of(request)).set_status(contact_id, status)
    except OutreachError as exc:
        return error(str(exc))
    return refresh(request, "Saved.")


@router.post("/contacts/{contact_id}/delete", dependencies=signed_in)
def contact_delete(request: Request, contact_id: int):
    Outreach(ctx_of(request)).delete(contact_id)
    return refresh(request, "Person removed.")


# ======================================================================= intake

@router.get("/intake", response_class=HTMLResponse, dependencies=signed_in)
def intake_page(request: Request):
    ctx = ctx_of(request)
    return page(request, "intake.html", title="Inbox", items=ctx.db.all("SELECT * FROM inbox_items ORDER BY received DESC LIMIT 80"),
                watch=ctx.db.all("SELECT * FROM watch_companies ORDER BY name"), gmail=ctx.settings.has("gmail_refresh_token"),
                last=ctx.settings.get("gmail_last_check"))


@router.post("/intake/check", dependencies=signed_in)
def intake_check(request: Request):
    ctx = ctx_of(request)
    if not ctx.settings.has("gmail_refresh_token"):
        return error("Connect Gmail on the Setup page first.")
    job_id = ctx.queue.enqueue("inbox", {"manual": True}, dedupe=f"inbox:manual:{int(time.time() // 60)}")
    if job_id is None:
        return toast("A check is already running.")
    return fragment(request, "_job.html", job_id=job_id, label="Checking Gmail", done_url="/intake")


@router.post("/intake/{item_id}/undo", dependencies=signed_in)
def intake_undo(request: Request, item_id: int):
    ctx = ctx_of(request)
    item = ctx.db.one("SELECT * FROM inbox_items WHERE id = ?", (item_id,))
    if not item or not item["audit_id"]:
        return error("Nothing to undo.")
    try:
        ctx.roles.undo(item["audit_id"])
    except RoleError as exc:
        return error(str(exc))
    ctx.db.run("UPDATE inbox_items SET status = 'undone' WHERE id = ?", (item_id,))
    return refresh(request, "Undone.")


@router.post("/intake/{item_id}/dismiss", dependencies=signed_in)
def intake_dismiss(request: Request, item_id: int):
    ctx_of(request).db.run("UPDATE inbox_items SET status = 'done' WHERE id = ? AND status = 'review'", (item_id,))
    return toast("Dismissed.", html="")


@router.post("/intake/watch", dependencies=signed_in)
async def watch_add(request: Request):
    import re
    ctx, f = ctx_of(request), await form(request)
    name, board = " ".join(str(f.get("name", "")).split())[:120], str(f.get("board", ""))
    slug = str(f.get("slug", "")).strip()
    m = re.search(r"(?:job-)?boards\.greenhouse\.io/([A-Za-z0-9._-]+)"
                  r"|jobs\.lever\.co/([A-Za-z0-9._-]+)"
                  r"|jobs\.ashbyhq\.com/([A-Za-z0-9._-]+)", slug)
    if m:
        board = "greenhouse" if m.group(1) else "lever" if m.group(2) else "ashby"
        slug = m.group(1) or m.group(2) or m.group(3)
    if board not in ("greenhouse", "lever", "ashby") or not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", slug) or not name:
        return error("Add the company name and its Greenhouse, Lever or Ashby careers link.")
    ctx.db.run("INSERT INTO watch_companies (name, board, slug, keywords) VALUES (?, ?, ?, ?) ON CONFLICT (board, slug) DO UPDATE SET "
               "name = excluded.name, keywords = excluded.keywords, active = 1", (name, board, slug, str(f.get("keywords", ""))[:300]))
    return refresh(request, f"Watching {name}.")


@router.post("/intake/watch/{wid}/delete", dependencies=signed_in)
def watch_delete(request: Request, wid: int):
    ctx_of(request).db.run("DELETE FROM watch_companies WHERE id = ?", (wid,))
    return refresh(request, "Stopped watching that company.")


# ======================================================================= export

@router.get("/export", dependencies=signed_in)
def export(request: Request):
    """Everything in the user's records as CSV files in one zip, plus the document PDFs. Secrets are left out.
    CSVs start with a UTF-8 byte order mark so Excel shows accents and other scripts correctly."""
    ctx = ctx_of(request)
    buffer = io.BytesIO()
    tables = ("roles", "role_events", "contacts", "outreach_messages", "experience", "education",
              "certifications", "projects", "facts", "skills", "profile_questions",
              "proposals", "documents", "practice_questions",
              "practice_attempts", "reminders", "inbox_items", "watch_companies", "chat_threads", "chat_messages", "audit")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for table in tables:
            rows = ctx.db.all(f"SELECT * FROM {table}")  # nosec B608 - table names are the constants above
            out = io.StringIO()
            if rows:
                writer = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                for row in rows:  # a leading = + - @ would run as a spreadsheet formula
                    writer.writerow({k: ("'" + v if isinstance(v, str) and v and v[0] in "=+-@\t\r" else v) for k, v in row.items()})
            z.writestr(f"{table}.csv", "﻿" + out.getvalue())
        z.writestr("profile.json", json.dumps(ctx.profile.basics(), indent=2, ensure_ascii=False))
        role_names = {r["id"]: r["company"] for r in ctx.db.all("SELECT id, company FROM roles")}
        for doc in ctx.db.all("SELECT id, role_id, kind, pdf_file FROM documents"):
            path = documents.pdf_path(ctx, doc)
            if path:
                z.write(path, f"documents/{doc['id']}-{pdf_names('', doc['kind'], role_names.get(doc['role_id'], ''))[1]}")
    buffer.seek(0)
    stamp = _today(ctx)
    return StreamingResponse(buffer, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="jobhq-export-{stamp}.zip"'})
