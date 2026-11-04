"""The installer's questions (scripts/lib-gcp.sh), driven through bash with typed answers."""
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parents[1] / "scripts" / "lib-gcp.sh"


def run(snippet: str, answers: str) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", f'set -euo pipefail; . "{LIB}"; {snippet}'], input=answers,
                          capture_output=True, text=True, timeout=10)


def last(result: subprocess.CompletedProcess, var: str) -> str:
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().splitlines()[-1].removeprefix(f"{var}=")


# choose: a numbered menu
def pick(answers: str, default: str = "2") -> tuple[str, subprocess.CompletedProcess]:
    result = run(f'choose PICK "Size" {default} "trial|Trial size" "free|Forever free"; echo "PICK=$PICK"', answers)
    return last(result, "PICK"), result


def test_choose_shows_a_numbered_menu_and_marks_the_default():
    _, result = pick("\n")
    assert "  1) Trial size" in result.stderr
    assert "  2) Forever free" in result.stderr
    assert "[2]" in result.stderr


def test_choose_enter_takes_the_default():
    assert pick("\n")[0] == "free"


def test_choose_a_number_picks_that_item():
    assert pick("1\n")[0] == "trial"


def test_choose_accepts_the_value_itself():
    assert pick("trial\n")[0] == "trial"


def test_choose_asks_again_after_a_bad_answer():
    value, result = pick("7\nbig\n1\n")
    assert value == "trial"
    assert result.stderr.count("Type a number from 1 to 2") == 2


def test_choose_without_a_default_needs_an_answer():
    value, result = pick("\n2\n", default="0")
    assert value == "free"
    assert "Type a number from 1 to 2" in result.stderr


# pick_region
def region(answers: str, free: str = "") -> str:
    return last(run(f'pick_region {free}; echo "REGION=$REGION"', answers), "REGION")


def test_region_enter_takes_us_east1():
    assert region("\n") == "us-east1"


def test_region_menu_numbers():
    assert region("2\n") == "us-central1"
    assert region("3\n") == "us-west1"


def test_region_other_asks_for_a_name_on_trial():
    assert region("4\neurope-west2\n") == "europe-west2"


def test_region_other_name_is_checked():
    result = run('pick_region; echo "REGION=$REGION"', "4\nnot a region\nasia-east1\n")
    assert last(result, "REGION") == "asia-east1"
    assert "isn't a region" in result.stderr


def test_region_free_has_no_other_option():
    result = run('pick_region free; echo "REGION=$REGION"', "4\n2\n")
    assert last(result, "REGION") == "us-central1"
    assert "Other region" not in result.stderr


# ask_valid: asks again until the answer matches
def test_ask_valid_repeats_until_it_matches():
    result = run('ask_valid N "Short name" "" "^[a-z][a-z0-9-]{1,20}$" "Use lowercase letters."; echo "N=$N"', "Pat Lee\npat\n")
    assert last(result, "N") == "pat"
    assert result.stderr.count("Use lowercase letters.") == 1


def test_ask_valid_uses_the_default():
    result = run('ask_valid N "Name" "jobhq" "^[a-z]+$" "Letters."; echo "N=$N"', "\n")
    assert last(result, "N") == "jobhq"


# pick_timezone
def test_timezone_menu_number():
    assert last(run('pick_timezone; echo "TZ_NAME=$TZ_NAME"', "3\n"), "TZ_NAME") == "America/Denver"


def test_timezone_default_is_eastern():
    assert last(run('pick_timezone; echo "TZ_NAME=$TZ_NAME"', "\n"), "TZ_NAME") == "America/New_York"


def test_timezone_other_is_checked_against_the_zone_list():
    result = run('pick_timezone; echo "TZ_NAME=$TZ_NAME"', "8\nMars/Olympus\nEurope/London\n")
    assert last(result, "TZ_NAME") == "Europe/London"
    assert "isn't a time zone" in result.stderr


