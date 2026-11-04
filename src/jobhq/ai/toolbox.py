"""The app's tools that Claude can call, grouped by task.

Record changes (roles, events, reminders) apply right away and are logged with Undo.
Profile changes only file a proposal for the user to approve.
Nothing here reaches the network, the filesystem or another copy's data.
"""
from __future__ import annotations

from ..context import Ctx
from ..notify import ReminderError
from ..proposals import ProposalError
from ..roles import CLOSED, EVENT_KINDS, STATUSES, RoleError, lane
from .runner import Tool, ToolRefused, untrusted

AI_EDITABLE = ("status", "next_step", "next_step_date", "notes", "warm_path", "applied_on", "pay_min", "pay_max", "location",
               "work_mode", "url", "posted_on")
# Status rules for the chat AI, enforced here rather than in the prompt: a crafted posting or email
# that steers the chat must not be able to mark someone hired or bring a closed role back.
AI_NEW_STATUSES = ("saved", "applied")
ON_ROLE_PAGE = "The user can change that themselves on the role page; tell them so instead."
# Titles, companies, locations and links come from alert emails and job boards, and timeline notes from
# AI summaries of emails, so the chat AI sees them inside an untrusted wrapper.
OUTSIDE = "roles (titles, companies, places and timeline notes come from job boards and emails)"


def _role_line(r: dict) -> str:
    name, why = lane(r)
    pay = f" ${r['pay_min']:,}-{r['pay_max'] or r['pay_min']:,}" if r.get("pay_min") else ""
    step = f" | next: {r['next_step']} {r['next_step_date']}" if r.get("next_step") else ""
    score = r["fit_score"] if r["fit_score"] is not None else "not scored"
    return f"[role {r['id']}] {r['title']} at {r['company']} | {r['status']} | lane {name} ({why}) | score {score}{pay}{step}"


def _check_status(current: str | None, new: str) -> None:
    if new not in STATUSES:
        raise ToolRefused(f"status must be one of {', '.join(STATUSES)}")
    if new == "accepted":
        raise ToolRefused(f"you can't mark a role accepted. {ON_ROLE_PAGE}")
    if current is None and new not in AI_NEW_STATUSES:
        raise ToolRefused(f"a new role starts as saved or applied. {ON_ROLE_PAGE}")
    if current in CLOSED and new != current:
        raise ToolRefused(f"this role is {current}, and you can't reopen it. {ON_ROLE_PAGE}")


