"""Profile and role data the way job seekers type it: pay in any unit, distances, dates, certifications and projects."""
import asyncio

import pytest

from jobhq import documents
from jobhq.ai import tasks
from jobhq.ai.runner import Result
from jobhq.pay import parse_distance, parse_pay, parse_pay_range
from jobhq.profile import clean_basics, date_key
from jobhq.roles import RoleError
from jobhq.web.common import pay_range

# ------------------------------------------------------------------ pay parsing


@pytest.mark.parametrize("typed, yearly, period", [
    ("18", 37440, "hour"), ("18.5", 38480, "hour"), ("7", 14560, "hour"), ("200", 416000, "hour"),
    ("201", 201000, "year"), ("150", 312000, "hour"), ("999", 999000, "year"), ("1000", 1000, "year"), ("45000", 45000, "year"),
    ("18/hr", 37440, "hour"), ("18hr", 37440, "hour"), ("18 hr", 37440, "hour"), ("18 p/h", 37440, "hour"), ("18/h", 37440, "hour"),
    ("18 ph", 37440, "hour"), ("$18 an hour", 37440, "hour"), ("18 per hour", 37440, "hour"), ("18 hourly", 37440, "hour"),
    ("60k", 60000, "year"), ("60K", 60000, "year"), ("60k/yr", 60000, "year"), ("60k a year", 60000, "year"), ("60,000 per year", 60000, "year"),
    ("60000/year", 60000, "year"), ("60000 yearly", 60000, "year"), ("60000 annually", 60000, "year"), ("60k pa", 60000, "year"),
    ("$150,000", 150000, "year"), ("1.2m", 1200000, "year"), ("3,000 a month", 36000, "month"), ("3000/mo", 36000, "month"),
    ("3000 pm", 36000, "month"), ("900 a week", 46800, "week"), ("200 a day", 52000, "day"), ("200 daily", 52000, "day"),
    ("18,50/hr", 38480, "hour"), ("1.234,56 a month", 14815, "month"), ("1,234.56 a month", 14815, "month"),
    ("45 000", 45000, "year"), ("45 000 €", 45000, "year"), ("１８/hr", 37440, "hour"),
])
def test_parse_pay(typed, yearly, period):
    pay, err = parse_pay(typed)
    assert err is None, err
    assert pay.yearly == yearly and pay.period == period and pay.as_typed == typed.strip()


@pytest.mark.parametrize("typed, currency", [("C$380k", "CAD"), ("CA$380k", "CAD"), ("€45k", "EUR"), ("45k EUR", "EUR"), ("£30,000", "GBP"),
                                             ("USD 90k", "USD"), ("90k usd", "USD"), ("$90k", ""), ("90k", "")])
def test_parse_pay_keeps_currency(typed, currency):
    pay, err = parse_pay(typed)
    assert err is None and pay.currency == currency


def test_bare_small_numbers_are_flagged_as_assumed():
    assert parse_pay("18")[0].assumed and parse_pay("150")[0].assumed
    assert not parse_pay("18/hr")[0].assumed and not parse_pay("45000")[0].assumed


@pytest.mark.parametrize("typed", ["nan", "inf", "-inf", "1e400", "1e30", "-5", "0", "0/hr", "lots", "18/fortnight", "3", "99999999",
                                   "18 €  $", "1,2,3", "60k-70k-80k", "--5"])
def test_bad_pay_is_a_clear_error_not_a_crash(typed):
    pay, err = parse_pay(typed)
    assert pay is None and err
    # Only formats that really work are suggested.
    for example in ("18/hr", "45k a year"):
        if example in err:
            assert parse_pay(example)[1] is None


def test_pay_ranges():
    low, high, err = parse_pay_range("60k-70k")
    assert err is None and (low.yearly, high.yearly) == (60000, 70000)
    low, high, _ = parse_pay_range("17–19/hr")
    assert (low.yearly, high.yearly, low.period) == (35360, 39520, "hour")
    low, high, _ = parse_pay_range("60 to 70k")
    assert (low.yearly, high.yearly) == (60000, 70000)
    assert parse_pay_range("70k-60k")[2]
    assert parse_pay("60k-70k", pick="high")[0].yearly == 70000


