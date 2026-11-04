"""Background work: a durable job queue in SQLite plus a small scheduler.

Jobs survive restarts. A job that was running when the app stopped is retried once its
lease runs out. Failed jobs retry with backoff up to max_attempts. Scheduled work
(inbox check, morning digest, reminders) is enqueued with a dedupe key per time slot,
so a restart never runs the same slot twice.
"""
from __future__ import annotations

import asyncio
import logging
import time
import traceback
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .db import Database, dumps, loads

log = logging.getLogger("jobhq.worker")
LEASE_S = 900
Handler = Callable[[dict], Awaitable[object]]


class Queue:
    def __init__(self, db: Database):
        self.db = db

    def enqueue(self, kind: str, payload: dict | None = None, *, run_at: float | None = None, dedupe: str | None = None,
                max_attempts: int = 3) -> int | None:
        """Add a job. Returns its id, or None when a job with the same dedupe key already exists."""
        cur = self.db.run(
            "INSERT INTO jobs (kind, payload, run_at, status, max_attempts, dedupe, created) VALUES (?, ?, ?, 'queued', ?, ?, ?) "
            "ON CONFLICT (dedupe) DO NOTHING",
            (kind, dumps(payload or {}), run_at or time.time(), max_attempts, dedupe, time.time()),
        )
        return cur.lastrowid if cur.rowcount else None

    def claim(self) -> dict | None:
        now = time.time()
        with self.db.tx() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE (status = 'queued' AND run_at <= ?) OR (status = 'running' AND locked_until < ?) "
                "ORDER BY run_at LIMIT 1", (now, now),
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_until = ? WHERE id = ?",
                         (now + LEASE_S, row["id"]))
        job = dict(row)
        job["attempts"] += 1
        job["payload"] = loads(job["payload"], {})
        return job

    def done(self, job_id: int, result=None) -> None:
        self.db.run("UPDATE jobs SET status = 'done', result = ?, finished = ?, locked_until = NULL WHERE id = ?",
                    (dumps(result) if result is not None else None, time.time(), job_id))

    def failed(self, job: dict, error: str) -> None:
        if job["attempts"] >= job["max_attempts"]:
            self.db.run("UPDATE jobs SET status = 'failed', last_error = ?, finished = ?, locked_until = NULL WHERE id = ?",
                        (error[:1000], time.time(), job["id"]))
        else:
            delay = 30 * (4 ** (job["attempts"] - 1))
            self.db.run("UPDATE jobs SET status = 'queued', last_error = ?, run_at = ?, locked_until = NULL WHERE id = ?",
                        (error[:1000], time.time() + delay, job["id"]))

    def get(self, job_id: int) -> dict | None:
        return self.db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))

    def prune(self, older_than_s: float = 30 * 86400) -> None:
        self.db.run("DELETE FROM jobs WHERE status IN ('done', 'failed') AND finished < ?", (time.time() - older_than_s,))


@dataclass
class Schedule:
    """Run kind at each slot. slot(now) returns (slot key, start time) for the slot now is in, or None to skip."""

    kind: str
    slot: Callable[[datetime], tuple[str, datetime] | None]


def every_minutes(minutes: int, start_hour: int = 0, end_hour: int = 24) -> Callable[[datetime], tuple[str, datetime] | None]:
    def slot(now: datetime):
        if not (start_hour <= now.hour < end_hour):
            return None
        floored = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % minutes) if minutes < 60 else \
            now.replace(minute=0, second=0, microsecond=0)
        return floored.strftime("%Y%m%d%H%M"), floored
    return slot


def daily_at(hour: int, minute: int = 0) -> Callable[[datetime], tuple[str, datetime] | None]:
    def slot(now: datetime):
        start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if now < start or now - start > timedelta(hours=3):  # missed by more than 3 hours: skip that day
            return None
        return start.strftime("%Y%m%d"), start
    return slot


class Worker:
    def __init__(self, queue: Queue, tz, handlers: dict[str, Handler] | None = None, schedules: list[Schedule] | None = None):
        self.queue, self.tz = queue, tz
        self.handlers: dict[str, Handler] = dict(handlers or {})
        self.schedules = list(schedules or [])
        self._stop = asyncio.Event()

    def on(self, kind: str):
        def register(fn: Handler):
            self.handlers[kind] = fn
            return fn
        return register

    def tick_schedules(self, now: datetime | None = None) -> None:
        now = now or datetime.now(self.tz)
        for sched in self.schedules:
            slot = sched.slot(now)
            if slot:
                key, start = slot
                self.queue.enqueue(sched.kind, {"slot": key}, run_at=start.timestamp(), dedupe=f"{sched.kind}:{key}", max_attempts=2)

    async def run_one(self) -> bool:
        job = self.queue.claim()
        if not job:
            return False
        handler = self.handlers.get(job["kind"])
        if handler is None:
            self.queue.failed({**job, "attempts": job["max_attempts"]}, f"no handler for {job['kind']}")
            return True
        try:
            result = await handler(job["payload"])
            self.queue.done(job["id"], result)
        except Exception as exc:
            log.warning("job %s (%s) failed: %s", job["id"], job["kind"], type(exc).__name__)
            log.debug("%s", traceback.format_exc())
            self.queue.failed(job, f"{type(exc).__name__}: {exc}")
        return True

    async def loop(self, concurrency: int = 2) -> None:
        last_schedule = 0.0

        async def lane():
            while not self._stop.is_set():
                try:
                    worked = await self.run_one()
                except Exception:
                    log.exception("worker lane error")
                    worked = False
                if not worked:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=1.0)
                    except TimeoutError:
                        pass

        async def scheduler():
            nonlocal last_schedule
            while not self._stop.is_set():
                if time.time() - last_schedule >= 20:
                    try:
                        self.tick_schedules()
                    except Exception:
                        log.exception("scheduler error")
                    last_schedule = time.time()
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=5.0)
                except TimeoutError:
                    pass

        await asyncio.gather(scheduler(), *(lane() for _ in range(concurrency)))

    def stop(self) -> None:
        self._stop.set()
