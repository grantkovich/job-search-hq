"""Request guards for every response and every state-changing request.

- Security headers: a strict Content Security Policy (scripts only from this site, no
  inline script, no outside images, so AI output can't leak data through an image URL),
  HSTS, no framing, no referrer, microphone allowed for this site only.
- Cross-site request protection: every POST/PUT/PATCH/DELETE must come from this exact
  origin (Origin and Sec-Fetch-Site) and carry the session's CSRF token in X-CSRF-Token.
  Sibling copies on other subdomains count as cross-origin.
- Rate limits for sign-in, AI and uploads.
- Body size limits.
"""
from __future__ import annotations

import hmac
import json
import re
import threading
import time
from collections import defaultdict, deque

from .auth import Sessions, cookie_name
from .config import Config

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; manifest-src 'self'; worker-src 'self'; media-src 'self' blob:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'none'; object-src 'none'"
)
HEADERS = [
    (b"content-security-policy", CSP.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"microphone=(self), camera=(), geolocation=(), payment=(), usb=(), interest-cohort=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
]
# Requests that can't carry a CSRF header and prove themselves another way.
CSRF_EXEMPT_PREFIXES = ("/push/action/",)
# Only these routes take big bodies (a voice recording); everything else keeps the 1 MB limit.
UPLOAD_PATHS = re.compile(r"/practice/q/\d+/voice|/profile/import")
# State-changing requests that start (or may start) a paid AI run, or fetch from the internet.
AI_PATHS = re.compile(r"/(chat|interview|documents|practice|intake|contacts)(/.*)?"
                      r"|/roles/\d+/(score|fetch|documents/[^/]+)|/questions/\d+/answer|/settings/claude/test")
MAX_BODY = 1_000_000
MAX_UPLOAD = 25_000_000


class RateLimiter:
    """Sliding window per (bucket, client). One app process per copy, so memory is the right place."""

    LIMITS = {"auth": (20, 600), "ai": (30, 600), "upload": (20, 600), "default": (600, 60)}

    def __init__(self):
        self._hits: dict[tuple[str, str], deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, bucket: str, client: str) -> bool:
        limit, window = self.LIMITS.get(bucket, self.LIMITS["default"])
        now = time.monotonic()
        with self._lock:
            hits = self._hits[(bucket, client)]
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                return False
            hits.append(now)
            if len(self._hits) > 10_000:  # bound memory under a flood
                self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v and now - v[-1] < 600})
            return True


def bucket_for(path: str, method: str) -> str:
    if path.startswith("/auth/") or path == "/login":
        return "auth"
    if method in UNSAFE and UPLOAD_PATHS.fullmatch(path):
        return "upload"
    if method in UNSAFE and AI_PATHS.fullmatch(path):
        return "ai"
    return "default"


def body_limit(path: str) -> int:
    return MAX_UPLOAD if UPLOAD_PATHS.fullmatch(path) else MAX_BODY


def _header(scope, name: bytes) -> str:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return ""


def _cookie(scope, name: str) -> str | None:
    raw = _header(scope, b"cookie")
    for part in raw.split(";"):
        key, _, value = part.strip().partition("=")
        if key == name:
            return value
    return None


class GuardMiddleware:
    def __init__(self, app, config: Config, sessions: Sessions, limiter: RateLimiter | None = None):
        self.app, self.config, self.sessions = app, config, sessions
        self.limiter = limiter or RateLimiter()
        self.cookie = cookie_name(config)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        method, path = scope["method"], scope["path"]
        client = (scope.get("client") or ("?", 0))[0]

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {k.lower() for k, _ in headers}
                headers += [h for h in HEADERS if h[0] not in present]
                if self.config.secure:
                    headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
                if not path.startswith("/static/") and b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        if not self.limiter.allow(bucket_for(path, method), client):
            return await _reject(send_with_headers, 429, "Too many requests. Wait a few minutes and try again.")

        length = _header(scope, b"content-length")
        limit = body_limit(path)
        if length and (not length.isdigit() or int(length) > limit):
            return await _reject(send_with_headers, 413, "That's too large to upload.")

        if method in UNSAFE and not path.startswith(CSRF_EXEMPT_PREFIXES):
            problem = self._cross_site_problem(scope)
            if problem:
                return await _reject(send_with_headers, 403, problem)

        receive = _limited_receive(receive, limit)
        try:
            await self.app(scope, receive, send_with_headers)
        except BodyTooLarge:
            await _reject(send_with_headers, 413, "That's too large to upload.")

    def _cross_site_problem(self, scope) -> str | None:
        origin = _header(scope, b"origin")
        if origin and origin != self.config.origin:
            return "Blocked: this request came from another site."
        fetch_site = _header(scope, b"sec-fetch-site")
        if fetch_site and fetch_site not in ("same-origin", "none"):
            return "Blocked: this request came from another site."
        session = self.sessions.lookup(_cookie(scope, self.cookie))
        if session is None:
            # Signing in starts from the sign-in page, which is a GET, so nothing unsafe is allowed signed out.
            return "Your session ended. Reload the page and sign in again."
        sent = _header(scope, b"x-csrf-token")
        if not sent or not hmac.compare_digest(sent, session.csrf):
            return "Blocked: the security token was missing or out of date. Reload the page."
        return None


class BodyTooLarge(Exception):
    pass


def _limited_receive(receive, limit: int):
    seen = 0

    async def wrapped():
        nonlocal seen
        message = await receive()
        if message["type"] == "http.request":
            seen += len(message.get("body", b""))
            if seen > limit:
                raise BodyTooLarge()
        return message

    return wrapped


async def _reject(send, status: int, message: str):
    body = json.dumps({"error": message}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [
        (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
        (b"hx-reswap", b"none"), (b"hx-trigger", json.dumps({"jobhq:error": message}).encode()),
    ]})
    await send({"type": "http.response.body", "body": body})