@pytest.mark.parametrize("typed, miles", [("15", 15), ("15 miles", 15), ("15mi", 15), ("25 km", 16), ("0", 0), ("", None)])
def test_parse_distance(typed, miles):
    assert parse_distance(typed) == (miles, None)


@pytest.mark.parametrize("typed", ["nan", "-5", "15k", "15/hr", "far", "1e400"])
def test_bad_distance(typed):
    value, err = parse_distance(typed)
    assert value is None and "distance" in err


# ------------------------------------------------------------------ profile basics


def test_profile_keeps_what_was_typed_and_shows_units(ctx):
    data, errors = clean_basics({"name": "Sam", "pay_floor": "18/hr", "pay_target": "22/hr", "max_commute_miles": "25 km"})
    assert not errors
    assert data["pay_floor"] == 37440 and data["pay_floor_detail"]["as_typed"] == "18/hr" and data["pay_floor_detail"]["period"] == "hour"
    assert data["max_commute_miles"] == 16 and data["max_commute_text"] == "25 km"
    ctx.profile.save_basics(data)
    text = ctx.profile.as_text()
    assert "Base pay floor: 37,440 a year (typed 18/hr)" in text and "target: 45,760 a year (typed 22/hr)" in text
    # Saving the stored data again (as approving a suggestion does) keeps the typed form.
    again, errors = clean_basics(ctx.profile.basics())
    assert not errors and again["pay_floor_detail"]["as_typed"] == "18/hr"


def test_profile_currency_reaches_the_ai(ctx):
    ctx.profile.save_basics(clean_basics({"pay_floor": "C$380k"})[0])
    assert "Base pay floor: CAD 380,000 a year (typed C$380k)" in ctx.profile.as_text()


def test_pay_range_in_profile_floor_takes_low_target_takes_high():
    data, errors = clean_basics({"pay_floor": "60k-70k", "pay_target": "60k-70k"})
    assert not errors and data["pay_floor"] == 60000 and data["pay_target"] == 70000


@pytest.mark.parametrize("field, typed", [("pay_floor", "nan"), ("pay_floor", "1e400"), ("pay_floor", "-5"), ("pay_target", "inf"),
                                          ("max_commute_miles", "nan"), ("max_commute_miles", "15/hr")])
def test_bad_numbers_are_errors(field, typed):
    _, errors = clean_basics({field: typed})
    assert field in errors


def test_commute_error_is_about_distance():
    _, errors = clean_basics({"max_commute_miles": "an hour"})
    assert "distance" in errors["max_commute_miles"] and "18/hr" not in errors["max_commute_miles"]


def test_long_text_is_an_error_not_cut():
    _, errors = clean_basics({"summary": "x" * 1600})
    assert "1,500" in errors["summary"]


def test_profile_form_shows_typed_pay_and_assumed_reading(client, ctx):
    r = client.post("/profile/basics", data={"name": "Sam", "pay_floor": "18"})
    assert r.status_code == 200 and "$18 an hour" in r.headers["hx-trigger"] and "37,440" in r.headers["hx-trigger"]
    client.post("/profile/basics", data={"name": "Sam", "pay_floor": "18/hr", "max_commute_miles": "15 miles"})
    page = client.get("/profile").text
    assert 'value="18/hr"' in page and 'value="15 miles"' in page and "37440" not in page
    assert client.post("/profile/basics", data={"pay_floor": "nan"}).status_code == 400


# ------------------------------------------------------------------ role pay


def test_role_pay_accepts_hourly_and_ranges(ctx):
    rid = ctx.roles.create({"company": "Care Co", "title": "CNA"})
    ctx.roles.update(rid, {"pay_min": "17/hr", "pay_max": "19/hr"})
    r = ctx.roles.get(rid)
    assert (r["pay_min"], r["pay_max"]) == (35360, 39520)
    ctx.roles.update(rid, {"pay_min": "€40k-50k", "pay_max": ""})
    r = ctx.roles.get(rid)
    assert (r["pay_min"], r["pay_max"], r["pay_currency"]) == (40000, 50000, "EUR")
    ctx.roles.update(rid, {"pay_min": 150000, "pay_max": 180000})  # numbers (from the AI) are already yearly
    assert ctx.roles.get(rid)["pay_min"] == 150000


