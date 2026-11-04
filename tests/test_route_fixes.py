"""Regression tests for crash and data-loss bugs found by testers: forms that 500, rejected forms that
saved half their fields, exports that changed blank cells, and reminder times that drifted across DST."""
from __future__ import annotations

import csv
import io
import json
import time
import zipfile
from datetime import datetime, timedelta
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient

from jobhq.auth import cookie_name
from tests.conftest import BASE


@pytest.fixture
def c(app, ctx):
    """Signed in like `client`, but server errors come back as 500 responses instead of raising."""
    token, session = ctx.sessions.create("pytest", "127.0.0.1")
    with TestClient(app, base_url=BASE, follow_redirects=False, raise_server_exceptions=False) as cl:
        cl.cookies.set(cookie_name(ctx.config), token, domain="me.example.test")
        cl.headers.update({"X-CSRF-Token": session.csrf, "Origin": BASE, "Sec-Fetch-Site": "same-origin", "HX-Request": "true"})
        yield cl


def err(resp) -> str | None:
    return json.loads(resp.headers.get("hx-trigger", "{}")).get("jobhq:error")


def toast_of(resp) -> str | None:
    return json.loads(resp.headers.get("hx-trigger", "{}")).get("jobhq:toast")


# ------------------------------------------------------------------ PDF download names

def _doc(ctx, company: str, kind: str = "resume") -> int:
    rid = ctx.roles.create({"company": company, "title": "Ops"})
    fname = "doc-" + "c" * 24 + ".pdf"
    ctx.config.files_dir.mkdir(parents=True, exist_ok=True)
    (ctx.config.files_dir / fname).write_bytes(b"%PDF-1.4\n%%EOF")
    return ctx.db.run("INSERT INTO documents (role_id, kind, content, pdf_file, pages, checks, created, updated) "
                      "VALUES (?, ?, '{}', ?, 1, '[]', ?, ?)", (rid, kind, fname, time.time(), time.time())).lastrowid


@pytest.mark.parametrize("name, company, ascii_name", [
    ("王伟", "株式会社 Acme", "Resume_Acme.pdf"),
    ("Nguyễn Văn An", "Acme", "Nguyen_Van_An_Resume_Acme.pdf"),
    ("Siobhán O'Neill", "Café Ltd", "Siobhan_ONeill_Resume_CafeLtd.pdf"),
    ("José Núñez", "Acme", "Jose_Nunez_Resume_Acme.pdf"),
    ("محمد علي", "株式会社", "Resume_Company.pdf"),
])
@pytest.mark.parametrize("download", [0, 1])
def test_pdf_download_names_any_script(c, ctx, name, company, ascii_name, download):
    ctx.profile.save_basics({"name": name})
    did = _doc(ctx, company)
    r = c.get(f"/documents/{did}/pdf?download={download}")
    assert r.status_code == 200, r.status_code
    header = r.headers["content-disposition"]
    header.encode("ascii")  # the whole header must be plain ASCII
    assert f'filename="{ascii_name}"' in header
    assert header.startswith("attachment;" if download else "inline;")
    utf8 = unquote(header.split("filename*=UTF-8''", 1)[1])
    assert utf8.endswith(".pdf") and name.split(" ")[0] in utf8


# ------------------------------------------------------------------ export

def _export(c) -> zipfile.ZipFile:
    r = c.get("/export")
    assert r.status_code == 200
    return zipfile.ZipFile(io.BytesIO(r.content))


def _rows(z, table):
    raw = z.read(f"{table}.csv")
    assert raw.startswith(b"\xef\xbb\xbf"), "CSV needs a UTF-8 BOM so Excel shows accents"
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))


def test_export_keeps_blank_cells_blank_and_neutralises_formulas(c, ctx):
    ctx.roles.create({"company": "=HYPERLINK(\"http://evil\")", "title": "Ops", "notes": "-5 is fine"})
    ctx.roles.create({"company": "Zoë Café", "title": "Ops"})
    rows = _rows(_export(c), "roles")
    assert rows[0]["url"] == "" and rows[0]["location"] == ""
    assert rows[0]["company"].startswith("'=") and rows[0]["notes"] == "'-5 is fine"
    assert rows[1]["company"] == "Zoë Café"


