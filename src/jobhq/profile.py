"""The candidate profile: basic information, work history, education, certifications, projects, skills and facts.

Facts are the only material the AI may use for claims in resumes and letters. A fact
gets here only when the user typed it or approved it, never straight from the AI.
"""
from __future__ import annotations

import math
import re
import time

from .db import Database, dumps, loads
from .pay import Pay, describe, parse_distance, parse_pay_range, yearly_text

LIST_FIELDS = ("target_titles", "target_sectors", "avoid", "work_modes")
TEXT_FIELDS = {
    "name": 120, "email": 200, "phone": 40, "city": 80, "region": 80, "postal_code": 20, "country": 60,
    "linkedin": 200, "github": 200, "website": 200, "headline": 220, "summary": 1500, "level": 80,
    "work_authorization": 200, "notes": 2000,
}
LABELS = {"target_titles": "job titles", "target_sectors": "sectors", "avoid": "deal-breakers", "work_modes": "work modes"}
PAY_FIELDS = ("pay_floor", "pay_target")
NUMBER_FIELDS = ("pay_floor", "pay_target", "max_commute_miles")
HOURS_A_YEAR = 2080
WORK_MODES = ("remote", "hybrid", "onsite")
REQUIRED = {
    "name": "your name", "email": "your email", "city": "where you live", "target_titles": "the job titles you want",
    "work_modes": "remote, hybrid or on-site", "pay_floor": "the lowest base pay you'd take", "summary": "a short summary of your experience",
}
URL_RE = re.compile(r"^https://[^\s<>\"']+$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

EXPERIENCE_LIMITS = {"company": 120, "title": 160, "location": 120, "start_date": 40, "end_date": 40, "summary": 1500}
EDUCATION_LIMITS = {"school": 160, "degree": 120, "field": 120, "start_date": 40, "end_date": 40}
CERT_LIMITS = {"cert_name": 160, "cert_issuer": 160, "cert_date": 40}
PROJECT_LIMITS = {"project_name": 160, "project_what": 600, "project_date": 40}
FIELD_LABELS = {"start_date": "the start date", "end_date": "the end date", "cert_name": "the name", "cert_issuer": "the issuer",
                "cert_date": "the date", "project_name": "the name", "project_what": "what it was", "project_date": "the date"}


def _too_long(label: str, value: str, limit: int) -> str:
    return f"Keep {label} under {limit:,} characters (it's {len(value):,})."


def clean_record(form: dict, limits: dict[str, int], required: dict[str, str]) -> tuple[dict, str | None]:
    """Trim spaces and check lengths for one profile row. Too long is an error, never a silent cut."""
    data = {}
    for name, limit in limits.items():
        raw = str(form.get(name) or "").strip()
        value = raw if name in ("summary", "project_what") else " ".join(raw.split())
        if len(value) > limit:
            return {}, _too_long(FIELD_LABELS.get(name, f"the {name.replace('_', ' ')}"), value, limit)
        data[name] = value
    for name, message in required.items():
        if not data.get(name):
            return {}, message
    return data, None


def clean_years(raw) -> tuple[float | None, str | None]:
    if raw in (None, ""):
        return None, None
    try:
        years = float(str(raw).replace(",", "."))
    except ValueError:
        return None, "Years must be a number from 0 to 60."
    if not math.isfinite(years) or not 0 <= years <= 60:
        return None, "Years must be a number from 0 to 60."
    return years, None


def _pay_field(form: dict, name: str) -> tuple[dict | None, str | None]:
    """(stored detail, error). Stored numbers come back with their detail, so re-saving keeps what was typed."""
    raw = form.get(name)
    detail = form.get(f"{name}_detail")
    if not isinstance(raw, str) and raw is not None and isinstance(detail, dict) and detail.get("yearly") == raw:
        return detail, None
    low, high, err = parse_pay_range(raw)
    if err:
        return None, err
    pay: Pay | None = high if name == "pay_target" else low
    return (pay.as_dict() if pay else None), None


def clean_basics(form: dict) -> tuple[dict, dict]:
    """Check the basic information form. Returns (clean data, {field: problem})."""
    data, errors = {}, {}
    for name, limit in TEXT_FIELDS.items():
        value = " ".join(str(form.get(name) or "").split()) if name not in ("summary", "notes") else str(form.get(name) or "").strip()
        if len(value) > limit:
            errors[name] = _too_long(f"the {name.replace('_', ' ')}", value, limit)
        data[name] = value[:limit]
    for name in ("linkedin", "github", "website"):
        if data[name] and not URL_RE.match(data[name]):
            errors[name] = "Use a full https:// address."
    if data["email"] and not EMAIL_RE.match(data["email"]):
        errors["email"] = "That doesn't look like an email address."
    for name in LIST_FIELDS:
        raw = form.get(name)
        items = raw if isinstance(raw, list) else re.split(r"[\n,;]", str(raw or ""))
        items = [" ".join(str(i).split()) for i in items if i and str(i).strip()]
        if any(len(i) > 100 for i in items):
            errors[name] = f"Keep each of the {LABELS[name]} under 100 characters."
        elif len(items) > 30:
            errors[name] = f"List at most 30 {LABELS[name]}."
        data[name] = [i[:100] for i in items][:30]
    data["work_modes"] = [m.lower() for m in data["work_modes"] if m.lower() in WORK_MODES]
    for name in PAY_FIELDS:
        detail, err = _pay_field(form, name)
        if err:
            errors[name] = err
            detail = None
        data[name] = detail["yearly"] if detail else None
        data[f"{name}_detail"] = detail
    miles, err = parse_distance(form.get("max_commute_miles"))
    if err:
        errors["max_commute_miles"] = err
    data["max_commute_miles"] = miles
    typed = form.get("max_commute_text") if not isinstance(form.get("max_commute_miles"), str) else form.get("max_commute_miles")
    data["max_commute_text"] = str(typed or "").strip()[:30] if miles is not None else ""
    if data.get("pay_floor") and data.get("pay_target") and data["pay_target"] < data["pay_floor"]:
        errors["pay_target"] = "The target should be at least the floor."
    return data, errors


def pay_notes(data: dict) -> list[str]:
    """What a bare number was read as, so the user can see the guess: 'Pay floor: read 18 as $18 an hour (about $37,440 a year).'"""
    notes = []
    for name, label in (("pay_floor", "Pay floor"), ("pay_target", "Target pay")):
        d = data.get(f"{name}_detail")
        if d and d.get("assumed"):
            notes.append(f"{label}: read {d['as_typed']} as {describe(d)}. If you meant something else, add the unit, like 18k a year.")
    return notes


# ------------------------------------------------------------------ dates

MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
SEASONS = {"winter": 1, "spring": 4, "summer": 7, "fall": 10, "autumn": 10}
CURRENT_RE = re.compile(r"\b(present|current|now|today|ongoing|expected)\b", re.I)


def date_key(text: str) -> tuple[int, int]:
    """(year, month) from 'May 2025', '2025-05', '05/2025', '2025', 'Fall 2024'. (0, 0) when there's no year."""
    t = str(text or "").lower()
    year = re.search(r"(?<!\d)(19|20)\d{2}(?!\d)", t)
    if not year:
        return 0, 0
    y = int(year.group(0))
    month = 0
    if (m := re.search(rf"(?<!\d){y}[-/.](\d{{1,2}})(?!\d)", t)) or (m := re.search(rf"(?<!\d)(\d{{1,2}})[-/.]{y}", t)):
        month = int(m.group(1))
    elif m := re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?", t):
        month = MONTHS[m.group(1)]
    elif m := re.search(r"\b(winter|spring|summer|fall|autumn)\b", t):
        month = SEASONS[m.group(1)]
    return y, month if 1 <= month <= 12 else 0


def is_current(end: str, start: str = "") -> bool:
    return bool(CURRENT_RE.search(end or "")) or (not (end or "").strip() and bool((start or "").strip()))


def _newest_first(row: dict) -> tuple:
    end, start = date_key(row.get("end_date", "")), date_key(row.get("start_date", ""))
    current = is_current(row.get("end_date", ""), row.get("start_date", ""))
    return (row.get("sort", 0), 0 if current else 1, -(end[0] * 13 + end[1]), -(start[0] * 13 + start[1]), -row["id"])


EARLY_RE = re.compile(r"\b(intern|internship|part[- ]?time|student|co-?op|apprentice|trainee|summer|seasonal|volunteer|work[- ]study)\b", re.I)
ENTRY_RE = re.compile(r"\b(entry|new grad|graduate|intern|student|first job)\b", re.I)


class Profile:
    def __init__(self, db: Database):
        self.db = db

    def basics(self) -> dict:
        row = self.db.one("SELECT data FROM profile WHERE id = 1")
        return loads(row["data"], {}) if row else {}

    def save_basics(self, data: dict, conn=None) -> dict:
        before = self.basics()
        sql = ("INSERT INTO profile (id, data, updated) VALUES (1, ?, ?) "
               "ON CONFLICT (id) DO UPDATE SET data = excluded.data, updated = excluded.updated")
        (conn.execute if conn else self.db.run)(sql, (dumps(data), time.time()))
        return before

    def missing(self) -> list[str]:
        basics = self.basics()
        return [label for key, label in REQUIRED.items() if not basics.get(key)]

    def experience(self) -> list[dict]:
        """Current jobs first, then by end date, then start date, newest first."""
        return sorted(self.db.all("SELECT * FROM experience"), key=_newest_first)

    def education(self) -> list[dict]:
        return sorted(self.db.all("SELECT * FROM education"), key=lambda e: _newest_first({**e, "sort": 0}))

    def certifications(self) -> list[dict]:
        rows = self.db.all("SELECT * FROM certifications")
        return sorted(rows, key=lambda c: (-(date_key(c["on_date"])[0] * 13 + date_key(c["on_date"])[1]), -c["id"]))

    def projects(self) -> list[dict]:
        rows = self.db.all("SELECT * FROM projects")
        return sorted(rows, key=lambda p: (-(date_key(p["on_date"])[0] * 13 + date_key(p["on_date"])[1]), -p["id"]))

    def early_career(self) -> bool:
        """Little or no full work history: no jobs, only part-time or intern jobs, or an entry-level target."""
        jobs = self.experience()
        if not jobs or ENTRY_RE.search(self.basics().get("level") or ""):
            return True
        return all(EARLY_RE.search(" ".join((j["title"], j["summary"], j["start_date"], j["end_date"]))) for j in jobs)

    def facts(self, experience_id: int | None = None) -> list[dict]:
        if experience_id is None:
            return self.db.all("SELECT * FROM facts ORDER BY experience_id, id")
        return self.db.all("SELECT * FROM facts WHERE experience_id = ? ORDER BY id", (experience_id,))

    def skills(self) -> list[dict]:
        return self.db.all("SELECT * FROM skills ORDER BY status, name COLLATE NOCASE")

    def skill_status(self, name: str) -> str | None:
        return self.db.value("SELECT status FROM skills WHERE name = ? COLLATE NOCASE", (name.strip(),))

    def add_fact(self, conn, text: str, topic: str = "", experience_id: int | None = None, source: str = "user") -> int:
        now = time.time()
        return conn.execute(
            "INSERT INTO facts (topic, text, experience_id, source, created, updated) VALUES (?, ?, ?, ?, ?, ?)",
            (topic[:100], text.strip()[:1000], experience_id, source, now, now),
        ).lastrowid

    def add_certification(self, conn, form: dict) -> tuple[int | None, str | None]:
        data, err = clean_record(form, CERT_LIMITS, {"cert_name": "Add the certification or license name."})
        if err:
            return None, err
        return conn.execute("INSERT INTO certifications (name, issuer, on_date, created) VALUES (?, ?, ?, ?)",
                            (data["cert_name"], data["cert_issuer"], data["cert_date"], time.time())).lastrowid, None

    def add_project(self, conn, form: dict) -> tuple[int | None, str | None]:
        data, err = clean_record(form, PROJECT_LIMITS, {"project_name": "Add the project name."})
        if err:
            return None, err
        return conn.execute("INSERT INTO projects (name, what, on_date, created) VALUES (?, ?, ?, ?)",
                            (data["project_name"], data["project_what"], data["project_date"], time.time())).lastrowid, None

    def set_skill(self, conn, name: str, status: str, level: str = "", years: float | None = None, note: str = "") -> None:
        years, err = clean_years(years)
        if err:
            years = None
        conn.execute(
            "INSERT INTO skills (name, status, level, years, note) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (name) DO UPDATE SET status = excluded.status, level = excluded.level, years = excluded.years, note = excluded.note",
            (name.strip()[:80], status, level[:40], years, note[:300]),
        )

    def _pay_line(self, b: dict) -> str:
        floor = yearly_text(b["pay_floor"], b.get("pay_floor_detail")) if b.get("pay_floor") else ""
        target = yearly_text(b["pay_target"], b.get("pay_target_detail")) if b.get("pay_target") else ""
        return f"Base pay floor: {floor}; target: {target}"

    def as_text(self, limit: int = 40_000, facts: bool = True) -> str:
        """The whole profile as plain text for an AI prompt. This is the user's own data, not untrusted.

        The rules (basics, skills, never-used tools, education) come first so they always fit. When the
        text is too long, facts are left out starting with the oldest job's; every job is still listed.
        """
        b = self.basics()
        commute = b.get("max_commute_miles")
        head = [f"Name: {b.get('name', '')}", f"Location: {', '.join(x for x in (b.get('city'), b.get('region'), b.get('country')) if x)}",
                f"Headline: {b.get('headline', '')}", f"Summary: {b.get('summary', '')}",
                f"Target titles: {', '.join(b.get('target_titles', []))}", f"Target level: {b.get('level', '')}",
                f"Target sectors: {', '.join(b.get('target_sectors', []))}", f"Deal-breakers: {', '.join(b.get('avoid', []))}",
                f"Work modes OK: {', '.join(b.get('work_modes', []))}", f"Max commute (miles): {commute if commute is not None else ''}",
                self._pay_line(b), f"Work authorization: {b.get('work_authorization', '')}", f"Other notes from the user: {b.get('notes', '')}"]
        have = [s for s in self.skills() if s["status"] == "have"]
        never = [s["name"] for s in self.skills() if s["status"] == "never"]
        head.append("Skills: " + "; ".join(f"{s['name']}{' (' + s['level'] + ')' if s['level'] else ''}"
                                           f"{', ' + str(s['years']) + ' yrs' if s['years'] else ''}" for s in have))
        if never:
            head.append("Never used (do not claim): " + ", ".join(never))
        head.append("Education:")
        head += [f"- {' '.join(x for x in (e['degree'], e['field']) if x) or '(no degree listed)'}, {e['school']} "
                 f"({e['start_date']} to {e['end_date'] or ('present' if e['start_date'] else '')})" for e in self.education()]
        certs, projects = self.certifications(), self.projects()
        if certs:
            head.append("Certifications and licenses:")
            head += [f"- [cert {c['id']}] {c['name']}{', ' + c['issuer'] if c['issuer'] else ''}{' (' + c['on_date'] + ')' if c['on_date'] else ''}"
                     for c in certs]
        if projects:
            head.append("Projects:")
            head += [f"- [project {p['id']}] {p['name']}{' (' + p['on_date'] + ')' if p['on_date'] else ''}{': ' + p['what'] if p['what'] else ''}"
                     for p in projects]
        loose = [f for f in self.facts() if f["experience_id"] is None] if facts else []
        if loose:
            head.append("Other facts:")
            head += [f"  * [fact {f['id']}] {f['text']}" for f in loose]
        head += ["", "Experience:"]
        jobs = []
        for job in self.experience():
            lines = [f"- [exp {job['id']}] {job['title']}, {job['company']} ({job['start_date']} to {job['end_date'] or 'present'}) {job['location']}"]
            if job["summary"]:
                lines.append(f"  {job['summary']}")
            jobs.append((lines, [f"  * [fact {f['id']}] {f['text']}" for f in self.facts(job["id"])] if facts else []))
        size = sum(len(x) + 1 for x in head) + sum(len(x) + 1 for lines, fs in jobs for x in lines + fs)
        dropped = 0
        for _, fs in reversed(jobs):  # oldest job first
            while fs and size > limit:
                size -= len(fs.pop()) + 1
                dropped += 1
        out = head + [x for lines, fs in jobs for x in lines + fs]
        if dropped:
            out.append(f"({dropped} facts from older jobs left out to fit.)")
        return "\n".join(out)[:limit]
