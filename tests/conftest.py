"""Test fixtures: a real copy of the app on a temp folder, a scripted fake Claude, fake Google."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from jobhq.ai.runner import Result, Runner
from jobhq.auth import cookie_name
from jobhq.config import load
from jobhq.context import build

BASE = "https://me.example.test"
OWNER = "owner@example.com"
CLIENT_ID = "client-123.apps.googleusercontent.com"


def make_env(tmp: Path, **extra) -> dict:
    return {
        "JOBHQ_OWNER_EMAIL": OWNER, "JOBHQ_BASE_URL": BASE, "JOBHQ_SECRET": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "JOBHQ_GOOGLE_CLIENT_ID": CLIENT_ID, "JOBHQ_GOOGLE_CLIENT_SECRET": "shh", "JOBHQ_DATA_DIR": str(tmp / "data"),
        "JOBHQ_TIMEZONE": "America/New_York", **extra,
    }


class FakeClaudeCode:
    """Stands in for Claude Code's own sign-in, so tests never run the real program."""

    def __init__(self, home: Path):
        self.signed_in = False
        self.home = home / "claude-home"
        self.logged_out = 0

    def env(self):
        self.home.mkdir(parents=True, exist_ok=True)
        return {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.home / ".claude")}

    def status(self, fresh=False):
        return {"signed_in": self.signed_in, "method": "claude.ai" if self.signed_in else "none", "error": ""}

    def logout(self):
        self.signed_in = False
        self.logged_out += 1
        return True

    def login_command(self):
        return ["true"]


class FakeRunner(Runner):
    """Plays the part of Claude. `script` maps a task name to a function(system, prompt, tools, schema) -> Result.
    A script can call tools through the `tools` dict to act the way Claude would."""

    def __init__(self):
        self.script: dict = {}
        self.calls: list[dict] = []

    async def run(self, task, system, prompt, *, tools=None, schema=None, max_turns=6, budget_usd=0.5, model=None):
        self.calls.append({"task": task, "system": system, "prompt": prompt, "tools": [t.name for t in tools or []], "schema": schema})
        fn = self.script.get(task)
        if fn is None:
            return Result(ok=True, text="(fake reply)", data={} if schema else None)
        out = fn(system, prompt, {t.name: t for t in tools or []}, schema)
        if hasattr(out, "__await__"):
            out = await out
        return out


def id_token(claims: dict) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")
    return f"{part({'alg': 'RS256'})}.{part(claims)}.sig"


class FakeGoogle:
    """Answers Google's token, revoke and Gmail endpoints."""

    def __init__(self):
        self.claims = {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "email": OWNER, "email_verified": True, "sub": "111"}
        self.token_extra: dict = {}
        self.token_status = 200
        self.messages: dict[str, dict] = {}
        self.requests: list[httpx.Request] = []
        self.board_payloads: dict[str, object] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            body = dict(httpx.QueryParams(request.content.decode()))
            if body.get("grant_type") == "refresh_token":
                return httpx.Response(200, json={"access_token": "at-1", "expires_in": 3600})
            nonce = self.nonce_for(body)
            claims = {**self.claims, "exp": time.time() + 300, "nonce": nonce}
            return httpx.Response(self.token_status, json={"access_token": "at", "id_token": id_token(claims), **self.token_extra})
        if url.startswith("https://oauth2.googleapis.com/revoke"):
            return httpx.Response(200)
        if url.startswith("https://gmail.googleapis.com/gmail/v1/users/me/messages/"):
            mid = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json=self.messages[mid])
        if url.startswith("https://gmail.googleapis.com/gmail/v1/users/me/messages"):
            return httpx.Response(200, json={"messages": [{"id": k} for k in self.messages]})
        for prefix, payload in self.board_payloads.items():
            if url.startswith(prefix):
                return httpx.Response(200, json=payload)
        return httpx.Response(404)

    nonce_value = ""

    def nonce_for(self, _body):
        return self.nonce_value


def gmail_message(mid: str, sender: str, subject: str, body: str, when: float | None = None) -> dict:
    return {"id": mid, "internalDate": str(int((when or time.time()) * 1000)), "payload": {
        "mimeType": "text/plain", "headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}],
        "body": {"data": base64.urlsafe_b64encode(body.encode()).decode()}}}


@pytest.fixture
def google():
    return FakeGoogle()


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def pushes():
    return []


@pytest.fixture
def ctx(tmp_path, runner, google, pushes):
    cfg = load(make_env(tmp_path), scrub=False)
    http = httpx.Client(transport=httpx.MockTransport(google.handler))
    c = build(cfg, runner=runner, http=http, push_sender=lambda sub, payload: pushes.append((sub, json.loads(payload))),
              claude_code=FakeClaudeCode(tmp_path))
    yield c
    c.db.close()


@pytest.fixture
def app(ctx):
    from jobhq.app import create_app
    return create_app(ctx, run_worker=False)


@pytest.fixture
def anon(app):
    with TestClient(app, base_url=BASE, follow_redirects=False) as client:
        yield client


@pytest.fixture
def client(app, ctx):
    """Signed in, with the CSRF token and same-origin headers a real browser would send through htmx."""
    token, session = ctx.sessions.create("pytest", "127.0.0.1")
    with TestClient(app, base_url=BASE, follow_redirects=False) as c:
        c.cookies.set(cookie_name(ctx.config), token, domain="me.example.test")
        c.headers.update({"X-CSRF-Token": session.csrf, "Origin": BASE, "Sec-Fetch-Site": "same-origin", "HX-Request": "true"})
        c.session = session
        yield c


async def drain(app, limit: int = 50) -> int:
    """Run queued background jobs until none are left."""
    worker = app.state.worker
    n = 0
    while n < limit and await worker.run_one():
        n += 1
    return n
