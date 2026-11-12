# Running the server

Everything below runs on the VM. Connect from Cloud Shell with `gcloud compute ssh jobhq --zone ZONE --tunnel-through-iap`.

## Everyday commands

| Task | Command |
|---|---|
| See who's set up | `sudo jobsctl list` |
| Health of every copy | `sudo jobsctl status` |
| Follow one copy's logs | `sudo jobsctl logs NAME` |
| Add a person | `sudo jobsctl add-user NAME EMAIL --timezone ZONE` (`--no-voice` turns voice practice off; `--voice` turns it on even on the free server) |
| Remove a person (keeps a backup) | `sudo jobsctl remove-user NAME` (add `--delete-data` to erase) |
| Update to the latest version | `sudo jobsctl update` (backs up first; database changes apply on start) |
| Run a specific release | `sudo jobsctl update --ref v0.2.0`, or `--version 0.2.0` with a prebuilt image |
| Sign a copy in to Claude Code with the person's own plan | `sudo jobsctl claude-login NAME` (`--status`, `--logout`) |
| Move everyone to another server | `sudo jobsctl export-server FILE` on the old one, `sudo jobsctl import-server FILE [--domain D]` on the new one |
| Move from the trial server to the free one | `bash scripts/move-to-free.sh` in Cloud Shell (does the export and import for you) |

## Prebuilt images

By default each server builds the app image itself, which takes 10 to 25 minutes the first time, depending on the server size. Each release also publishes a ready-made image for both chip types to GitHub's container registry, with build provenance. To use it, set it in `/srv/jobhq/vm.env` and update:

```
JOBHQ_IMAGE=ghcr.io/grantkovich/job-search-hq:0.1.0
sudo jobsctl update                 # pulls that image instead of building
sudo jobsctl update --version 0.2.0 # later: move to a newer release
```

You can pin an exact build with `ghcr.io/grantkovich/job-search-hq@sha256:<digest>`; the release log prints it. `install.sh` sets the prebuilt image automatically when `deploy/release.env` names one.

## Backups

- **Nightly:** a backup runs at 2:30 AM (`jobhq-backup.timer`) and keeps 14 per person in `/srv/jobhq/backups`. Each archive holds a consistent copy of the database, the person's PDFs and their settings file.
- **Hear when backups stop (optional):** after each good backup the VM pings the URL in `/etc/jobhq/checkin-url`, if that file exists. Use any dead-man's-switch service (for example healthchecks.io): `echo 'https://…' | sudo tee /etc/jobhq/checkin-url >/dev/null && sudo chmod 600 /etc/jobhq/checkin-url`. Servers set up before this option existed get it by re-running `scripts/bootstrap-vm.sh`.
- **Now:** `sudo jobsctl backup NAME` or `sudo jobsctl backup --all`.
- **Off the server:** copy backups to a Cloud Storage bucket from Cloud Shell, not from the VM, because the VM has no Google Cloud access on purpose:
  ```
  gcloud compute scp --zone ZONE --tunnel-through-iap jobhq:/srv/jobhq/backups/NAME-*.tar.gz .
  ```
  Backups contain the person's encryption secret, so store them somewhere private.
- **Restore:** `sudo jobsctl restore NAME /srv/jobhq/backups/NAME-YYYYMMDD-HHMMSS.tar.gz`. The data it replaces is set aside, not deleted.

## Undo a move

`move-to-free.sh` keeps a snapshot of the old server's disk until you delete it. To go back to the old server, in Cloud Shell:

```
# 1. Remove the new server, which frees the IP address (skip if it was never created)
gcloud compute instances update jobhq --zone ZONE --no-deletion-protection
gcloud compute instances delete jobhq --zone ZONE
# 2. Make a disk from the snapshot the move printed, and a server from that disk with the same IP address
gcloud compute disks create jobhq-restored --zone ZONE --source-snapshot SNAPSHOT --type pd-balanced
gcloud compute instances create jobhq --zone ZONE --machine-type e2-medium --disk name=jobhq-restored,boot=yes \
  --network jobhq-net --address "$(gcloud compute addresses describe jobhq-ip --region REGION --format='value(address)')" \
  --tags jobhq --no-service-account --no-scopes --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
  --metadata enable-oslogin=TRUE,block-project-ssh-keys=TRUE --deletion-protection
```

Everyone's data and settings come back as they were when the move started. The apps start on their own.

**If the move went to a different region,** the old server's disk and IP address were kept as they were, and the new server is `jobhq-free` in the new zone. Skip step 1 and the `disks create` line, and create the server in the old zone from the kept disk the move printed, with `--disk name=OLD-DISK,boot=yes` and the old region in `--region`. Then delete `jobhq-free` when you no longer need it.

## Secrets

- **Each copy's secret:** stored in `/srv/jobhq/instances/NAME.env` (root only). `sudo jobsctl rotate-secret NAME` makes a new one, re-encrypts the stored keys and signs everyone out.
- **Google client secret:** stored in `/srv/jobhq/vm.env`. If it leaks, create a new secret under Google Auth Platform → Clients, run `sudo jobsctl init` again, then delete the old secret.
- **Claude keys:** each person manages theirs in Settings. Revoke a leaked Anthropic key at console.anthropic.com.

## Sizing and scaling

- **Memory:** each copy uses about 150 MB idle. An AI task runs a Claude subprocess, about 200 MB while it works, at most two at a time per copy. Voice transcription loads a 150 MB model the first time it's used.
  - `e2-small` (2 GB) fits about 2 people without voice practice.
  - `e2-medium` (4 GB) fits 4 to 6.
- **Per-copy limits:** set `JOBHQ_MEMORY` and `JOBHQ_CPUS` in `instances/NAME.env`, then run `jobsctl update`.
- **More people:** resize the VM (stop it, Edit → machine type, start it), or run a second VM with its own domain. Each copy is self-contained, so moving one is a backup on the old server and a restore on the new one.
- **Why SQLite:** each copy is one person. SQLite in WAL mode handles that load easily, needs no database server, and makes a backup a single file. A copy is not meant to serve many users; add copies instead.

## Monitoring

- **Container health:** each container has a health check, and `jobsctl status` shows it. Docker restarts a copy that crashes.
- **Spend:** each person's Settings page shows the month's AI spend and the last runs. The monthly budget stops runaway spending.
- **Billing alerts:** set a budget alert in Google Cloud Billing (install guide, step 1).

## Updating the server itself

- **Automatic:** security updates install every day and reboot at 4 AM if needed. Containers start again on their own.
- **Major Debian upgrades:** create a new VM with `install.sh`, move each person with backup and restore, then delete the old VM. That's safer than an in-place upgrade.
