"""Checks against a real server made by install.sh, from the internet and over IAP SSH.

Skipped unless JOBHQ_CLOUD_VM="name zone project" and JOBHQ_CLOUD_HOSTS="pat.x.sslip.io sam.x.sslip.io" are set.
"""
from __future__ import annotations

import os
import socket
import subprocess

import httpx
import pytest

VM = os.environ.get("JOBHQ_CLOUD_VM", "").split()
HOSTS = os.environ.get("JOBHQ_CLOUD_HOSTS", "").split()
pytestmark = [pytest.mark.stack, pytest.mark.skipif(len(VM) != 3 or len(HOSTS) != 2, reason="set JOBHQ_CLOUD_VM and JOBHQ_CLOUD_HOSTS")]


def ssh(command: str) -> str:
    name, zone, project = VM
    out = subprocess.run(["gcloud", "compute", "ssh", name, "--zone", zone, "--project", project, "--tunnel-through-iap", "--quiet",
                          "--command", command], capture_output=True, text=True, timeout=300)
    return out.stdout


def test_real_certificate_and_headers():
    for host in HOSTS:
        with httpx.Client(timeout=20) as c:  # normal certificate checks: this must be a real, trusted certificate
            r = c.get(f"https://{host}/login")
        assert r.status_code == 200
        assert "script-src 'self'" in r.headers["content-security-policy"]
        assert "max-age=31536000" in r.headers["strict-transport-security"]
        assert r.headers["x-frame-options"] == "DENY"


def test_plain_http_redirects_to_https():
    r = httpx.get(f"http://{HOSTS[0]}/login", follow_redirects=False, timeout=20)
    assert r.status_code in (301, 308) and r.headers["location"].startswith(f"https://{HOSTS[0]}")


def test_only_web_ports_are_open():
    ip = socket.gethostbyname(HOSTS[0])
    for port in (22, 8000, 2375, 2376, 3389):
        s = socket.socket()
        s.settimeout(4)
        try:
            assert s.connect_ex((ip, port)) != 0, f"port {port} is reachable from the internet"
        finally:
            s.close()


def test_copies_are_separate_and_owned():
    for host, other in ((HOSTS[0], HOSTS[1]), (HOSTS[1], HOSTS[0])):
        text = httpx.get(f"https://{host}/login", timeout=20).text
        owner_hint = host.split(".")[0][:2]
        assert f"Only {owner_hint}" in text and f"Only {other.split('.')[0][:2]}…" not in text


def test_vm_hardening():
    out = ssh(
        "set +e; echo SA=$(curl -s -o /dev/null -w '%{http_code}' -H 'Metadata-Flavor: Google' "
        "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token); "
        "echo UU=$(systemctl is-enabled unattended-upgrades); echo TIMER=$(systemctl is-active jobhq-backup.timer); "
        "echo DROP=$(sudo iptables -S DOCKER-USER | grep 169.254.169.254 | grep -c DROP); "
        "echo PERM=$(sudo stat -c %a /srv/jobhq /srv/jobhq/vm.env /srv/jobhq/instances/pat.env | tr '\\n' ,); "
        "echo ICC=$(grep -c '\"icc\": false' /etc/docker/daemon.json)")
    values = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    assert values["SA"] == "404", "the VM must have no service account"
    assert values["UU"] == "enabled" and values["TIMER"] == "active" and values["DROP"] == "1" and values["ICC"] == "1"
    assert values["PERM"] == "700,600,600,"


def test_containers_cannot_reach_metadata_or_each_other():
    code = ("import socket\n"
            "socket.getaddrinfo('pypi.org', 443)\n"  # DNS must still work
            "for host, port in (('169.254.169.254', 80), ('app-sam', 8000)):\n"
            "  try:\n"
            "    socket.create_connection((host, port), timeout=4); print('REACHED', host)\n"
            "  except Exception:\n"
            "    print('blocked', host)")
    out = ssh("sudo docker compose -f /srv/jobhq/compose.yaml --project-directory /srv/jobhq --project-name jobhq "
              f"exec -T app-pat python -c \"{code}\"")
    assert "REACHED" not in out and out.count("blocked") == 2, out


def test_jobsctl_status_and_backup():
    out = ssh("sudo jobsctl status 2>&1; sudo jobsctl backup --all 2>&1; sudo ls -l /srv/jobhq/backups")
    assert out.count("OK: ") == 2, out
    assert "pat-" in out and "sam-" in out and "-rw-------" in out


def cloud_session(service: str) -> tuple[str, str]:
    out = ssh("sudo docker compose -f /srv/jobhq/compose.yaml --project-directory /srv/jobhq --project-name jobhq "
              f"exec -T {service} python -c \"from jobhq.config import load; from jobhq.context import build; "
              "t, s = build(load()).sessions.create('cloud-test'); print('SESSION', t, s.csrf)\"")
    line = next(ln for ln in out.splitlines() if ln.startswith("SESSION "))
    return line.split()[1], line.split()[2]


def test_signed_in_over_real_https_and_cross_copy_blocked():
    token, csrf = cloud_session("app-pat")
    cookie = {"Cookie": f"__Host-jobhq={token}"}
    with httpx.Client(timeout=20, follow_redirects=False) as c:
        assert c.get(f"https://{HOSTS[0]}/roles", headers=cookie).status_code == 200
        assert c.get(f"https://{HOSTS[1]}/roles", headers=cookie).headers.get("location") == "/login"
        good = {**cookie, "X-CSRF-Token": csrf, "HX-Request": "true", "Origin": f"https://{HOSTS[0]}", "Sec-Fetch-Site": "same-origin"}
        r = c.post(f"https://{HOSTS[0]}/roles", data={"company": "Cloud Co", "title": "Ops"}, headers=good)
        assert r.status_code == 204 and r.headers["hx-redirect"].startswith("/roles/")
        sibling = {**good, "Origin": f"https://{HOSTS[1]}", "Sec-Fetch-Site": "same-site"}
        assert c.post(f"https://{HOSTS[0]}/roles", data={"company": "X", "title": "Y"}, headers=sibling).status_code == 403
        assert "Cloud Co" in c.get(f"https://{HOSTS[0]}/roles", headers=cookie).text


def test_browser_on_real_https_has_no_errors_and_registers_the_service_worker():
    pw = pytest.importorskip("playwright.sync_api")
    token, _ = cloud_session("app-pat")
    with pw.sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        context.add_cookies([{"name": "__Host-jobhq", "value": token, "url": f"https://{HOSTS[0]}", "secure": True, "httpOnly": True}])
        page = context.new_page()
        problems = []
        page.on("console", lambda m: m.type == "error" and problems.append(m.text))
        page.on("pageerror", lambda e: problems.append(str(e)))
        for path in ("/", "/roles", "/profile", "/chat", "/setup", "/practice"):
            assert page.goto(f"https://{HOSTS[0]}{path}").status == 200
        scope = page.evaluate("navigator.serviceWorker.ready.then(r => r.scope)")
        assert scope == f"https://{HOSTS[0]}/"
        assert page.evaluate("'PushManager' in window")
        browser.close()
    assert problems == []
