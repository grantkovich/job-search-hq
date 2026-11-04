"""Upgrading a real server: a database written by the app one schema version back opens cleanly with today's code.

The old database is made with the module's own migration runner, stopped one migration early,
and filled the way the previous release wrote rows (bare-number pay with no *_detail, no pay_currency).
Then it is opened exactly the way the app starts (context.build), directly and after a jobsctl restore.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.machinery
import importlib.util
import json
import os
import sqlite3
import sys
import tarfile
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from jobhq import db as dbmod
from jobhq import documents
from jobhq.ai import tasks
from jobhq.ai.runner import Result
from jobhq.auth import cookie_name
from jobhq.config import load
from jobhq.context import build
from jobhq.migrations import MIGRATIONS

from .conftest import BASE, FakeClaudeCode, FakeGoogle, FakeRunner, make_env

OLD = len(MIGRATIONS) - 1  # the schema the previous release shipped with (8, before PROFILE_EXTRAS)
NEW_TABLES = ("certifications", "projects", "question_queue")
SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "jobsctl"


def make_old_db(path: Path, monkeypatch) -> dict:
    """The previous release's database: its migrations only, through the real Database class."""
    with monkeypatch.context() as m:
        m.setattr(dbmod, "MIGRATIONS", MIGRATIONS[:OLD])
        old = dbmod.Database(path)
        assert old.value("PRAGMA user_version") == OLD
        old.close()
    conn = sqlite3.connect(str(path))
    now = time.time()
    profile = {"name": "Pat Lee", "email": "pat@example.com", "phone": "555-0100", "city": "Springfield", "region": "IL", "country": "USA",
               "linkedin": "https://www.linkedin.com/in/patlee", "github": "", "website": "", "postal_code": "62701",
               "headline": "Operations leader", "summary": "Ten years running clinic operations.", "level": "Manager",
               "work_authorization": "US citizen", "notes": "", "target_titles": ["Operations Manager"], "target_sectors": ["Hospitals"],
               "avoid": ["Call centers"], "work_modes": ["remote", "hybrid"], "pay_floor": 95000, "pay_target": 110000, "max_commute_miles": 25}
    with conn:
        conn.execute("INSERT INTO profile (id, data, updated) VALUES (1, ?, ?)", (json.dumps(profile), now))
        exp = conn.execute("INSERT INTO experience (company, title, location, start_date, end_date, summary, sort) "
                           "VALUES ('Initrode', 'Clinic Operations Lead', 'Remote', 'Mar 2021', 'Sep 2026', 'Ran the scheduling desk.', 0)").lastrowid
        conn.execute("INSERT INTO experience (company, title, start_date, end_date) VALUES ('Acme Health', 'Front Desk Supervisor', 'Jan 2018', 'Mar 2021')")
        f1 = conn.execute("INSERT INTO facts (topic, text, experience_id, source, created, updated) VALUES ('migration', "
                          "'Moved 75,000 patient records to a new system', ?, 'user', ?, ?)", (exp, now, now)).lastrowid
        conn.execute("INSERT INTO facts (topic, text, experience_id, source, created, updated) VALUES ('', 'State University MBA, 2024', NULL, 'import', ?, ?)",
                     (now, now))
        conn.execute("INSERT INTO education (school, degree, field, start_date, end_date) VALUES ('State University', 'M.B.A.', '', '2022', 'Aug 2024')")
        conn.execute("INSERT INTO skills (name, status, level, years, note) VALUES ('Epic', 'have', 'expert', 4, '')")
        conn.execute("INSERT INTO skills (name, status) VALUES ('Marketo', 'never')")
        conn.execute("INSERT INTO profile_questions (topic, question, reason, status, created) VALUES ('Segment', 'Have you used Segment?', 'x', 'open', ?)",
                     (now,))
        role = conn.execute("INSERT INTO roles (company, title, url, location, work_mode, pay_min, pay_max, posting, status, created, updated) "
                            "VALUES ('Globex', 'Operations Manager', 'https://jobs.example.com/1', 'Remote', 'remote', 140000, 170000, "
                            "'We need Epic and Excel.', 'saved', ?, ?)", (now, now)).lastrowid
        hourly = conn.execute("INSERT INTO roles (company, title, pay_min, status, created, updated) "
                              "VALUES ('Initech', 'Practice Coordinator', 42000, 'applied', ?, ?)", (now, now)).lastrowid
        conn.execute("INSERT INTO role_events (role_id, on_date, kind, text, created) VALUES (?, '2026-11-01', 'interview', 'Screen', ?)", (role, now))
        contact = conn.execute("INSERT INTO contacts (role_id, name, company, relation, created, updated) VALUES (?, 'Sam Doe', 'Globex', 'warm', ?, ?)",
                               (role, now, now)).lastrowid
        conn.execute("INSERT INTO outreach_messages (contact_id, kind, body, created) VALUES (?, 'email', 'Hi Sam', ?)", (contact, now))
        conn.execute("INSERT INTO reminders (text, due, role_id, created) VALUES ('Follow up with Globex', ?, ?, ?)", (now + 86400, role, now))
        conn.execute("INSERT INTO settings (key, value, updated) VALUES ('paper', '\"us-letter\"', ?)", (now,))
        conn.execute("INSERT INTO settings (key, value, updated) VALUES ('claude_mode', '\"claude_login\"', ?)", (now,))
        conn.execute("INSERT INTO proposals (at, kind, title, payload, source) VALUES (?, 'profile_field', 'Raise target', ?, 'chat')",
                     (now, json.dumps({"field": "pay_target", "value": 190000})))
        conn.execute("INSERT INTO proposals (at, kind, title, payload, source) VALUES (?, 'profile_fact', 'A fact', ?, 'chat')",
                     (now, json.dumps({"text": "Built lead scoring", "experience_id": exp})))
    conn.close()
    return {"exp": exp, "fact": f1, "role": role, "hourly_role": hourly}


