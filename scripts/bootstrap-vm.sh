#!/usr/bin/env bash
# Prepare a fresh Debian 12 VM for Job Search HQ. install.sh runs this over IAP SSH; you can also
# run it yourself on any Debian or Ubuntu server:  sudo JOBHQ_REPO_URL=https://... bash bootstrap-vm.sh
#
# Installs Docker from Docker's own apt repository, turns on automatic security updates, blocks
# containers from reaching the cloud metadata server, clones the app to /srv/jobhq/repo, installs
# jobsctl, and schedules a nightly backup. Safe to re-run.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run with sudo."; exit 1; }
REPO_URL="${JOBHQ_REPO_URL:?set JOBHQ_REPO_URL (or 'local' with JOBHQ_SOURCE_TARBALL)}"
SOURCE_TARBALL="${JOBHQ_SOURCE_TARBALL:-}"
REPO_REF="${JOBHQ_REPO_REF:-main}"
SIZE="${JOBHQ_SIZE:-}"
SUGGESTED_DOMAIN="${JOBHQ_SUGGESTED_DOMAIN:-}"
IMAGE="${JOBHQ_IMAGE:-}"
ROOT=/srv/jobhq
export DEBIAN_FRONTEND=noninteractive

apt-get update -q
apt-get install -y -q ca-certificates curl gnupg git unattended-upgrades python3 iptables-persistent

# Docker Engine and the Compose plugin, signed by Docker's key.
. /etc/os-release
install -m 0755 -d /etc/apt/keyrings
if [ ! -s /etc/apt/keyrings/docker.asc ]; then
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
fi
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/${ID} ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Docker daemon: rotate logs, no inter-container traffic on the default bridge, no new privileges by default.
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"},
  "icc": false,
  "no-new-privileges": true,
  "live-restore": true
}
JSON
systemctl enable --now docker
systemctl restart docker

# Containers must never reach the metadata server (a server-side request forgery would otherwise
# be one step from the VM's identity). The VM has no service account either; this is a second lock.
# On Google Cloud the same address is also the VM's DNS server, so DNS (port 53) stays open and
# everything else to it is dropped. Rules are inserted at the top, so the DROP goes in first.
iptables -C DOCKER-USER -d 169.254.169.254 -j DROP 2>/dev/null || iptables -I DOCKER-USER -d 169.254.169.254 -j DROP
for proto in udp tcp; do
  iptables -C DOCKER-USER -d 169.254.169.254 -p "$proto" --dport 53 -j ACCEPT 2>/dev/null \
    || iptables -I DOCKER-USER -d 169.254.169.254 -p "$proto" --dport 53 -j ACCEPT
done
netfilter-persistent save >/dev/null 2>&1 || true

# Automatic security updates, with a reboot at 4 AM when one is needed.
cat > /etc/apt/apt.conf.d/52jobhq-upgrades <<'CONF'
Unattended-Upgrade::Automatic-Reboot "true";
Unattended-Upgrade::Automatic-Reboot-Time "04:00";
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
CONF

# Swap on small servers (the free e2-micro has 1 GB of memory), so a busy moment slows down instead of failing.
MEM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
if [ "$MEM_KB" -lt 2600000 ] && ! swapon --show | grep -q /swapfile; then
  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile >/dev/null && swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
  sysctl -q vm.swappiness=10 && echo 'vm.swappiness=10' > /etc/sysctl.d/90-jobhq.conf
fi

# The app.
mkdir -p "$ROOT"
chmod 700 "$ROOT"
# What install.sh knew, for `jobsctl init` to offer: server size, the free sslip.io address, a prebuilt image.
umask 077
printf 'JOBHQ_SIZE=%s\nJOBHQ_SUGGESTED_DOMAIN=%s\nJOBHQ_IMAGE=%s\n' "$SIZE" "$SUGGESTED_DOMAIN" "$IMAGE" > "$ROOT/install.env"
umask 022
if [ -n "$SOURCE_TARBALL" ]; then
  rm -rf "$ROOT/repo.new" && mkdir -p "$ROOT/repo.new"
  tar -xzf "$SOURCE_TARBALL" -C "$ROOT/repo.new" --no-same-owner
  rm -rf "$ROOT/repo" && mv "$ROOT/repo.new" "$ROOT/repo" && rm -f "$SOURCE_TARBALL"
elif [ -d "$ROOT/repo/.git" ]; then
  if ! { git -C "$ROOT/repo" fetch -q origin && git -C "$ROOT/repo" checkout -q "$REPO_REF" && git -C "$ROOT/repo" pull -q --ff-only; }; then
    echo "Warning: couldn't update the app's code from $REPO_URL; keeping the copy already on this server." >&2
  fi
else
  # Clone beside it, then swap: a server first installed from uploaded files has a repo/ folder with no .git.
  rm -rf "$ROOT/repo.new"
  git clone -q --branch "$REPO_REF" "$REPO_URL" "$ROOT/repo.new"
  rm -rf "$ROOT/repo" && mv "$ROOT/repo.new" "$ROOT/repo"
fi
install -m 0755 "$ROOT/repo/scripts/jobsctl" /usr/local/bin/jobsctl

# Nightly backups at 2:30 AM, kept 14 per person in /srv/jobhq/backups (root-only: each holds its copy's key).
mkdir -p "$ROOT/backups" && chmod 700 "$ROOT/backups"
cat > /etc/systemd/system/jobhq-backup.service <<'UNIT'
[Unit]
Description=Back up every Job Search HQ copy
After=docker.service
[Service]
Type=oneshot
UMask=0077
ExecStart=/usr/local/bin/jobsctl backup --all --quiet
# Optional: after a good backup, ping a check-in URL (a dead-man's switch such as healthchecks.io)
# so you hear when backups stop. Put the URL in /etc/jobhq/checkin-url (root-only) to turn it on.
ExecStartPost=-/bin/sh -c 'f=/etc/jobhq/checkin-url; [ -s "$f" ] && curl -fsS -m 10 -X POST "$(cat "$f")" >/dev/null'
UNIT
cat > /etc/systemd/system/jobhq-backup.timer <<'UNIT'
[Unit]
Description=Nightly Job Search HQ backup
[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now jobhq-backup.timer

echo
echo "Bootstrap finished. Next: sudo jobsctl init"