@pytest.mark.parametrize("fields, message", [
    ({"pay_min": "inf"}, "Pay from"), ({"pay_min": "1e30"}, "Pay from"), ({"pay_max": "-5"}, "Pay to"),
    ({"pay_min": float("inf")}, "Pay from"), ({"pay_min": 10**30}, "Pay from"), ({"pay_min": -5}, "Pay from"),
    ({"pay_min": "90k", "pay_max": "60k"}, "at least"),
])
def test_role_pay_validation(ctx, fields, message):
    rid = ctx.roles.create({"company": "Acme", "title": "Ops"})
    with pytest.raises(RoleError, match=message) as exc:
        ctx.roles.update(rid, fields)
    assert "pay_min" not in str(exc.value) and "pay_max" not in str(exc.value)


def test_role_pay_max_below_existing_min_is_refused(ctx):
    rid = ctx.roles.create({"company": "Acme", "title": "Ops", "pay_min": "90k"})
    with pytest.raises(RoleError, match="at least"):
        ctx.roles.update(rid, {"pay_max": "60k"})


def test_role_page_pay_form_error_is_friendly(client, ctx):
    rid = ctx.roles.create({"company": "Care Co", "title": "CNA"})
    r = client.post(f"/roles/{rid}", data={"pay_min": "lots"})
    assert r.status_code == 400 and "pay_min" not in r.headers["hx-trigger"]
    client.post(f"/roles/{rid}", data={"pay_min": "17/hr", "pay_max": "19/hr"})
    assert "$35,360–$39,520 a year (about $17–$19 an hour)" in client.get(f"/roles/{rid}").text


def test_pay_range_filter():
    assert pay_range(35360, 39520) == "$35,360–$39,520 a year (about $17–$19 an hour)"
    assert pay_range(150000, 180000) == "$150,000–$180,000 a year"
    assert pay_range(36400, None) == "$36,400 a year (about $17.50 an hour)"
    assert pay_range(40000, 50000, "EUR") == "€40,000–€50,000 a year (about €19–€24 an hour)"  # "about" rounds inexact rates
    assert pay_range(62000, 74000) == "$62,000–$74,000 a year (about $30–$36 an hour)"
    assert pay_range(120000, 120000, "CHF") == "CHF 120,000 a year"
    assert pay_range(None, None) == ""


# ------------------------------------------------------------------ dates and order


@pytest.mark.parametrize("text, key", [("May 2025", (2025, 5)), ("2025-05", (2025, 5)), ("05/2025", (2025, 5)), ("2025", (2025, 0)),
                                       ("Sept. 2022 (part-time)", (2022, 9)), ("Fall 2024", (2024, 10)), ("garbage", (0, 0))])
def test_date_key(text, key):
    assert date_key(text) == key


def test_work_history_and_education_are_newest_first(ctx):
    for title, start, end in [("Old", "Jan 2019", "Dec 2024"), ("Mid", "Jan 2025", "Aug 2025"), ("Now", "Sep 2025", ""),
                              ("Also now", "2020", "Present")]:
        ctx.db.run("INSERT INTO experience (company, title, start_date, end_date) VALUES ('A', ?, ?, ?)", (title, start, end))
    assert [j["title"] for j in ctx.profile.experience()] == ["Now", "Also now", "Mid", "Old"]
    for school, end in [("High school", "2019"), ("College", "May 2023"), ("Grad school", "")]:
        ctx.db.run("INSERT INTO education (school, start_date, end_date) VALUES (?, '2024', ?)", (school, end))
    assert [e["school"] for e in ctx.profile.education()] == ["Grad school", "College", "High school"]


# ------------------------------------------------------------------ AI profile text


