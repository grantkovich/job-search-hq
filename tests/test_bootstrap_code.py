"""bootstrap-vm.sh's code step: getting the app onto the server, fresh or over an earlier install."""
import re
import subprocess
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
SCRIPT = (ROOT_DIR / "scripts" / "bootstrap-vm.sh").read_text()


def code_step() -> str:
    """The part of the script between '# The app.' and installing jobsctl, which needs no root and no network."""
    return re.search(r"# The app\.\n(.*?)\ninstall -m 0755 \"\$ROOT/repo/scripts/jobsctl\"", SCRIPT, re.S).group(1)


def source_repo(tmp_path: Path) -> Path:
    src = tmp_path / "src"
    src.mkdir()
    (src / "marker.txt").write_text("from git\n")
    for cmd in (["init", "-q", "-b", "main"], ["add", "."],
                ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "first"]):
        subprocess.run(["git", "-C", str(src), *cmd], check=True)
    return src


def run_step(tmp_path: Path, src: Path) -> subprocess.CompletedProcess:
    root = tmp_path / "root"
    script = (f'set -euo pipefail\nROOT={root}\nREPO_URL=file://{src}\nREPO_REF=main\nSOURCE_TARBALL=\n'
              f'SIZE=standard\nSUGGESTED_DOMAIN=\nIMAGE=\n' + code_step())
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def test_fresh_server_clones_the_code(tmp_path):
    src = source_repo(tmp_path)
    result = run_step(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "root/repo/marker.txt").read_text() == "from git\n"
    assert (tmp_path / "root/repo/.git").is_dir()


def test_server_installed_from_uploaded_files_switches_to_git(tmp_path):
    """A server first set up with 'local' (an uploaded copy, no .git) and later re-run with the GitHub URL."""
    src = source_repo(tmp_path)
    old = tmp_path / "root/repo"
    old.mkdir(parents=True)
    (old / "old.txt").write_text("uploaded copy\n")
    result = run_step(tmp_path, src)
    assert result.returncode == 0, result.stderr
    assert (old / "marker.txt").read_text() == "from git\n"
    assert (old / ".git").is_dir()  # so `jobsctl update` can pull from now on
    assert not (old / "old.txt").exists()
    assert not (tmp_path / "root/repo.new").exists()
