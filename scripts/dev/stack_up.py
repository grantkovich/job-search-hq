"""Bring up the real Docker stack locally with jobsctl: Caddy + two copies (pat, sam) on *.localhost with Caddy's own CA.
Root checks and chown are patched out because this runs as a normal Mac user; everything else is the real script."""
import importlib.machinery
import importlib.util
import os
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[2]
root = Path(os.environ.get("JOBHQ_E2E_ROOT", repo / ".e2e-root"))
root.mkdir(exist_ok=True)
if not (root / "repo").exists():
    (root / "repo").symlink_to(repo)
os.environ["JOBHQ_ROOT"] = str(root)
loader = importlib.machinery.SourceFileLoader("jobsctl", str(repo / "scripts/jobsctl"))
spec = importlib.util.spec_from_loader("jobsctl", loader)
j = importlib.util.module_from_spec(spec)
loader.exec_module(j)
j.os.geteuid = lambda: 0
j.os.chown = lambda *a, **k: None
j.HOST_RE = __import__("re").compile(r"^[a-z0-9.-]+$")  # allow *.localhost for the local test
import argparse  # noqa: E402 - after the module patches above

if sys.argv[1] == "up":
    vm = {"JOBHQ_DOMAIN": "localhost", "ACME_EMAIL": "admin@example.com",
          "JOBHQ_GOOGLE_CLIENT_ID": "local-test.apps.googleusercontent.com", "JOBHQ_GOOGLE_CLIENT_SECRET": "not-real"}
    if os.environ.get("JOBHQ_TEST_IMAGE"):  # test the prebuilt-image path against a local registry
        vm["JOBHQ_IMAGE"] = os.environ["JOBHQ_TEST_IMAGE"]
    if os.environ.get("JOBHQ_TEST_SIZE"):
        vm["JOBHQ_SIZE"] = os.environ["JOBHQ_TEST_SIZE"]
    j.write_env(j.VM_ENV, vm)
    for d in (j.INSTANCES, j.DATA, j.BACKUPS):
        d.mkdir(parents=True, exist_ok=True)
    for name, email in (("pat", "pat@example.com"), ("sam", "sam@example.com")):
        if not (j.INSTANCES / f"{name}.env").exists():
            j.cmd_add_user(argparse.Namespace(name=name, email=email, timezone="America/New_York", no_voice=False, address=None))
            (j.DATA / name).chmod(0o777)  # the Mac user isn't uid 10001; colima maps writes through
    j.render()
    j.apply()  # an existing root is rebuilt from the current code too
elif sys.argv[1] == "backup":
    j.cmd_backup(argparse.Namespace(name=sys.argv[2], all=False, quiet=False))
elif sys.argv[1] == "restore":
    j.cmd_restore(argparse.Namespace(name=sys.argv[2], file=sys.argv[3]))
elif sys.argv[1] == "export":
    j.cmd_export_server(argparse.Namespace(file=sys.argv[2]))
elif sys.argv[1] == "import":
    j.write_env(j.INSTALL_ENV, {"JOBHQ_SIZE": os.environ.get("JOBHQ_TEST_SIZE", ""), "JOBHQ_IMAGE": os.environ.get("JOBHQ_TEST_IMAGE", "")})
    j.cmd_import_server(argparse.Namespace(file=sys.argv[2], domain=sys.argv[3] if len(sys.argv) > 3 else None, keep_voice=False))
elif sys.argv[1] == "status":
    j.cmd_status(argparse.Namespace())
elif sys.argv[1] == "down":
    j.compose("down", "-v", check=False)
