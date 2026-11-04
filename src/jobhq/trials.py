"""Free trials that make the $20-a-month setup work, and reminders before any of them bills.

- Google Cloud: $300 credit for 90 days. When it ends without an upgrade, Google stops the server and
  deletes it after a 30-day grace period. Before then: upgrade and move to the free e2-micro
  (scripts/move-to-free.sh), or export your data.
- LinkedIn Premium Career: usually a 1-month trial that renews into a paid plan unless cancelled, and
  can't be repeated for 12 months.
Lengths vary by offer, so each is editable.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .context import Ctx
from .notify import ReminderError

TRIALS = {
    "gcp": {"label": "Google Cloud free trial", "days": 90, "warn": (10, 3),
            "what": "move to the free server (whoever runs your server does this; it takes about 35 minutes), or download your data",
            "link": "https://console.cloud.google.com/billing"},
    "linkedin": {"label": "LinkedIn Premium trial", "days": 30, "warn": (5, 1),
                 "what": "keep it, or cancel before it renews",
                 "link": "https://www.linkedin.com/premium/manage/"},
}


class TrialError(ValueError):
    pass


def _key(name: str, field: str) -> str:
    return f"trial_{name}_{field}"


def get(ctx: Ctx) -> list[dict]:
    today = datetime.now(ctx.config.tz).date()
    out = []
    for name, info in TRIALS.items():
        start = ctx.settings.get(_key(name, "start"))
        days = int(ctx.settings.get(_key(name, "days"), info["days"]))
        entry = {"name": name, **info, "days": days, "start": start or "", "end": "", "left": None}
        if start:
            end = date.fromisoformat(start) + timedelta(days=days)
            entry.update(end=end.isoformat(), left=(end - today).days)
        out.append(entry)
    return out


DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y")


def parse_date(text: str) -> date | None:
    """A date as people type it: 2026-11-03, 11/03/2026, 11/3/26, Nov 3, 2026 or 3 November 2026 (US month-first)."""
    text = " ".join(str(text or "").replace(",", " ").split())
    if len(text) >= 10 and text[4:5] == "-" and "T" in text[10:11]:
        text = text[:10]  # an ISO timestamp
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def save(ctx: Ctx, name: str, start: str, days: str | int | None = None) -> dict:
    if name not in TRIALS:
        raise TrialError("unknown trial")
    info = TRIALS[name]
    start = (start or "").strip()
    if start:  # check everything before touching the reminders already set, so a rejected edit changes nothing
        begun = parse_date(start)
        if begun is None:
            raise TrialError("Use a date like 2026-11-03 or 11/03/2026.")
        try:
            length = int(str(days).strip()) if str(days if days is not None else "").strip() else info["days"]
        except ValueError as exc:
            raise TrialError("The trial length must be a whole number of days.") from exc
        if not 1 <= length <= 400:
            raise TrialError("The trial length must be 1 to 400 days.")
        today = datetime.now(ctx.config.tz).date()
        if begun > today + timedelta(days=60):
            raise TrialError("That start date is too far ahead.")
    for rid in ctx.settings.get(_key(name, "reminders"), []) or []:  # replace earlier reminders for this trial
        try:
            ctx.reminders.cancel(int(rid))
        except ReminderError:
            pass
    if not start:
        for field in ("start", "days", "reminders"):
            ctx.settings.delete(_key(name, field))
        return {"cleared": True}
    end = begun + timedelta(days=length)
    ids = []
    for before in info["warn"]:
        when = end - timedelta(days=before)
        due = datetime(when.year, when.month, when.day, 9, 0, tzinfo=ctx.config.tz).timestamp()
        text = f"{info['label']} ends {end:%b %-d} ({before} day{'s' if before != 1 else ''} left): {info['what']}. See Setup."
        try:
            ids.append(ctx.reminders.set(text, due))
        except ReminderError:
            continue  # that warning day has already passed
    ctx.settings.set(_key(name, "start"), begun.isoformat())
    ctx.settings.set(_key(name, "days"), length)
    ctx.settings.set(_key(name, "reminders"), ids)
    return {"end": end.isoformat(), "reminders": len(ids)}


def urgent(ctx: Ctx, within_days: int = 14) -> list[dict]:
    return [t for t in get(ctx) if t["left"] is not None and t["left"] <= within_days]
