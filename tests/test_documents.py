import asyncio

import pytest

from jobhq import documents
from jobhq.ai.runner import Result
from jobhq.profile import clean_basics


@pytest.fixture
def profile(ctx):
    ctx.profile.save_basics(clean_basics({"name": "Pat Lee", "email": "pat@example.com", "phone": "555-0100", "city": "Austin", "region": "TX",
                                          "linkedin": "https://www.linkedin.com/in/patlee"})[0])
    exp = ctx.db.run("INSERT INTO experience (company, title, start_date, end_date) VALUES ('Acme', 'Ops Lead', 'Jan 2020', '')").lastrowid
    with ctx.db.tx() as conn:
        f1 = ctx.profile.add_fact(conn, "Migrated 75,000 contacts to HubSpot", experience_id=exp)
        f2 = ctx.profile.add_fact(conn, "Built lead scoring that raised MQL to SQL conversion 18%", experience_id=exp)
        ctx.profile.set_skill(conn, "Marketo", "never")
    rid = ctx.roles.create({"company": "Globex", "title": "Marketing Ops Manager", "posting": "We need HubSpot and Marketo."})
    return {"exp": exp, "facts": (f1, f2), "role": rid}


def script_resume(runner, exp, facts, extra_bullet=None):
    bullets = [{"text": "Migrated 75,000 contacts to HubSpot with no downtime", "fact_ids": [facts[0]]},
               {"text": "Raised MQL to SQL conversion 18% with new lead scoring", "fact_ids": [facts[1]]}]
    if extra_bullet:
        bullets.append(extra_bullet)
    runner.script["draft_resume"] = lambda *_: Result(ok=True, data={"summary": "Marketing ops lead.", "experience": [
        {"experience_id": exp, "bullets": bullets}], "skills": ["HubSpot", "Salesforce"], "keywords_missing": ["Marketo"]})
    runner.script["claims"] = lambda *_: Result(ok=True, data={"problems": []})


def test_resume_is_drafted_built_and_checked(ctx, runner, profile):
    script_resume(runner, profile["exp"], profile["facts"])
    doc_id = asyncio.run(documents.create(ctx, profile["role"], "resume"))
    doc = documents.get(ctx, doc_id)
    assert doc["pages"] == 1 and doc["status"] == "draft"
    assert all(c["ok"] for c in doc["checks"]), doc["checks"]
    path = documents.pdf_path(ctx, doc)
    assert path and path.read_bytes().startswith(b"%PDF") and oct(path.stat().st_mode & 0o777) == "0o600"
    _, text = documents.pdf_facts(path.read_bytes())
    assert "pat@example.com" in text and "555-0100" in text and "75,000" in text


def test_never_used_tools_and_uncited_claims_are_flagged(ctx, runner, profile):
    script_resume(runner, profile["exp"], profile["facts"], {"text": "Ran Marketo nurture programs for 40 brands", "fact_ids": []})
    runner.script["claims"] = lambda *_: Result(ok=True, data={"problems": [{"text": "Ran Marketo nurture", "issue": "not in the facts"}]})
    doc = documents.get(ctx, asyncio.run(documents.create(ctx, profile["role"], "resume")))
    failed = {c["name"]: c["detail"] for c in doc["checks"] if not c["ok"]}
    assert "No tools marked never used" in failed and "Marketo" in failed["No tools marked never used"]
    assert "Every claim cites a profile fact" in failed
    assert "Claims match the facts (AI check)" in failed


def test_document_markup_cannot_run(ctx, runner, profile):
    script_resume(runner, profile["exp"], profile["facts"], {"text": '#read("/etc/passwd") #import "x.typ" $x$ <y>', "fact_ids": [profile["facts"][0]]})
    doc = documents.get(ctx, asyncio.run(documents.create(ctx, profile["role"], "resume")))
    _, text = documents.pdf_facts(documents.pdf_path(ctx, doc).read_bytes())
    assert '#read("/etc/passwd")' in text and "root:" not in text


def test_letter_must_fit_one_page(ctx, runner, profile):
    long = {"text": "word " * 400, "fact_ids": [profile["facts"][0]]}
    runner.script["draft_cover_letter"] = lambda *_: Result(ok=True, data={"greeting": "Dear Hiring Manager,", "paragraphs": [long, long, long],
                                                                            "closing": "Sincerely,"})
    runner.script["claims"] = lambda *_: Result(ok=True, data={"problems": []})
    doc = documents.get(ctx, asyncio.run(documents.create(ctx, profile["role"], "cover_letter")))
    failed = [c["name"] for c in doc["checks"] if not c["ok"]]
    assert "Fits on one page" in failed and "Under 350 words" in failed


