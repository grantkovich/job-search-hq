"""Claude access without the app ever holding a Claude.ai token: an API key, or the person's own
Claude Code sign-in kept inside Claude Code."""
import asyncio
import json
import os
import stat

import pytest

from jobhq.ai.claude_code import ClaudeCode
from jobhq.ai.runner import AIUnavailable, ClaudeRunner
from jobhq.store import SECRET_KEYS


def test_no_claude_ai_token_is_ever_a_setting():
    assert not [k for k in SECRET_KEYS if "oauth" in k or "token" in k and "gmail" not in k]


def test_migration_removes_tokens_from_the_old_design(ctx):
    from jobhq.db import _split
    from jobhq.migrations import DROP_CLAUDE_TOKENS, MIGRATIONS
    assert DROP_CLAUDE_TOKENS in MIGRATIONS
    ctx.db.run("INSERT INTO settings (key, value, updated) VALUES ('claude_oauth_token', 'v1:old', 0)")
    ctx.db.run("INSERT OR REPLACE INTO settings (key, value, updated) VALUES ('claude_mode', '\"subscription\"', 0)")
    with ctx.db.tx() as conn:
        for statement in _split(DROP_CLAUDE_TOKENS):
            conn.execute(statement)
    assert ctx.db.value("SELECT COUNT(*) FROM settings WHERE key = 'claude_oauth_token'") == 0
    assert ctx.settings.get("claude_mode") == "claude_login"


def test_pasted_plan_token_is_refused_and_never_stored(client, ctx):
    r = client.post("/settings/claude", data={"mode": "api_key", "api_key": "sk-ant-oat01-abcdefghijklmnop"})
    assert "Claude plan token" in r.headers["hx-trigger"]
    r = client.post("/settings/claude", data={"mode": "api_key", "oauth_token": "sk-ant-oat01-abcdefghijklmnop"})
    assert r.status_code == 200
    assert "sk-ant-oat01" not in json.dumps(ctx.db.all("SELECT * FROM settings"))


def test_plan_mode_needs_claude_code_signed_in(client, ctx):
    r = client.post("/settings/claude", data={"mode": "claude_login"})
    assert "isn't signed in" in r.headers["hx-trigger"] and ctx.claude_mode() == "api_key"
    ctx.claude_code.signed_in = True
    client.post("/settings/claude", data={"mode": "claude_login"})
    assert ctx.claude_mode() == "claude_login" and ctx.claude_ready() and ctx.claude_credentials() == {}
    page = client.get("/settings").text
    assert "signed in" in page and "sudo jobsctl claude-login me" in page
    client.post("/settings/claude/logout")
    assert ctx.claude_code.logged_out == 1 and ctx.claude_mode() == "api_key" and not ctx.claude_ready()


def test_signed_out_plan_is_not_ready(ctx):
    ctx.settings.set("claude_mode", "claude_login")
    assert not ctx.claude_ready()
    ctx.claude_code.signed_in = True
    assert ctx.claude_ready()


def test_runner_on_a_plan_uses_claude_codes_own_folder_and_no_key(ctx, monkeypatch):
    import claude_agent_sdk
    seen = {}

    async def fake_query(prompt, options):
        seen["options"] = options
        async for _ in prompt:
            break
        yield claude_agent_sdk.ResultMessage(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
                                             session_id="s", total_cost_usd=0.02, usage={}, result="hi")

    monkeypatch.setattr(claude_agent_sdk, "query", fake_query)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-be-used")
    ctx.settings.set("claude_mode", "claude_login")
    ctx.claude_code.signed_in = True
    ctx.settings.set("monthly_budget_usd", 1.0)
    ctx.spend.record("x", "m", 5.0, 0, 0, True, "", 0)  # over the API budget: must not block a plan
    runner = ClaudeRunner(ctx.claude_credentials, ctx.spend, ctx.config.work_dir, ctx.model, home=ctx.claude_code.env)
    result = asyncio.run(runner.run("chat", "s", "p", budget_usd=0.5))
    env = seen["options"].env
    assert result.ok
    assert env["ANTHROPIC_API_KEY"] == ""  # a stray key in the environment is blanked, so the plan is what's used
    assert env["HOME"] == str(ctx.claude_code.home) and env["CLAUDE_CONFIG_DIR"].endswith("claude-home/.claude")
    assert seen["options"].max_budget_usd == 0.5


def test_runner_with_an_api_key_still_has_the_cap(ctx):
    ctx.settings.set("claude_api_key", "sk-ant-x")
    ctx.settings.set("monthly_budget_usd", 1.0)
    ctx.spend.record("x", "m", 5.0, 0, 0, True, "", 0)
    runner = ClaudeRunner(ctx.claude_credentials, ctx.spend, ctx.config.work_dir, ctx.model, home=ctx.claude_code.env)
    with pytest.raises(AIUnavailable, match="budget"):
        asyncio.run(runner.run("chat", "s", "p"))


@pytest.fixture
def fake_cli(tmp_path):
    """A stand-in for the claude program that answers `auth status --json` the way Claude Code does."""
    script = tmp_path / "claude"
    script.write_text('#!/bin/sh\n'
                      'if [ "$1 $2" = "auth status" ]; then\n'
                      '  if [ -f "$CLAUDE_CONFIG_DIR/signed-in" ]; then\n'
                      '    echo \'{"loggedIn": true, "authMethod": "claude.ai", "accessToken": "SECRET-SHOULD-NOT-LEAK"}\'\n'
                      '  else echo \'{"loggedIn": false, "authMethod": "none"}\'; fi\n'
                      'elif [ "$1 $2" = "auth logout" ]; then rm -f "$CLAUDE_CONFIG_DIR/signed-in"; fi\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script)


def test_real_status_parsing_never_returns_secrets(tmp_path, fake_cli):
    cc = ClaudeCode(tmp_path, cli=fake_cli)
    assert cc.status() == {"signed_in": False, "method": "none", "error": ""}
    (tmp_path / "claude-home" / ".claude").mkdir(parents=True)
    (tmp_path / "claude-home" / ".claude" / "signed-in").write_text("")
    status = cc.status(fresh=True)
    assert status == {"signed_in": True, "method": "claude.ai", "error": ""}
    assert "SECRET" not in json.dumps(status)
    assert cc.logout() and not cc.status(fresh=True)["signed_in"]


def test_missing_cli_is_reported_not_crashed(tmp_path):
    cc = ClaudeCode(tmp_path, cli=str(tmp_path / "nope"))
    assert cc.status()["signed_in"] is False and cc.status()["error"]


def test_cli_status_command(tmp_path, fake_cli, monkeypatch, capsys):
    from jobhq import cli

    from .conftest import make_env
    for key, value in make_env(tmp_path).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("JOBHQ_CLAUDE_CLI", fake_cli)
    assert cli.main(["claude-status"]) == 1
    assert "not signed in" in capsys.readouterr().out
    os.environ.pop("JOBHQ_CLAUDE_CLI", None)
