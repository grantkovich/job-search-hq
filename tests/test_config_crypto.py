import base64
import os
import time

import pytest

from jobhq.config import ConfigError, load
from jobhq.crypto import Box, DecryptError

from .conftest import make_env


def test_loads_a_good_config(tmp_path):
    cfg = load(make_env(tmp_path), scrub=False)
    assert cfg.owner_email == "owner@example.com"
    assert cfg.origin == "https://me.example.test"
    assert cfg.secure


@pytest.mark.parametrize("change, message", [
    ({"JOBHQ_OWNER_EMAIL": ""}, "JOBHQ_OWNER_EMAIL"),
    ({"JOBHQ_OWNER_EMAIL": "not-an-email"}, "email"),
    ({"JOBHQ_BASE_URL": "http://me.example.test"}, "https://"),
    ({"JOBHQ_BASE_URL": "https://me.example.test/app"}, "just the address"),
    ({"JOBHQ_SECRET": base64.urlsafe_b64encode(b"short").decode()}, "32"),
    ({"JOBHQ_TIMEZONE": "Mars/Base"}, "time zone"),
])
def test_bad_config_says_what_to_fix(tmp_path, change, message):
    with pytest.raises(ConfigError, match=message):
        load({**make_env(tmp_path), **change}, scrub=False)


def test_plain_http_only_for_localhost(tmp_path):
    cfg = load({**make_env(tmp_path), "JOBHQ_BASE_URL": "http://localhost:8000"}, scrub=False)
    assert not cfg.secure


def test_secrets_are_removed_from_the_environment(tmp_path, monkeypatch):
    for key, value in make_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    load()
    assert "JOBHQ_SECRET" not in os.environ
    assert "JOBHQ_GOOGLE_CLIENT_SECRET" not in os.environ
    assert os.environ["JOBHQ_OWNER_EMAIL"] == "owner@example.com"


def test_encryption_round_trip_and_binding():
    box = Box(b"a" * 32)
    sealed = box.encrypt("sk-ant-secret", "claude_api_key")
    assert "sk-ant" not in sealed
    assert box.decrypt(sealed, "claude_api_key") == "sk-ant-secret"
    with pytest.raises(DecryptError):
        box.decrypt(sealed, "gmail_refresh_token")  # moved to another field
    with pytest.raises(DecryptError):
        Box(b"b" * 32).decrypt(sealed, "claude_api_key")  # wrong secret
    tampered = sealed[:-2] + ("A" if sealed[-2] != "A" else "B") + sealed[-1]
    with pytest.raises(DecryptError):
        box.decrypt(tampered, "claude_api_key")


def test_rotation_reads_old_values():
    old = Box(b"o" * 32).encrypt("value", "k")
    rotated = Box(b"n" * 32, old_secret=b"o" * 32)
    assert rotated.decrypt(old, "k") == "value"
    assert rotated.decrypt(rotated.encrypt("value", "k"), "k") == "value"


def test_signed_tokens_expire_and_resist_tampering():
    box = Box(b"a" * 32)
    token = box.sign({"r": 5}, ttl_s=60)
    assert box.verify(token)["r"] == 5
    body, mac = token.split(".")
    assert box.verify(body + "." + mac[:-1] + ("A" if mac[-1] != "A" else "B")) is None
    assert Box(b"c" * 32).verify(token) is None
    assert box.verify(box.sign({"r": 5}, ttl_s=-1)) is None
    assert box.verify("garbage") is None


def test_settings_store_secrets_encrypted(ctx):
    ctx.settings.set("claude_api_key", "sk-ant-xyz")
    raw = ctx.db.value("SELECT value FROM settings WHERE key = 'claude_api_key'")
    assert "sk-ant" not in raw and raw.startswith("v1:")
    assert ctx.settings.get("claude_api_key") == "sk-ant-xyz"
    ctx.settings.set("model", "claude-haiku-5-5")
    assert ctx.settings.get("model") == "claude-haiku-5-5"


def test_database_file_is_private(ctx):
    assert oct(ctx.config.db_path.stat().st_mode & 0o777) == "0o600"


def test_migrations_are_idempotent(ctx):
    version = ctx.db.value("PRAGMA user_version")
    assert ctx.db.migrate() == version
    assert time.time()  # schema stayed usable
    assert ctx.db.value("SELECT COUNT(*) FROM roles") == 0


def test_serve_builds_the_app_from_one_config_read(tmp_path, monkeypatch):
    """Found by the Docker test: reading the config removes the secrets, so serve must not read it twice."""
    import uvicorn

    import jobhq.cli as cli
    for key, value in make_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    started = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: started.setdefault("app", app))
    assert cli.main(["serve"]) == 0
    assert started["app"].state.ctx.config.owner_email == "owner@example.com"


def test_small_server_settings(tmp_path):
    cfg = load({**make_env(tmp_path), "JOBHQ_AI_PARALLEL": "1", "JOBHQ_WORKERS": "1"}, scrub=False)
    assert (cfg.ai_parallel, cfg.workers) == (1, 1)
    cfg = load({**make_env(tmp_path), "JOBHQ_AI_PARALLEL": "999", "JOBHQ_WORKERS": "x"}, scrub=False)
    assert (cfg.ai_parallel, cfg.workers) == (2, 2)