def open_like_the_app(data_dir: Path, runner=None):
    """context.build the way tests/conftest.py's ctx fixture does, pointed at an existing data folder."""
    cfg = load({**make_env(data_dir.parent), "JOBHQ_DATA_DIR": str(data_dir)}, scrub=False)
    google = FakeGoogle()
    return build(cfg, runner=runner or FakeRunner(), http=httpx.Client(transport=httpx.MockTransport(google.handler)),
                 push_sender=lambda *_: None, claude_code=FakeClaudeCode(data_dir.parent))


def counts(path: Path) -> dict:
    conn = sqlite3.connect(str(path))
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}  # noqa: S608 - names from sqlite_master
    finally:
        conn.close()


def check_upgraded(ctx, ids: dict, before: dict) -> None:
    db = ctx.db
    assert db.value("PRAGMA user_version") == len(MIGRATIONS)
    assert db.value("PRAGMA integrity_check") == "ok" and not db.all("PRAGMA foreign_key_check")
    after = counts(ctx.config.db_path)
    for table, n in before.items():
        assert after[table] == n, table  # nothing lost or duplicated
    assert all(after[t] == 0 for t in NEW_TABLES)
    assert {r["name"] for r in db.all("PRAGMA table_info(roles)")} >= {"pay_currency", "pay_min", "pay_max"}
    role = ctx.roles.get(ids["role"])
    assert (role["pay_min"], role["pay_max"], role["pay_currency"]) == (140000, 170000, "")
    b = ctx.profile.basics()
    assert b["pay_floor"] == 95000 and "pay_floor_detail" not in b
    assert ctx.settings.get("paper") == "us-letter" and ctx.claude_mode() == "claude_login"
    assert ctx.proposals.count() == 2

    text = ctx.profile.as_text()  # the scoring and drafting prompts start from this
    assert "Base pay floor: 95,000 a year; target: 110,000 a year" in text
    assert f"[fact {ids['fact']}] Moved 75,000 patient records" in text and "Never used (do not claim): Marketo" in text
    assert "Certifications" not in text and "Projects:" not in text


