"""Encryption for secrets kept in the database (Claude key, Gmail refresh token, push keys) and
signed short-lived tokens.

AES-256-GCM with a key derived from JOBHQ_SECRET by HKDF. Each value is bound to its
setting name through the associated data, so a ciphertext copied into another field
fails to decrypt. JOBHQ_SECRET_OLD, when set, is tried for decryption so the secret
can be rotated (`jobhq rotate-secret` re-encrypts everything under the new one).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

PREFIX = "v1:"


class DecryptError(ValueError):
    pass


def _derive(master: bytes, purpose: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"jobhq", info=purpose).derive(master)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Box:
    def __init__(self, secret: bytes, old_secret: bytes | None = None):
        self._keys = [AESGCM(_derive(secret, b"settings v1"))]
        if old_secret:
            self._keys.append(AESGCM(_derive(old_secret, b"settings v1")))
        self._sign_key = _derive(secret, b"tokens v1")

    def encrypt(self, plaintext: str, context: str) -> str:
        nonce = os.urandom(12)
        sealed = self._keys[0].encrypt(nonce, plaintext.encode(), context.encode())
        return PREFIX + _b64(nonce + sealed)

    def decrypt(self, token: str, context: str) -> str:
        if not token.startswith(PREFIX):
            raise DecryptError("not an encrypted value")
        try:
            raw = _unb64(token[len(PREFIX):])
        except ValueError as exc:
            raise DecryptError("damaged value") from exc
        for key in self._keys:
            try:
                return key.decrypt(raw[:12], raw[12:], context.encode()).decode()
            except Exception:  # noqa: S112 - InvalidTag; try the old key next
                continue
        raise DecryptError("can't decrypt: wrong JOBHQ_SECRET, or the value was moved or changed")

    # Signed tokens for links that can't carry a CSRF header (push notification buttons).
    def sign(self, payload: dict, ttl_s: int) -> str:
        body = _b64(json.dumps({**payload, "exp": int(time.time()) + ttl_s}, separators=(",", ":")).encode())
        mac = _b64(hmac.new(self._sign_key, body.encode(), hashlib.sha256).digest())
        return f"{body}.{mac}"

    def verify(self, token: str) -> dict | None:
        body, _, mac = (token or "").partition(".")
        if not body or not mac:
            return None
        expected = _b64(hmac.new(self._sign_key, body.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(expected, mac):
            return None
        try:
            payload = json.loads(_unb64(body))
        except ValueError:
            return None
        if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
            return None
        return payload


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def token_hash(token: str) -> str:
    """What the database keeps instead of a session id or one-time state, so a stolen copy of the database can't be replayed."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_master_secret() -> str:
    return _b64(os.urandom(32))
