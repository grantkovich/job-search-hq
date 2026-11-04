#!/usr/bin/env bash
# Create a Job Search HQ server on Google Cloud. Run it in Cloud Shell (shell.cloud.google.com),
# which already has gcloud and is signed in, so it works from a Chromebook or any browser:
#
#   git clone https://github.com/grantkovich/job-search-hq.git && cd job-search-hq && bash scripts/install.sh
#
# Two sizes:
#   trial        e2-medium. Comfortable, voice practice on. About $25 a month, which a new account's
#                $300 trial credit covers for the 90-day trial.
#   forever free e2-micro on Google's always-free tier (us-west1, us-central1 or us-east1 only).
#                Voice practice off. Needs a paid (upgraded) billing account to stay free.
# Start on trial and move with scripts/move-to-free.sh before the trial ends, or start free.
#
# What it creates in the project you pick: its own network with two firewall rules (web from anywhere;
# SSH only through Google's IAP tunnel), a static IP, and a Debian 12 VM with Shielded VM, OS Login,
# no service account and deletion protection. Each person gets NAME.<your domain>, or, to try it with no
# domain, a free NAME.<ip>.sslip.io address; both get real HTTPS certificates. Re-running is safe, and it's
# also how you add another person to an existing server.
set -euo pipefail
cd "$(dirname "$0")/.."
. scripts/lib-gcp.sh

REPO_URL="${JOBHQ_REPO_URL:-$(git config --get remote.origin.url 2>/dev/null || true)}"
REPO_REF="${JOBHQ_REPO_REF:-main}"
IMAGE="${JOBHQ_IMAGE:-$(sed -n 's/^JOBHQ_IMAGE=//p' deploy/release.env 2>/dev/null | head -1)}"

command -v gcloud >/dev/null || { echo "gcloud isn't installed. Run this in Cloud Shell: https://shell.cloud.google.com"; exit 1; }
[ -n "$REPO_URL" ] || REPO_URL=$(ask "Git URL of the Job Search HQ repository (or 'local' to upload this folder)")
case "$REPO_URL" in https://*|local) ;; *) echo "Use the https:// clone URL of the repository, or 'local'."; exit 1 ;; esac

say "1/7  Google Cloud project"
echo "Job Search HQ goes in its own Google Cloud project. Pick one of yours, or make a new one (recommended)."
pick_project
gcloud config set project "$PROJECT" >/dev/null 2>&1   # saved now, so a re-run offers this project again
link_billing "$PROJECT"
echo "Turning on the Google services it needs (about a minute)..."
gcloud services enable compute.googleapis.com iap.googleapis.com gmail.googleapis.com < /dev/null >/dev/null

say "2/7  Size and place"
echo "Server size:"
choose SIZE "Size" 1 \
  "trial|Trial: e2-medium, about \$25 a month, which the \$300 trial credit covers. Voice practice on." \
  "free|Forever free: e2-micro on Google's free tier. Voice practice off. Needs an upgraded billing account."
size_settings "$SIZE"
if [ "$SIZE" = "free" ]; then pick_region free; else pick_region; fi
until gcloud compute regions describe "$REGION" --format="value(name)" < /dev/null >/dev/null 2>&1; do
  echo "Google Cloud has no region called $REGION (or your project can't use it). Pick again."
  if [ "$SIZE" = "free" ]; then pick_region free; else pick_region; fi
done
ZONE="${JOBHQ_ZONE:-${REGION}-b}"
VM="${JOBHQ_VM:-jobhq}"
echo "The server will be called $VM, in zone $ZONE."

say "3/7  Network, static IP and firewall"
gcp_network
echo "Network and firewall are ready."
gcp_ip "$VM" "$REGION"
echo "Its address is $IP."

say "4/7  The server ($MACHINE)"
gcp_create_vm "$VM" "$ZONE" "$MACHINE" "$DISK_TYPE" "$IP"
echo "The server is running."

say "5/7  Installing Docker on the server (a few minutes)"
gcp_wait_ssh "$VM" "$ZONE"
gcp_bootstrap "$VM" "$ZONE" "$REPO_URL" "$REPO_REF" "$JOBHQ_SIZE" "$(sslip_domain "$IP")" "$IMAGE"

say "6/7  Web address, the first person and Google sign-in"
EXISTING_DOMAIN=$(server_domain "$VM" "$ZONE")
if [ -n "$EXISTING_DOMAIN" ]; then
  DOMAIN="$EXISTING_DOMAIN"
  echo "This server is already set up, with addresses under $DOMAIN. (To change that, see docs/install.md, step 7.)"
else
  pick_address "$IP"
fi
echo
echo "Each person gets their own private copy at https://NAME.$DOMAIN, with real HTTPS."
echo "Start with the person who will use it: you, or the person you're setting this up for."
ask_valid NAME "Short name, lowercase, like pat (it becomes the address), or Enter to finish later" "" '^$|^[a-z][a-z0-9-]{1,20}$' \
  "Use 2 to 21 lowercase letters, numbers or dashes, starting with a letter, like pat or sam-lee."
