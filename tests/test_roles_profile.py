from datetime import date, timedelta

import pytest

from jobhq.profile import clean_basics
from jobhq.proposals import ProposalError
from jobhq.roles import RoleError, grade, lane


def gates(**results):
    return {g: {"result": results.get(g, "pass"), "why": f"{g} reason"} for g in ("employer", "sector", "location", "pay")}


def role(**kw):
    base = {"status": "saved", "fit_score": 80, "gates": gates(), "warm_path": "", "posted_on": date.today().isoformat(), "created": 0}
    return {**base, **kw}


def test_grades():
    assert (grade(90), grade(75), grade(74), grade(60), grade(59), grade(None)) == ("A", "A", "B", "B", "C", "")


@pytest.mark.parametrize("kw, expected", [
    ({}, "ready"),
    ({"fit_score": 65}, "ready"),  # B and recent
    ({"fit_score": 65, "posted_on": (date.today() - timedelta(days=20)).isoformat()}, "later"),  # B, cold and older
    ({"fit_score": 65, "posted_on": (date.today() - timedelta(days=20)).isoformat(), "warm_path": "Dana"}, "ready"),
    ({"fit_score": 40}, "later"),
    ({"gates": gates(pay="fail")}, "call"),
    ({"gates": gates(pay="unknown")}, "check"),
    ({"fit_score": None}, "check"),
    ({"posted_on": (date.today() - timedelta(days=60)).isoformat()}, "expired"),
    ({"status": "applied"}, "pipeline"),
    ({"status": "rejected"}, "closed"),
])
def test_lanes(kw, expected):
    assert lane(role(**kw))[0] == expected


def test_create_update_and_undo(ctx):
    rid = ctx.roles.create({"company": "Acme", "title": "Ops Lead"})
    audit_id = ctx.roles.update(rid, {"status": "applied", "next_step": "Follow up"})
    r = ctx.roles.get(rid)
    assert r["status"] == "applied" and r["applied_on"] == date.today().isoformat()
    assert [e["kind"] for e in ctx.roles.events(rid)] == ["status"]
    ctx.roles.undo(audit_id)
    r = ctx.roles.get(rid)
    assert r["status"] == "saved" and r["next_step"] == "" and r["applied_on"] == ""
    with pytest.raises(RoleError):
        ctx.roles.undo(audit_id)  # only once


def test_undo_refuses_after_a_later_change(ctx):
    rid = ctx.roles.create({"company": "Acme", "title": "Ops"})
    first = ctx.roles.update(rid, {"status": "applied"})
    ctx.roles.update(rid, {"status": "interviewing"})
    with pytest.raises(RoleError, match="changed again"):
        ctx.roles.undo(first)


@pytest.mark.parametrize("fields, message", [
    ({"status": "hired"}, "status"),
    ({"work_mode": "moon"}, "work_mode"),
    ({"next_step_date": "next tuesday"}, "date"),
    ({"url": "javascript:alert(1)"}, "https"),
    ({"pay_min": "lots"}, "Pay from"),
    ({"owner": "x"}, "can't be changed"),
])
def test_role_validation(ctx, fields, message):
    rid = ctx.roles.create({"company": "Acme", "title": "Ops"})
    with pytest.raises(RoleError, match=message):
        ctx.roles.update(rid, fields)


def test_due_lists(ctx):
    today = date.today()
    a = ctx.roles.create({"company": "A", "title": "x", "status": "applied", "applied_on": (today - timedelta(days=10)).isoformat()})
    b = ctx.roles.create({"company": "B", "title": "y", "next_step": "Send samples", "next_step_date": today.isoformat()})
    ctx.roles.add_event(b, "interview", (today + timedelta(days=2)).isoformat(), "Panel")
    due = ctx.roles.due(today)
    assert [r["id"] for r in due["followups"]] == [a]
    assert [r["id"] for r in due["next_steps"]] == [b]
    assert [e["role_id"] for e in due["interviews"]] == [b]
    ctx.roles.add_event(a, "followup", today.isoformat(), "Emailed Dana")
    assert ctx.roles.due(today)["followups"] == []


