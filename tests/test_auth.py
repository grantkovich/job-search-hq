from html import unescape
from urllib.parse import parse_qs, urlsplit

from jobhq.auth import GMAIL_SCOPE, cookie_name


def start_login(anon, google, path="/auth/google"):
    response = anon.get(path)
    assert response.status_code == 303
    url = response.headers["location"]
    params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    google.nonce_value = params["nonce"]
    return params


def test_full_sign_in(anon, google, ctx):
    params = start_login(anon, google)
    assert params["code_challenge_method"] == "S256"
    assert params["redirect_uri"] == "https://me.example.test/auth/callback"
    assert params["login_hint"] == "owner@example.com"
    response = anon.get("/auth/callback", params={"state": params["state"], "code": "c1"})
    assert response.status_code == 303 and response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert cookie_name(ctx.config) == "__Host-jobhq"
    assert "__Host-jobhq=" in cookie and "HttpOnly" in cookie and "Secure" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert anon.get("/").status_code == 200
    assert ctx.settings.get("owner_sub") == "111"
    sent = google.requests[-1].content.decode()
    assert "code_verifier=" in sent and "client_secret=shh" in sent


def test_someone_else_cannot_sign_in(anon, google, ctx):
    google.claims["email"] = "intruder@example.com"
    params = start_login(anon, google)
    response = anon.get("/auth/callback", params={"state": params["state"], "code": "c"})
    assert response.status_code == 200 and "belongs to someone else" in unescape(response.text)
    assert "set-cookie" not in response.headers or "__Host-jobhq=" not in response.headers.get("set-cookie", "")
    assert anon.get("/").status_code == 303


def test_unverified_email_rejected(anon, google):
    google.claims["email_verified"] = False
    params = start_login(anon, google)
    assert "belongs to someone else" in unescape(anon.get("/auth/callback", params={"state": params["state"], "code": "c"}).text)


def test_account_id_is_pinned_after_first_sign_in(anon, google, ctx):
    ctx.settings.set("owner_sub", "999")
    params = start_login(anon, google)
    assert "isn't the one that set up" in unescape(anon.get("/auth/callback", params={"state": params["state"], "code": "c"}).text)


def test_state_is_single_use(anon, google):
    params = start_login(anon, google)
    assert anon.get("/auth/callback", params={"state": params["state"], "code": "c"}).status_code == 303
    again = anon.get("/auth/callback", params={"state": params["state"], "code": "c"})
    assert "expired or was already used" in unescape(again.text)


def test_forged_state_rejected(anon, google):
    start_login(anon, google)
    assert "expired or was already used" in unescape(anon.get("/auth/callback", params={"state": "made-up", "code": "c"}).text)


def test_wrong_audience_and_nonce_rejected(anon, google):
    params = start_login(anon, google)
    google.claims["aud"] = "other-app"
    assert "different app" in unescape(anon.get("/auth/callback", params={"state": params["state"], "code": "c"}).text)
    google.claims["aud"] = "client-123.apps.googleusercontent.com"
    params = start_login(anon, google)
    google.nonce_value = "wrong"
    assert "didn't match" in unescape(anon.get("/auth/callback", params={"state": params["state"], "code": "c"}).text)


def test_google_error_and_cancel(anon, google):
    params = start_login(anon, google)
    assert "cancelled" in unescape(anon.get("/auth/callback", params={"state": params["state"], "error": "access_denied"}).text)
    params = start_login(anon, google)
    google.token_status = 400
    assert "didn't accept" in unescape(anon.get("/auth/callback", params={"state": params["state"], "code": "c"}).text)


def test_new_session_on_each_sign_in(anon, google, ctx):
    params = start_login(anon, google)
    anon.get("/auth/callback", params={"state": params["state"], "code": "c"})
    first = anon.cookies.get("__Host-jobhq")
    params = start_login(anon, google)
    anon.get("/auth/callback", params={"state": params["state"], "code": "c"})
    second = anon.cookies.get("__Host-jobhq")
    assert first != second
    assert ctx.sessions.lookup(first) is None


def test_gmail_connect_needs_scope_and_refresh_token(client, google, ctx):
    response = client.get("/auth/gmail")
    params = {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}
    assert GMAIL_SCOPE in params["scope"] and params["access_type"] == "offline"
    google.nonce_value = params["nonce"]
    google.token_extra = {"scope": "openid email"}
    r = client.get("/auth/callback", params={"state": params["state"], "code": "c"})
    assert r.headers.get("hx-redirect", r.headers.get("location")) == "/setup?gmail=missing"
    assert not ctx.settings.has("gmail_refresh_token")

    response = client.get("/auth/gmail")
    params = {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}
    google.nonce_value = params["nonce"]
    google.token_extra = {"scope": f"openid email {GMAIL_SCOPE}", "refresh_token": "rt-1"}
    client.get("/auth/callback", params={"state": params["state"], "code": "c"})
    assert ctx.settings.get("gmail_refresh_token") == "rt-1"
    raw = ctx.db.value("SELECT value FROM settings WHERE key = 'gmail_refresh_token'")
    assert "rt-1" not in raw


def test_gmail_connect_requires_owner_account(client, google, ctx):
    response = client.get("/auth/gmail")
    params = {k: v[0] for k, v in parse_qs(urlsplit(response.headers["location"]).query).items()}
    google.nonce_value = params["nonce"]
    google.claims["email"] = "other@example.com"
    google.token_extra = {"scope": GMAIL_SCOPE, "refresh_token": "rt-x"}
    assert "belongs to someone else" in unescape(client.get("/auth/callback", params={"state": params["state"], "code": "c"}).text)
    assert not ctx.settings.has("gmail_refresh_token")


def test_logout_and_sign_out_everywhere(client, ctx):
    other, _ = ctx.sessions.create("other device")
    assert client.post("/logout").headers["hx-redirect"] == "/login"
    assert ctx.sessions.lookup(other) is not None
    assert client.get("/").status_code in (303, 401)
    token, session = ctx.sessions.create("again")
    client.cookies.set("__Host-jobhq", token, domain="me.example.test")
    client.headers["X-CSRF-Token"] = session.csrf
    client.post("/settings/sessions/end-all")
    assert ctx.sessions.lookup(other) is None and ctx.sessions.lookup(token) is None


def test_sessions_expire_when_idle(ctx, monkeypatch):
    import jobhq.auth as auth
    token, _ = ctx.sessions.create()
    ctx.db.run("UPDATE sessions SET last_seen = last_seen - ?", (auth.SESSION_IDLE_S + 10,))
    assert ctx.sessions.lookup(token) is None


def test_session_ids_are_stored_hashed(ctx):
    token, _ = ctx.sessions.create()
    assert ctx.db.value("SELECT COUNT(*) FROM sessions WHERE id_hash = ?", (token,)) == 0