def test_profile_text_keeps_rules_first_and_trims_old_facts(ctx):
    ctx.profile.save_basics(clean_basics({"name": "Senior Pat"})[0])
    with ctx.db.tx() as conn:
        ctx.profile.set_skill(conn, "Marketo", "never")
        ctx.profile.set_skill(conn, "HubSpot", "have")
        for year in range(2010, 2018):
            exp = conn.execute("INSERT INTO experience (company, title, start_date, end_date) VALUES (?, 'Lead', ?, ?)",
                               (f"Co{year}", str(year), str(year + 1))).lastrowid
            for i in range(12):
                ctx.profile.add_fact(conn, f"Result {i} at Co{year}: " + "did a detailed thing " * 12, experience_id=exp)
    ctx.db.run("INSERT INTO education (school, degree) VALUES ('State University', 'MBA')")
    text = ctx.profile.as_text(20_000)
    assert len(text) <= 20_000
    assert "Never used (do not claim): Marketo" in text and "State University" in text and "HubSpot" in text
    assert "Result 0 at Co2017" in text and "Result 0 at Co2010" not in text  # the oldest job's facts go first
    assert "[exp 1] Lead, Co2010" in text  # every job is still listed
    short = ctx.profile.as_text(6000, facts=False)
    assert "Never used (do not claim): Marketo" in short and "Result" not in short


# ------------------------------------------------------------------ certifications, projects and the resume


@pytest.fixture
def new_grad(ctx):
    ctx.profile.save_basics(clean_basics({"name": "Riley Chen", "email": "riley@example.com", "phone": "555-0111", "city": "Baltimore",
                                          "region": "MD", "level": "entry level"})[0])
    ctx.db.run("INSERT INTO education (school, degree, field, start_date, end_date) VALUES ('Towson University', 'B.S.', 'Computer Science', "
               "'2022', 'May 2026')")
    ctx.db.run("INSERT INTO education (school, degree, field, start_date, end_date) VALUES ('Coding Bootcamp', '', '', '', '2021')")
    exp = ctx.db.run("INSERT INTO experience (company, title, start_date, end_date) VALUES ('Campus Library', 'Student Assistant (part-time)', "
                     "'Sep 2023', '')").lastrowid
    with ctx.db.tx() as conn:
        fact = ctx.profile.add_fact(conn, "Helped 40 students a week find research sources", experience_id=exp)
        cert = ctx.profile.add_certification(conn, {"cert_name": "AWS Certified Cloud Practitioner", "cert_issuer": "Amazon Web Services",
                                                    "cert_date": "Mar 2025"})[0]
        proj = ctx.profile.add_project(conn, {"project_name": "Bus Tracker", "project_what": "Built a web app that shows live bus arrivals "
                                                                                              "for 3,000 riders", "project_date": "2025"})[0]
    rid = ctx.roles.create({"company": "Globex", "title": "Junior Developer", "posting": "Entry-level developer. AWS a plus."})
    return {"exp": exp, "fact": fact, "cert": cert, "proj": proj, "role": rid}


def test_certifications_and_projects_reach_the_ai(ctx, new_grad):
    text = ctx.profile.as_text()
    assert f"[cert {new_grad['cert']}] AWS Certified Cloud Practitioner, Amazon Web Services (Mar 2025)" in text
    assert f"[project {new_grad['proj']}] Bus Tracker (2025): Built a web app" in text
    assert "certification_ids" in tasks.RESUME_SCHEMA["properties"] and "project_ids" in tasks.RESUME_SCHEMA["properties"]
    assert "certification" in tasks.DRAFT_SYSTEM.lower() and "project" in tasks.DRAFT_SYSTEM.lower()


def _script(runner, new_grad, **extra):
    runner.script["draft_resume"] = lambda *_: Result(ok=True, data={
        "summary": "Computer science graduate.", "experience": [{"experience_id": new_grad["exp"], "bullets": [
            {"text": "Helped 40 students a week find research sources", "fact_ids": [new_grad["fact"]]}]}],
        "skills": ["Python"], "keywords_missing": [], **extra})
    runner.script["claims"] = lambda *_: Result(ok=True, data={"problems": []})


def test_new_grad_resume_has_education_first_and_certs_and_projects(ctx, runner, new_grad):
    _script(runner, new_grad, certification_ids=[new_grad["cert"]], project_ids=[new_grad["proj"]])
    doc = documents.get(ctx, asyncio.run(documents.create(ctx, new_grad["role"], "resume")))
    assert doc["pages"] == 1 and all(c["ok"] for c in doc["checks"]), doc["checks"]
    _, text = documents.pdf_facts(documents.pdf_path(ctx, doc).read_bytes())
    flat = " ".join(text.split())
    order = [flat.index(s) for s in ("SUMMARY", "EDUCATION", "CERTIFICATIONS", "PROJECTS", "EXPERIENCE", "SKILLS")]
    assert order == sorted(order), flat
    assert "AWS Certified Cloud Practitioner" in flat and "Bus Tracker" in flat and "3,000 riders" in flat
    assert "— Coding Bootcamp" not in flat and "Coding Bootcamp" in flat


