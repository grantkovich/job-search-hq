"""Roles: every job the user is looking at or has applied to, its timeline, and its lane.

Lanes sort the open roles by what to do next:
  ready   - passes every gate and fits well: apply
  call    - fails a gate or needs a judgment only the user can make
  check   - something is unknown (pay, location, sector) or it isn't scored yet
  later   - weaker fit
  expired - closed or too old
Roles that have been applied to move to the pipeline (applied, interviewing, offer).

Every change is written to the audit log with before and after, so any of them can be undone.
"""
from __future__ import annotations

import math
import time
from datetime import date, datetime, timedelta

from .db import Database, dumps, loads
from .pay import MAX_YEARLY, parse_pay_range
from .store import Audit

PIPELINE = ("applied", "interviewing", "offer")
CLOSED = ("accepted", "rejected", "withdrawn", "closed", "skipped")
STATUSES = ("saved",) + PIPELINE + CLOSED
EDITABLE = {"company": 120, "title": 160, "url": 500, "location": 120, "work_mode": 10, "posting": 60_000, "posted_on": 10,
            "status": 12, "warm_path": 300, "next_step": 300, "next_step_date": 10, "applied_on": 10, "notes": 5000,
            "pay_min": None, "pay_max": None, "pay_currency": 3, "fit_score": None, "fit": None, "gates": None, "source": 40}
PAY_LABELS = {"pay_min": "Pay from", "pay_max": "Pay to"}
GATES = ("employer", "sector", "location", "pay")
EVENT_KINDS = ("interview", "followup", "note", "status", "email", "deadline")
EXPIRE_DAYS = 45
FOLLOW_UP_DAYS = 7


class RoleError(ValueError):
    pass


def grade(score: int | None) -> str:
    if score is None:
        return ""
    return "A" if score >= 75 else "B" if score >= 60 else "C"


def access(role: dict, today: date) -> int:
    if role.get("warm_path"):
        return 1
    posted = _date(role.get("posted_on")) or _date_from_ts(role.get("created"))
    return 2 if posted and (today - posted).days <= 7 else 3


def lane(role: dict, today: date | None = None) -> tuple[str, str]:
    """(lane, why) for a role that hasn't been applied to."""
    today = today or date.today()
    if role["status"] in PIPELINE:
        return "pipeline", role["status"]
    if role["status"] in CLOSED:
        return "closed", role["status"]
    posted = _date(role.get("posted_on"))
    if posted and (today - posted).days > EXPIRE_DAYS:
        return "expired", f"posted {(today - posted).days} days ago"
    gates = loads(role.get("gates"), {}) if isinstance(role.get("gates"), str) else (role.get("gates") or {})
    failed = [g for g in GATES if (gates.get(g) or {}).get("result") == "fail"]
    unknown = [g for g in GATES if (gates.get(g) or {}).get("result", "unknown") == "unknown"]
    if failed:
        reasons = "; ".join((gates[g].get("why") or g) for g in failed)
        return "call", f"fails {', '.join(failed)}: {reasons}"[:200]
    if role.get("fit_score") is None:
        return "check", "not scored yet"
    if unknown:
        return "check", f"unknown: {', '.join(unknown)}"
    g, a = grade(role["fit_score"]), access(role, today)
    if g == "A" or (g == "B" and a <= 2):
        return "ready", f"grade {g}" + (", warm path" if a == 1 else "")
    return "later", f"grade {g}" + (", older cold posting" if a == 3 else "")


def _date(text) -> date | None:
    try:
        return date.fromisoformat(str(text)[:10]) if text else None
    except ValueError:
        return None


def _date_from_ts(ts) -> date | None:
    return datetime.fromtimestamp(ts).date() if ts else None


def _clean_pay(key: str, value, fields: dict, out: dict) -> None:
    """Role pay is kept as yearly whole numbers. Typed text goes through the shared pay parser; numbers (the AI's,
    already yearly) are only checked. A range typed in one box fills both ends."""
    label = PAY_LABELS[key]
    if value in (None, ""):
        out.setdefault(key, None)
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value) or not 0 < value <= MAX_YEARLY:
            raise RoleError(f"{label}: enter a yearly amount above zero and under {MAX_YEARLY:,}.")
        out[key] = int(value)
        return
    low, high, err = parse_pay_range(value)
    if err:
        raise RoleError(f"{label}: {err}")
    out[key] = (high if key == "pay_max" else low).yearly
    other = "pay_max" if key == "pay_min" else "pay_min"
    if low.yearly != high.yearly and fields.get(other) in (None, ""):
        out[other] = (low if other == "pay_min" else high).yearly
    if low.currency:
        out["pay_currency"] = low.currency


def check_pay_order(pay_min, pay_max) -> None:
    if pay_min and pay_max and pay_max < pay_min:
        raise RoleError("Pay to should be at least Pay from.")


