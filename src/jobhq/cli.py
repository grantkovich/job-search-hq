"""`jobhq` commands, run inside the app container (jobsctl on the host calls these).

  jobhq serve            run the web app and background worker
  jobhq check            check the settings without starting (exit 1 on a problem)
  jobhq backup DIR       write a consistent copy of the database and files to DIR
  jobhq rotate-secret    re-encrypt stored secrets after JOBHQ_SECRET changed (old one in JOBHQ_SECRET_OLD)
  jobhq claude-login     sign in to Claude Code with your own Claude plan (interactive; Claude Code's own flow)
  jobhq claude-logout    sign Claude Code out
  jobhq claude-status    show whether Claude Code is signed in
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess  # noqa: S404
import sys
import time
from pathlib import Path


def _logging() -> None:
    logging.basicConfig(level=os.environ.get("JOBHQ_LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    from . import config

    argv = sys.argv[1:] if argv is None else argv
    command = argv[0] if argv else "serve"
    _logging()
    try:
        cfg = config.load()
    except config.ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        return 1

    if command == "check":
        from .context import build
        ctx = build(cfg)
        print(f"OK: {cfg.instance_name} at {cfg.base_url}, owner {cfg.owner_email}, schema v{ctx.db.value('PRAGMA user_version')}, "
              f"Claude {'connected (' + ctx.claude_mode() + ')' if ctx.claude_ready() else 'not connected'}, voice {'on' if cfg.voice else 'off'}")
        return 0
    if command == "serve":
        import uvicorn

        from .app import create_app
        from .context import build
        app = create_app(build(cfg))  # reuse the config read above: reading it removed the secrets from the environment
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), proxy_headers=True,  # noqa: S104  # nosec B104 - container port, only Caddy reaches it
                    forwarded_allow_ips="*", server_header=False, date_header=False, access_log=False, timeout_graceful_shutdown=20)
        return 0
    if command == "backup":
        if len(argv) < 2:
            print("usage: jobhq backup DIR", file=sys.stderr)
            return 2
        from .context import build
        ctx = build(cfg)
        dest = Path(argv[1]) / time.strftime("%Y%m%d-%H%M%S")
        dest.mkdir(parents=True, exist_ok=False)
        ctx.db.backup_to(dest / "app.db")
        if cfg.files_dir.exists():
            shutil.copytree(cfg.files_dir, dest / "files")
        print(dest)
        return 0
    if command in ("claude-login", "claude-logout", "claude-status"):
        from .ai.claude_code import ClaudeCode
        from .context import build
        ctx = build(cfg)
        cc = ClaudeCode(cfg.work_dir)
        if command == "claude-status":
            st = cc.status(fresh=True)
            print(f"Claude Code: {'signed in' if st['signed_in'] else 'not signed in'} ({st['method']}){' ' + st['error'] if st['error'] else ''}")
            return 0 if st["signed_in"] else 1
        if command == "claude-logout":
            cc.logout()
            ctx.settings.set("claude_mode", "api_key")
            print("Claude Code is signed out. This copy will use an API key if one is saved in Settings.")
            return 0
        print("Claude Code will show a link. Open it in a browser, sign in to your own Claude account, and paste the code back here.\n"
              "Job Search HQ never sees your Claude password or token.\n")
        login_env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "TERM": os.environ.get("TERM", "xterm"), **cc.env()}
        code = subprocess.call(cc.login_command(), env=login_env)  # noqa: S603 - Claude Code's own sign-in, unmodified
        if cc.status(fresh=True)["signed_in"]:
            ctx.settings.set("claude_mode", "claude_login")
            print("\nSigned in. This copy now uses your Claude plan.")
            return 0
        print("\nNot signed in. Run this again to retry.", file=sys.stderr)
        return code or 1
    if command == "rotate-secret":
        if not cfg.old_secret:
            print("Set JOBHQ_SECRET_OLD to the previous secret first.", file=sys.stderr)
            return 1
        from .context import build
        ctx = build(cfg)
        count = ctx.settings.reencrypt()
        ctx.sessions.end_all()
        print(f"Re-encrypted {count} secrets and signed out every device. Remove JOBHQ_SECRET_OLD and restart.")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
