"""Everything one copy of the app needs, built once at startup and shared."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .ai.claude_code import ClaudeCode
from .ai.runner import DEFAULT_MODEL, ClaudeRunner, Runner
from .auth import GoogleAuth, Sessions
from .config import Config
from .crypto import Box
from .db import Database
from .notify import Push, Reminders
from .profile import Profile
from .proposals import Proposals
from .roles import Roles
from .store import Audit, Settings, Spend
from .worker import Queue


@dataclass
class Ctx:
    config: Config
    db: Database
    box: Box
    settings: Settings
    audit: Audit
    spend: Spend
    sessions: Sessions
    google: GoogleAuth
    queue: Queue
    profile: Profile
    roles: Roles
    proposals: Proposals
    push: Push
    reminders: Reminders
    runner: Runner
    http: httpx.Client
    claude_code: ClaudeCode
    extras: dict = field(default_factory=dict)

    def model(self) -> str:
        return self.settings.get("model", DEFAULT_MODEL)

    def claude_mode(self) -> str:
        return "claude_login" if self.settings.get("claude_mode", "api_key") == "claude_login" else "api_key"

    def claude_credentials(self) -> dict[str, str] | None:
        """What the AI runner adds to Claude Code's environment, or None when Claude isn't set up.
        With an API key: the key. With the person's own Claude Code sign-in: nothing, because Claude Code
        reads its own credentials from its config folder and the app never touches them."""
        if self.claude_mode() == "claude_login":
            return {} if self.claude_code.status()["signed_in"] else None
        key = self.settings.get("claude_api_key")
        return {"ANTHROPIC_API_KEY": key} if key else None

    def claude_ready(self) -> bool:
        return self.claude_credentials() is not None


def build(config: Config, runner: Runner | None = None, http: httpx.Client | None = None, push_sender=None,
          claude_code: ClaudeCode | None = None) -> Ctx:
    for path in (config.data_dir, config.files_dir, config.work_dir):
        Path(path).mkdir(parents=True, exist_ok=True)
        try:
            Path(path).chmod(0o700)
        except OSError:
            pass
    db = Database(config.db_path)
    box = Box(config.secret, config.old_secret)
    settings = Settings(db, box)
    audit = Audit(db)
    spend = Spend(db, settings, config.tz)
    http = http or httpx.Client(timeout=20, follow_redirects=False, headers={"User-Agent": "JobSearchHQ/0.1 (+self-hosted)"})
    push = Push(db, settings, box, config.owner_email, config.base_url, sender=push_sender)
    profile = Profile(db)
    ctx = Ctx(
        config=config, db=db, box=box, settings=settings, audit=audit, spend=spend, sessions=Sessions(db),
        google=GoogleAuth(config, db, settings, http), queue=Queue(db), profile=profile, roles=Roles(db, audit),
        proposals=Proposals(db, audit, profile), push=push, reminders=Reminders(db, push, box, config.tz),
        runner=runner, http=http, claude_code=claude_code or ClaudeCode(config.work_dir),  # type: ignore[arg-type]
    )
    if ctx.runner is None:
        ctx.runner = ClaudeRunner(ctx.claude_credentials, spend, config.work_dir, ctx.model, home=ctx.claude_code.env,
                                  max_parallel=config.ai_parallel)
    return ctx
