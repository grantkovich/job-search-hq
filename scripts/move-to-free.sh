#!/usr/bin/env bash
# Move a Job Search HQ server to Google's always-free e2-micro before the 90-day trial ends.
# Run it in Cloud Shell from the repository folder:  bash scripts/move-to-free.sh
#
# Before you start: upgrade the billing account to a paid account (Billing -> Overview -> Upgrade), or Google
# stops everything when the trial ends. The free tier needs a paid account; small charges may still apply
# (see docs/cheap.md).
#
# What it does:
#   1. backs up everyone on the current server and copies the backup here (it holds secrets; it's deleted at the end)
#   2. deletes the current server but KEEPS its disk, so you can go back if anything is wrong
#   3. creates the free e2-micro with the same IP address when it's in a free region (addresses stay the same)
#   4. installs the app, restores everyone, and checks they're running
set -Eeuo pipefail
cd "$(dirname "$0")/.."
. scripts/lib-gcp.sh

REPO_URL="${JOBHQ_REPO_URL:-$(git config --get remote.origin.url 2>/dev/null || true)}"
[ -n "$REPO_URL" ] || REPO_URL="local"
REPO_REF="${JOBHQ_REPO_REF:-main}"
IMAGE="${JOBHQ_IMAGE:-$(sed -n 's/^JOBHQ_IMAGE=//p' deploy/release.env 2>/dev/null | head -1)}"

instance_rows() { gcloud compute instances list --format="value(name,zone.basename(),machineType.basename())" < /dev/null; }

say "1/6  Which server"
echo "The project your Job Search HQ server is in:"
pick_project existing
gcloud config set project "$PROJECT" >/dev/null
SERVERS=()
while read -r name zone machine; do [ -n "$name" ] && SERVERS+=("$name $zone|$name ($machine, in $zone)"); done < <(instance_rows)
[ "${#SERVERS[@]}" -gt 0 ] || { echo "There are no servers in $PROJECT."; exit 1; }
echo "The server to move:"
choose PICKED "Server" 1 "${SERVERS[@]}"
OLD="${PICKED%% *}"; OLD_ZONE="${PICKED#* }"
OLD_REGION="${OLD_ZONE%-*}"
echo
echo "The free tier needs a paid (upgraded) billing account, or Google stops everything when the trial ends."
echo "Check or upgrade it here:  https://console.cloud.google.com/billing"
choose UPGRADED "Has it been upgraded" 0 "yes|Yes, it's upgraded" "no|Not yet"
[ "$UPGRADED" = "yes" ] || { echo "Upgrade first (Billing -> Overview -> Upgrade), then run this again."; exit 1; }

if is_free_region "$OLD_REGION"; then
  REGION="$OLD_REGION"; ZONE="$OLD_ZONE"; SAME_IP=1
  echo "$OLD is in $REGION, a free region, so the IP address and everyone's addresses stay the same."
else
  echo "$OLD is in $OLD_REGION, which the free tier doesn't cover, so the new server gets a new IP address."
  echo "With the free sslip.io addresses, each person's address changes and you'll add new redirect URIs to the"
  echo "Google sign-in client. With your own domain, addresses stay the same and you'll update one DNS record."
  pick_region free
  ZONE="${JOBHQ_ZONE:-${REGION}-b}"; SAME_IP=0
fi
NEW="$OLD"; [ "$SAME_IP" = 1 ] || NEW="${JOBHQ_VM:-$OLD-free}"
[ "$SAME_IP" = 1 ] || echo "The new server will be called $NEW, in zone $ZONE."
KEPT_DISK="(not made yet)"; DOMAIN_ARG=""

recovery() {
  echo
  echo "Something went wrong, so the script stopped. Your data is safe:"
  echo "  - the backup here in Cloud Shell: $LOCAL"
  echo "  - a snapshot of the old server's disk: $KEPT_DISK"
  echo "To try the restore again on the new server:"
  echo "  gcloud compute scp \"$LOCAL\" $NEW:/tmp/jobhq-move.tgz --zone $ZONE --tunnel-through-iap"
  echo "  gcloud compute ssh $NEW --zone $ZONE --tunnel-through-iap --command \"sudo jobsctl import-server /tmp/jobhq-move.tgz $DOMAIN_ARG\""
  echo "Or bring the old server back from the snapshot: see docs/operations.md, \"Undo a move\"."
}

backup_failed() {
  gcloud compute ssh "$OLD" --zone "$OLD_ZONE" --tunnel-through-iap --quiet --command "sudo rm -f /tmp/jobhq-move.tgz" < /dev/null >/dev/null 2>&1 || true
  rm -f "$LOCAL"
  echo
  echo "The backup step failed, so nothing was changed: the old server is untouched. Fix the problem above and run this again."
}
trap 'backup_failed' ERR
say "2/6  Backing up everyone"
STAMP=$(date +%Y%m%d-%H%M%S)
LOCAL="$HOME/jobhq-move-$STAMP.tgz"
: > "$LOCAL" && chmod 600 "$LOCAL"  # private before anything is written to it
quiet_gcloud compute ssh "$OLD" --zone "$OLD_ZONE" --tunnel-through-iap --quiet --command \
  "sudo jobsctl export-server /tmp/jobhq-move.tgz && sudo chown \$(id -u) /tmp/jobhq-move.tgz"