def test_clean_basics():
    data, errors = clean_basics({"name": "  Pat   Lee ", "pay_floor": "150k", "pay_target": "$180,000", "work_modes": ["remote", "space"],
                                 "target_titles": "Ops Manager\nRevOps Lead", "linkedin": "http://bad", "email": "pat@example.com"})
    assert data["name"] == "Pat Lee" and data["pay_floor"] == 150000 and data["pay_target"] == 180000
    assert data["work_modes"] == ["remote"] and data["target_titles"] == ["Ops Manager", "RevOps Lead"]
    assert "linkedin" in errors
    _, errors = clean_basics({"pay_floor": "200k", "pay_target": "150k"})
    assert "pay_target" in errors


@pytest.mark.parametrize("typed", ["18/hr", "$18 an hour", "18 per hour", "$18/hour", "18 hourly"])
def test_hourly_pay_becomes_yearly(typed):
    """Hourly job seekers type an hourly wage; it's kept as a full-time yearly amount (2,080 hours)."""
    data, errors = clean_basics({"pay_floor": typed})
    assert not errors and data["pay_floor"] == 37440


def test_anything_else_box_reaches_the_ai(ctx):
    """The form says "Anything else the AI should know", so the AI must see it."""
    ctx.profile.save_basics(clean_basics({"name": "Pat", "notes": "No overnight shifts. Back after two years caring for a parent."})[0])
    assert "No overnight shifts. Back after two years caring for a parent." in ctx.profile.as_text()


def test_profile_missing_and_text(ctx):
    assert "your name" in ctx.profile.missing()
    ctx.profile.save_basics(clean_basics({"name": "Pat", "email": "p@x.com", "city": "Austin", "target_titles": "Ops", "work_modes": ["remote"],
                                          "pay_floor": "100k", "summary": "Ops person"})[0])
    assert ctx.profile.missing() == []
    exp = ctx.db.run("INSERT INTO experience (company, title, start_date) VALUES ('Acme', 'Lead', '2020')").lastrowid
    with ctx.db.tx() as conn:
        ctx.profile.add_fact(conn, "Cut costs 20%", experience_id=exp)
        ctx.profile.set_skill(conn, "Marketo", "never")
    text = ctx.profile.as_text()
    assert "[exp 1] Lead, Acme" in text and "Cut costs 20%" in text and "Never used (do not claim): Marketo" in text


def test_proposals_need_approval(ctx):
    exp = ctx.db.run("INSERT INTO experience (company, title) VALUES ('Acme', 'Lead')").lastrowid
    pid = ctx.proposals.file("profile_fact", "t", {"text": "Led a team of 5", "experience_id": exp})
    assert ctx.profile.facts() == []
    assert ctx.proposals.file("profile_fact", "t", {"text": "Led a team of 5", "experience_id": exp}) == pid  # no duplicates
    ctx.proposals.decide(pid, True, edited_text="Led a team of six")
    assert [f["text"] for f in ctx.profile.facts()] == ["Led a team of six"]
    with pytest.raises(ProposalError):
        ctx.proposals.decide(pid, True)


def test_proposal_validation(ctx):
    with pytest.raises(ProposalError):
        ctx.proposals.file("profile_fact", "t", {"text": ""})
    with pytest.raises(ProposalError):
        ctx.proposals.file("profile_fact", "t", {"text": "x", "experience_id": 999})
    with pytest.raises(ProposalError):
        ctx.proposals.file("skill", "t", {"name": "SQL", "status": "expert"})
    with pytest.raises(ProposalError):
        ctx.proposals.file("profile_field", "t", {"field": "email", "value": "evil@x.com"})
    pid = ctx.proposals.file("profile_field", "t", {"field": "pay_floor", "value": "120k"})
    ctx.proposals.decide(pid, True)
    assert ctx.profile.basics()["pay_floor"] == 120000


def test_reject_changes_nothing(ctx):
    pid = ctx.proposals.file("skill", "t", {"name": "SQL", "status": "have"})
    ctx.proposals.decide(pid, False)
    assert ctx.profile.skills() == []
