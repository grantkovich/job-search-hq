"""Changes the AI suggests that wait for the user's Approve.

The AI can't change the profile directly. It files a proposal; the user sees exactly
what would change and approves or rejects it. Approving applies it in one transaction
and logs it in the audit trail.
"""
from __future__ import annotations

import time

from .db import Database, dumps, loads
from .profile import Profile, clean_basics
from .store import Audit

KINDS = ("profile_fact", "skill", "profile_field")
PROFILE_FIELDS = ("headline", "summary", "level", "target_titles", "target_sectors", "avoid", "work_modes", "pay_floor",
                  "pay_target", "max_commute_miles", "city", "region", "country", "work_authorization")


class ProposalError(ValueError):
    pass


class Proposals:
    def __init__(self, db: Database, audit: Audit, profile: Profile):
        self.db, self.audit, self.profile = db, audit, profile

    def file(self, kind: str, title: str, payload: dict, source: str = "") -> int:
        if kind not in KINDS:
            raise ProposalError("unknown proposal kind")
        self._validate(kind, payload)
        dup = self.db.one("SELECT id FROM proposals WHERE status = 'pending' AND kind = ? AND payload = ?", (kind, dumps(payload)))
        if dup:
            return dup["id"]
        return self.db.run("INSERT INTO proposals (at, kind, title, payload, source) VALUES (?, ?, ?, ?, ?)",
                           (time.time(), kind, title[:200], dumps(payload), source[:100])).lastrowid

    def _validate(self, kind: str, p: dict) -> None:
        if kind == "profile_fact":
            if not str(p.get("text", "")).strip():
                raise ProposalError("a fact needs text")
            if len(str(p["text"])) > 1000:
                raise ProposalError("keep a fact under 1000 characters")
            exp = p.get("experience_id")
            if exp is not None and not self.db.one("SELECT 1 FROM experience WHERE id = ?", (int(exp),)):
                raise ProposalError(f"there's no experience entry {exp}")
        elif kind == "skill":
            if not str(p.get("name", "")).strip() or p.get("status") not in ("have", "never"):
                raise ProposalError("a skill needs a name and status 'have' or 'never'")
        elif kind == "profile_field":
            if p.get("field") not in PROFILE_FIELDS:
                raise ProposalError(f"field must be one of: {', '.join(PROFILE_FIELDS)}")

    def pending(self) -> list[dict]:
        rows = self.db.all("SELECT * FROM proposals WHERE status = 'pending' ORDER BY id")
        for row in rows:
            row["payload"] = loads(row["payload"], {})
        return rows

    def count(self) -> int:
        return int(self.db.value("SELECT COUNT(*) FROM proposals WHERE status = 'pending'") or 0)

    def decide(self, proposal_id: int, approve: bool, edited_text: str | None = None) -> str:
        with self.db.tx() as conn:
            row = conn.execute("SELECT * FROM proposals WHERE id = ? AND status = 'pending'", (proposal_id,)).fetchone()
            if not row:
                raise ProposalError("That suggestion was already handled.")
            if not approve:
                conn.execute("UPDATE proposals SET status = 'rejected', decided = ? WHERE id = ?", (time.time(), proposal_id))
                return "Rejected."
            payload = loads(row["payload"], {})
            if edited_text is not None and row["kind"] == "profile_fact":
                payload["text"] = edited_text.strip()[:1000]
                self._validate("profile_fact", payload)
            summary = self._apply(conn, row["kind"], payload)
            conn.execute("UPDATE proposals SET status = 'approved', decided = ? WHERE id = ?", (time.time(), proposal_id))
            return summary

    def _apply(self, conn, kind: str, p: dict) -> str:
        if kind == "profile_fact":
            fact_id = self.profile.add_fact(conn, p["text"], p.get("topic", ""), p.get("experience_id"), source="interview")
            self.audit.log(conn, "user", "create", "fact", fact_id, None, p, f"Approved fact: {p['text'][:80]}")
            return "Added to your profile."
        if kind == "skill":
            self.profile.set_skill(conn, p["name"], p["status"], p.get("level", ""), p.get("years"), p.get("note", ""))
            self.audit.log(conn, "user", "update", "skill", p["name"], None, p, f"Skill {p['name']}: {p['status']}")
            return "Skill saved."
        if kind == "profile_field":
            basics = self.profile.basics()
            before = {p["field"]: basics.get(p["field"])}
            basics[p["field"]] = p.get("value")
            cleaned, errors = clean_basics(basics)
            if p["field"] in errors:
                raise ProposalError(errors[p["field"]])
            self.profile.save_basics(cleaned, conn)
            self.audit.log(conn, "user", "update", "profile", p["field"], before, {p["field"]: p.get("value")}, f"Profile {p['field']}")
            return "Profile updated."
        raise ProposalError("unknown proposal kind")