def test_draft_needs_some_profile_but_no_posting(ctx):
    rid = ctx.roles.create({"company": "A", "title": "B"})
    with pytest.raises(documents.DocumentError, match="Profile page"):
        asyncio.run(documents.create(ctx, rid, "resume"))


def test_edit_rebuild_approve_and_download(client, ctx, runner, profile, app):
    script_resume(runner, profile["exp"], profile["facts"])
    doc_id = asyncio.run(documents.create(ctx, profile["role"], "resume"))
    old_file = documents.get(ctx, doc_id)["pdf_file"]
    assert client.post(f"/documents/{doc_id}/edit", data={"summary": "New summary", "b-0-0": "Moved 75,000 contacts to HubSpot"}).status_code == 204
    doc = documents.get(ctx, doc_id)
    assert doc["content"]["summary"] == "New summary" and doc["pdf_file"] != old_file
    assert not (ctx.config.files_dir / old_file).exists()
    client.post(f"/documents/{doc_id}/approve")
    assert documents.get(ctx, doc_id)["status"] == "approved"
    pdf = client.get(f"/documents/{doc_id}/pdf?download=1")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert 'filename="Pat_Lee_Resume_Globex.pdf"' in pdf.headers["content-disposition"]
    assert pdf.headers["content-security-policy"].startswith("sandbox")


def test_pdf_paths_cannot_escape(ctx):
    assert documents.pdf_path(ctx, {"pdf_file": "../app.db"}) is None
    assert documents.pdf_path(ctx, {"pdf_file": "doc-zz.pdf"}) is None


def _letter(runner, paragraphs):
    runner.script["draft_cover_letter"] = lambda *_: Result(ok=True, data={"greeting": "Dear Hiring Manager,", "paragraphs": paragraphs,
                                                                            "closing": "Sincerely,"})
    runner.script["claims"] = lambda *_: Result(ok=True, data={"problems": []})


def test_letter_opening_and_closing_need_no_citation(ctx, runner, profile):
    """Found with real Claude: letters naturally open and close without citing facts. Only the middle carries claims."""
    f1, f2 = profile["facts"]
    _letter(runner, [{"text": "I'm writing about the Marketing Ops Manager role at Globex.", "fact_ids": []},
                     {"text": "At Acme I migrated 75,000 contacts to HubSpot.", "fact_ids": [f1]},
                     {"text": "My lead scoring raised MQL to SQL conversion 18%.", "fact_ids": [f2]},
                     {"text": "I'd welcome the chance to talk.", "fact_ids": []}])
    doc = documents.get(ctx, asyncio.run(documents.create(ctx, profile["role"], "cover_letter")))
    assert next(c for c in doc["checks"] if c["name"] == "Every claim cites a profile fact")["ok"], doc["checks"]


def test_letter_middle_paragraphs_must_cite(ctx, runner, profile):
    f1 = profile["facts"][0]
    _letter(runner, [{"text": "Opening.", "fact_ids": []}, {"text": "I ran a team of 40.", "fact_ids": []},
                     {"text": "At Acme I migrated 75,000 contacts.", "fact_ids": [f1]}, {"text": "Closing.", "fact_ids": []}])
    doc = documents.get(ctx, asyncio.run(documents.create(ctx, profile["role"], "cover_letter")))
    check = next(c for c in doc["checks"] if c["name"] == "Every claim cites a profile fact")
    assert not check["ok"] and "team of 40" in check["detail"]


def test_letter_prompt_says_middle_paragraphs_cite_a_fact(ctx, runner, profile):
    """Found with real Claude (3 of 7 letters): a middle paragraph built only on education, a [project] or a gap
    cited no fact, so the citation check failed. Those have no fact ids, so the prompt must say where they go."""
    f1 = profile["facts"][0]
    _letter(runner, [{"text": "Opening.", "fact_ids": []}, {"text": "Migrated 75,000 contacts.", "fact_ids": [f1]},
                     {"text": "Closing.", "fact_ids": []}])
    asyncio.run(documents.create(ctx, profile["role"], "cover_letter"))
    system = next(c["system"] for c in runner.calls if c["task"] == "draft_cover_letter")
    assert "Every paragraph except the first and last cites at least one fact id" in system
    assert "education, certifications, projects or a gap" in system


def test_writing_prompts_keep_gaps_out_and_judge_only_user_claims():
    from jobhq.ai import tasks
    assert "not even to say" in tasks.DRAFT_SYSTEM
    assert "not claims about the user" in tasks.CLAIMS_SYSTEM
    assert "schedule" in tasks.SCORE_SYSTEM and "Other notes from the user" in tasks.SCORE_SYSTEM
