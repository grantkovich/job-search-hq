"""People to reach at each company, and the messages drafted for them.

The app never touches LinkedIn: no API, no scraping, no sending. It drafts a connection note,
InMail, email or follow-up from the person's own profile facts; the person copies it into LinkedIn
or email themselves, then marks it sent here. That logs it on the role's timeline and sets a
follow-up reminder. A warm contact becomes the role's warm path, which moves it up the lanes.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

from .ai.runner import AIUnavailable, untrusted
from .context import Ctx
from .roles import RoleError

KINDS = {
    "connection_note": {"label": "Connection note", "limit": 200, "subject": False,
                        "how": "a LinkedIn connection request note under 200 characters (fits free and Premium accounts)"},
    "inmail": {"label": "InMail", "limit": 1200, "subject": True,
               "how": "a LinkedIn InMail: a subject under 80 characters and a body of 4 to 7 short sentences"},
    "email": {"label": "Email", "limit": 1500, "subject": True, "how": "a short email with a subject line"},
    "follow_up": {"label": "Follow-up", "limit": 700, "subject": False,
                  "how": "a brief, friendly follow-up to an earlier message that got no reply, 2 to 4 sentences"},
}
STATUSES = ("to_contact", "sent", "replied", "no_reply")
FOLLOW_UP_DAYS = 7
URL_RE = re.compile(r"^https://([a-z]+\.)?linkedin\.com/in/[^\s<>\"']+$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

DRAFT_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "body": {"type": "string"}},
    "required": ["subject", "body"],
}
DRAFT_SYSTEM = """You write outreach messages for a job seeker to send themselves, in their own plain, warm voice.
Rules:
- Short and specific: why this person, why this company and role, one concrete result from the profile, and one clear, small ask
  (a 15-minute call, advice, or whether they'd be open to referring them). No flattery, no hype, no em dashes.
- Use only facts from the profile. Never invent shared history, numbers, or connections. If how they know the person is given, use it.
- Warm contacts: friendly and direct. Cold contacts: courteous, easy to say no to.
- It's fine to say they're reconnecting or rebuilding their network.
- Only messages listed as already sent were sent. Never say you wrote before unless one is listed.
- The body is only the message itself: no character counts, notes or placeholders.
Return the subject (empty if the format has none) and the body."""


class OutreachError(ValueError):
    pass


def fit(text: str, limit: int) -> str:
    """Trim to the limit at the last sentence end, or failing that the last word, rather than mid-word."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "), *(i for i in (len(cut) - 1,) if cut[-1:] in ".?!"))
    if end >= limit // 2:
        return cut[: end + 1]
    space = cut.rfind(" ")
    return cut[:space].rstrip(" ,;:") if space >= limit // 2 else cut


def clean_contact(form: dict) -> dict:
    data = {
        "name": " ".join(str(form.get("name") or "").split())[:120],
        "title": " ".join(str(form.get("title") or "").split())[:160],
        "company": " ".join(str(form.get("company") or "").split())[:120],
        "relation": "warm" if form.get("relation") == "warm" else "cold",
        "how_known": " ".join(str(form.get("how_known") or "").split())[:300],
        "linkedin_url": str(form.get("linkedin_url") or "").strip()[:300],
        "email": str(form.get("email") or "").strip()[:200],
        "notes": str(form.get("notes") or "").strip()[:2000],
    }
    if not data["name"]:
        raise OutreachError("Add the person's name.")
    if data["linkedin_url"] and not URL_RE.match(data["linkedin_url"]):
        raise OutreachError("Use their LinkedIn profile link, like https://www.linkedin.com/in/name")
    if data["email"] and not EMAIL_RE.match(data["email"]):
        raise OutreachError("That email address doesn't look right.")
    return data


