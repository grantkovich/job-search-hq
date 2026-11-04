"""Settings for one copy of the app, read once from the environment at startup.

Secrets are removed from os.environ after they are read, so no child process
(the Claude CLI, the PDF builder) can inherit them.
"""
from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SECRET_VARS = ("JOBHQ_SECRET", "JOBHQ_SECRET_OLD", "JOBHQ_GOOGLE_CLIENT_SECRET")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ConfigError(ValueError):
    """The environment is missing something or has a bad value. The message says which and how to fix it."""


@dataclass(frozen=True)
class Config:
    owner_email: str
    base_url: str
    secret: bytes
    google_client_id: str
    google_client_secret: str = field(repr=False)
    data_dir: Path
    timezone: str = "America/New_York"
    old_secret: bytes | None = field(default=None, repr=False)
    voice: bool = False
    instance_name: str = "me"
    ai_parallel: int = 2
    workers: int = 2

    @property
    def origin(self) -> str:
        parts = urlsplit(self.base_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).hostname or ""

    @property
    def secure(self) -> bool:
        return self.base_url.startswith("https://")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def files_dir(self) -> Path:
        return self.data_dir / "files"

    @property
    def work_dir(self) -> Path:
        """Scratch space for the AI and the PDF builder. Never holds anything worth keeping."""
        return self.data_dir / "work"


def _decode_secret(name: str, value: str) -> bytes:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise ConfigError(f"{name} must be base64. Make one with: jobsctl secret") from exc
    if len(raw) < 32:
        raise ConfigError(f"{name} must be at least 32 random bytes. Make one with: jobsctl secret")
    return raw


def _small_int(value: str | None, default: int) -> int:
    """1 to 8; anything else falls back to the default. The small-server profile sets 1."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return number if 1 <= number <= 8 else default


def load(env: dict[str, str] | None = None, scrub: bool = True) -> Config:
    """Read and check the settings. With scrub, secrets are deleted from os.environ afterwards."""
    source = os.environ if env is None else env

    def need(name: str) -> str:
        value = (source.get(name) or "").strip()
        if not value:
            raise ConfigError(f"{name} is not set. See docs/install.md, step 'Add a person'.")
        return value

    owner = need("JOBHQ_OWNER_EMAIL").lower()
    if not EMAIL_RE.match(owner):
        raise ConfigError("JOBHQ_OWNER_EMAIL doesn't look like an email address.")

    base_url = need("JOBHQ_BASE_URL").rstrip("/")
    parts = urlsplit(base_url)
    local = parts.hostname in ("localhost", "127.0.0.1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ConfigError("JOBHQ_BASE_URL must start with https:// (plain http is allowed only for localhost).")
    if parts.path or parts.query or parts.fragment:
        raise ConfigError("JOBHQ_BASE_URL must be just the address, like https://me.example.com")

    tz = (source.get("JOBHQ_TIMEZONE") or "America/New_York").strip()
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"JOBHQ_TIMEZONE '{tz}' isn't a known time zone, for example America/Chicago.") from exc

    old = (source.get("JOBHQ_SECRET_OLD") or "").strip()
    config = Config(
        owner_email=owner,
        base_url=base_url,
        secret=_decode_secret("JOBHQ_SECRET", need("JOBHQ_SECRET")),
        old_secret=_decode_secret("JOBHQ_SECRET_OLD", old) if old else None,
        google_client_id=need("JOBHQ_GOOGLE_CLIENT_ID"),
        google_client_secret=need("JOBHQ_GOOGLE_CLIENT_SECRET"),
        data_dir=Path(source.get("JOBHQ_DATA_DIR") or "/data"),
        timezone=tz,
        voice=(source.get("JOBHQ_VOICE") or "").lower() in ("1", "on", "true", "yes"),
        instance_name=(source.get("JOBHQ_INSTANCE") or "me").strip(),
        ai_parallel=_small_int(source.get("JOBHQ_AI_PARALLEL"), 2),
        workers=_small_int(source.get("JOBHQ_WORKERS"), 2),
    )
    if scrub and env is None:
        for name in SECRET_VARS:
            os.environ.pop(name, None)
    return config
