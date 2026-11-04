"""jobsctl renders the VM's compose.yaml and Caddyfile. These tests run it without Docker."""
import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "jobsctl"


@pytest.fixture
def jobsctl(tmp_path, monkeypatch):
    monkeypatch.setenv("JOBHQ_ROOT", str(tmp_path))
    loader = importlib.machinery.SourceFileLoader("jobsctl_under_test", str(SCRIPT))
    spec = importlib.util.spec_from_loader("jobsctl_under_test", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    yield module
    sys.modules.pop("jobsctl_under_test", None)


def setup_two(j):
    j.write_env(j.VM_ENV, {"JOBHQ_DOMAIN": "jobs.example.com", "ACME_EMAIL": "admin@example.com",
                           "JOBHQ_GOOGLE_CLIENT_ID": "x.apps.googleusercontent.com", "JOBHQ_GOOGLE_CLIENT_SECRET": "s"})
    for name, email in (("pat", "pat@example.com"), ("sam", "sam@example.com")):
        j.write_env(j.INSTANCES / f"{name}.env", {"JOBHQ_OWNER_EMAIL": email, "JOBHQ_BASE_URL": f"https://{name}.jobs.example.com",
                                                  "JOBHQ_SECRET": j.new_secret(), "JOBHQ_TIMEZONE": "America/New_York", "JOBHQ_VOICE": "on"})


def test_env_files_are_private(jobsctl, tmp_path):
    jobsctl.write_env(tmp_path / "x.env", {"A": "1"})
    assert oct((tmp_path / "x.env").stat().st_mode & 0o777) == "0o600"
    with pytest.raises(SystemExit):
        jobsctl.write_env(tmp_path / "y.env", {"A": "1\nB=2"})  # no smuggling extra settings


def test_render_isolates_each_copy(jobsctl):
    setup_two(jobsctl)
    jobsctl.render()
    text = jobsctl.COMPOSE.read_text()
    doc = json.loads(text.split("\n", 1)[1])
    apps = {k: v for k, v in doc["services"].items() if k.startswith("app-")}
    assert set(apps) == {"app-pat", "app-sam"}
    for name, svc in apps.items():
        person = name.removeprefix("app-")
        assert svc["networks"] == [f"net_{person}"]  # copies can't reach each other
        assert svc["read_only"] and svc["cap_drop"] == ["ALL"] and "no-new-privileges:true" in svc["security_opt"]
        assert svc["user"] == "10001:10001" and svc["volumes"] == [f"./data/{person}:/data"]
        assert "ports" not in svc  # only Caddy is reachable from outside
    assert set(doc["services"]["caddy"]["networks"]) == {"net_pat", "net_sam"}
    assert sum("build" in s for s in apps.values()) == 1
    caddy = jobsctl.CADDYFILE.read_text()
    assert "pat.jobs.example.com {" in caddy and "reverse_proxy app-sam:8000" in caddy and "max_size 30MB" in caddy


def test_add_user_validates(jobsctl, monkeypatch):
    import argparse
    monkeypatch.setattr(jobsctl.os, "geteuid", lambda: 0)
    setup_two(jobsctl)
    for name, email in (("Bad Name", "a@b.com"), ("ok", "not-email"), ("x", "a@b.com"), ("pat", "a@b.com")):
        with pytest.raises(SystemExit):
            jobsctl.cmd_add_user(argparse.Namespace(name=name, email=email, timezone="America/New_York", no_voice=False, address=None))


def test_added_message_skips_dns_for_free_addresses(jobsctl):
    free = jobsctl.added_message("sam", "sam.203-0-113-10.sslip.io", "sam@example.com")
    assert "DNS" not in free
    assert "https://sam.203-0-113-10.sslip.io/auth/callback" in free and "Add URI" in free
    assert "Test users" in free and "sam@example.com" in free and "Publish app" not in free
    own = jobsctl.added_message("sam", "sam.jobs.example.com", "sam@example.com")
    assert "DNS" in own and "https://sam.jobs.example.com/auth/callback" in own


def test_secrets_are_long_and_random(jobsctl):
    import base64
    a, b = jobsctl.new_secret(), jobsctl.new_secret()
    assert a != b and len(base64.urlsafe_b64decode(a + "=" * (-len(a) % 4))) == 32


def test_prebuilt_image_and_small_profile(jobsctl):
    setup_two(jobsctl)
    env = jobsctl.read_env(jobsctl.VM_ENV)
    env.update(JOBHQ_IMAGE="ghcr.io/grantkovich/job-search-hq:0.1.0", JOBHQ_SIZE="small")
    jobsctl.write_env(jobsctl.VM_ENV, env)
    jobsctl.render()
    doc = json.loads(jobsctl.COMPOSE.read_text().split("\n", 1)[1])
    apps = [v for k, v in doc["services"].items() if k.startswith("app-")]
    assert all(a["image"] == "ghcr.io/grantkovich/job-search-hq:0.1.0" and "build" not in a for a in apps)
    assert all(a["mem_limit"] == "700m" and a["environment"]["JOBHQ_AI_PARALLEL"] == "1" for a in apps)


def test_bad_image_reference_refused(jobsctl):
    setup_two(jobsctl)
    env = jobsctl.read_env(jobsctl.VM_ENV)
    env["JOBHQ_IMAGE"] = "evil image; rm -rf /"
    jobsctl.write_env(jobsctl.VM_ENV, env)
    with pytest.raises(SystemExit):
        jobsctl.render()


@pytest.mark.parametrize("image", ["ghcr.io/o/job-search-hq:0.1.0", "ghcr.io/o/job-search-hq@sha256:" + "a" * 64,
                                   "localhost:5000/o/job-search-hq:x"])
def test_with_version(jobsctl, image):
    assert jobsctl.with_version(image, "0.2.0").endswith("/job-search-hq:0.2.0")
    assert jobsctl.IMAGE_RE.match(jobsctl.with_version(image, "0.2.0"))


def test_restore_keeps_the_same_folder(jobsctl, monkeypatch, tmp_path):
    """Found by the Docker move test: a container's mount follows the folder it started with, so restore must
    replace the folder's contents, never the folder itself."""
    import io
    import tarfile
    monkeypatch.setattr(jobsctl.os, "geteuid", lambda: 0)
    monkeypatch.setattr(jobsctl.os, "chown", lambda *a, **k: None)
    monkeypatch.setattr(jobsctl, "compose", lambda *a, **k: None)
    setup_two(jobsctl)
    data = jobsctl.DATA / "pat"
    data.mkdir(parents=True)
    (data / "app.db").write_text("old")
    (data / "app.db-wal").write_text("stale wal")
    inode = data.stat().st_ino
    archive = tmp_path / "b.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("backup/app.db")
        info.size = 3
        tar.addfile(info, io.BytesIO(b"new"))
    jobsctl.cmd_restore(__import__("argparse").Namespace(name="pat", file=str(archive)))
    assert data.stat().st_ino == inode
    assert (data / "app.db").read_text() == "new" and not (data / "app.db-wal").exists()


def _tar(tmp_path, members):
    import io
    import tarfile
    path = tmp_path / "t.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        for name, kind in members:
            info = tarfile.TarInfo(name)
            if kind == "file":
                info.size = 2
                tar.addfile(info, io.BytesIO(b"ok"))
            elif kind == "link":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tar.addfile(info)
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                tar.addfile(info)
    return path


def test_safe_extract_plain_files(jobsctl, tmp_path):
    dest = tmp_path / "out"
    jobsctl.safe_extract(_tar(tmp_path, [("backup", "dir"), ("backup/app.db", "file"), ("instance.env", "file")]), dest)
    assert (dest / "backup" / "app.db").read_bytes() == b"ok" and oct((dest / "instance.env").stat().st_mode & 0o777) == "0o600"


@pytest.mark.parametrize("members", [[("../escape", "file")], [("/etc/evil", "file")], [("backup/link", "link")],
                                     [("backup/../../x", "file")]])
def test_safe_extract_refuses_tricks(jobsctl, tmp_path, members):
    with pytest.raises(SystemExit):
        jobsctl.safe_extract(_tar(tmp_path, members), tmp_path / "out")
    assert not (tmp_path / "escape").exists()
