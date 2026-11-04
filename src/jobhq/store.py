"""Settings, the audit log (with undo) and AI spend tracking."""
from __future__ import annotations

import time
from datetime import datetime

from .crypto import Box, DecryptError
from .db import Database, dumps, loads

SECRET_KEYS = {"claude_api_key", "gmail_refresh_token", "vapid_private_key"}


class Settings:
    def __init__(self, db: Database, box: Box):
        self.db, self.box = db, box

    def get(self, key: str, default=None):
        row = self.db.one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        if key in SECRET_KEYS:
            try:
                return self.box.decrypt(row["value"], key)
            except DecryptError:
                return default
        return loads(row["value"], default)

    def set(self, key: str, value) -> None:
        stored = self.box.encrypt(str(value), key) if key in SECRET_KEYS else dumps(value)
        self.db.run(
            "INSERT INTO settings (key, value, updated) VALUES (?, ?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated = excluded.updated",
            (key, stored, time.time()),
        )

    def delete(self, key: str) -> None:
        self.db.run("DELETE FROM settings WHERE key = ?", (key,))

    def has(self, key: str) -> bool:
        return self.get(key) not in (None, "")

    def reencrypt(self) -> int:
        """After JOBHQ_SECRET changes (old one in JOBHQ_SECRET_OLD), seal every secret under the new key."""
        count = 0
        for key in SECRET_KEYS:
            value = self.get(key)
            if value:
                self.set(key, value)
                count += 1
        return count


class Audit:
    """Every change to the user's records is logged with before and after, so it can be shown and undone."""

    def __init__(self, db: Database):
        self.db = db

    def log(self, conn, actor: str, action: str, entity: str, entity_id, before=None, after=None, summary: str = "") -> int:
        cur = conn.execute(
            "INSERT INTO audit (at, actor, action, entity, entity_id, before, after, summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), actor, action, entity, str(entity_id or ""), dumps(before) if before is not None else None,
             dumps(after) if after is not None else None, summary[:300]),
        )
        return cur.lastrowid

    def recent(self, limit: int = 50) -> list[dict]:
        rows = self.db.all("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
        for row in rows:
            row["before"], row["after"] = loads(row["before"]), loads(row["after"])
        return rows


class Spend:
    def __init__(self, db: Database, settings: Settings, tz):
        self.db, self.settings, self.tz = db, settings, tz

    def month_start(self) -> float:
        today = datetime.now(self.tz)
        return today.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()

    def this_month(self) -> float:
        return float(self.db.value("SELECT COALESCE(SUM(cost_usd), 0) FROM ai_runs WHERE at >= ?", (self.month_start(),)) or 0)

    def cap(self) -> float:
        return float(self.settings.get("monthly_budget_usd", 25.0))

    def remaining(self) -> float:
        return max(0.0, self.cap() - self.this_month())

    def record(self, task: str, model: str, cost: float, input_tokens: int, output_tokens: int, ok: bool, error: str, duration_ms: int) -> None:
        self.db.run(
            "INSERT INTO ai_runs (at, task, model, cost_usd, input_tokens, output_tokens, ok, error, duration_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (time.time(), task, model, cost, input_tokens, output_tokens, int(ok), error[:500], duration_ms),
        )