def test_export_profile_json_is_readable_utf8(c, ctx):
    ctx.profile.save_basics({"name": "José Núñez"})
    raw = _export(c).read("profile.json").decode("utf-8")
    assert "José Núñez" in raw and json.loads(raw)["name"] == "José Núñez"


def test_export_has_suggestions_and_pdfs(c, ctx):
    ctx.db.run("INSERT INTO proposals (at, kind, title, payload) VALUES (?, 'fact', 'A suggestion', '{}')", (time.time(),))
    did = _doc(ctx, "Acme")
    z = _export(c)
    assert _rows(z, "proposals")[0]["title"] == "A suggestion"
    pdfs = [n for n in z.namelist() if n.startswith("documents/") and n.endswith(".pdf")]
    assert len(pdfs) == 1 and str(did) in pdfs[0]
    assert z.read(pdfs[0]).startswith(b"%PDF")


# ------------------------------------------------------------------ trials

def test_rejected_trial_edit_keeps_reminders(c, ctx):
    today = datetime.now(ctx.config.tz).date()
    c.post("/setup/trials", data={"name": "gcp", "start": today.isoformat(), "days": "90"})
    assert len(ctx.reminders.pending()) == 2
    for bad in ({"start": "soon", "days": "90"}, {"start": today.isoformat(), "days": "x"},
                {"start": (today + timedelta(days=90)).isoformat(), "days": "90"}):
        r = c.post("/setup/trials", data={"name": "gcp", **bad})
        assert err(r), bad
        assert len(ctx.reminders.pending()) == 2, f"{bad} cancelled the trial's reminders"
    assert ctx.settings.get("trial_gcp_start") == today.isoformat()


def test_trial_accepts_us_dates(c, ctx):
    today = datetime.now(ctx.config.tz).date()
    r = c.post("/setup/trials", data={"name": "gcp", "start": today.strftime("%m/%d/%Y"), "days": "90"})
    assert r.status_code == 204 and "reminders set" in toast_of(r)
    assert ctx.settings.get("trial_gcp_start") == today.isoformat()
    assert len(ctx.reminders.pending()) == 2


# ------------------------------------------------------------------ settings

def test_rejected_settings_save_nothing(c, ctx):
    for budget in ("abc", "nan", "inf", "-5"):
        r = c.post("/settings/claude", data={"api_key": "sk-ant-api03-abc", "mode": "api_key", "model": "x", "budget": budget})
        assert r.status_code == 400 and err(r), budget
        assert not ctx.settings.has("claude_api_key"), budget
        assert ctx.settings.get("monthly_budget_usd") is None, budget


@pytest.mark.parametrize("raw, saved", [("$25", 25.0), ("25.00", 25.0), ("$1,000", 1000.0), ("", 25.0)])
def test_budget_formats(c, ctx, raw, saved):
    r = c.post("/settings/claude", data={"api_key": "sk-ant-api03-abc", "budget": raw})
    assert r.status_code == 200, err(r)
    assert ctx.settings.get("monthly_budget_usd") == saved and ctx.settings.has("claude_api_key")


# ------------------------------------------------------------------ reminders

