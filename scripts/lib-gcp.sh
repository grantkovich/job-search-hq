# shellcheck shell=bash disable=SC2034  # sets variables for the scripts that source it
# Shared Google Cloud steps for install.sh and move-to-free.sh. Source it; don't run it.
# Every step is safe to repeat: existing pieces are kept.

FREE_REGIONS="us-west1 us-central1 us-east1"   # where Google's always-free e2-micro is free
NET="jobhq-net"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
# ask QUESTION [DEFAULT] prints the answer (Enter gives the default). Fails when input runs out, so a script
# under set -e stops instead of looping on an empty answer.
ask() {
  local prompt="$1" default="${2:-}" reply
  printf '%s%s: ' "$prompt" "${default:+ [$default]}" >&2
  read -r reply || { echo >&2; echo "No answer given; stopping." >&2; return 1; }
  echo "${reply:-$default}"
}

# ask_valid VAR QUESTION DEFAULT REGEX HINT: asks until the answer matches REGEX, then sets VAR
ask_valid() {
  local __var="$1" question="$2" default="$3" regex="$4" hint="$5" reply
  while :; do
    reply=$(ask "$question" "$default") || return 1
    if [[ "$reply" =~ $regex ]]; then printf -v "$__var" '%s' "$reply"; return 0; fi
    echo "$hint" >&2
  done
}

# choose VAR QUESTION DEFAULT_NUMBER "value|label"...: a numbered menu. Takes the number or the value itself,
# and asks again otherwise. DEFAULT_NUMBER 0 means there's no default.
choose() {
  local __var="$1" question="$2" def="$3" reply i n item
  shift 3
  local values=() labels=()
  for item in "$@"; do values+=("${item%%|*}"); labels+=("${item#*|}"); done
  n=${#values[@]}
  for i in "${!values[@]}"; do printf '  %d) %s\n' $((i + 1)) "${labels[$i]}" >&2; done
  while :; do
    if [ "$def" -gt 0 ]; then reply=$(ask "$question" "$def") || return 1; else reply=$(ask "$question") || return 1; fi
    if [[ "$reply" =~ ^[0-9]+$ ]] && [ "$reply" -ge 1 ] && [ "$reply" -le "$n" ]; then
      printf -v "$__var" '%s' "${values[$((reply - 1))]}"; return 0
    fi
    for i in "${!values[@]}"; do
      if [ -n "$reply" ] && [ "$reply" = "${values[$i]}" ]; then printf -v "$__var" '%s' "$reply"; return 0; fi
    done
    echo "Type a number from 1 to $n." >&2
  done
}
# Non-interactive gcloud calls get no input, so they can never swallow answers meant for this script's prompts.
# Returns gcloud's own exit status (the scripts use pipefail; awk always succeeds, unlike grep with no output),
# so a failed remote step stops the calling script.
quiet_gcloud() {
  gcloud "$@" < /dev/null 2>&1 | awk 'tolower($0) !~ /numpy|tcp_upload_bandwidth|^warning:? *$|^$|next: sudo jobsctl/'
}

# size_settings SIZE -> sets MACHINE, DISK_TYPE, JOBHQ_SIZE
size_settings() {
  case "$1" in
    free) MACHINE="e2-micro";  DISK_TYPE="pd-standard"; JOBHQ_SIZE="small" ;;
    *)    MACHINE="e2-medium"; DISK_TYPE="pd-balanced"; JOBHQ_SIZE="" ;;
  esac
}

is_free_region() { case " $FREE_REGIONS " in *" $1 "*) return 0 ;; esac; return 1; }

# pick_region [free] -> sets REGION. The free size only offers the three free-tier regions.
pick_region() {
  local regions=("us-east1|us-east1, South Carolina" "us-central1|us-central1, Iowa" "us-west1|us-west1, Oregon")
  if [ "${1:-}" = "free" ]; then
    echo "Region (the free tier only covers these three):" >&2
    choose REGION "Region" 1 "${regions[@]}"
  else
    echo "Region (pick the one nearest you):" >&2
    choose REGION "Region" 1 "${regions[@]}" "other|Other region (you'll type its name)"
    if [ "$REGION" = "other" ]; then
      while :; do
        REGION=$(ask "Region name, like europe-west2") || return 1
        REGION=$(printf '%s' "$REGION" | tr '[:upper:]' '[:lower:]')
        [[ "$REGION" =~ ^[a-z]+-[a-z]+[0-9]+$ ]] && break
        echo "That isn't a region name. They look like europe-west2; the list is at https://cloud.google.com/compute/docs/regions-zones" >&2
      done
    fi
  fi
}