quiet_gcloud compute scp "$OLD":/tmp/jobhq-move.tgz "$LOCAL" --zone "$OLD_ZONE" --tunnel-through-iap --quiet
chmod 600 "$LOCAL"
quiet_gcloud compute ssh "$OLD" --zone "$OLD_ZONE" --tunnel-through-iap --quiet --command "sudo shred -u /tmp/jobhq-move.tgz"
[ -s "$LOCAL" ] || { echo "The backup didn't arrive. Nothing was changed."; exit 1; }
echo "Backup saved here as $LOCAL"
OLD_DOMAIN=$(tar -xzOf "$LOCAL" vm.env 2>/dev/null | sed -n 's/^JOBHQ_DOMAIN=//p' | head -1 || true)

trap 'recovery' ERR
say "3/6  Retiring the old server (its disk is kept)"
OLD_DISK=$(gcloud compute instances describe "$OLD" --zone "$OLD_ZONE" --format="value(disks[0].source.basename())")
gcloud compute instances update "$OLD" --zone "$OLD_ZONE" --no-deletion-protection --quiet
gcloud compute instances set-disk-auto-delete "$OLD" --zone "$OLD_ZONE" --disk "$OLD_DISK" --no-auto-delete --quiet
gcloud compute instances delete "$OLD" --zone "$OLD_ZONE" --quiet
KEPT_DISK="$OLD_DISK"
if [ "$SAME_IP" = 1 ]; then
  # The new server's boot disk takes the server's name, so give the kept disk another one.
  KEPT_DISK="$OLD_DISK-before-move-$STAMP"
  gcloud compute disks snapshot "$OLD_DISK" --zone "$OLD_ZONE" --snapshot-names "$KEPT_DISK" --quiet >/dev/null
  gcloud compute disks delete "$OLD_DISK" --zone "$OLD_ZONE" --quiet >/dev/null
  echo "Kept a snapshot of the old disk: $KEPT_DISK (snapshot)"
fi

say "4/6  Creating the free server"
size_settings free
gcp_network
gcp_ip "$NEW" "$REGION"
gcp_create_vm "$NEW" "$ZONE" "$MACHINE" "$DISK_TYPE" "$IP"
gcp_wait_ssh "$NEW" "$ZONE"
DOMAIN=$(sslip_domain "$IP")
gcp_bootstrap "$NEW" "$ZONE" "$REPO_URL" "$REPO_REF" "$JOBHQ_SIZE" "$DOMAIN" "$IMAGE"

say "5/6  Restoring everyone"
quiet_gcloud compute scp "$LOCAL" "$NEW":/tmp/jobhq-move.tgz --zone "$ZONE" --tunnel-through-iap --quiet
# A new IP means new sslip.io addresses; your own domain stays, and its DNS record moves to the new IP.
if [ "$SAME_IP" = 0 ] && [[ "$OLD_DOMAIN" == *.sslip.io || -z "$OLD_DOMAIN" ]]; then DOMAIN_ARG="--domain $DOMAIN"; fi
quiet_gcloud compute ssh "$NEW" --zone "$ZONE" --tunnel-through-iap --quiet --command "sudo jobsctl import-server /tmp/jobhq-move.tgz $DOMAIN_ARG"
EXPECTED=$(tar -tzf "$LOCAL" | grep -c '^instances/.*\.env$')
STATUS=""
for _ in $(seq 1 30); do
  STATUS=$(quiet_gcloud compute ssh "$NEW" --zone "$ZONE" --tunnel-through-iap --quiet --command "sudo jobsctl status" || true)
  [ "$(printf '%s\n' "$STATUS" | grep -c '^OK: ')" -ge "$EXPECTED" ] && break
  sleep 10
done
printf '%s\n' "$STATUS" | grep '^OK: ' || true
if [ "$(printf '%s\n' "$STATUS" | grep -c '^OK: ')" -lt "$EXPECTED" ]; then
  echo "Not everyone came back up ($EXPECTED expected)."
  false
fi
trap - ERR
quiet_gcloud compute ssh "$NEW" --zone "$ZONE" --tunnel-through-iap --quiet --command "sudo shred -u /tmp/jobhq-move.tgz" || true

say "6/6  Done"
shred -u "$LOCAL" 2>/dev/null || rm -f "$LOCAL"
echo "Everyone ($EXPECTED) is on the free server $NEW (e2-micro) at $IP. Open each person's address and check their data."
echo "Voice practice is off on the small server."
if [ "$SAME_IP" = 0 ] && [ -n "$OLD_DOMAIN" ] && [[ "$OLD_DOMAIN" != *.sslip.io ]]; then
  echo
  echo "Your own domain stays: at your DNS provider, change the A record for *.$OLD_DOMAIN to $IP."
  echo "HTTPS and sign-in work again once that change is live (usually a few minutes)."
fi
echo
echo "When you're sure everything is there, delete what was kept from the old server:"
if [ "$SAME_IP" = 1 ]; then
  echo "    gcloud compute snapshots delete $KEPT_DISK"
else
  echo "    gcloud compute disks delete $KEPT_DISK --zone $OLD_ZONE"
  echo "    gcloud compute addresses delete $OLD-ip --region $OLD_REGION"
fi