on_server() { gcloud compute ssh "$VM" --zone "$ZONE" --tunnel-through-iap --quiet "$@"; }
retry_line="cd job-search-hq && git pull && bash scripts/install.sh"
if [ -n "$NAME" ]; then
  ask_valid EMAIL "Their Google account email (only this account can sign in)" "" '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' \
    "That doesn't look like an email address."
  pick_timezone
  REDIRECT="https://$NAME.$DOMAIN/auth/callback"
  echo
  if [ -n "$EXISTING_DOMAIN" ]; then
    add_person_guide "$PROJECT" "$REDIRECT" "$EMAIL"
  else
    google_signin_guide "$PROJECT" "$REDIRECT" "$EMAIL"
    ask_valid CLIENT_ID "Paste the Client ID" "" '^[0-9]+-[a-z0-9]+\.apps\.googleusercontent\.com$' \
      "Client IDs look like 1234567890-abc123.apps.googleusercontent.com. Copy it from the box Google showed."
    read_secret CLIENT_SECRET
    echo "Saving the sign-in settings on the server..."
    if ! out=$(printf '%s\n\n%s\n%s\n' "$DOMAIN" "$CLIENT_ID" "$CLIENT_SECRET" | on_server --command "sudo jobsctl init" 2>&1); then
      unset CLIENT_SECRET
      printf '%s\n' "$out" | grep -v -i 'numpy' | grep -v '^$' | tail -5
      cat <<MSG

The server couldn't save the sign-in settings (the lines above say why). Nothing is lost.
Run the setup again with:  $retry_line
If Google's box with the Client secret is closed, make a new secret first: on the Clients page, open
Job Search HQ, choose "Add secret", and paste the new one when asked.
MSG
      exit 1
    fi
    unset CLIENT_SECRET
    echo "Saved."
  fi
  echo
  echo "Building and starting $NAME's copy. The first time, this takes 10 to 25 minutes, and the screen won't"
  echo "change while it works. That's normal: don't press anything until it says it's done."
  # %q quotes each value for the server's shell, so an email like o'brien@example.com can't break the command
  person_cmd="sudo jobsctl add-user $(printf '%q' "$NAME") $(printf '%q' "$EMAIL") --timezone $(printf '%q' "$TZ_NAME")"
  if out=$(on_server --command "$person_cmd" < /dev/null 2>&1); then
    printf '%s\n' "$out" | grep -E "^Added" || true
  elif printf '%s' "$out" | grep -q "already exists"; then
    echo "$NAME already has a copy on this server, so there's nothing to add."
  else
    printf '%s\n' "$out" | grep -v -i 'numpy' | grep -v '^$' | tail -5
    echo
    echo "Adding $NAME didn't finish (the lines above say why). Run the setup again with:  $retry_line"
    exit 1
  fi

  # Anything typed during the long wait would otherwise answer the next question.
  if [ -t 0 ]; then while read -r -t 1 _typed_ahead; do :; done; fi

  say "7/7  Claude"
  echo "How should $NAME's copy reach Claude?"
  choose CLAUDE "Claude" 1 \
    "plan|Their own Claude plan (Pro is \$20 a month): sign in to Claude Code now" \
    "key|An Anthropic API key, pay per use: paste it in the app's Settings later"
  claude_ok=1
  if [ "$CLAUDE" = "plan" ]; then
    cat <<MSG
Claude Code, Anthropic's own program, will show a link. Open it, sign in to $NAME's own Claude account, and
paste the code it gives you back here. Job Search HQ never asks for, reads or stores that sign-in.
MSG
    on_server -- -t "sudo jobsctl claude-login $(printf '%q' "$NAME")" \
      2> >(grep -v -i -e numpy -e tcp_upload_bandwidth -e '^warning: *$' >&2) || claude_ok=0
  fi
  echo
  echo "Done. Next:"
  echo "  1. Open https://$NAME.$DOMAIN and choose \"Sign in with Google\", using $EMAIL."
  echo "  2. Work down the Setup page. docs/user-guide.md explains each part of the app."
  if [ "$CLAUDE" = "plan" ] && [ "$claude_ok" = 1 ]; then echo "  3. In Settings, choose \"My own Claude plan\", Save, then Test Claude."; fi
  if [ "$CLAUDE" = "plan" ] && [ "$claude_ok" = 0 ]; then
    echo "  3. The Claude sign-in didn't finish. Do it later: connect with"
    echo "       gcloud compute ssh $VM --zone $ZONE --tunnel-through-iap"
    echo "     then run  sudo jobsctl claude-login $NAME  and, in Settings, choose \"My own Claude plan\" and Save."
  fi
  if [ "$CLAUDE" = "key" ]; then echo "  3. In Settings, paste the API key, set a monthly budget, Save, then Test Claude."; fi
elif [ -n "$EXISTING_DOMAIN" ]; then
  echo "Nobody added this time. To add someone later, run the setup again:  $retry_line"
else
  echo "Finish later: run the setup again with  $retry_line"
  echo "and give the same project, size and region. (Or by hand: docs/install.md, step 3, \"Finishing later instead\".)"
fi
echo
echo "Server: $VM ($MACHINE) at $IP in $ZONE. Cost check: https://console.cloud.google.com/billing?project=$PROJECT"
if [ "$SIZE" = "trial" ]; then echo "Before the 90-day trial ends, move to the free server with: bash scripts/move-to-free.sh"; fi
exit 0