def test_resume_with_real_history_keeps_experience_first(ctx, runner, profile_with_jobs):
    data = documents.render_data(ctx, "resume", {"summary": "", "experience": [], "skills": []}, {"company": "X"})
    assert data["order"].index("experience") < data["order"].index("education")


@pytest.fixture
def profile_with_jobs(ctx):
    ctx.db.run("INSERT INTO experience (company, title, start_date) VALUES ('Acme', 'Operations Manager', '2018')")
    ctx.db.run("INSERT INTO education (school, degree) VALUES ('State U', 'BA')")


def test_bullet_citing_another_jobs_fact_is_flagged(ctx, runner, new_grad):
    other = ctx.db.run("INSERT INTO experience (company, title, start_date, end_date) VALUES ('Cafe', 'Barista', '2021', '2022')").lastrowid
    with ctx.db.tx() as conn:
        cafe_fact = ctx.profile.add_fact(conn, "Trained 4 new baristas", experience_id=other)
    runner.script["draft_resume"] = lambda *_: Result(ok=True, data={"summary": "", "skills": [], "keywords_missing": [], "experience": [
        {"experience_id": new_grad["exp"], "bullets": [{"text": "Trained 4 new staff", "fact_ids": [cafe_fact]}]}]})
    runner.script["claims"] = lambda *_: Result(ok=True, data={"problems": []})
    doc = documents.get(ctx, asyncio.run(documents.create(ctx, new_grad["role"], "resume")))
    failed = {c["name"]: c["detail"] for c in doc["checks"] if not c["ok"]}
    assert "Each bullet cites facts from its own job" in failed and "Trained 4 new staff" in failed["Each bullet cites facts from its own job"]


@pytest.mark.parametrize("never, text, flagged", [("Go", "Built a go-to-market plan", False), ("R", "Led R&D budgeting", False),
                                                  ("Go", "Wrote services in Go", True), ("R", "Modeled churn in R.", True),
                                                  ("C", "Wrote C# tools", False), ("Marketo", "Ran marketo-based nurture", True)])
def test_never_used_short_names(ctx, runner, new_grad, never, text, flagged):
    with ctx.db.tx() as conn:
        ctx.profile.set_skill(conn, never, "never")
    runner.script["draft_resume"] = lambda *_: Result(ok=True, data={"summary": "", "skills": [], "keywords_missing": [], "experience": [
        {"experience_id": new_grad["exp"], "bullets": [{"text": text, "fact_ids": [new_grad["fact"]]}]}]})
    runner.script["claims"] = lambda *_: Result(ok=True, data={"problems": []})
    doc = documents.get(ctx, asyncio.run(documents.create(ctx, new_grad["role"], "resume")))
    check = next(c for c in doc["checks"] if c["name"] == "No tools marked never used")
    assert check["ok"] is (not flagged)


def test_cna_resume_with_license_and_no_jobs(ctx, runner):
    ctx.profile.save_basics(clean_basics({"name": "Dana Brooks", "email": "dana@example.com", "phone": "555-0122", "city": "Springfield"})[0])
    ctx.db.run("INSERT INTO education (school, degree, end_date) VALUES ('Harford Community College', 'CNA Training Program', '2025')")
    with ctx.db.tx() as conn:
        ctx.profile.add_certification(conn, {"cert_name": "Certified Nursing Assistant (CNA)", "cert_issuer": "Maryland Board of Nursing",
                                             "cert_date": "Jun 2025"})
    rid = ctx.roles.create({"company": "Care Home", "title": "CNA", "notes": "Recruiter call: nights not required"})  # no posting
    runner.script["draft_resume"] = lambda *_: Result(ok=True, data={"summary": "Licensed CNA.", "experience": [], "skills": ["Patient care"],
                                                                     "keywords_missing": []})
    runner.script["claims"] = lambda *_: Result(ok=True, data={"problems": []})
    doc = documents.get(ctx, asyncio.run(documents.create(ctx, rid, "resume")))
    _, text = documents.pdf_facts(documents.pdf_path(ctx, doc).read_bytes())
    assert "Certified Nursing Assistant (CNA)" in text and "Maryland Board of Nursing" in text
    assert "no posting" in runner.calls[0]["prompt"].lower() and "nights not required" in runner.calls[0]["prompt"]


