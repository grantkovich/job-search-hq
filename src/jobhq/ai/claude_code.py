"""The person's own Claude Code sign-in, kept entirely inside Claude Code.

Anthropic's terms (code.claude.com/docs/en/legal-and-compliance) don't let an app collect, store
or pass along Claude.ai credentials, but they do allow a person to sign in to the unmodified
Claude Code with their own plan. So this app never sees a Claude.ai token:

- Signing in happens in a terminal, through Claude Code's own flow: `jobsctl claude-login NAME`
  on the server runs `claude auth login --claudeai` inside that person's copy.
- Claude Code keeps its own credentials in its config folder under /data/work/claude-home.
- The app only asks Claude Code `auth status` (signed in or not, and how) and can run `auth logout`.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess  # noqa: S404 - fixed argument lists, never a shell
import time
from pathlib import Path

log = logging.getLogger("jobhq.claude_code")
STATUS_TTL_S = 60


def bundled_cli() -> str | None:
    """The Claude Code program shipped with the Agent SDK, unmodified."""
    try:
        import claude_agent_sdk
    except ImportError:
        return None
    folder = Path(claude_agent_sdk.__file__).parent / "_bundled"
    for name in ("claude", "claude.exe"):
        if (folder / name).is_file():
            return str(folder / name)
    return None


class ClaudeCode:
    def __init__(self, work_dir: Path, cli: str | None = None):
        self.home = Path(work_dir) / "claude-home"
        self.cli = cli or os.environ.get("JOBHQ_CLAUDE_CLI") or bundled_cli()
        self._status: tuple[float, dict] | None = None

    def env(self) -> dict[str, str]:
        """Where Claude Code keeps its own sign-in for this copy. The AI runner uses the same place."""
        self.home.mkdir(parents=True, exist_ok=True)
        return {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude")}

    def _run(self, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        if not self.cli:
            raise FileNotFoundError("Claude Code isn't installed")
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", **self.env(), "DISABLE_AUTOUPDATER": "1", "DISABLE_TELEMETRY": "1"}
        return subprocess.run([self.cli, *args], capture_output=True, text=True, timeout=timeout, env=env, check=False)  # noqa: S603

    def status(self, fresh: bool = False) -> dict:
        """{"signed_in": bool, "method": str, "error": str}. Never includes a token."""
        if self._status and not fresh and time.time() - self._status[0] < STATUS_TTL_S:
            return self._status[1]
        try:
            out = self._run("auth", "status", "--json")
            data = json.loads(out.stdout or "{}")
            result = {"signed_in": bool(data.get("loggedIn")), "method": str(data.get("authMethod") or "none")[:40], "error": ""}
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            log.warning("claude auth status failed: %s", type(exc).__name__)
            result = {"signed_in": False, "method": "none", "error": "Claude Code couldn't be checked."}
        self._status = (time.time(), result)
        return result

    def logout(self) -> bool:
        try:
            ok = self._run("auth", "logout").returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
        self._status = None
        return ok

    def login_command(self) -> list[str]:
        """What `jobhq claude-login` runs, interactively, in the person's terminal."""
        if not self.cli:
            raise FileNotFoundError("Claude Code isn't installed")
        return [self.cli, "auth", "login", "--claudeai"]