class Toolbox:
    """Builds the tool list for one AI run and remembers what it changed, for the Undo buttons."""

    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.actions: list[dict] = []
        self.proposed: list[int] = []

    # ---------------------------------------------------------------- reading
    def find_roles(self, args):
        text = str(args.get("query") or "").strip()
        status = args.get("status")
        rows = self.ctx.roles.search(text, 30) if text else self.ctx.roles.all((status,) if status in STATUSES else None)[:40]
        if not rows:
            return "No roles match."
        return untrusted(OUTSIDE, "\n".join(_role_line(r) for r in rows), 40_000)

    def get_role(self, args):
        role = self.ctx.roles.get(int(args["role_id"]))
        if not role:
            raise ToolRefused("no role with that id")
        events = self.ctx.roles.events(role["id"])[:20]
        lines = [_role_line(role), f"url: {role['url']}", f"location: {role['location']} {role['work_mode']}",
                 f"applied_on: {role['applied_on']} | warm path: {role['warm_path']}", f"notes: {role['notes']}", "timeline:"]
        lines += [f"  {e['on_date']} {e['kind']}: {e['text']}" for e in events]
        people = self.ctx.db.all("SELECT name, title, relation, status, last_contacted FROM contacts WHERE role_id = ?", (role["id"],))
        if people:
            lines.append("people: " + "; ".join(f"{p['name']} ({p['title']}, {p['relation']}, {p['status']} {p['last_contacted']})" for p in people))
        out = [f"Role {role['id']}:", untrusted(OUTSIDE, "\n".join(lines), 20_000)]
        if role["posting"]:
            out.append(untrusted("job posting", role["posting"], 12_000))
        return "\n".join(out)

    def whats_due(self, _args):
        due = self.ctx.roles.due()
        lines = ["Next steps due:"] + [f"  [role {r['id']}] {r['company']}: {r['next_step']} ({r['next_step_date']})" for r in due["next_steps"]]
        lines += ["Interviews this week:"] + [f"  [role {e['role_id']}] {e['company']} {e['on_date']}: {e['text']}" for e in due["interviews"]]
        lines += ["Follow-ups owed:"] + [f"  [role {r['id']}] {r['company']} {r['title']} (applied {r['applied_on']})" for r in due["followups"]]
        lines += ["Pending reminders:"] + [f"  [reminder {r['id']}] {self.ctx.reminders.describe(r['due'])}: {r['text']}"
                                           for r in self.ctx.reminders.pending()]
        return "What's due:\n" + untrusted(OUTSIDE, "\n".join(lines), 20_000)

    def get_profile(self, _args):
        return self.ctx.profile.as_text()

    # --------------------------------------------------------------- changing
    def add_role(self, args):
        fields = {k: args[k] for k in ("company", "title", "url", "location", "work_mode", "notes", "status") if args.get(k)}
        if "status" in fields:
            _check_status(None, str(fields["status"]))
        try:
            role_id = self.ctx.roles.create(fields, actor="ai")
        except RoleError as exc:
            raise ToolRefused(str(exc)) from exc
        audit_id = self.ctx.db.value("SELECT MAX(id) FROM audit WHERE entity = 'role' AND entity_id = ?", (str(role_id),))
        self.actions.append({"audit_id": audit_id, "summary": f"Added {fields['title']} at {fields['company']}"})
        return f"Added as role {role_id}."

    def update_role(self, args):
        fields = {k: v for k, v in (args.get("changes") or {}).items() if k in AI_EDITABLE}
        if not fields:
            raise ToolRefused(f"nothing to change; allowed fields: {', '.join(AI_EDITABLE)}")
        if "status" in fields:
            current = self.ctx.roles.get(int(args["role_id"]))
            if not current:
                raise ToolRefused("no role with that id")
            _check_status(current["status"], str(fields["status"]))
        try:
            audit_id = self.ctx.roles.update(int(args["role_id"]), fields, actor="ai")
        except RoleError as exc:
            raise ToolRefused(str(exc)) from exc
        if audit_id:
            role = self.ctx.roles.get(int(args["role_id"]))
            self.actions.append({"audit_id": audit_id, "summary": f"{role['company']}: " + ", ".join(f"{k} → {v}" for k, v in fields.items())})
        return "Done. The user can undo it." if audit_id else "No change was needed."

    def log_event(self, args):
        try:
            audit_id = self.ctx.roles.add_event(int(args["role_id"]), str(args["kind"]), str(args["on_date"]), str(args.get("text", "")), actor="ai")
        except RoleError as exc:
            raise ToolRefused(str(exc)) from exc
        self.actions.append({"audit_id": audit_id, "summary": f"Logged {args['kind']} on {args['on_date']}"})
        return "Logged."

    def set_reminder(self, args):
        due = self.ctx.reminders.parse_local(str(args.get("when", "")))
        if due is None:
            raise ToolRefused("when must look like 2026-11-04T09:00 (the user's local time)")
        try:
            rid = self.ctx.reminders.set(str(args.get("text", "")), due, args.get("role_id"))
        except ReminderError as exc:
            raise ToolRefused(str(exc)) from exc
        self.actions.append({"reminder_id": rid, "summary": f"Reminder {self.ctx.reminders.describe(due)}: {args.get('text')}"})
        return f"Reminder {rid} set for {self.ctx.reminders.describe(due)}."

    def cancel_reminder(self, args):
        try:
            self.ctx.reminders.cancel(int(args["reminder_id"]))
        except ReminderError as exc:
            raise ToolRefused(str(exc)) from exc
        return "Cancelled."

    def propose_fact(self, args):
        try:
            pid = self.ctx.proposals.file("profile_fact", f"Add to profile: {str(args.get('text', ''))[:80]}",
                                          {"text": str(args.get("text", "")), "topic": str(args.get("topic", ""))[:100],
                                           "experience_id": args.get("experience_id")}, source="ai")
        except (ProposalError, ValueError) as exc:
            raise ToolRefused(str(exc)) from exc
        self.proposed.append(pid)
        return "Filed for the user's approval. Don't treat it as part of the profile until approved."

    def propose_skill(self, args):
        try:
            years = float(args["years"]) if args.get("years") not in (None, "") else None
            pid = self.ctx.proposals.file("skill", f"Skill: {args.get('name')} ({args.get('status')})",
                                          {"name": str(args.get("name", ""))[:80], "status": args.get("status"),
                                           "level": str(args.get("level", ""))[:40], "years": years, "note": str(args.get("note", ""))[:300]},
                                          source="ai")
        except (ProposalError, ValueError) as exc:
            raise ToolRefused(str(exc)) from exc
        self.proposed.append(pid)
        return "Filed for the user's approval."

    # ------------------------------------------------------------------ sets
    def chat_tools(self) -> list[Tool]:
        return self._reading() + self._records() + self._profile_proposals()

    def interview_tools(self) -> list[Tool]:
        return [self._tool_get_profile()] + self._profile_proposals()

    def _tool_get_profile(self) -> Tool:
        return Tool("get_profile", "The user's whole profile: basics, experience with fact ids, education, skills.", {"type": "object", "properties": {}},
                    self.get_profile)

    def _reading(self) -> list[Tool]:
        return [
            Tool("find_roles", "Search the user's roles by company or title, or list them by status.",
                 {"type": "object", "properties": {"query": {"type": "string"}, "status": {"type": "string", "enum": list(STATUSES)}}},
                 self.find_roles),
            Tool("get_role", "One role with its timeline and posting text.",
                 {"type": "object", "properties": {"role_id": {"type": "integer"}}, "required": ["role_id"]}, self.get_role),
            Tool("whats_due", "Next steps due, interviews this week, follow-ups owed and pending reminders.",
                 {"type": "object", "properties": {}}, self.whats_due),
            self._tool_get_profile(),
        ]

    def _records(self) -> list[Tool]:
        return [
            Tool("add_role", "Add a role the user tells you about.", {"type": "object", "properties": {
                "company": {"type": "string"}, "title": {"type": "string"}, "url": {"type": "string"}, "location": {"type": "string"},
                "work_mode": {"type": "string", "enum": ["remote", "hybrid", "onsite"]}, "notes": {"type": "string"},
                "status": {"type": "string", "enum": list(AI_NEW_STATUSES)}}, "required": ["company", "title"]}, self.add_role),
            Tool("update_role", f"Change a role. Allowed fields: {', '.join(AI_EDITABLE)}. Dates are YYYY-MM-DD. Statuses: {', '.join(STATUSES)}. "
                 "You can't set accepted or reopen a closed role; the user does that on the role page.",
                 {"type": "object", "properties": {"role_id": {"type": "integer"}, "changes": {"type": "object"}},
                  "required": ["role_id", "changes"]}, self.update_role),
            Tool("log_event", "Add to a role's timeline: an interview, follow-up, note, email or deadline.",
                 {"type": "object", "properties": {"role_id": {"type": "integer"}, "kind": {"type": "string", "enum": list(EVENT_KINDS)},
                                                   "on_date": {"type": "string", "description": "YYYY-MM-DD"}, "text": {"type": "string"}},
                  "required": ["role_id", "kind", "on_date"]}, self.log_event),
            Tool("set_reminder", "Set a reminder the user asked for. Only when they ask.",
                 {"type": "object", "properties": {"text": {"type": "string"}, "when": {"type": "string", "description": "YYYY-MM-DDTHH:MM, user's local time"},
                                                   "role_id": {"type": "integer"}}, "required": ["text", "when"]}, self.set_reminder),
            Tool("cancel_reminder", "Cancel a pending reminder.", {"type": "object", "properties": {"reminder_id": {"type": "integer"}},
                                                                   "required": ["reminder_id"]}, self.cancel_reminder),
        ]

    def _profile_proposals(self) -> list[Tool]:
        return [
            Tool("propose_fact", "Suggest adding a fact to the profile, in the user's words. The user approves it first. "
                 "Only facts the user stated; never invent or embellish.",
                 {"type": "object", "properties": {"text": {"type": "string"}, "topic": {"type": "string"},
                                                   "experience_id": {"type": "integer"}}, "required": ["text"]}, self.propose_fact),
            Tool("propose_skill", "Suggest recording a skill or tool the user has ('have') or has never used ('never').",
                 {"type": "object", "properties": {"name": {"type": "string"}, "status": {"type": "string", "enum": ["have", "never"]},
                                                   "level": {"type": "string"}, "years": {"type": "number"}, "note": {"type": "string"}},
                  "required": ["name", "status"]}, self.propose_skill),
        ]


def today_line(ctx: Ctx) -> str:
    from datetime import datetime
    now = datetime.now(ctx.config.tz)
    return f"Today is {now:%A, %B %-d, %Y}, {now:%-I:%M %p} in {ctx.config.timezone}. ({now.date().isoformat()})"
