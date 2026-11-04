"""Reminders and web push notifications.

Push messages go from this copy straight to the browser's push service (Google for
Chrome and ChromeOS, Apple for Safari, Mozilla for Firefox), signed with this copy's own
VAPID key. Only those push services are accepted as subscription endpoints, so a forged
subscription can't make the server call an arbitrary address.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from .crypto import Box
from .db import Database, dumps, loads
from .store import Settings

log = logging.getLogger("jobhq.notify")
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com", ".notify.windows.com",
              "android.googleapis.com", "push.services.mozilla.com")
MAX_AHEAD_S = 366 * 86400
PRESETS = {"hour": "In 1 hour", "evening": "This evening (6 PM)", "tomorrow": "Tomorrow 9 AM", "monday": "Monday 9 AM"}


class ReminderError(ValueError):
    pass


def preset_due(code: str, now: datetime) -> float | None:
    """When a preset falls, from now (aware datetime in the user's zone). None if it has passed or isn't a preset."""
    if code == "hour":  # in real time: aware datetime + timedelta is wall-clock arithmetic and drifts an hour across DST
        return (now.astimezone(UTC) + timedelta(hours=1)).timestamp()
    if code == "evening":
        at = now.replace(hour=18, minute=0, second=0, microsecond=0)
        return at.timestamp() if at > now else None
    if code == "tomorrow":
        return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    if code == "monday":
        return (now + timedelta(days=7 - now.weekday())).replace(hour=9, minute=0, second=0, microsecond=0).timestamp()
    return None


def allowed_endpoint(endpoint: str) -> bool:
    parts = urlsplit(endpoint or "")
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.port not in (None, 443) or parts.username or parts.password:
        return False
    return any(host.endswith(h) if h.startswith(".") else host == h for h in PUSH_HOSTS)


class Push:
    def __init__(self, db: Database, settings: Settings, box: Box, owner_email: str, base_url: str, sender=None):
        self.db, self.settings, self.box = db, settings, box
        self.owner_email, self.base_url = owner_email, base_url
        self._sender = sender  # tests pass a fake; production uses pywebpush

    def public_key(self) -> str:
        key = self.settings.get("vapid_public_key")
        if not key:
            key = self._generate()
        return key

    def _generate(self) -> str:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec

        private = ec.generate_private_key(ec.SECP256R1())
        pem = private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
        raw = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        public = base64.urlsafe_b64encode(raw).decode().rstrip("=")
        self.settings.set("vapid_private_key", pem)
        self.settings.set("vapid_public_key", public)
        return public

    def subscribe(self, sub: dict, user_agent: str = "") -> None:
        endpoint = str(sub.get("endpoint", ""))
        keys = sub.get("keys") or {}
        if not allowed_endpoint(endpoint) or not isinstance(keys, dict) or not keys.get("p256dh") or not keys.get("auth"):
            raise ReminderError("That browser's push service isn't supported.")
        if len(endpoint) > 1000:
            raise ReminderError("That push address is too long.")
        self.db.run("INSERT INTO push_subscriptions (endpoint, keys, user_agent, created) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (endpoint) DO UPDATE SET keys = excluded.keys, failures = 0",
                    (endpoint, dumps({"p256dh": str(keys["p256dh"])[:200], "auth": str(keys["auth"])[:100]}), user_agent[:200], time.time()))

    def unsubscribe(self, endpoint: str) -> None:
        self.db.run("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))

    def count(self) -> int:
        return int(self.db.value("SELECT COUNT(*) FROM push_subscriptions") or 0)

    def send(self, title: str, body: str, url: str = "/", actions: list[dict] | None = None, tag: str = "") -> int:
        """Send to every subscribed device. Returns how many accepted it."""
        subs = self.db.all("SELECT * FROM push_subscriptions")
        if not subs:
            return 0
        payload = json.dumps({"title": title[:120], "body": body[:300], "url": url if url.startswith("/") else "/",
                              "actions": actions or [], "tag": tag[:60]})
        sent = 0
        for sub in subs:
            if not allowed_endpoint(sub["endpoint"]):
                continue
            try:
                self._deliver({"endpoint": sub["endpoint"], "keys": loads(sub["keys"], {})}, payload)
                sent += 1
                if sub["failures"]:
                    self.db.run("UPDATE push_subscriptions SET failures = 0 WHERE id = ?", (sub["id"],))
            except GoneError:
                self.db.run("DELETE FROM push_subscriptions WHERE id = ?", (sub["id"],))
            except Exception as exc:  # one bad device must not stop the others
                log.warning("push failed: %s", type(exc).__name__)
                self.db.run("UPDATE push_subscriptions SET failures = failures + 1 WHERE id = ?", (sub["id"],))
                self.db.run("DELETE FROM push_subscriptions WHERE failures >= 20")
        return sent

    def _deliver(self, subscription: dict, payload: str) -> None:
        if self._sender:
            return self._sender(subscription, payload)
        from pywebpush import WebPushException, webpush

        try:
            webpush(subscription, payload, vapid_private_key=self._vapid(), vapid_claims={"sub": f"mailto:{self.owner_email}"},
                    ttl=86400, timeout=10)
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                raise GoneError() from exc
            raise

    def _vapid(self):
        from py_vapid import Vapid02  # RFC 8292 "vapid t=..., k=..." headers, what pywebpush expects

        self.public_key()
        return Vapid02.from_pem(self.settings.get("vapid_private_key").encode())


class GoneError(Exception):
    """The browser unsubscribed; forget it."""


class Reminders:
    def __init__(self, db: Database, push: Push, box: Box, tz):
        self.db, self.push, self.box, self.tz = db, push, box, tz

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def parse_local(self, text: str) -> float | None:
        """'2026-11-04T09:00' in the user's zone (or with an offset) as epoch seconds."""
        try:
            when = datetime.fromisoformat(str(text).strip())
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=self.tz)
        return when.timestamp()

    def describe(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, self.tz).strftime("%a %b %-d, %-I:%M %p")

    def set(self, text: str, due: float, role_id: int | None = None) -> int:
        text = " ".join(str(text or "").split())[:200]
        if not text:
            raise ReminderError("A reminder needs some text.")
        now = time.time()
        if due < now + 30:
            raise ReminderError("That time has already passed.")
        if due > now + MAX_AHEAD_S:
            raise ReminderError("Reminders can be set up to a year ahead.")
        return self.db.run("INSERT INTO reminders (text, due, role_id, created) VALUES (?, ?, ?, ?)",
                           (text, due, role_id, now)).lastrowid

    def get(self, reminder_id: int) -> dict | None:
        return self.db.one("SELECT * FROM reminders WHERE id = ?", (reminder_id,))

    def pending(self) -> list[dict]:
        return self.db.all("SELECT * FROM reminders WHERE status = 'pending' ORDER BY due")

    def recent_sent(self, days: int = 3) -> list[dict]:
        return self.db.all("SELECT * FROM reminders WHERE status = 'sent' AND sent > ? ORDER BY sent DESC", (time.time() - days * 86400,))

    def cancel(self, reminder_id: int) -> None:
        if not self.db.run("UPDATE reminders SET status = 'cancelled' WHERE id = ? AND status = 'pending'", (reminder_id,)).rowcount:
            raise ReminderError("That reminder isn't pending anymore.")

    def done(self, reminder_id: int) -> bool:
        """Mark it done. False if it was already done, cancelled or snoozed."""
        return bool(self.db.run("UPDATE reminders SET status = 'done' WHERE id = ? AND status IN ('pending', 'sent')",
                                (reminder_id,)).rowcount)

    def snooze(self, reminder_id: int, code: str) -> int:
        rec = self.get(reminder_id)
        if not rec:
            raise ReminderError("I don't have that reminder anymore.")
        due = preset_due(code, self.now())
        if due is None:
            raise ReminderError("That snooze time has passed.")
        with self.db.tx():
            if not self.done(reminder_id):  # already handled: snoozing it again would make a second copy
                raise ReminderError("That reminder was already handled.")
            return self.set(rec["text"], due, rec["role_id"])

    def action_token(self, reminder_id: int) -> str:
        """For a notification's buttons. One use: a button acts only while its reminder is still pending or sent,
        and either button moves it to done (a snooze makes a new reminder with its own id and token), so a
        replayed token does nothing. See act()."""
        return self.box.sign({"r": reminder_id, "p": "reminder"}, ttl_s=7 * 86400)

    def act(self, reminder_id: int, action: str) -> None:
        """A notification button. Raises ReminderError if the reminder was already handled."""
        if action == "snooze":
            self.snooze(reminder_id, "hour")
        elif not self.done(reminder_id):
            raise ReminderError("That reminder was already handled.")

    def fire_due(self) -> int:
        """Send every reminder whose time has come. Marked sent first, so a crash can't send it twice."""
        due = self.db.all("SELECT * FROM reminders WHERE status = 'pending' AND due <= ? ORDER BY due LIMIT 50", (time.time(),))
        for rec in due:
            if not self.db.run("UPDATE reminders SET status = 'sent', sent = ? WHERE id = ? AND status = 'pending'",
                               (time.time(), rec["id"])).rowcount:
                continue
            token = self.action_token(rec["id"])
            self.push.send("Reminder", rec["text"], url="/reminders", tag=f"reminder-{rec['id']}", actions=[
                {"action": "done", "title": "Done", "token": token},
                {"action": "snooze", "title": "Snooze 1 hour", "token": token},
            ])
        return len(due)