# pick_project and link_billing, against a stand-in gcloud
FAKE_GCLOUD = r"""#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$*" in
  "projects list"*) printf '%b' "${FAKE_PROJECTS:-alpha-proj\njobhq-261104-123\n}" ;;
  "config get-value project"*) echo "${FAKE_CURRENT:-}" ;;
  "projects create taken-id-123"*) echo "ERROR: already in use" >&2; exit 1 ;;
  "projects create"*) exit 0 ;;
  "billing projects describe"*)
    if [ -n "${FAKE_BILLING_ERROR:-}" ]; then echo "ERROR: PERMISSION_DENIED" >&2; exit 1; fi
    if [ -f "$FAKE_DIR/linked" ]; then echo True; else echo False; fi ;;
  "billing accounts list"*) printf '%b' "${FAKE_ACCOUNTS:-}" ;;
  "billing projects link"*) touch "$FAKE_DIR/linked" ;;
esac
"""


def gcp(tmp_path, snippet: str, answers: str, **env) -> tuple[subprocess.CompletedProcess, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    fake = bin_dir / "gcloud"
    fake.write_text(FAKE_GCLOUD)
    fake.chmod(0o755)
    log = tmp_path / "gcloud.log"
    log.touch()
    import os
    full_env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LOG": str(log), "FAKE_DIR": str(tmp_path), **env}
    result = subprocess.run(["bash", "-c", f'set -euo pipefail; . "{LIB}"; {snippet}'], input=answers,
                            capture_output=True, text=True, timeout=10, env=full_env)
    return result, log.read_text()


def test_project_menu_lists_existing_projects_and_new(tmp_path):
    result, _ = gcp(tmp_path, 'pick_project; echo "PROJECT=$PROJECT"', "2\n")
    assert "  1) alpha-proj" in result.stderr
    assert "  2) jobhq-261104-123" in result.stderr
    assert "  3) Create a new project" in result.stderr
    assert last(result, "PROJECT") == "jobhq-261104-123"


def test_project_default_is_the_current_jobhq_project(tmp_path):
    result, log = gcp(tmp_path, 'pick_project; echo "PROJECT=$PROJECT"', "\n", FAKE_CURRENT="jobhq-261104-123")
    assert last(result, "PROJECT") == "jobhq-261104-123"
    assert "projects create" not in log


def test_project_default_is_an_existing_jobhq_project_on_a_rerun(tmp_path):
    """Found by the installer audit: a re-run that stopped before the project was saved defaulted to a second project."""
    result, log = gcp(tmp_path, 'pick_project; echo "PROJECT=$PROJECT"', "\n", FAKE_CURRENT="alpha-proj")
    assert last(result, "PROJECT") == "jobhq-261104-123" and "projects create" not in log


def test_project_default_is_new_otherwise(tmp_path):
    result, log = gcp(tmp_path, 'pick_project; echo "PROJECT=$PROJECT"', "\nmy-jobs-123\n", FAKE_CURRENT="alpha-proj",
                      FAKE_PROJECTS="alpha-proj\nbeta-proj\n")
    assert last(result, "PROJECT") == "my-jobs-123"
    assert "projects create my-jobs-123" in log


def test_new_project_id_is_checked_and_retried_when_taken(tmp_path):
    result, log = gcp(tmp_path, 'pick_project; echo "PROJECT=$PROJECT"', "3\nBad ID\ntaken-id-123\nfree-id-456\n")
    assert last(result, "PROJECT") == "free-id-456"
    assert "6 to 30 lowercase letters" in result.stderr
    assert "couldn't create" in result.stderr
    assert "projects create free-id-456" in log


def test_billing_already_linked_asks_nothing(tmp_path):
    (tmp_path / "linked").touch()
    result, log = gcp(tmp_path, 'link_billing p1; echo DONE', "")
    assert result.returncode == 0, result.stderr
    assert "billing accounts list" not in log


def test_billing_picks_an_account_and_links_it(tmp_path):
    result, log = gcp(tmp_path, 'link_billing p1; echo DONE', "2\n", FAKE_ACCOUNTS="AAA-111\tTrial account\nBBB-222\tOther\n")
    assert result.returncode == 0, result.stderr
    assert "  1) Trial account (AAA-111)" in result.stderr
    assert "billing projects link p1 --billing-account BBB-222" in log


def test_billing_with_no_account_explains_and_waits(tmp_path):
    # one Enter, but no account appears, so it checks again; then input runs out and it stops instead of looping
    result, _ = gcp(tmp_path, 'link_billing p1; echo DONE', "\n", FAKE_ACCOUNTS="")
    assert result.returncode != 0
    assert "cloud.google.com/free" in result.stderr


def test_project_existing_mode_offers_no_new_project(tmp_path):
    result, _ = gcp(tmp_path, 'pick_project existing; echo "PROJECT=$PROJECT"', "\n", FAKE_CURRENT="alpha-proj")
    assert "Create a new project" not in result.stderr
    assert last(result, "PROJECT") == "alpha-proj"


# pick_address: the free sslip.io address, or your own domain with a DNS check
def address(answers: str, resolves_to: str = "") -> subprocess.CompletedProcess:
    stub = f'resolve_ip() {{ echo "{resolves_to}"; }}'
    return run(f'{stub}; pick_address 35.1.2.3; echo "DOMAIN=$DOMAIN"', answers)


def test_address_default_is_the_free_one_and_names_the_risk():
    result = address("\n")
    assert last(result, "DOMAIN") == "35-1-2-3.sslip.io"
    assert "own risk" in result.stderr


def test_own_domain_shows_the_record_and_checks_it():
    result = address("2\njobs.example.com\n\n", resolves_to="35.1.2.3")
    assert last(result, "DOMAIN") == "jobs.example.com"
    assert "*.jobs.example.com" in result.stderr and "35.1.2.3" in result.stderr
    assert "DNS works" in result.stderr


def test_own_domain_not_resolving_yet_can_fall_back_to_free():
    result = address("2\njobs.example.com\n\n3\n")
    assert last(result, "DOMAIN") == "35-1-2-3.sslip.io"
    assert "doesn't point at this server yet" in result.stderr


def test_own_domain_can_continue_before_dns_works():
    assert last(address("2\njobs.example.com\n\n2\n", resolves_to="9.9.9.9"), "DOMAIN") == "jobs.example.com"


def test_own_domain_name_is_checked():
    result = address("2\nhttps://jobs.example.com\njobs.example.com\n\n", resolves_to="35.1.2.3")
    assert last(result, "DOMAIN") == "jobs.example.com"
    assert "isn't a domain name" in result.stderr


def test_resolve_ip_reads_real_dns():
    assert last(run('echo "IP=$(resolve_ip localhost)"', ""), "IP") in ("127.0.0.1", "::1")


# Found by the installer audit
def test_prompts_stop_at_end_of_input_even_without_set_e():
    result = subprocess.run(["bash", "-c", f'. "{LIB}"; ask_valid N "Name" "" "^x$" "Use x."; echo "rc=$?"; '
                             f'choose C "Pick" 0 "a|A" "b|B"; echo "rc=$?"'], input="", capture_output=True, text=True, timeout=10)
    assert result.stdout.split() == ["rc=1", "rc=1"]


def test_region_name_is_lowercased():
    assert region("4\nEurope-West2\n") == "europe-west2"


def test_timezone_name_must_match_case_exactly():
    result = run('pick_timezone; echo "TZ_NAME=$TZ_NAME"', "8\neurope/london\nEurope/London\n")
    assert last(result, "TZ_NAME") == "Europe/London" and "isn't a time zone" in result.stderr


def test_secret_pasted_twice_or_with_spaces_is_asked_again():
    result = run('read_secret S; echo "S=$S"', "GOCSPX-abc123GOCSPX-abc123\nGOCSPX-abc 123\nGOCSPX-abc123\n")
    assert last(result, "S") == "GOCSPX-abc123"
    assert result.stderr.count("pasted twice") == 1 and result.stderr.count("has a space") == 1


def test_billing_status_error_is_explained(tmp_path):
    result, _ = gcp(tmp_path, 'link_billing p1; echo DONE', "\n", FAKE_BILLING_ERROR="1")
    assert "couldn't check billing" in result.stderr and "console.cloud.google.com/billing" in result.stderr