def clean(fields: dict) -> dict:
    out = {}
    for key, value in fields.items():
        if key not in EDITABLE:
            raise RoleError(f"'{key}' can't be changed")
        limit = EDITABLE[key]
        if key in ("pay_min", "pay_max"):
            _clean_pay(key, value, fields, out)
        elif key == "fit_score":
            if value in (None, ""):
                out[key] = None
                continue
            try:
                number = float(str(value).replace(",", ""))
            except ValueError as exc:
                raise RoleError("The fit score must be a number from 0 to 100.") from exc
            if not math.isfinite(number) or not 0 <= number <= 100:
                raise RoleError("The fit score must be a number from 0 to 100.")
            out[key] = int(number)
        elif key == "pay_currency":
            out[key] = str(value or "").strip().upper()[:3]
        elif key in ("fit", "gates"):
            out[key] = value if isinstance(value, str) else dumps(value or {})
        else:
            text = str(value if value is not None else "").strip()
            if key not in ("posting", "notes"):
                text = " ".join(text.split())
            out[key] = text[:limit]
    check_pay_order(out.get("pay_min"), out.get("pay_max"))
    if "status" in out and out["status"] not in STATUSES:
        raise RoleError(f"status must be one of: {', '.join(STATUSES)}")
    if "work_mode" in out and out["work_mode"] not in ("", "remote", "hybrid", "onsite"):
        raise RoleError("work_mode must be remote, hybrid or onsite")
    for key in ("posted_on", "next_step_date", "applied_on"):
        if out.get(key) and _date(out[key]) is None:
            raise RoleError(f"{key} must be a date like 2026-11-03")
    if out.get("url") and not out["url"].startswith(("https://", "http://")):
        raise RoleError("url must start with https://")
    return out


