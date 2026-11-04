"""Google sign-in (OpenID Connect, authorization code flow with PKCE) and server-side sessions.

Only the owner of this copy can sign in: a verified Google account whose email matches
JOBHQ_OWNER_EMAIL. After the first sign-in the Google account id ("sub") is pinned too,
so a recycled or renamed address can't take over.

The ID token comes straight from Google's token endpoint over TLS, which OpenID Connect
Core 3.1.3.7 allows in place of checking its signature. Its issuer, audience, expiry and
nonce are still checked.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from .config import Config
from .crypto import new_token, token_hash
from .db import Database
from .store import Settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - an endpoint, not a password
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
ISSUERS = {"https://accounts.google.com", "accounts.google.com"}
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
STATE_TTL_S = 600
SESSION_IDLE_S = 7 * 86400
SESSION_MAX_S = 30 * 86400
TOUCH_EVERY_S = 300
COOKIE = "__Host-jobhq"
COOKIE_DEV = "jobhq"  # plain http on localhost can't use the __Host- prefix
# Ties a sign-in's state to the browser that started it, so a callback link made elsewhere can't be used here.
FLOW_COOKIE = "__Host-jobhq-oauth"
FLOW_COOKIE_DEV = "jobhq-oauth"


class AuthError(Exception):
    """Sign-in failed. The message is safe to show."""


def cookie_name(config: Config) -> str:
    return COOKIE if config.secure else COOKIE_DEV


def flow_cookie_name(config: Config) -> str:
    return FLOW_COOKIE if config.secure else FLOW_COOKIE_DEV


# ------------------------------------------------------------------ sessions

@dataclass
class Session:
    id_hash: str
    csrf: str
    created: float


class Sessions:
    def __init__(self, db: Database):
        self.db = db

    def create(self, user_agent: str = "", ip: str = "") -> tuple[str, Session]:
        token, csrf, now = new_token(), new_token(), time.time()
        self.db.run(
            "INSERT INTO sessions (id_hash, csrf, created, last_seen, expires, user_agent, ip) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (token_hash(token), csrf, now, now, now + SESSION_MAX_S, user_agent[:200], ip[:64]),
        )
        return token, Session(token_hash(token), csrf, now)

    def lookup(self, token: str | None) -> Session | None:
        if not token or len(token) > 100:
            return None
        row = self.db.one("SELECT * FROM sessions WHERE id_hash = ?", (token_hash(token),))
        if not row:
            return None
        now = time.time()
        if now > row["expires"] or now - row["last_seen"] > SESSION_IDLE_S:
            self.db.run("DELETE FROM sessions WHERE id_hash = ?", (row["id_hash"],))
            return None
        if now - row["last_seen"] > TOUCH_EVERY_S:
            self.db.run("UPDATE sessions SET last_seen = ? WHERE id_hash = ?", (now, row["id_hash"]))
        return Session(row["id_hash"], row["csrf"], row["created"])

    def end(self, token: str | None) -> None:
        if token:
            self.db.run("DELETE FROM sessions WHERE id_hash = ?", (token_hash(token),))

    def end_all(self) -> int:
        return self.db.run("DELETE FROM sessions").rowcount

    def list(self) -> list[dict]:
        return self.db.all("SELECT id_hash, created, last_seen, user_agent, ip FROM sessions ORDER BY last_seen DESC")

    def cleanup(self) -> None:
        now = time.time()
        self.db.run("DELETE FROM sessions WHERE expires < ? OR last_seen < ?", (now, now - SESSION_IDLE_S))
        self.db.run("DELETE FROM oauth_states WHERE created < ?", (now - STATE_TTL_S,))


# --------------------------------------------------------------------- OAuth

def _binding(state: str) -> str:
    """The flow cookie's value for a state. Only this site can set the (__Host-) cookie, so a callback link
    started in another browser, or by someone else, arrives without the matching value."""
    return token_hash("oauth-binding:" + state)


def _pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def _jwt_claims(token: str) -> dict:
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError) as exc:
        raise AuthError("Google sent a sign-in token that couldn't be read.") from exc


class GoogleAuth:
    def __init__(self, config: Config, db: Database, settings: Settings, http: httpx.Client | None = None):
        self.config, self.db, self.settings = config, db, settings
        self.http = http or httpx.Client(timeout=15)

    @property
    def redirect_uri(self) -> str:
        return f"{self.config.base_url}/auth/callback"

    def start(self, purpose: str) -> str:
        """The Google URL to send the browser to."""
        return self.begin(purpose)[0]

    def begin(self, purpose: str) -> tuple[str, str]:
        """(Google URL, browser binding). The caller puts the binding in a short-lived cookie and passes it back to finish()."""
        state, nonce, verifier = new_token(), new_token(), new_token(48)
        self.db.run("DELETE FROM oauth_states WHERE created < ?", (time.time() - STATE_TTL_S,))
        self.db.run(
            "INSERT INTO oauth_states (state_hash, purpose, nonce, verifier, created) VALUES (?, ?, ?, ?, ?)",
            (token_hash(state), purpose, nonce, verifier, time.time()),
        )
        params = {
            "client_id": self.config.google_client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid email",
            "state": state,
            "nonce": nonce,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
            "login_hint": self.config.owner_email,
            "prompt": "select_account",
        }
        if purpose == "gmail":
            params.update(scope=f"openid email {GMAIL_SCOPE}", access_type="offline", prompt="consent", include_granted_scopes="false")
        return f"{AUTH_URL}?{urlencode(params)}", _binding(state)

    def finish(self, state: str | None, code: str | None, error: str | None = None, binding: str | None = None) -> tuple[str, dict]:
        """Check the callback and return (purpose, token response). Raises AuthError.
        binding is the flow cookie's value; without the one begin() made for this state, the callback is refused."""
        if error:
            raise AuthError("Google sign-in was cancelled." if error == "access_denied" else "Google reported a problem with sign-in.")
        if not state or not code or len(state) > 200 or len(code) > 2000:
            raise AuthError("The sign-in link was incomplete. Please try again.")
        with self.db.tx() as conn:  # one use only
            row = conn.execute("SELECT * FROM oauth_states WHERE state_hash = ?", (token_hash(state),)).fetchone()
            conn.execute("DELETE FROM oauth_states WHERE state_hash = ?", (token_hash(state),))
        if not row or time.time() - row["created"] > STATE_TTL_S:
            raise AuthError("That sign-in link expired or was already used. Please try again.")
        if not binding or not hmac.compare_digest(str(binding), _binding(state)):
            raise AuthError("This sign-in was started in a different browser. Please start again from this one.")
        try:
            response = self.http.post(TOKEN_URL, data={
                "code": code, "client_id": self.config.google_client_id, "client_secret": self.config.google_client_secret,
                "redirect_uri": self.redirect_uri, "grant_type": "authorization_code", "code_verifier": row["verifier"],
            })
        except httpx.HTTPError as exc:
            raise AuthError("Couldn't reach Google to finish signing in.") from exc
        if response.status_code != 200:
            raise AuthError("Google didn't accept the sign-in. Check the client ID, secret and redirect address in the setup guide.")
        tokens = response.json()
        claims = _jwt_claims(tokens.get("id_token", ""))
        self._check_claims(claims, row["nonce"])
        tokens["claims"] = claims
        return row["purpose"], tokens

    def _check_claims(self, claims: dict, nonce: str) -> None:
        if claims.get("iss") not in ISSUERS:
            raise AuthError("The sign-in token didn't come from Google.")
        aud = claims.get("aud")
        if aud != self.config.google_client_id and not (isinstance(aud, list) and self.config.google_client_id in aud):
            raise AuthError("The sign-in token was for a different app.")
        if claims.get("azp") not in (None, self.config.google_client_id):
            raise AuthError("The sign-in token was for a different app.")
        if float(claims.get("exp", 0)) < time.time():
            raise AuthError("The sign-in token expired. Please try again.")
        if claims.get("nonce") != nonce:
            raise AuthError("The sign-in token didn't match this sign-in. Please try again.")
        self.check_owner(claims)

    def check_owner(self, claims: dict) -> None:
        email = str(claims.get("email", "")).lower()
        if claims.get("email_verified") is not True or email != self.config.owner_email:  # the JSON true, not "false" or 1
            raise AuthError("This copy of Job Search HQ belongs to someone else. Sign in with the owner's Google account.")
        pinned = self.settings.get("owner_sub")
        sub = str(claims.get("sub", ""))
        if not sub:
            raise AuthError("Google didn't identify the account.")
        if pinned and pinned != sub:
            raise AuthError("This Google account isn't the one that set up this copy.")
        if not pinned:
            self.settings.set("owner_sub", sub)

    def revoke(self, token: str) -> None:
        try:
            self.http.post(REVOKE_URL, data={"token": token})
        except httpx.HTTPError:
            pass  # removing it locally is what matters; Google drops unused grants anyway
