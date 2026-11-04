"""Browser end-to-end tests: the real app on a live port, its background worker running,
driven by Chromium through Playwright. Claude and Google are fakes; everything else is real.

Run with:  pytest tests/e2e   (needs `pip install playwright` and `playwright install chromium`)
"""
from __future__ import annotations

import base64
import http.server
import json
import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

pw = pytest.importorskip("playwright.sync_api")

from jobhq.config import load  # noqa: E402
from jobhq.context import build  # noqa: E402

from ..conftest import FakeClaudeCode, FakeGoogle, FakeRunner  # noqa: E402


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveApp:
    def __init__(self, tmp: Path):
        import uvicorn

        from jobhq.app import create_app

        self.port = free_port()
        self.base = f"http://localhost:{self.port}"
        self.runner, self.google, self.pushes = FakeRunner(), FakeGoogle(), []
        env = {"JOBHQ_OWNER_EMAIL": "owner@example.com", "JOBHQ_BASE_URL": self.base,
               "JOBHQ_SECRET": base64.urlsafe_b64encode(b"e" * 32).decode(), "JOBHQ_GOOGLE_CLIENT_ID": "client-123.apps.googleusercontent.com",
               "JOBHQ_GOOGLE_CLIENT_SECRET": "shh", "JOBHQ_DATA_DIR": str(tmp / "data")}
        self.ctx = build(load(env, scrub=False), runner=self.runner, http=httpx.Client(transport=httpx.MockTransport(self.google.handler)),
                         push_sender=lambda sub, payload: self.pushes.append(json.loads(payload)),
                         claude_code=FakeClaudeCode(tmp))
        self.app = create_app(self.ctx, run_worker=True)
        self.consent = FakeConsentScreen(self)
        import jobhq.auth
        self._real_auth_url, jobhq.auth.AUTH_URL = jobhq.auth.AUTH_URL, f"{self.consent.base}/o/oauth2/v2/auth"
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(100):
            try:
                if httpx.get(f"{self.base}/healthz", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.05)

    def stop(self):
        import jobhq.auth
        jobhq.auth.AUTH_URL = self._real_auth_url
        self.consent.stop()
        self.server.should_exit = True
        self.thread.join(timeout=10)
        self.ctx.db.close()


class FakeConsentScreen:
    """Stands in for Google's sign-in page: notes the nonce and sends the browser back with a code, as Google would."""

    def __init__(self, live):
        port = free_port()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                params = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                live.google.nonce_value = params.get("nonce", "")
                live.google.last_auth = params
                self.send_response(302)
                self.send_header("Location", f"{params['redirect_uri']}?" + urlencode({"state": params["state"], "code": "fake-code"}))
                self.end_headers()

            def log_message(self, *_):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"

    def stop(self):
        self.httpd.shutdown()


@pytest.fixture
def live(tmp_path):
    app = LiveApp(tmp_path)
    yield app
    app.stop()


class Problems(list):
    """Console errors, page errors and CSP violations seen during a test. Every test asserts it stays empty."""


@pytest.fixture
def browser_page(live):
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(base_url=live.base, viewport={"width": 1280, "height": 900})
        page = context.new_page()
        problems = Problems()
        page.on("console", lambda m: m.type == "error" and problems.append(f"console: {m.text}"))
        page.on("pageerror", lambda e: problems.append(f"pageerror: {e}"))

        page.problems = problems
        page.live = live
        yield page
        context.close()
        browser.close()


@pytest.fixture
def signed_in(browser_page):
    page = browser_page
    page.goto("/login")
    page.get_by_role("link", name="Sign in with Google").click()
    page.wait_for_url(f"{page.live.base}/")
    return page


class Attacker:
    """A web page on a different origin, for cross-site attacks."""

    def __init__(self):
        self.port = free_port()
        self.pages: dict[str, str] = {}
        pages = self.pages

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                body = pages.get(self.path, "not found").encode()
                self.send_response(200 if self.path in pages else 404)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.httpd.shutdown()


@pytest.fixture
def attacker():
    a = Attacker()
    yield a
    a.stop()


def wait_for(fn, timeout=15.0, step=0.1):
    end = time.time() + timeout
    while time.time() < end:
        value = fn()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("timed out waiting")