class Roles:
    def __init__(self, db: Database, audit: Audit):
        self.db, self.audit = db, audit

    def get(self, role_id: int) -> dict | None:
        return self.db.one("SELECT * FROM roles WHERE id = ?", (role_id,))

    def find_ref(self, source_ref: str) -> dict | None:
        return self.db.one("SELECT * FROM roles WHERE source_ref = ?", (source_ref,))

    def all(self, statuses: tuple[str, ...] | None = None) -> list[dict]:
        if statuses:
            marks = ",".join("?" * len(statuses))
            return self.db.all(f"SELECT * FROM roles WHERE status IN ({marks}) ORDER BY updated DESC", statuses)  # nosec B608 - only ? marks
        return self.db.all("SELECT * FROM roles ORDER BY updated DESC")

    def search(self, text: str, limit: int = 20) -> list[dict]:
        like = f"%{text.strip()[:80]}%"
        return self.db.all("SELECT * FROM roles WHERE company LIKE ? OR title LIKE ? ORDER BY updated DESC LIMIT ?", (like, like, limit))

    def create(self, fields: dict, actor: str = "user", source_ref: str | None = None) -> int:
        data = clean(fields)
        if not data.get("company") or not data.get("title"):
            raise RoleError("A role needs a company and a title.")
        now = time.time()
        with self.db.tx() as conn:
            cols = ["created", "updated", "source_ref", *data.keys()]
            # nosec B608 - column names come from clean(), which only passes EDITABLE keys; values are bound
            cur = conn.execute(f"INSERT INTO roles ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",  # nosec B608
                               (now, now, source_ref, *data.values()))
            role_id = cur.lastrowid
            self.audit.log(conn, actor, "create", "role", role_id, None, data, f"Added {data['title']} at {data['company']}")
        return role_id

    def update(self, role_id: int, fields: dict, actor: str = "user", summary: str = "") -> int:
        """Change a role. Returns the audit id (for Undo)."""
        data = clean(fields)
        with self.db.tx() as conn:
            row = conn.execute("SELECT * FROM roles WHERE id = ?", (role_id,)).fetchone()
            if not row:
                raise RoleError("That role doesn't exist.")
            check_pay_order(data.get("pay_min", row["pay_min"]), data.get("pay_max", row["pay_max"]))
            before = {k: row[k] for k in data}
            changed = {k: v for k, v in data.items() if before[k] != v}
            if not changed:
                return 0
            if changed.get("status") == "applied" and not row["applied_on"] and "applied_on" not in changed:
                changed["applied_on"] = date.today().isoformat()
                before["applied_on"] = row["applied_on"]
            sets = ", ".join(f"{k} = ?" for k in changed)
            conn.execute(f"UPDATE roles SET {sets}, updated = ? WHERE id = ?", (*changed.values(), time.time(), role_id))  # nosec B608 - EDITABLE keys
            if "status" in changed:
                conn.execute("INSERT INTO role_events (role_id, on_date, kind, text, created) VALUES (?, ?, 'status', ?, ?)",
                             (role_id, date.today().isoformat(), f"{before['status']} → {changed['status']}", time.time()))
            label = summary or f"Updated {row['title']} at {row['company']}: " + ", ".join(changed)
            return self.audit.log(conn, actor, "update", "role", role_id, {k: before[k] for k in changed}, changed, label)

    def add_event(self, role_id: int, kind: str, on_date: str, text: str, actor: str = "user") -> int:
        if kind not in EVENT_KINDS:
            raise RoleError(f"kind must be one of: {', '.join(EVENT_KINDS)}")
        if _date(on_date) is None:
            raise RoleError("on_date must be a date like 2026-11-03")
        with self.db.tx() as conn:
            if not conn.execute("SELECT 1 FROM roles WHERE id = ?", (role_id,)).fetchone():
                raise RoleError("That role doesn't exist.")
            event_id = conn.execute("INSERT INTO role_events (role_id, on_date, kind, text, created) VALUES (?, ?, ?, ?, ?)",
                                    (role_id, on_date[:10], kind, text.strip()[:1000], time.time())).lastrowid
            conn.execute("UPDATE roles SET updated = ? WHERE id = ?", (time.time(), role_id))
            return self.audit.log(conn, actor, "create", "role_event", event_id, None,
                                  {"role_id": role_id, "kind": kind, "on_date": on_date, "text": text}, f"{kind} on {on_date}")

    def events(self, role_id: int) -> list[dict]:
        return self.db.all("SELECT * FROM role_events WHERE role_id = ? ORDER BY on_date DESC, id DESC", (role_id,))

    def undo(self, audit_id: int, actor: str = "user") -> str:
        """Reverse one logged change. Refuses if the record changed again since."""
        with self.db.tx() as conn:
            entry = conn.execute("SELECT * FROM audit WHERE id = ?", (audit_id,)).fetchone()
            if not entry or entry["undone"]:
                raise RoleError("Nothing to undo there.")
            before, after = loads(entry["before"]), loads(entry["after"])
            if entry["entity"] == "role" and entry["action"] == "update":
                row = conn.execute("SELECT * FROM roles WHERE id = ?", (entry["entity_id"],)).fetchone()
                if not row:
                    raise RoleError("That role was deleted.")
                if any(row[k] != v for k, v in after.items()):
                    raise RoleError("That role changed again since, so this can't be undone safely.")
                before = {k: v for k, v in before.items() if k in EDITABLE}  # column names only from the known list
                sets = ", ".join(f"{k} = ?" for k in before)
                conn.execute(f"UPDATE roles SET {sets}, updated = ? WHERE id = ?",  # nosec B608 - EDITABLE keys only
                             (*before.values(), time.time(), entry["entity_id"]))
            elif entry["entity"] == "role" and entry["action"] == "create":
                conn.execute("DELETE FROM roles WHERE id = ?", (entry["entity_id"],))
            elif entry["entity"] == "role_event" and entry["action"] == "create":
                conn.execute("DELETE FROM role_events WHERE id = ?", (entry["entity_id"],))
            else:
                raise RoleError("That change can't be undone.")
            conn.execute("UPDATE audit SET undone = 1 WHERE id = ?", (audit_id,))
            self.audit.log(conn, actor, "undo", entry["entity"], entry["entity_id"], after, before, f"Undid: {entry['summary']}")
        return entry["summary"]

    def board(self, today: date | None = None) -> dict[str, list[dict]]:
        today = today or date.today()
        lanes: dict[str, list[dict]] = {k: [] for k in ("ready", "call", "check", "later", "expired", "pipeline", "closed")}
        for role in self.all():
            name, why = lane(role, today)
            lanes[name].append({**role, "lane_why": why, "grade": grade(role["fit_score"])})
        for name in ("ready", "later", "check", "call"):
            lanes[name].sort(key=lambda r: -(r["fit_score"] or 0))
        return lanes

    def due(self, today: date | None = None, days_ahead: int = 2) -> dict[str, list[dict]]:
        """What needs attention: next steps due, interviews coming up, follow-ups owed."""
        today = today or date.today()
        horizon = (today + timedelta(days=days_ahead)).isoformat()
        next_steps = self.db.all("SELECT * FROM roles WHERE next_step_date != '' AND next_step_date <= ? AND status NOT IN "
                                 "('accepted','rejected','withdrawn','closed','skipped') ORDER BY next_step_date", (horizon,))
        interviews = self.db.all("SELECT e.*, r.company, r.title FROM role_events e JOIN roles r ON r.id = e.role_id "
                                 "WHERE e.kind = 'interview' AND e.on_date >= ? AND e.on_date <= ? ORDER BY e.on_date",
                                 (today.isoformat(), (today + timedelta(days=7)).isoformat()))
        cutoff = (today - timedelta(days=FOLLOW_UP_DAYS)).isoformat()
        followups = self.db.all(
            "SELECT r.* FROM roles r WHERE r.status = 'applied' AND r.applied_on != '' AND r.applied_on <= ? AND NOT EXISTS "
            "(SELECT 1 FROM role_events e WHERE e.role_id = r.id AND e.kind IN ('followup','interview','email') AND e.on_date > ?) "
            "ORDER BY r.applied_on", (cutoff, cutoff))
        return {"next_steps": next_steps, "interviews": interviews, "followups": followups}

    def counts(self) -> dict[str, int]:
        rows = self.db.all("SELECT status, COUNT(*) AS n FROM roles GROUP BY status")
        return {r["status"]: r["n"] for r in rows}
