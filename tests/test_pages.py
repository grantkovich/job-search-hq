import asyncio
import csv
import io
import zipfile

import pytest

from jobhq import practice
from jobhq.ai.runner import Result

from .conftest import drain

PAGES = ["/", "/setup", "/settings", "/profile", "/suggestions", "/chat", "/interview", "/roles", "/roles?show=pipeline", "/roles?show=closed", "/outreach",
         "/roles/new", "/documents", "/practice", "/reminders", "/intake"]


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_signed_in(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text[:500]
    assert "<title>" in response.text and 'name="csrf-token"' in response.text
    assert "style=" not in response.text  # the CSP blocks inline styles, so none may be used


@pytest.mark.parametrize("path", PAGES)
def test_every_page_needs_sign_in(anon, path):
    assert anon.get(path).status_code == 303


def test_today_shows_a_reconnect_banner_when_gmail_access_ended(client, ctx):
    assert "Reconnect Gmail" not in client.get("/").text
    ctx.settings.set("gmail_status", "Google ended Gmail access. Connect it again on the Setup page.")
    page = client.get("/").text
    assert "Reconnect Gmail" in page and 'href="/auth/gmail"' in page


def test_role_page_and_unknown_ids(client, ctx):
    rid = ctx.roles.create({"company": "Acme <script>", "title": "Ops"})
    page = client.get(f"/roles/{rid}")
    assert page.status_code == 200 and "Acme &lt;script&gt;" in page.text
    assert client.get("/roles/9999").status_code == 404


def test_add_role_and_score(client, ctx, runner, app):
    ctx.settings.set("claude_api_key", "sk-ant-x")
    runner.script["score"] = lambda *_: Result(ok=True, data={
        "skills": 20, "experience": 20, "level": 18, "culture": 20, "why": "Strong fit.", "unknown_tools": ["Marketo", "Looker"],
        "gates": {g: {"result": "pass", "why": "ok"} for g in ("employer", "sector", "location", "pay")}, "pay_min": 150000, "pay_max": 180000,
        "location": "Remote", "work_mode": "remote"})
    response = client.post("/roles", data={"company": "Acme", "title": "Ops Lead", "posting": "Need Marketo and Looker."})
    rid = int(response.headers["hx-redirect"].rsplit("/", 1)[1])
    asyncio.run(drain(app))
    r = ctx.roles.get(rid)
    assert r["fit_score"] == 78 and r["pay_min"] == 150000 and r["work_mode"] == "remote"
    assert {q["topic"] for q in ctx.db.all("SELECT topic FROM profile_questions")} == {"Marketo", "Looker"}
    assert ctx.roles.board()["ready"][0]["id"] == rid
    client.post("/questions/1/never")
    assert ctx.profile.skill_status("Marketo") == "never"


def test_profile_forms(client, ctx):
    assert client.post("/profile/basics", data={"name": "Pat", "pay_floor": "150k", "work_modes": ["remote", "hybrid"]}).status_code == 200
    assert ctx.profile.basics()["work_modes"] == ["remote", "hybrid"]
    assert "linkedin" in client.post("/profile/basics", data={"linkedin": "javascript:alert(1)"}).headers["hx-trigger"]
    client.post("/profile/experience", data={"company": "Acme", "title": "Lead"})
    exp = ctx.profile.experience()[0]["id"]
    client.post("/profile/facts", data={"text": "Did a thing", "experience_id": str(exp)})
    assert ctx.profile.facts(exp)[0]["text"] == "Did a thing"
    assert "doesn't exist" in client.post("/profile/facts", data={"text": "x", "experience_id": "999"}).headers["hx-trigger"]


def test_practice_typed_answer_is_graded(client, ctx, runner, app):
    ctx.settings.set("claude_api_key", "sk-ant-x")
    runner.script["practice_grade"] = lambda *_: Result(ok=True, data={"structure": 4, "evidence": 3, "fit": 4, "length": 5, "worked": "Clear.",
                                                                        "fix": "Add a number.", "follow_up": "How did you measure it?"})
    response = client.post("/practice/question", data={"question": "Tell me about a migration you led."})
    qid = int(response.headers["hx-redirect"].rsplit("/", 1)[1])
    client.post(f"/practice/q/{qid}/answer", data={"answer": "Um, I led the HubSpot migration, like, for 75,000 contacts in six weeks.",
                                                  "seconds": "40"})
    asyncio.run(drain(app))
    attempt = ctx.db.one("SELECT * FROM practice_attempts")
    assert attempt["total"] == 16 and attempt["fillers"] == 2 and attempt["wpm"] == 21.0
    assert "How did you measure it?" in client.get(f"/practice/q/{qid}").text


def test_voice_is_off_unless_enabled(ctx):
    with pytest.raises(practice.VoiceError, match="turned off"):
        practice.transcribe(ctx, b"x" * 2000, "audio/webm")


def test_export_has_records_but_no_secrets(client, ctx):
    ctx.settings.set("claude_api_key", "sk-ant-supersecret")
    ctx.roles.create({"company": "=HYPERLINK(\"http://evil\")", "title": "Ops"})
    response = client.get("/export")
    z = zipfile.ZipFile(io.BytesIO(response.content))
    everything = b"".join(z.read(n) for n in z.namelist())
    assert b"sk-ant-supersecret" not in everything
    rows = list(csv.DictReader(io.StringIO(z.read("roles.csv").decode())))
    assert rows[0]["company"].startswith("'=")  # spreadsheet formula injection neutralised


def test_export_includes_certifications_and_projects(client, ctx):
    """Found by the docs pass: the new profile tables were missing from Download my data."""
    ctx.db.run("INSERT INTO certifications (name, created) VALUES ('CPR/AED', 0)")
    ctx.db.run("INSERT INTO projects (name, created) VALUES ('Food pantry inventory', 0)")
    z = zipfile.ZipFile(io.BytesIO(client.get("/export").content))
    assert "CPR/AED" in z.read("certifications.csv").decode("utf-8-sig")
    assert "Food pantry inventory" in z.read("projects.csv").decode("utf-8-sig")
