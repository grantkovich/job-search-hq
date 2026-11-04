"""Tailored resumes and cover letters: draft with the AI, check every claim, build the PDF.

Checks, all shown to the user before they approve:
- every bullet and paragraph cites profile facts that exist
- no tool the user marked "never used" appears
- an AI pass compares each claim with the facts it cites
- the PDF has the right page count (resume 1-2, letter 1)
- the PDF's text layer has the name, email and phone (what applicant tracking systems read)
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import secrets
import time
from datetime import date
from pathlib import Path

from ..ai import tasks
from ..context import Ctx
from ..db import dumps, loads

HERE = Path(__file__).parent
PAGES = {"resume": (1, 2), "cover_letter": (1, 1)}


class DocumentError(Exception):
    pass


def _contact(b: dict) -> list[str]:
    place = ", ".join(x for x in (b.get("city"), b.get("region")) if x)
    links = [re.sub(r"^https://(www\.)?", "", b[k]).rstrip("/") for k in ("linkedin", "github", "website") if b.get(k)]
    return [x for x in (place, b.get("phone"), b.get("email"), *links) if x]


def _dates(start: str, end: str) -> str:
    return f"{start} – {end or 'Present'}" if start else (end or "")


def render_data(ctx: Ctx, kind: str, content: dict, role: dict) -> dict:
    """Everything the template needs, as plain strings and lists."""
    b = ctx.profile.basics()
    base = {"name": b.get("name") or "Your Name", "contact": _contact(b), "paper": ctx.settings.get("paper", "us-letter")}
    if kind == "cover_letter":
        return {**base, "date": date.today().strftime("%B %-d, %Y"), "company": role["company"], "greeting": content.get("greeting", ""),
                "paragraphs": [p["text"] for p in content.get("paragraphs", [])], "closing": content.get("closing", "Sincerely,")}
    jobs = {j["id"]: j for j in ctx.profile.experience()}
    chosen = {e["experience_id"]: e for e in content.get("experience", []) if e.get("experience_id") in jobs}
    experience = []
    for job in jobs.values():  # keep every job, in profile order; untailored ones keep their summary
        entry = chosen.get(job["id"])
        bullets = [x["text"] for x in entry["bullets"]] if entry else ([job["summary"]] if job["summary"] else [])
        experience.append({"title": job["title"], "company": job["company"], "location": job["location"],
                           "dates": _dates(job["start_date"], job["end_date"]), "bullets": bullets})
    education = [{"degree": " ".join(x for x in (e["degree"], e["field"]) if x), "school": e["school"], "dates": _dates(e["start_date"], e["end_date"])}
                 for e in ctx.profile.education()]
    certs = ctx.profile.certifications()  # every certification and license shows; the AI only picks the order
    picked = [i for i in content.get("certification_ids") or [] if isinstance(i, int)]
    certs.sort(key=lambda c: picked.index(c["id"]) if c["id"] in picked else len(picked))
    projects = {p["id"]: p for p in ctx.profile.projects()}
    if isinstance(content.get("project_ids"), list):  # the AI picks which projects fit this role
        project_rows = [projects[i] for i in dict.fromkeys(content["project_ids"]) if i in projects]
    else:
        project_rows = list(projects.values())
    order = (["summary", "education", "certifications", "projects", "experience", "skills"] if ctx.profile.early_career()
             else ["summary", "experience", "education", "certifications", "projects", "skills"])
    return {**base, "headline": b.get("headline", ""), "summary": content.get("summary", ""), "experience": experience,
            "education": education, "skills": content.get("skills", []), "order": order,
            "certifications": [{"name": c["name"], "issuer": c["issuer"], "date": c["on_date"]} for c in certs],
            "projects": [{"name": p["name"], "what": p["what"], "date": p["on_date"]} for p in project_rows]}


def mentions(name: str, text: str) -> bool:
    """Does the text name this tool? Short names (Go, R, C) must match case and stand alone, so "go-to-market",
    "R&D" and "C#" don't count as Go, R or C."""
    if len(name) <= 2:
        return bool(re.search(rf"(?<![\w\-&+#./]){re.escape(name)}(?![\w\-&+#/]|\.\w)", text))
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(name.lower())}(?![a-z0-9])", text.lower()))


def build_pdf(kind: str, data: dict) -> bytes:
    import typst

    template = HERE / ("resume.typ" if kind == "resume" else "letter.typ")
    return typst.compile(str(template), root=str(HERE), sys_inputs={"data": json.dumps(data)}, ignore_system_fonts=True)


def pdf_facts(pdf: bytes) -> tuple[int, str]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(pdf))
    return len(reader.pages), "\n".join(page.extract_text() or "" for page in reader.pages)


def items_of(kind: str, content: dict) -> list[dict]:
    if kind == "resume":
        return [b for e in content.get("experience", []) for b in e.get("bullets", [])]
    return list(content.get("paragraphs", []))