def check_pages_and_documents(ctx, ids: dict) -> None:
    from jobhq.app import create_app

    app = create_app(ctx, run_worker=False)
    token, session = ctx.sessions.create("pytest", "127.0.0.1")
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        c.cookies.set(cookie_name(ctx.config), token, domain="me.example.test")
        c.headers.update({"X-CSRF-Token": session.csrf, "Origin": BASE, "Sec-Fetch-Site": "same-origin", "HX-Request": "true"})
        page = c.get("/profile")
        assert page.status_code == 200, page.text[:500]
        assert 'name="pay_floor" maxlength="60" placeholder="e.g. 18/hr, 45k a year or 3,000 a month" value="95000"' in page.text
        assert 'value="110000"' in page.text and "Initrode" in page.text
        role_page = c.get(f"/roles/{ids['role']}")
        assert role_page.status_code == 200 and "$140,000–$170,000 a year" in role_page.text
        low_pay = c.get(f"/roles/{ids['hourly_role']}")  # under $100k also shows the hourly rate, rounded from the yearly number
        assert low_pay.status_code == 200 and "$42,000 a year (about $20 an hour)" in low_pay.text
        for path in ("/", "/roles", "/roles?show=pipeline", "/outreach", "/reminders", "/suggestions", "/documents"):
            assert c.get(path).status_code == 200, path
        # Re-saving the old profile unchanged keeps the old numbers, now with what was typed alongside.
        form = {k: v for k, v in ctx.profile.basics().items() if not isinstance(v, list)}
        form.update(target_titles="Operations Manager", work_modes=["remote", "hybrid"], target_sectors="Hospitals", avoid="Call centers",
                    pay_floor="95000", pay_target="110000", max_commute_miles="25")
        assert c.post("/profile/basics", data=form).status_code in (200, 204, 303)
        b = ctx.profile.basics()
        assert b["pay_floor"] == 95000 and b["pay_target"] == 110000 and b["pay_floor_detail"]["period"] == "year"
        # The new tables work on the upgraded file: a certification shows on the profile page.
        assert c.post("/profile/certifications", data={"cert_name": "Initrode Builder", "cert_issuer": "Initrode"}).status_code in (200, 204, 303)
        assert "Initrode Builder" in c.get("/profile").text

    # An old pending proposal still applies against the old-format profile.
    pid = next(p["id"] for p in ctx.proposals.pending() if p["kind"] == "profile_field")
    ctx.proposals.decide(pid, approve=True)
    assert ctx.profile.basics()["pay_target"] == 190000

    # Scoring builds its prompt and files tool questions past the daily cap into the new question_queue table.
    runner = ctx.runner
    runner.script["score"] = lambda *_: Result(ok=True, data={"skills": 20, "experience": 20, "level": 18, "culture": 15, "why": "Good fit.",
                                                               "gates": {}, "unknown_tools": [f"Tool{i}" for i in range(8)]})
    out = asyncio.run(tasks.score_role(ctx, ids["role"]))
    assert out["ok"] and out["score"] == 73
    assert "Base pay floor: 95,000 a year" in runner.calls[-1]["prompt"]
    assert ctx.db.value("SELECT COUNT(*) FROM question_queue") > 0

    # A resume drafts and builds from the upgraded profile.
    runner.script["draft_resume"] = lambda *_: Result(ok=True, data={"summary": "Clinic operations lead.", "experience": [
        {"experience_id": ids["exp"], "bullets": [{"text": "Moved 75,000 patient records", "fact_ids": [ids["fact"]]}]}],
        "skills": ["Epic"], "keywords_missing": []})
    runner.script["claims"] = lambda *_: Result(ok=True, data={"problems": []})
    doc = documents.get(ctx, asyncio.run(documents.create(ctx, ids["role"], "resume")))
    assert doc["pages"] in (1, 2) and all(c["ok"] for c in doc["checks"]), doc["checks"]
    _, pdf_text = documents.pdf_facts(documents.pdf_path(ctx, doc).read_bytes())
    assert "Pat Lee" in pdf_text and "75,000" in pdf_text and "Initrode Builder" in pdf_text


