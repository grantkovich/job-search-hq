"""SQLite storage. One file per copy of the app, in WAL mode.

Every value goes in through bound parameters. Schema changes are numbered
migrations tracked by PRAGMA user_version, applied at startup in one transaction each.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .migrations import MIGRATIONS


def _connect(path: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=10, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA trusted_schema = OFF")
    return conn


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._local = threading.local()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._raw() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
        self.migrate()
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    @contextmanager
    def _raw(self) -> Iterator[sqlite3.Connection]:
        conn = _connect(self.path)
        try:
            yield conn
        finally:
            conn.close()

    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._local.conn = _connect(self.path)
        return conn

    def migrate(self) -> int:
        with self._raw() as conn:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            if current > len(MIGRATIONS):
                raise RuntimeError(f"The database is newer (v{current}) than this app (v{len(MIGRATIONS)}). Update the app.")
            for number, script in enumerate(MIGRATIONS[current:], start=current + 1):
                conn.execute("BEGIN IMMEDIATE")
                try:
                    for statement in _split(script):
                        conn.execute(statement)
                    conn.execute(f"PRAGMA user_version = {int(number)}")
                    conn.execute("COMMIT")
                except Exception:
                    conn.execute("ROLLBACK")
                    raise
            return len(MIGRATIONS)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """A write transaction. Takes the write lock at the start, so read-then-write can't race."""
        conn = self._conn()
        if conn.in_transaction:  # nested: join the outer transaction
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

    def all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        return [dict(row) for row in self._conn().execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        row = self._conn().execute(sql, params).fetchone()
        return dict(row) if row else None

    def value(self, sql: str, params: tuple | dict = ()):
        row = self._conn().execute(sql, params).fetchone()
        return row[0] if row else None

    def run(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self.tx() as conn:
            return conn.execute(sql, params)

    def backup_to(self, target: Path) -> None:
        """A consistent copy while the app keeps running."""
        dest = sqlite3.connect(str(target))
        try:
            self._conn().backup(dest)
        finally:
            dest.close()

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None


def _split(script: str) -> list[str]:
    """Split a migration into statements. Migrations never contain ';' inside strings or triggers."""
    return [part.strip() for part in script.split(";") if part.strip()]


def dumps(value) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def loads(text: str | None, default=None):
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def now() -> float:
    return time.time()