# (No apostrophes inside "${x:-...}" anywhere in these scripts: macOS's bash 3.2 reads them as opening a quote.)
pause() {
  local prompt="Press Enter when that is done"
  [ -z "${1:-}" ] || prompt="$1"
  ask "$prompt" >/dev/null
}

project_ids() { gcloud projects list --format="value(projectId)" --sort-by=projectId 2>/dev/null < /dev/null || true; }

# pick_project [existing] -> sets PROJECT: one of the account's projects, or a new one it creates.
# With "existing", only the account's projects are offered, and the current one is the default.
pick_project() {
  local mode="${1:-}" current ids=() items=() def i id suggest
  current=$(gcloud config get-value project 2>/dev/null < /dev/null || true)
  while IFS= read -r id; do [ -n "$id" ] && ids+=("$id"); done < <(project_ids)
  if [ "$mode" = "existing" ]; then
    [ "${#ids[@]}" -gt 0 ] || { echo "Your Google account has no projects yet." >&2; return 1; }
    def=1
  else
    def=$(( ${#ids[@]} + 1 ))
  fi
  for i in "${!ids[@]}"; do
    items+=("${ids[$i]}|${ids[$i]}")
    if [ "${ids[$i]}" = "$current" ] && { [ "$mode" = "existing" ] || [[ "$current" == jobhq* ]]; }; then def=$((i + 1)); fi
  done
  # A re-run that stopped before the project was saved still defaults to the jobhq project it made, never a second one.
  if [ "$mode" != "existing" ] && [ "$def" -eq $(( ${#ids[@]} + 1 )) ]; then
    for i in "${!ids[@]}"; do if [[ "${ids[$i]}" == jobhq* ]]; then def=$((i + 1)); break; fi; done
  fi
  if [ "$mode" = "existing" ]; then
    choose PROJECT "Project" "$def" "${items[@]}"
    return 0
  fi
  items+=("new|Create a new project")
  choose PROJECT "Project" "$def" "${items[@]}"
  [ "$PROJECT" = "new" ] || return 0
  suggest="jobhq-$(date +%y%m%d)-$((RANDOM % 900 + 100))"
  while :; do
    ask_valid PROJECT "New project ID (Enter for $suggest)" "$suggest" '^[a-z][a-z0-9-]{4,28}[a-z0-9]$' \
      "Project IDs are 6 to 30 lowercase letters, numbers and dashes, starting with a letter."
    if gcloud projects create "$PROJECT" --name="Job Search HQ" < /dev/null; then return 0; fi
    echo "Google couldn't create $PROJECT (IDs are unique across all of Google Cloud, so it may be taken). Try another." >&2
  done
}

# link_billing PROJECT: makes sure the project has billing, linking one of the account's billing accounts
billing_enabled() { gcloud billing projects describe "$1" --format="value(billingEnabled)" 2>/dev/null < /dev/null || echo error; }
billing_accounts() { gcloud billing accounts list --filter="open=true" --format="value(name.basename(),displayName)" 2>/dev/null < /dev/null || true; }
link_billing() {
  local project="$1" lines=() items=() line account
  local status
  while status=$(billing_enabled "$project"); [ "$status" != "True" ]; do
    lines=(); items=()
    if [ "$status" = "error" ]; then
      echo "Google Cloud couldn't check billing for $project (your account may not have permission). Check it, and link a billing account, here:" >&2
      echo "  https://console.cloud.google.com/billing/linkedaccount?project=$project" >&2
      pause "Press Enter once billing is linked"
      continue
    fi
    while IFS= read -r line; do [ -n "$line" ] && lines+=("$line"); done < <(billing_accounts)
    if [ "${#lines[@]}" -eq 0 ]; then
      echo "Your Google account has no billing account yet. Start the free trial (\$300 of credit for 90 days):" >&2
      echo "  https://cloud.google.com/free" >&2
      pause "Press Enter once the trial is set up"
      continue
    fi
    echo "Billing account for $project (the free trial's credit is in one of these):" >&2
    for line in "${lines[@]}"; do items+=("${line%%$'\t'*}|${line#*$'\t'} (${line%%$'\t'*})"); done
    choose account "Billing account" 1 "${items[@]}"
    if ! line=$(gcloud billing projects link "$project" --billing-account "$account" < /dev/null 2>&1 >/dev/null); then
      echo "That didn't work: $(printf '%s\n' "$line" | grep -m1 -i 'error\|message' | sed 's/^ *//')" >&2
      echo "If it mentions a quota, that billing account has too many projects: delete an old one, or pick another account." >&2
      echo "Or link it in the console instead:" >&2
      echo "  https://console.cloud.google.com/billing/linkedaccount?project=$project" >&2
      pause "Press Enter once billing is linked"
    fi
  done
  echo "Billing is linked." >&2
}

# read_secret VAR: a hidden paste that can't be empty
read_secret() {
  local __var="$1" reply=""
  while [ -z "$reply" ]; do
    printf 'Paste the Client secret (it stays hidden as you paste, then press Enter): ' >&2
    read -r -s reply || { echo >&2; echo "No answer given; stopping." >&2; return 1; }
    echo >&2
    if [ -z "$reply" ]; then
      echo "Nothing was pasted. Copy the Client secret from the box Google showed." >&2
    elif [ "$(printf '%s' "$reply" | grep -o 'GOCSPX-' | wc -l | tr -d ' ')" -gt 1 ]; then
      echo "That looks like the secret pasted twice. Paste it once." >&2; reply=""
    elif [[ "$reply" =~ [[:space:]] ]]; then
      echo "That has a space in it, so it isn't just the secret. Copy only the Client secret and paste it once." >&2; reply=""
    elif [[ "$reply" != GOCSPX-* ]]; then
      echo "(Client secrets usually start with GOCSPX-. Using what you pasted.)" >&2
    fi
  done
  printf -v "$__var" '%s' "$reply"
}

# server_domain VM ZONE: the base address of a server that's already set up (has a Google sign-in client), or nothing
server_domain() {
  gcloud compute ssh "$1" --zone "$2" --tunnel-through-iap --quiet --command \
    "sudo sh -c 'grep -q \"^JOBHQ_GOOGLE_CLIENT_ID=.\" /srv/jobhq/vm.env && sed -n \"s/^JOBHQ_DOMAIN=/domain=/p\" /srv/jobhq/vm.env' 2>/dev/null" \
    < /dev/null 2>/dev/null | sed -n 's/^domain=//p' | head -1 || true
}

# google_signin_guide PROJECT REDIRECT_URI: the console steps for a first Google sign-in client, one screen at a time
google_signin_guide() {
  local project="$1" redirect="$2" email="$3"
  cat >&2 <<EOF
This sets up the "Sign in with Google" button and Gmail reading for your server. It's four short parts in
the Google Cloud console, 15 to 20 minutes the first time. Keep this window open next to the browser; it waits after each part.

Part 1 of 4: register the app
  Open  https://console.cloud.google.com/auth/overview?project=$project
  Choose "Get started" and fill in four screens, choosing Next after each:
    App information       App name: Job Search HQ
                          User support email: pick your email
    Audience              External
    Contact information   your email
    Finish                tick the box agreeing to the Google API Services: User Data Policy,
                          then Continue, then Create
  If you see an overview page with no "Get started" button, this part is already done.
EOF
  pause "Press Enter when Part 1 is done"
  cat >&2 <<EOF

Part 2 of 4: let it read Gmail
  Open  https://console.cloud.google.com/auth/scopes?project=$project
  Choose "Add or remove scopes". A panel opens. Scroll to the bottom of it, to "Manually add scopes",
  and paste these three lines:
      openid
      https://www.googleapis.com/auth/userinfo.email
      https://www.googleapis.com/auth/gmail.readonly
  Choose "Add to table", then "Update". Back on the page, choose "Save".
  (Gmail access is read only: Job Search HQ can't send, delete or label anything.)
EOF
  pause "Press Enter when Part 2 is done"
  cat >&2 <<EOF

Part 3 of 4: say who can use it
  Open  https://console.cloud.google.com/auth/audience?project=$project
  Leave "Publishing status" on "Testing". Under "Test users", choose "Add users", paste
      $email
  and choose "Save". Everyone you add later goes on this list too (up to 100 people).
  Don't skip this: Connect Gmail fails with "Access blocked: ... has not completed the Google
  verification process" for anyone who isn't on the list.
  What "Testing" means: Google ends Gmail access every 7 days. Job Search HQ notices, sends a notification
  and shows "Reconnect Gmail" on Today; reconnecting takes a few seconds.
EOF
  pause "Press Enter when Part 3 is done"
  cat >&2 <<EOF

Part 4 of 4: create the sign-in client
  Open  https://console.cloud.google.com/auth/clients?project=$project
  Choose "Create client", then:
    Application type                Web application
    Name                            Job Search HQ
    Authorized JavaScript origins   leave empty
    Authorized redirect URIs        choose "Add URI" and paste exactly:
                                    $redirect
  Choose "Create". A box shows the Client ID and Client secret.
  Google shows the secret only this once: keep that box open (or choose "Download JSON") until you've
  pasted both below.
EOF
}

# add_person_guide PROJECT REDIRECT_URI EMAIL: let one more person in through the existing sign-in setup
add_person_guide() {
  cat >&2 <<EOF
Google sign-in is already set up on this server. Two small additions let this person in:
  1. Their address. Open  https://console.cloud.google.com/auth/clients?project=$1
     choose the client named Job Search HQ, and under "Authorized redirect URIs" choose "Add URI" and paste:
       $2
     Then choose "Save". Google says it can take a few minutes to start working.
  2. Their Google account. Open  https://console.cloud.google.com/auth/audience?project=$1
     and under "Test users" choose "Add users", paste  $3  and choose "Save".
     (If "Publishing status" says "In production", skip this step.)
EOF
  pause "Press Enter when both are saved"
}

# resolve_ip NAME: the IPv4 address NAME points at, or nothing
resolve_ip() {
  python3 -c 'import socket, sys
try:
    print(socket.gethostbyname(sys.argv[1]))
except OSError:
    pass' "$1" 2>/dev/null || true
}

# pick_address IP -> sets DOMAIN, the base address people get NAME.<DOMAIN> under: the free sslip.io one,
# or the person's own domain once a wildcard DNS record points it at IP
pick_address() {
  local ip="$1" free base got next
  free=$(sslip_domain "$ip")
  echo "Each person's address is their short name in front of a base address. Choose the base:" >&2
  choose ADDRESS_KIND "Address" 1 \
    "free|Free: NAME.$free through the free sslip.io service, at your own risk (see docs/security.md)" \
    "own|Your own domain, about \$10 to \$15 a year, like NAME.jobs.example.com (recommended for long-term use)"
  if [ "$ADDRESS_KIND" = "free" ]; then DOMAIN="$free"; return 0; fi
  ask_valid base "Base domain, like jobs.example.com" "" '^([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$' \
    "That isn't a domain name. Type it in lowercase with no https:// or slashes, like jobs.example.com."
  cat >&2 <<EOF

At the company your domain's DNS is with (where you bought it, or Cloudflare), add one record:
  Type    A
  Name    *.$base
          (most providers want only the part before your domain: for jobs.example.com that's *.jobs)
  Value   $ip
The * covers everyone you add, so this is the only record you'll need.
EOF
  pause "Press Enter once the record is saved"
  while :; do
    got=$(resolve_ip "dns-check.$base")
    if [ "$got" = "$ip" ]; then
      echo "DNS works: dns-check.$base points at this server ($ip)." >&2
      DOMAIN="$base"; return 0
    fi
    echo "dns-check.$base doesn't point at this server yet${got:+ (it points at $got)}. New records can take a few minutes." >&2
    choose next "Next" 1 "wait|Check again" "skip|Continue anyway (HTTPS starts working once DNS does)" \
      "free|Use the free sslip.io address instead"
    case "$next" in
      skip) DOMAIN="$base"; return 0 ;;
      free) DOMAIN="$free"; ADDRESS_KIND="free"; return 0 ;;
      *) pause "Press Enter to check again" ;;
    esac
  done
}

# pick_timezone -> sets TZ_NAME
pick_timezone() {
  local reply
  echo "Time zone (reminders and the morning summary use it):" >&2
  choose TZ_NAME "Time zone" 1 "America/New_York|US Eastern (America/New_York)" "America/Chicago|US Central (America/Chicago)" \
    "America/Denver|US Mountain (America/Denver)" "America/Phoenix|Arizona (America/Phoenix)" \
    "America/Los_Angeles|US Pacific (America/Los_Angeles)" "America/Anchorage|Alaska (America/Anchorage)" \
    "Pacific/Honolulu|Hawaii (Pacific/Honolulu)" \
    "other|Somewhere else (you'll type its name)"
  [ "$TZ_NAME" = "other" ] || return 0
  while :; do
    reply=$(ask "Time zone name, like Europe/London") || return 1
    # find matches names case-sensitively, even on a Mac disk that ignores case; the Linux server is case-sensitive
    if [[ "$reply" =~ ^[A-Za-z_]+(/[A-Za-z0-9_+-]+)+$ ]] && { [ ! -d /usr/share/zoneinfo ] || \
         [ -n "$(find /usr/share/zoneinfo/ -path "/usr/share/zoneinfo/$reply" -print -quit 2>/dev/null)" ]; }; then
      TZ_NAME="$reply"; return 0
    fi
    echo "\"$reply\" isn't a time zone. Names look like Europe/London or America/Toronto." >&2
  done
}

# quietly CMD...: runs a gcloud step and hides its chatter (hints, disk warnings), but shows everything if it fails
quietly() {
  local out
  if out=$("$@" < /dev/null 2>&1); then return 0; fi
  printf '%s\n' "$out" >&2
  return 1
}

# gcp_network: its own network, so the default network's broad rules (SSH and RDP from anywhere) never apply
gcp_network() {
  gcloud compute networks describe "$NET" >/dev/null 2>&1 || quietly gcloud compute networks create "$NET" --subnet-mode auto
  gcloud compute firewall-rules describe "$NET-web" >/dev/null 2>&1 || quietly gcloud compute firewall-rules create "$NET-web" \
    --network "$NET" --direction INGRESS --allow tcp:80,tcp:443,udp:443 --source-ranges 0.0.0.0/0 --target-tags jobhq
  gcloud compute firewall-rules describe "$NET-iap-ssh" >/dev/null 2>&1 || quietly gcloud compute firewall-rules create "$NET-iap-ssh" \
    --network "$NET" --direction INGRESS --allow tcp:22 --source-ranges 35.235.240.0/20 --target-tags jobhq
}

# gcp_ip VM REGION -> sets IP (a static address named VM-ip)
gcp_ip() {
  gcloud compute addresses describe "$1-ip" --region "$2" >/dev/null 2>&1 || quietly gcloud compute addresses create "$1-ip" --region "$2"
  IP=$(gcloud compute addresses describe "$1-ip" --region "$2" --format="value(address)")
}

# gcp_create_vm VM ZONE MACHINE DISK_TYPE IP
gcp_create_vm() {
  local image="debian-12"
  case "$3" in t2a-*|c4a-*) image="debian-12-arm64" ;; esac
  gcloud compute instances describe "$1" --zone "$2" >/dev/null 2>&1 && return 0
  quietly gcloud compute instances create "$1" --zone "$2" --machine-type "$3" \
    --image-family "$image" --image-project debian-cloud --boot-disk-size 30GB --boot-disk-type "$4" \
    --network "$NET" --address "$5" --tags jobhq --no-service-account --no-scopes \
    --shielded-secure-boot --shielded-vtpm --shielded-integrity-monitoring \
    --metadata enable-oslogin=TRUE,block-project-ssh-keys=TRUE --deletion-protection
}

# gcp_wait_ssh VM ZONE
gcp_wait_ssh() {

  for _ in $(seq 1 30); do
    gcloud compute ssh "$1" --zone "$2" --tunnel-through-iap --quiet --command "true" < /dev/null >/dev/null 2>&1 && return 0
    sleep 6
  done
  echo "Couldn't connect to $1 over IAP. Check the jobhq-net-iap-ssh firewall rule."; return 1
}

# gcp_bootstrap VM ZONE REPO_URL REPO_REF JOBHQ_SIZE SUGGESTED_DOMAIN IMAGE   (run from the repository folder)
gcp_bootstrap() {
  local vm="$1" zone="$2" url="$3" ref="$4" size="$5" domain="$6" image="$7" source_arg=""
  quiet_gcloud compute scp scripts/bootstrap-vm.sh "$vm":/tmp/bootstrap-vm.sh --zone "$zone" --tunnel-through-iap --quiet
  if [ "$url" = "local" ]; then
    COPYFILE_DISABLE=1 tar --no-xattrs --exclude=.git --exclude='.e2e-*' --exclude='*.env' --exclude=data --exclude=backups \
      --exclude=__pycache__ -czf /tmp/jobhq-source.tgz . 2>/dev/null || \
      tar --exclude=.git --exclude='.e2e-*' --exclude='*.env' --exclude=data --exclude=backups --exclude=__pycache__ -czf /tmp/jobhq-source.tgz .
    quiet_gcloud compute scp /tmp/jobhq-source.tgz "$vm":/tmp/jobhq-source.tgz --zone "$zone" --tunnel-through-iap --quiet
    rm -f /tmp/jobhq-source.tgz
    source_arg="JOBHQ_SOURCE_TARBALL=/tmp/jobhq-source.tgz"
  fi
  quiet_gcloud compute ssh "$vm" --zone "$zone" --tunnel-through-iap --quiet --command \
    "sudo JOBHQ_REPO_URL='$url' JOBHQ_REPO_REF='$ref' JOBHQ_SIZE='$size' JOBHQ_SUGGESTED_DOMAIN='$domain' JOBHQ_IMAGE='$image' $source_arg bash /tmp/bootstrap-vm.sh"
}

sslip_domain() { echo "$(echo "$1" | tr . -).sslip.io"; }