def test_previous_schema_upgrades_once_and_keeps_every_row(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    ids = make_old_db(data / "app.db", monkeypatch)
    before = counts(data / "app.db")
    assert not set(NEW_TABLES) & set(before)

    ctx = open_like_the_app(data)
    try:
        check_upgraded(ctx, ids, before)
        audit_rows = ctx.db.value("SELECT COUNT(*) FROM audit")
    finally:
        ctx.db.close()

    again = open_like_the_app(data)  # a second start: nothing re-runs, nothing changes
    try:
        assert again.db.migrate() == len(MIGRATIONS) and again.db.value("PRAGMA user_version") == len(MIGRATIONS)
        assert counts(data / "app.db") == {**before, **{t: 0 for t in NEW_TABLES}, "audit": audit_rows}
        check_pages_and_documents(again, ids)
    finally:
        again.db.close()


@pytest.fixture
def jobsctl(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBHQ_ROOT", str(tmp_path / "srv"))
    loader = importlib.machinery.SourceFileLoader("jobsctl_upgrade_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader("jobsctl_upgrade_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    yield module
    sys.modules.pop("jobsctl_upgrade_test", None)


def test_backup_from_the_previous_release_restores_and_upgrades(tmp_path, monkeypatch, jobsctl):
    # The old server's copy, and a backup laid out the way jobsctl backup packs one (backup/app.db, backup/files, instance.env).
    old_data = tmp_path / "old" / "data"
    (old_data / "files").mkdir(parents=True)
    ids = make_old_db(old_data / "app.db", monkeypatch)
    before = counts(old_data / "app.db")
    with monkeypatch.context() as m:
        m.setattr(dbmod, "MIGRATIONS", MIGRATIONS[:OLD])
        snap = tmp_path / "snap"
        snap.mkdir()
        old = dbmod.Database(old_data / "app.db")
        old.backup_to(snap / "app.db")
        old.close()
    archive = tmp_path / "pat-old.tar.gz"
    jobsctl.INSTANCES.mkdir(parents=True)
    jobsctl.write_env(jobsctl.INSTANCES / "pat.env", {"JOBHQ_OWNER_EMAIL": "pat@example.com", "JOBHQ_BASE_URL": "https://pat.jobs.example.com",
                                                     "JOBHQ_SECRET": jobsctl.new_secret()})
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(snap / "app.db", arcname="backup/app.db")
        tar.add(old_data / "files", arcname="backup/files")
        tar.add(jobsctl.INSTANCES / "pat.env", arcname="instance.env")

    # The new server's copy (empty, already on the new schema), restored with jobsctl's own cmd_restore.
    data = jobsctl.DATA / "pat"
    data.mkdir(parents=True)
    open_like_the_app(data).db.close()
    calls = []
    monkeypatch.setattr(jobsctl, "need_root", lambda: None)
    monkeypatch.setattr(jobsctl, "compose", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(os, "chown", lambda *a, **k: None)
    jobsctl.cmd_restore(argparse.Namespace(name="pat", file=str(archive)))
    assert calls == [("stop", "app-pat"), ("start", "app-pat")]
    assert sqlite3.connect(str(data / "app.db")).execute("PRAGMA user_version").fetchone()[0] == OLD

    ctx = open_like_the_app(data)  # what the container does when compose starts it again
    try:
        check_upgraded(ctx, ids, before)
        check_pages_and_documents(ctx, ids)
    finally:
        ctx.db.close()