def test_reminder_unknown_role_is_a_clear_error(c, ctx):
    when = (datetime.now(ctx.config.tz) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
    r = c.post("/reminders", data={"text": "x", "when": when, "role_id": "9999"})
    assert r.status_code == 400 and "role" in err(r)
    assert ctx.reminders.pending() == []


def test_reminder_typed_time_message(c):
    r = c.post("/reminders", data={"text": "x", "when": "tomorrow 9am"})
    assert r.status_code == 400 and "date and time picker" in err(r)


def test_reminder_passed_preset_message(c, ctx, monkeypatch):
    monkeypatch.setattr(ctx.reminders, "now", lambda: datetime(2026, 11, 4, 19, 0, tzinfo=ctx.config.tz))
    r = c.post("/reminders", data={"text": "x", "preset": "evening"})
    assert err(r) == "That time has already passed; pick a later one."


def test_hour_preset_is_one_real_hour_across_dst():
    from jobhq.notify import preset_due
    ny = ZoneInfo("America/New_York")
    for now in (datetime(2026, 11, 1, 1, 30, tzinfo=ny), datetime(2026, 11, 1, 1, 30, fold=1, tzinfo=ny),
                datetime(2026, 3, 8, 1, 30, tzinfo=ny), datetime(2026, 7, 1, 12, 0, tzinfo=ny)):
        assert preset_due("hour", now) - now.timestamp() == 3600, now


# ------------------------------------------------------------------ posting fetch on Add role

def test_add_role_survives_a_network_error(c, ctx, monkeypatch):
    import jobhq.intake as intake
    monkeypatch.setattr(intake, "_public", lambda host: True)

    def boom(request):
        raise httpx.ConnectTimeout("timed out", request=request)

    ctx.http = httpx.Client(transport=httpx.MockTransport(boom))
    r = c.post("/roles", data={"company": "Acme", "title": "Ops", "url": "https://jobs.example.com/1"})
    assert r.status_code < 400
    role = ctx.roles.all()[0]
    assert role["url"] == "https://jobs.example.com/1" and role["posting"] == ""
    assert "couldn't be fetched" in toast_of(r) and "paste" in toast_of(r).lower()


@pytest.mark.parametrize("url", ["https://jobs.example.com:abc/1", "https://[::1/x"])
def test_add_role_with_a_malformed_link_still_saves(c, ctx, url):
    r = c.post("/roles", data={"company": "Acme", "title": "Ops", "url": url})
    assert r.status_code < 400, (url, r.status_code, err(r))
    assert len(ctx.roles.all()) == 1
    assert "couldn't be fetched" in toast_of(r)


def test_add_role_link_without_scheme(c, ctx):
    r = c.post("/roles", data={"company": "Acme", "title": "Ops", "url": "www.linkedin.com/jobs/view/123", "posting": "text"})
    assert r.status_code < 400, err(r)
    assert ctx.roles.all()[0]["url"] == "https://www.linkedin.com/jobs/view/123"


def test_fetch_posting_errors_are_fetch_errors(ctx, monkeypatch):
    import jobhq.intake as intake
    monkeypatch.setattr(intake, "_public", lambda host: True)

    def boom(request):
        raise httpx.ReadTimeout("slow", request=request)

    ctx.http = httpx.Client(transport=httpx.MockTransport(boom))
    for url in ("https://jobs.example.com/1", "https://jobs.example.com:abc/1", "https://[::1/x"):
        with pytest.raises(intake.FetchError):
            intake.fetch_posting(ctx, url)


def test_fetch_posting_adds_https(ctx, monkeypatch):
    import jobhq.intake as intake
    monkeypatch.setattr(intake, "_public", lambda host: True)
    seen = []

    def ok(request):
        seen.append(str(request.url))
        return httpx.Response(200, text="<p>Posting</p>", headers={"content-type": "text/html"})

    ctx.http = httpx.Client(transport=httpx.MockTransport(ok))
    assert "Posting" in intake.fetch_posting(ctx, "jobs.example.com/1")
    assert seen == ["https://jobs.example.com/1"]


def test_role_fetch_button_network_error(c, ctx, monkeypatch):
    import jobhq.intake as intake
    monkeypatch.setattr(intake, "_public", lambda host: True)

    def boom(request):
        raise httpx.ConnectError("refused", request=request)

    ctx.http = httpx.Client(transport=httpx.MockTransport(boom))
    rid = ctx.roles.create({"company": "Acme", "title": "Ops", "url": "https://jobs.example.com/1"})
    r = c.post(f"/roles/{rid}/fetch")
    assert r.status_code == 400 and err(r)


def test_add_role_too_long_posting_is_a_clear_error(c, ctx):
    r = c.post("/roles", data={"company": "Acme", "title": "Ops", "posting": "x" * 60_001})
    assert r.status_code == 400 and "60,000" in err(r)
    assert ctx.roles.all() == []