def test_profile_page_adds_and_deletes_certifications_and_projects(client, ctx):
    assert client.post("/profile/certifications", data={"cert_name": "CPR/BLS", "cert_issuer": "American Heart Association",
                                                        "cert_date": "2025"}).status_code == 204
    assert client.post("/profile/projects", data={"project_name": "Garden app", "project_what": "Tracks watering", "project_date": "2024"}).status_code == 204
    assert client.post("/profile/certifications", data={"cert_name": ""}).status_code == 400
    assert client.post("/profile/projects", data={"project_name": "x" * 500}).status_code == 400
    page = client.get("/profile").text
    assert "CPR/BLS" in page and "American Heart Association" in page and "Garden app" in page
    cert = ctx.profile.certifications()[0]["id"]
    proj = ctx.profile.projects()[0]["id"]
    client.post(f"/profile/certifications/{cert}/delete")
    client.post(f"/profile/projects/{proj}/delete")
    assert ctx.profile.certifications() == [] and ctx.profile.projects() == []


def test_long_job_dates_are_an_error_not_cut(client, ctx):
    r = client.post("/profile/experience", data={"company": "Acme", "title": "Aide", "start_date": "September 2022 (part-time, weekends only)"})
    assert r.status_code == 400
    assert client.post("/profile/experience", data={"company": "Acme", "title": "Aide", "start_date": "September 2022 (part-time)"}).status_code == 204
    assert ctx.profile.experience()[0]["start_date"] == "September 2022 (part-time)"


@pytest.mark.parametrize("years", ["inf", "nan", "-1", "61", "lots"])
def test_skill_years_must_be_sensible(client, ctx, years):
    assert client.post("/profile/skills", data={"name": "Excel", "years": years}).status_code == 400
    assert ctx.profile.skills() == []


# ------------------------------------------------------------------ roles with no posting, question queue


def test_role_without_posting_can_be_scored(ctx, runner, client):
    ctx.profile.save_basics(clean_basics({"name": "Dana"})[0])
    rid = ctx.roles.create({"company": "Care Home", "title": "CNA", "notes": "Recruiter said days only, 18/hr"})
    runner.script["score"] = lambda *_: Result(ok=True, data={"skills": 20, "experience": 15, "level": 20, "culture": 15, "why": "Good fit.",
                                                              "gates": {g: {"result": "pass", "why": "ok"} for g in ("employer", "sector", "location", "pay")},
                                                              "unknown_tools": []})
    out = asyncio.run(tasks.score_role(ctx, rid))
    assert out["ok"] and "without a posting" in out["note"]
    assert "Recruiter said days only" in runner.calls[-1]["prompt"]
    r = ctx.roles.get(rid)
    assert r["fit_score"] == 70 and "Scored without a posting" in r["fit"]
    ctx.claude_ready = lambda: True
    assert client.post(f"/roles/{rid}/score").status_code == 200


def test_tool_questions_past_the_daily_cap_wait_their_turn(ctx):
    rid = ctx.roles.create({"company": "Acme", "title": "Ops"})
    role = ctx.roles.get(rid)
    tools = [f"Tool{i}" for i in range(8)]
    assert tasks.add_profile_questions(ctx, role, tools) == 5
    assert ctx.db.value("SELECT COUNT(*) FROM question_queue") == 3
    ctx.db.run("UPDATE profile_questions SET created = created - 90000")  # a day later
    assert tasks.release_questions(ctx) == 3
    assert {q["topic"] for q in ctx.db.all("SELECT topic FROM profile_questions")} == set(tools)
    assert ctx.db.value("SELECT COUNT(*) FROM question_queue") == 0


def test_score_prompt_mentions_currency():
    assert "currency" in tasks.SCORE_SYSTEM.lower() and "unknown" in tasks.SCORE_SYSTEM
