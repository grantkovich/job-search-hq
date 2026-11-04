"""Checks against the real Docker stack (Caddy + two copies) brought up by jobsctl.

Skipped unless JOBHQ_STACK_ROOT points at a jobsctl root with copies named pat and sam on *.localhost
(see scripts/dev/stack_up.py). Talks HTTPS to Caddy on 127.0.0.1 and runs commands inside the containers.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from pathlib import Path

import httpx
import pytest

ROOT = os.environ.get("JOBHQ_STACK_ROOT")
pytestmark = [pytest.mark.stack, pytest.mark.skipif(not ROOT, reason="set JOBHQ_STACK_ROOT to run")]
_real_getaddrinfo = socket.getaddrinfo


@pytest.fixture(autouse=True)
def localhost_names(monkeypatch):
    def resolve(host, *args, **kw):
        return _real_getaddrinfo("127.0.0.1" if str(host).endswith(".localhost") else host, *args, **kw)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def compose(*args, check=True, input=None):
    return subprocess.run(["docker", "compose", "-f", f"{ROOT}/compose.yaml", "--project-name", "jobhq", *args],
                          check=check, capture_output=True, text=True, input=input, timeout=300)


def py(service, code, check=True):
    return compose("exec", "-T", service, "python", "-c", code, check=check)


def client(**kw):
    return httpx.Client(verify=False, timeout=20, follow_redirects=False, **kw)


def session_for(service) -> tuple[str, str]:
    out = py(service, "from jobhq.config import load; from jobhq.context import build; c = build(load()); "
                      "t, s = c.sessions.create('stack-test'); print(t, s.csrf)").stdout.split()
    return out[-2], out[-1]


def test_containers_are_healthy():
    for _ in range(60):
        states = {r["Service"]: r.get("Health", "") for r in map(json.loads, compose("ps", "--format", "json").stdout.splitlines())}
        if all(v in ("healthy", "") for v in states.values()) and states.get("app-pat") == "healthy":
            break
        time.sleep(2)
    assert states["app-pat"] == "healthy" and states["app-sam"] == "healthy", states


def test_https_and_headers_through_caddy():
    with client() as c:
        r = c.get("https://pat.localhost/login")
        assert r.status_code == 200 and r.http_version in ("HTTP/1.1", "HTTP/2")
        assert "script-src 'self'" in r.headers["content-security-policy"]
        assert "max-age=31536000" in r.headers["strict-transport-security"]
        assert "server" not in {k.lower() for k in r.headers} or "uvicorn" not in r.headers.get("server", "").lower()
        assert c.get("https://pat.localhost/healthz").json() == {"ok": True}
        assert "Only pa" in c.get("https://pat.localhost/login").text and "Only sa" in c.get("https://sam.localhost/login").text
        r = c.get("http://pat.localhost/login")
        assert r.status_code in (301, 308) and r.headers["location"].startswith("https://pat.localhost")


def test_unknown_host_gets_nothing():
    with client() as c:
        try:
            r = c.get("https://evil.localhost/")
            assert r.status_code >= 400 or "Job Search HQ" not in r.text
        except httpx.ConnectError:
            pass  # TLS refused: no certificate for that name, which is also fine


def test_container_hardening():
    for service in ("app-pat", "app-sam"):
        cid = compose("ps", "-q", service).stdout.strip()
        info = json.loads(subprocess.run(["docker", "inspect", cid], capture_output=True, text=True, check=True).stdout)[0]
        host = info["HostConfig"]
        assert info["Config"]["User"] == "10001:10001"
        assert host["ReadonlyRootfs"] is True and host["CapDrop"] == ["ALL"] and "no-new-privileges:true" in host["SecurityOpt"]
        assert host["Memory"] > 0 and host["PidsLimit"] == 512 and not host.get("PortBindings")
        out = compose("exec", "-T", service, "sh", "-c", "id -u; touch /etc/x 2>&1; touch /app/src/x 2>&1; echo ok > /tmp/x && cat /tmp/x").stdout
        assert out.splitlines()[0] == "10001" and "Read-only file system" in out and out.strip().endswith("ok")


def test_copies_cannot_reach_each_other():
    out = py("app-pat", "import socket\ntry:\n socket.create_connection(('app-sam', 8000), timeout=3); print('REACHED')\n"
                          "except Exception as e: print('blocked', type(e).__name__)").stdout
    assert "blocked" in out
    out = py("app-pat", "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode())").stdout
    assert '"ok":true' in out.replace(" ", "")


def test_sessions_and_data_are_separate_per_copy():
    token, csrf = session_for("app-pat")
    with client() as c:
        c.cookies.set("__Host-jobhq", token, domain="pat.localhost")
        r = c.get("https://pat.localhost/roles", headers={"Cookie": f"__Host-jobhq={token}"})
        assert r.status_code == 200 and "Roles" in r.text
        r = c.get("https://sam.localhost/roles", headers={"Cookie": f"__Host-jobhq={token}"})
        assert r.status_code == 303 and r.headers["location"] == "/login"
        headers = {"Cookie": f"__Host-jobhq={token}", "X-CSRF-Token": csrf, "HX-Request": "true"}
        ok = c.post("https://pat.localhost/roles", data={"company": "Stack Co", "title": "Ops"},
                    headers={**headers, "Origin": "https://pat.localhost", "Sec-Fetch-Site": "same-origin"})
        assert ok.status_code == 204 and ok.headers["hx-redirect"].startswith("/roles/")
        cross = c.post("https://pat.localhost/roles", data={"company": "Sibling Co", "title": "Ops"},
                       headers={**headers, "Origin": "https://sam.localhost", "Sec-Fetch-Site": "same-site"})
        assert cross.status_code == 403
    assert "Stack Co" in py("app-pat", "from jobhq.config import load; from jobhq.context import build; "
                                         "print([r['company'] for r in build(load()).roles.all()])").stdout
    assert "Stack Co" not in py("app-sam", "from jobhq.config import load; from jobhq.context import build; "
                                           "print([r['company'] for r in build(load()).roles.all()])").stdout


def test_pdf_builds_inside_the_read_only_container():
    out = py("app-pat", "from jobhq.documents import build_pdf, pdf_facts\n"
                          "pdf = build_pdf('resume', {'name': 'Pat', 'contact': ['pat@example.com'], 'headline': '', 'summary': 'Hi',"
                          " 'experience': [{'title': 'Lead', 'company': 'Acme', 'location': '', 'dates': '2020', 'bullets': ['Did it']}],"
                          " 'education': [], 'skills': ['SQL']})\nprint(pdf[:4], pdf_facts(pdf)[0])").stdout
    assert "b'%PDF' 1" in out


def test_claude_cli_runs_inside_the_container():
    code = ("import subprocess, pathlib, claude_agent_sdk as s\n"
            "cli = next(pathlib.Path(s.__file__).parent.joinpath('_bundled').glob('claude*'))\n"
            "pathlib.Path('/data/work/home').mkdir(parents=True, exist_ok=True)\n"
            "r = subprocess.run([str(cli), '--version'], capture_output=True, text=True, timeout=60,\n"
            "                   env={'HOME': '/data/work/home', 'PATH': '/usr/bin:/bin'})\n"
            "print(r.returncode, r.stdout.strip(), r.stderr.strip()[:200])")
    out = py("app-pat", code).stdout
    assert out.startswith("0 ") and "Claude Code" in out, out


def test_voice_transcription_in_the_container():
    clip = Path(os.environ.get("JOBHQ_STACK_CLIP", ""))
    if not clip.is_file():
        pytest.skip("set JOBHQ_STACK_CLIP to an audio file")
    cid = compose("ps", "-q", "app-pat").stdout.strip()
    subprocess.run(["docker", "cp", str(clip), f"{cid}:/data/work/clip.m4a"], check=True)
    code = ("import os; os.environ['JOBHQ_VOICE'] = 'on'\n"
            "from jobhq.config import load; from jobhq.context import build; from jobhq import practice\n"
            "c = build(load()); data = open('/data/work/clip.m4a', 'rb').read(); os.remove('/data/work/clip.m4a')\n"
            "text, secs = practice.transcribe(c, data, 'audio/mp4', 'HubSpot'); print(round(secs), text)")
    out = compose("exec", "-T", "app-pat", "python", "-c", code, check=False)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "hubspot" in out.stdout.lower() and "75,000" in out.stdout.replace(" ", "").replace("seventy-fivethousand", "75,000"), out.stdout
    left = compose("exec", "-T", "app-pat", "sh", "-c", "ls /data/work | grep -c voice- || true").stdout.strip()
    assert left in ("0", "")


def test_restart_keeps_data_and_sessions():
    token, _ = session_for("app-pat")
    compose("restart", "app-pat")
    for _ in range(60):
        try:
            with client() as c:
                r = c.get("https://pat.localhost/roles", headers={"Cookie": f"__Host-jobhq={token}"})
            if r.status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(1)
    assert r.status_code == 200 and "Stack Co" in r.text


def test_claude_code_sign_in_runs_in_the_container():
    """Claude Code's own sign-in starts inside the read-only container and shows Anthropic's sign-in link.
    Finishing it needs a person with a Claude account, so this stops at the link."""
    status = compose("exec", "-T", "app-pat", "python", "-m", "jobhq.cli", "claude-status", check=False)
    assert status.returncode in (0, 1) and "Claude Code:" in status.stdout
    out = compose("exec", "-T", "app-pat", "sh", "-c", "timeout 25 python -m jobhq.cli claude-login < /dev/null 2>&1 | head -20", check=False)
    assert "https://claude.com/" in out.stdout or "https://claude.ai/" in out.stdout, out.stdout[-1000:]
    assert "never sees your Claude password" in out.stdout


def test_small_profile_limits_when_set():
    doc = json.loads(Path(ROOT, "compose.yaml").read_text().split("\n", 1)[1])
    vm = Path(ROOT, "vm.env").read_text()
    if "JOBHQ_SIZE=small" not in vm:
        pytest.skip("not the small-server profile")
    app = doc["services"]["app-pat"]
    assert app["mem_limit"] == "700m" and app["environment"]["JOBHQ_AI_PARALLEL"] == "1"
    out = py("app-pat", "from jobhq.config import load; c = load(); print(c.ai_parallel, c.workers)").stdout.split()
    assert out[-2:] == ["1", "1"]