def static_checks(ctx: Ctx, kind: str, content: dict, pdf: bytes) -> list[dict]:
    checks = []
    fact_ids = {f["id"] for f in ctx.profile.facts()}
    items = items_of(kind, content)
    # A letter's opening and closing paragraphs frame it rather than make claims, so only its middle must cite facts.
    claims = items[1:-1] if kind == "cover_letter" and len(items) > 2 else items
    uncited = [i["text"][:80] for i in claims if not [f for f in i.get("fact_ids", []) if f in fact_ids]]
    checks.append({"name": "Every claim cites a profile fact", "ok": not uncited,
                   "detail": "; ".join(uncited)[:400] if uncited else ""})
    if kind == "resume":
        job_of = {f["id"]: f["experience_id"] for f in ctx.profile.facts()}
        crossed = [b["text"][:80] for e in content.get("experience", []) for b in e.get("bullets", [])
                   if any(job_of.get(f) not in (None, e.get("experience_id")) for f in b.get("fact_ids", []) if f in job_of)]
        checks.append({"name": "Each bullet cites facts from its own job", "ok": not crossed, "detail": "; ".join(crossed)[:400]})
    never = [s["name"] for s in ctx.profile.skills() if s["status"] == "never"]
    text_all = " ".join([content.get("summary", ""), *[i["text"] for i in items], *content.get("skills", [])])
    used = [n for n in never if mentions(n, text_all)]
    checks.append({"name": "No tools marked never used", "ok": not used, "detail": ", ".join(used)})
    pages, text = pdf_facts(pdf)
    low, high = PAGES[kind]
    checks.append({"name": f"Page count ({low}-{high})" if low != high else "Fits on one page", "ok": low <= pages <= high,
                   "detail": f"{pages} page{'s' if pages != 1 else ''}"})
    b = ctx.profile.basics()
    flat = re.sub(r"\s+", "", text)
    missing = [label for label, value in (("name", b.get("name")), ("email", b.get("email")), ("phone", b.get("phone")))
               if value and re.sub(r"\s+", "", value) not in flat]
    checks.append({"name": "Name, email and phone readable by applicant tracking systems", "ok": not missing, "detail": ", ".join(missing)})
    if kind == "cover_letter":
        words = len(" ".join(p["text"] for p in items).split())
        checks.append({"name": "Under 350 words", "ok": words <= 350, "detail": f"{words} words"})
    return checks


def _save_pdf(ctx: Ctx, pdf: bytes) -> str:
    name = f"doc-{secrets.token_hex(12)}.pdf"
    path = ctx.config.files_dir / name
    path.write_bytes(pdf)
    path.chmod(0o600)
    return name


async def create(ctx: Ctx, role_id: int, kind: str) -> int:
    role = ctx.roles.get(role_id)
    if not role:
        raise DocumentError("That role doesn't exist.")
    p = ctx.profile
    if not (p.experience() or p.education() or p.certifications() or p.projects()):
        raise DocumentError("Add your work history, education, certifications or projects on the Profile page first.")
    result = await tasks.draft(ctx, role_id, kind)
    if not result.ok:
        raise DocumentError(result.error)
    content = result.data
    return await _finish(ctx, None, role, kind, content)


async def rebuild(ctx: Ctx, doc_id: int, content: dict) -> int:
    doc = ctx.db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if not doc:
        raise DocumentError("That document doesn't exist.")
    return await _finish(ctx, doc, ctx.roles.get(doc["role_id"]), doc["kind"], content)


async def _finish(ctx: Ctx, doc: dict | None, role: dict, kind: str, content: dict) -> int:
    data = render_data(ctx, kind, content, role)
    try:
        pdf = await asyncio.wait_for(asyncio.to_thread(build_pdf, kind, data), timeout=60)
    except Exception as exc:
        raise DocumentError("The PDF couldn't be built.") from exc
    checks = static_checks(ctx, kind, content, pdf)
    problems = await tasks.check_claims(ctx, items_of(kind, content))
    if problems is None:
        checks.append({"name": "Claims match the facts (AI check)", "ok": False, "detail": "The check couldn't run; review the claims yourself."})
    else:
        checks.append({"name": "Claims match the facts (AI check)", "ok": not problems,
                       "detail": "; ".join(f"“{p['text'][:80]}”: {p['issue']}" for p in problems)[:1200]})
    pages, _ = pdf_facts(pdf)
    name = _save_pdf(ctx, pdf)
    now = time.time()
    if doc:
        old = doc["pdf_file"]
        ctx.db.run("UPDATE documents SET content = ?, pdf_file = ?, pages = ?, checks = ?, status = 'draft', updated = ? WHERE id = ?",
                   (dumps(content), name, pages, dumps(checks), now, doc["id"]))
        _remove(ctx, old)
        return doc["id"]
    return ctx.db.run("INSERT INTO documents (role_id, kind, content, pdf_file, pages, checks, created, updated) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                      (role["id"], kind, dumps(content), name, pages, dumps(checks), now, now)).lastrowid


def _remove(ctx: Ctx, name: str) -> None:
    if name and re.fullmatch(r"doc-[0-9a-f]{24}\.pdf", name):
        (ctx.config.files_dir / name).unlink(missing_ok=True)


def pdf_path(ctx: Ctx, doc: dict) -> Path | None:
    name = doc.get("pdf_file") or ""
    if not re.fullmatch(r"doc-[0-9a-f]{24}\.pdf", name):
        return None
    path = ctx.config.files_dir / name
    return path if path.is_file() else None


def get(ctx: Ctx, doc_id: int) -> dict | None:
    doc = ctx.db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if doc:
        doc["content"], doc["checks"] = loads(doc["content"], {}), loads(doc["checks"], [])
    return doc


def delete(ctx: Ctx, doc_id: int) -> None:
    doc = ctx.db.one("SELECT * FROM documents WHERE id = ?", (doc_id,))
    if doc:
        ctx.db.run("DELETE FROM documents WHERE id = ?", (doc_id,))
        _remove(ctx, doc["pdf_file"])