class Outreach:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.db = ctx.db

    def add(self, role_id: int | None, form: dict) -> int:
        data = clean_contact(form)
        role = self.ctx.roles.get(role_id) if role_id else None
        if role_id and not role:
            raise OutreachError("That role doesn't exist.")
        if role and not data["company"]:
            data["company"] = role["company"]
        now = time.time()
        cid = self.db.run(
            "INSERT INTO contacts (role_id, name, title, company, relation, how_known, linkedin_url, email, notes, created, updated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (role_id, data["name"], data["title"], data["company"], data["relation"], data["how_known"], data["linkedin_url"],
             data["email"], data["notes"], now, now)).lastrowid
        if role and data["relation"] == "warm" and not role["warm_path"]:
            how = f" ({data['how_known']})" if data["how_known"] else ""
            self.ctx.roles.update(role["id"], {"warm_path": f"{data['name']}{how}"[:300]}, summary=f"Warm path: {data['name']}")
        return cid

    def get(self, contact_id: int) -> dict | None:
        return self.db.one("SELECT * FROM contacts WHERE id = ?", (contact_id,))

    def for_role(self, role_id: int) -> list[dict]:
        rows = self.db.all("SELECT * FROM contacts WHERE role_id = ? ORDER BY relation DESC, id", (role_id,))
        for row in rows:
            row["messages"] = self.messages(row["id"])
        return rows

    def all(self) -> list[dict]:
        return self.db.all("SELECT c.*, r.title AS role_title FROM contacts c LEFT JOIN roles r ON r.id = c.role_id "
                           "ORDER BY CASE c.status WHEN 'to_contact' THEN 0 WHEN 'sent' THEN 1 ELSE 2 END, c.updated DESC")

    def messages(self, contact_id: int) -> list[dict]:
        return self.db.all("SELECT * FROM outreach_messages WHERE contact_id = ? ORDER BY id DESC", (contact_id,))

    def delete(self, contact_id: int) -> None:
        self.db.run("DELETE FROM contacts WHERE id = ?", (contact_id,))

    def follow_ups_due(self) -> list[dict]:
        cutoff = (datetime.now(self.ctx.config.tz).date() - timedelta(days=FOLLOW_UP_DAYS)).isoformat()
        return self.db.all("SELECT * FROM contacts WHERE status = 'sent' AND last_contacted != '' AND last_contacted <= ? ORDER BY last_contacted",
                           (cutoff,))

    async def draft(self, contact_id: int, kind: str) -> int:
        if kind not in KINDS:
            raise OutreachError("Unknown kind of message.")
        contact = self.get(contact_id)
        if not contact:
            raise OutreachError("That contact doesn't exist.")
        role = self.ctx.roles.get(contact["role_id"]) if contact["role_id"] else None
        spec = KINDS[kind]
        earlier = [m for m in self.messages(contact_id) if m["status"] == "sent"][:3]  # unsent drafts were never seen
        prompt = (f"Write {spec['how']}.\n\nTo: {contact['name']}, {contact['title']} at {contact['company']}\n"
                  f"Relationship: {contact['relation']}{'; ' + contact['how_known'] if contact['how_known'] else ''}\n"
                  f"Their notes: {contact['notes']}\n"
                  + (f"About the role: {role['title']} at {role['company']}\n{untrusted('job posting', role['posting'], 6000)}\n" if role else "")
                  + ("Messages already sent to them:\n" + "\n---\n".join(m["body"] for m in earlier) + "\n" if earlier else "")
                  + f"\nThe sender's profile:\n{self.ctx.profile.as_text(12_000)}")
        result = await self.ctx.runner.run(f"outreach_{kind}", DRAFT_SYSTEM, prompt, schema=DRAFT_SCHEMA, max_turns=2, budget_usd=0.15)
        if not result.ok:
            raise AIUnavailable(result.error or "The draft couldn't be written.")
        body = " ".join(str(result.data.get("body", "")).split()) if kind == "connection_note" else str(result.data.get("body", "")).strip()
        body = fit(body, spec["limit"])
        subject = " ".join(str(result.data.get("subject", "")).split())[:120] if spec["subject"] else ""
        return self.db.run("INSERT INTO outreach_messages (contact_id, kind, subject, body, created) VALUES (?, ?, ?, ?, ?)",
                           (contact_id, kind, subject, body, time.time())).lastrowid

    def edit(self, message_id: int, subject: str, body: str) -> None:
        msg = self.db.one("SELECT * FROM outreach_messages WHERE id = ?", (message_id,))
        if not msg:
            raise OutreachError("That draft doesn't exist.")
        limit = KINDS[msg["kind"]]["limit"]
        self.db.run("UPDATE outreach_messages SET subject = ?, body = ? WHERE id = ?", (subject.strip()[:120], body.strip()[:limit], message_id))

    def mark_sent(self, message_id: int) -> dict:
        msg = self.db.one("SELECT * FROM outreach_messages WHERE id = ?", (message_id,))
        if not msg:
            raise OutreachError("That draft doesn't exist.")
        contact = self.get(msg["contact_id"])
        today = datetime.now(self.ctx.config.tz).date()
        with self.db.tx() as conn:
            conn.execute("UPDATE outreach_messages SET status = 'sent', sent_on = ? WHERE id = ?", (today.isoformat(), message_id))
            conn.execute("UPDATE contacts SET status = 'sent', last_contacted = ?, updated = ? WHERE id = ?",
                         (today.isoformat(), time.time(), contact["id"]))
        if contact["role_id"]:
            try:
                self.ctx.roles.add_event(contact["role_id"], "followup", today.isoformat(),
                                         f"{KINDS[msg['kind']]['label']} sent to {contact['name']}")
            except RoleError:
                pass
        when = today + timedelta(days=FOLLOW_UP_DAYS)
        due = datetime(when.year, when.month, when.day, 9, 0, tzinfo=self.ctx.config.tz).timestamp()
        rid = self.ctx.reminders.set(f"Follow up with {contact['name']} at {contact['company'] or 'their company'} if they haven't replied",
                                     due, contact["role_id"])
        return {"reminder_id": rid, "due": due}

    def set_status(self, contact_id: int, status: str) -> None:
        if status not in STATUSES:
            raise OutreachError("Unknown status.")
        contact = self.get(contact_id)
        if not contact:
            raise OutreachError("That contact doesn't exist.")
        self.db.run("UPDATE contacts SET status = ?, updated = ? WHERE id = ?", (status, time.time(), contact_id))
        if status == "replied" and contact["role_id"]:
            today = datetime.now(self.ctx.config.tz).date().isoformat()
            self.ctx.roles.add_event(contact["role_id"], "note", today, f"{contact['name']} replied")
