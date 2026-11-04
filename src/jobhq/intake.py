"""Where jobs and job news come from: Gmail (read only), public job boards, pasted postings.

Gmail: the app asks Google for read-only access, keeps the refresh token encrypted, and
reads only messages matching job alert senders or job-related searches. It never sends,
labels or deletes.

Inbox rules (decided by code, not the AI):
- A message from LinkedIn/Indeed/etc. job alerts adds new roles to "saved".
- An employer message updates a role only when the sender's registered domain is the
  company's whole name (or a domain recorded for it), or a contract hiring system whose
  mail names the company, and only in ways listed in EMAIL_EFFECTS. Tools anyone can
  sign up for (Calendly, HackerRank, Google Forms...) always go to review.
- It never marks anything as hired or accepted, and never reopens a closed role.
- Everything else goes to the review list with no change.
Every change is logged with Undo.

Public boards: Greenhouse, Lever and Ashby publish open JSON APIs for each company's
board. No scraping anywhere.
"""
from __future__ import annotations

import base64
import html
import ipaddress
import logging
import re
import socket
import time
from datetime import date
from email.utils import parseaddr
from urllib.parse import urlsplit

import httpx

from .context import Ctx
from .db import dumps
from .roles import CLOSED, RoleError

log = logging.getLogger("jobhq.intake")
GMAIL = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105
ALERT_SENDERS = {
    "jobalerts-noreply@linkedin.com": "linkedin", "jobs-noreply@linkedin.com": "linkedin",
    "alert@indeed.com": "indeed", "noreply@indeed.com": "indeed", "donotreply@match.indeed.com": "indeed",
}
# Hiring systems sold to employers under contract. Their mail is trusted only when the company's
# name is also in the sender's display name or the subject, since many companies share them.
HIRING_SYSTEMS = ("greenhouse.io", "greenhouse-mail.io", "lever.co", "ashbyhq.com", "myworkday.com", "workday.com", "icims.com",
                  "smartrecruiters.com", "jobvite.com", "successfactors.com", "successfactors.eu", "taleo.net", "rippling.com",
                  "hirevue.com", "goodtime.io", "modernhire.com")
# Tools anyone can sign up for in minutes, so their mail proves nothing about who sent it: always review.
OPEN_SIGNUP = ("calendly.com", "hackerrank.com", "codesignal.com", "testgorilla.com", "workable.com", "workablemail.com", "breezy.hr",
               "jazzhr.com", "recruitee.com", "teamtailor.com", "bamboohr.com", "typeform.com", "jotform.com", "googlegroups.com",
               "zoom.us", "docusign.net", "mailchimp.com", "sendgrid.net", "hubspotemail.net")
OPEN_SIGNUP_ADDRESSES = ("forms-receipts-noreply@google.com", "drive-shares-noreply@google.com", "drive-shares-dm-noreply@google.com",
                         "calendar-notification@google.com", "noreply-forms@google.com")
# Sites that list other companies' jobs: a role's link on one of these says nothing about the company's own domain.
JOB_SITES = ("linkedin.com", "lnkd.in", "indeed.com", "glassdoor.com", "ziprecruiter.com", "wellfound.com", "angel.co", "builtin.com",
             "dice.com", "monster.com", "simplyhired.com", "jobgether.com", "swooped.co", "google.com", "otta.com", "welcometothejungle.com")
# Personal mail services: a contact's address there says nothing about the company's domain.
FREE_MAIL = ("gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com", "msn.com", "yahoo.com", "icloud.com", "me.com",
             "aol.com", "proton.me", "protonmail.com", "gmx.com", "mail.com", "yandex.com", "fastmail.com")
# Public suffixes with two parts, so "acme.co.uk" counts as Acme's own domain, not "co.uk".
TWO_PART_SUFFIXES = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "com.au", "net.au", "org.au", "edu.au", "gov.au", "co.nz",
    "org.nz", "co.jp", "ne.jp", "or.jp", "co.in", "net.in", "org.in", "com.br", "com.mx", "com.sg", "com.hk", "co.za", "com.tr",
    "com.cn", "co.kr", "co.il", "com.ar", "com.co", "co.id", "com.my", "com.ph", "com.tw", "com.pl", "com.es", "co.th", "com.vn",
}
_CORP_WORDS = {"inc", "incorporated", "llc", "llp", "lp", "ltd", "limited", "corp", "corporation", "co", "company", "plc", "gmbh",
               "ag", "sa", "bv", "nv", "the", "group", "holdings", "holding"}
_GENERIC_WORDS = {"health", "healthcare", "technologies", "technology", "tech", "labs", "software", "systems", "solutions",
                  "services", "global", "international", "medicine", "medical", "ai", "hq", "and"}
# What an employer email may do to a role, by kind. Anything not listed only goes to review.
EMAIL_EFFECTS = {
    "application_received": {"from": ("saved",), "status": "applied"},
    "rejection": {"from": ("saved", "applied", "interviewing"), "status": "rejected"},
    "interview_request": {"from": ("saved", "applied", "interviewing"), "status": "interviewing", "next_step": "Reply to schedule the interview"},
    "interview_scheduled": {"from": ("saved", "applied", "interviewing"), "status": "interviewing", "event": "interview"},
    "assessment": {"from": ("saved", "applied", "interviewing"), "next_step": "Take the assessment", "event": "deadline"},
    "offer": {"from": ("applied", "interviewing"), "status": "offer", "next_step": "Review the offer"},
    "needs_reply": {"from": ("saved", "applied", "interviewing", "offer"), "next_step": "Reply to the email"},
}
SEARCH = ('(from:(' + " OR ".join(ALERT_SENDERS) + ')) OR (subject:(application OR interview OR assessment OR offer OR "next steps" '
          'OR candidacy OR "thank you for applying") -category:promotions)')


class GmailError(Exception):
    pass


# ------------------------------------------------------------------ Gmail

class Gmail:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self._token: tuple[str, float] | None = None

    def connected(self) -> bool:
        return self.ctx.settings.has("gmail_refresh_token")

    def _access_token(self) -> str:
        if self._token and self._token[1] > time.time() + 60:
            return self._token[0]
        refresh = self.ctx.settings.get("gmail_refresh_token")
        if not refresh:
            raise GmailError("Gmail isn't connected.")
        response = self.ctx.http.post(TOKEN_URL, data={"client_id": self.ctx.config.google_client_id,
                                                       "client_secret": self.ctx.config.google_client_secret,
                                                       "refresh_token": refresh, "grant_type": "refresh_token"})
        if response.status_code == 400 and "invalid_grant" in response.text:
            self.ctx.settings.delete("gmail_refresh_token")
            self.ctx.settings.set("gmail_status", "Google ended Gmail access, so new job alerts and emails aren't coming in. "
                                                  "Connect it again on the Setup page; it takes a few seconds.")
            try:  # once per disconnect: the token is gone now, so later checks stop at "isn't connected"
                self.ctx.push.send("Reconnect Gmail", "Google ended Job Search HQ's Gmail access, so job alerts aren't coming in. "
                                   "Tap to reconnect.", "/setup#gmail", tag="gmail")
            except Exception:  # noqa: BLE001 - a failed notification must not hide the disconnect
                logging.getLogger(__name__).warning("couldn't send the Gmail reconnect notification", exc_info=True)
            raise GmailError("Gmail access was revoked.")
        if response.status_code != 200:
            raise GmailError(f"Google token refresh failed ({response.status_code}).")
        data = response.json()
        self._token = (data["access_token"], time.time() + int(data.get("expires_in", 3000)))
        return self._token[0]

    def _get(self, path: str, params: dict | None = None) -> dict:
        response = self.ctx.http.get(f"{GMAIL}{path}", params=params, headers={"Authorization": f"Bearer {self._access_token()}"})
        if response.status_code != 200:
            raise GmailError(f"Gmail request failed ({response.status_code}).")
        return response.json()

    def search(self, query: str, newer_than_days: int = 2, limit: int = 50) -> list[str]:
        data = self._get("/messages", {"q": f"({query}) newer_than:{newer_than_days}d", "maxResults": limit})
        return [m["id"] for m in data.get("messages", [])]

    def message(self, message_id: str) -> dict:
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,64}", message_id):
            raise GmailError("bad message id")
        data = self._get(f"/messages/{message_id}", {"format": "full"})
        headers = {h["name"].lower(): h["value"] for h in data.get("payload", {}).get("headers", [])}
        return {"id": message_id, "from": headers.get("from", ""), "subject": headers.get("subject", ""),
                "received": int(data.get("internalDate", "0")) / 1000, "body": _body_text(data.get("payload", {}))}


def _body_text(payload: dict) -> str:
    """Plain text of a message: the text/plain part, or text/html stripped of tags."""
    plain, htm = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime in ("text/plain", "text/html"):
            text = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
            (plain if mime == "text/plain" else htm).append(text)
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain:
        return "\n".join(plain)[:50_000]
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", "\n".join(htm), flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</div>|</tr>", "\n", text, flags=re.I)
    text = re.sub(r'<a [^>]*href="([^"]+)"[^>]*>', r" \1 ", text, flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", " ", text))[:50_000]


# --------------------------------------------------------------- alert mail

LINKEDIN_JOB = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d{6,12})")


def parse_alert(source: str, body: str) -> list[dict]:
    """Jobs listed in an alert email. LinkedIn's plain text lists title, company, location, then a View job link."""
    jobs: list[dict] = []
    if source == "linkedin":
        lines = [ln.strip() for ln in body.splitlines()]
        seen = set()
        for i, line in enumerate(lines):
            m = LINKEDIN_JOB.search(line)
            if not m or m.group(1) in seen:
                continue
            block = [ln for ln in lines[max(0, i - 6):i] if ln and not ln.lower().startswith(("view job", "http", "---"))][-3:]
            if len(block) < 2:
                continue
            title, company = block[0], block[1]
            location = block[2] if len(block) > 2 else ""
            seen.add(m.group(1))
            jobs.append({"ref": f"linkedin:{m.group(1)}", "title": title[:160], "company": company[:120], "location": location[:120],
                         "url": f"https://www.linkedin.com/jobs/view/{m.group(1)}/"})
    elif source == "indeed":
        for m in re.finditer(r"indeed\.com/(?:rc/clk|viewjob|pagead/clk)\?[^\s\"'<>]*jk=([0-9a-f]{16})", body):
            if any(j["ref"] == f"indeed:{m.group(1)}" for j in jobs):
                continue
            before = [ln.strip() for ln in body[:m.start()].splitlines() if ln.strip()][-3:]
            if len(before) >= 2:
                jobs.append({"ref": f"indeed:{m.group(1)}", "title": before[-2][:160], "company": before[-1][:120], "location": "",
                             "url": f"https://www.indeed.com/viewjob?jk={m.group(1)}"})
    return jobs[:40]


# --------------------------------------------------------------- the rules

def domain_of(sender: str) -> str:
    return parseaddr(sender)[1].rpartition("@")[2].lower()


def registrable(domain: str) -> str:
    """The part of a host name someone registers: acme.com for mail.acme.com, acme.co.uk for jobs.acme.co.uk. "" if none."""
    labels = [x for x in domain.lower().strip(".").split(".") if x]
    n = 3 if len(labels) >= 2 and ".".join(labels[-2:]) in TWO_PART_SUFFIXES else 2
    return ".".join(labels[-n:]) if len(labels) >= n else ""


def _within(domain: str, names) -> bool:
    return any(domain == h or domain.endswith("." + h) for h in names)


def _company_words(company: str) -> list[str]:
    return [w for w in re.sub(r"[^a-z0-9 ]", " ", company.lower().replace("&", " and ")).split() if w not in _CORP_WORDS]


def _company_labels(company: str) -> set[str]:
    """Domain labels that are the company's whole name: "Acme Health, Inc." -> acmehealth, acme; "Initech.io" -> initech, initechio."""
    labels = {registrable(t).split(".")[0] for t in re.findall(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", company.lower()) if registrable(t)}
    words = _company_words(company)
    for kept in (words, [w for w in words if w != "and"], [w for w in words if w not in _GENERIC_WORDS]):
        labels.add("".join(kept))
    return {x.replace("-", "") for x in labels if len(x.replace("-", "")) >= 2}


def sender_matches(domain: str, company: str, known: tuple[str, ...] | set[str] = ()) -> bool:
    """True when the domain is the company's own: its registrable label is the company's whole name, or it's a domain
    recorded for the company (see known_domains). A label that only contains or starts like the name doesn't count."""
    reg = registrable(domain)
    if not reg:
        return False
    if reg in known:
        return True
    return reg.split(".")[0].replace("-", "") in _company_labels(company)


def _names_company(company: str, text: str) -> bool:
    words = [w for w in _company_words(company) if w != "and"]
    said = [w for w in re.sub(r"[^a-z0-9 ]", " ", text.lower().replace("&", " and ")).split() if w != "and"]
    n = len(words)
    return bool(words) and any(said[i:i + n] == words for i in range(len(said) - n + 1))


def sender_trusted(sender: str, subject: str, company: str, known: tuple[str, ...] | set[str] = ()) -> bool:
    """May this email change the company's role? The company's own domain, or a contract hiring system that names the company."""
    display, address = parseaddr(sender)
    address = address.lower()
    domain = address.rpartition("@")[2]
    if not domain or address in OPEN_SIGNUP_ADDRESSES or _within(domain, OPEN_SIGNUP) or "via google" in display.lower():
        return False
    if _within(domain, HIRING_SYSTEMS):
        return _names_company(company, f"{display} {subject}")
    return sender_matches(domain, company, known)


def known_domains(ctx: Ctx, role: dict) -> set[str]:
    """Domains recorded for the role's company: its posting link (unless that's a job site or hiring system) and its contacts' emails."""
    hosts = [urlsplit(role.get("url") or "").hostname or ""]
    hosts += [str(c["email"]).rpartition("@")[2] for c in ctx.db.all("SELECT email FROM contacts WHERE role_id = ? AND email != ''", (role["id"],))]
    shared = JOB_SITES + HIRING_SYSTEMS + OPEN_SIGNUP + FREE_MAIL
    return {registrable(h) for h in hosts if registrable(h) and not _within(registrable(h), shared)}


def match_role(ctx: Ctx, company: str, title: str) -> dict | None:
    company = company.strip().lower()
    if not company:
        return None
    candidates = [r for r in ctx.roles.all() if r["company"].strip().lower() == company or
                  (len(company) >= 4 and (company in r["company"].lower() or r["company"].lower() in company))]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    t = title.lower()
    titled = [r for r in candidates if t and (t in r["title"].lower() or r["title"].lower() in t)]
    return titled[0] if len(titled) == 1 else None


def apply_email(ctx: Ctx, item: dict, info: dict) -> tuple[str, str, int | None, int | None]:
    """Decide what an employer email does. Returns (status, outcome text, role id, audit id)."""
    effect = EMAIL_EFFECTS.get(info["kind"])
    if not effect:
        return "review" if info["kind"] in ("recruiter_outreach",) else "done", info["summary"][:200], None, None
    role = match_role(ctx, info["company"], info["role_title"])
    if not role:
        return "review", f"{info['company']}: {info['summary']}"[:200] + " (no matching role)", None, None
    if not sender_trusted(item["from"], item.get("subject", ""), role["company"], known_domains(ctx, role)):
        return "review", f"Sender {domain_of(item['from'])} isn't {role['company']} or a hiring system naming it", role["id"], None
    if role["status"] in CLOSED:
        return "review", f"{role['company']} is already {role['status']}; not reopening it", role["id"], None
    if role["status"] not in effect["from"]:
        return "review", f"{role['company']} is {role['status']}; this email would move it backwards", role["id"], None
    changes = {}
    if effect.get("status") and effect["status"] != role["status"]:
        changes["status"] = effect["status"]
    if effect.get("next_step"):
        changes["next_step"] = effect["next_step"]
        if info.get("event_date"):
            changes["next_step_date"] = info["event_date"]
    audit_id = None
    try:
        if changes:
            audit_id = ctx.roles.update(role["id"], changes, actor="system", summary=f"Email from {role['company']}: {info['summary'][:120]}")
        if effect.get("event") and info.get("event_date"):
            ctx.roles.add_event(role["id"], effect["event"], info["event_date"], info["summary"][:300], actor="system")
        ctx.roles.add_event(role["id"], "email", date.today().isoformat(), f"{info['kind'].replace('_', ' ')}: {info['summary'][:300]}", actor="system")
    except RoleError as exc:
        return "review", f"Couldn't apply: {exc}", role["id"], None
    return "applied", f"{role['company']}: {info['summary']}"[:200], role["id"], audit_id


async def check_inbox(ctx: Ctx, gmail: Gmail, days: int = 2) -> dict:
    from .ai.tasks import classify_email

    if not gmail.connected():
        return {"skipped": "gmail not connected"}
    counts = {"alerts": 0, "new_roles": 0, "applied": 0, "review": 0}
    for message_id in gmail.search(SEARCH, newer_than_days=days):
        if ctx.db.one("SELECT 1 FROM inbox_items WHERE message_id = ?", (message_id,)):
            continue
        msg = gmail.message(message_id)
        sender = parseaddr(msg["from"])[1].lower()
        source = ALERT_SENDERS.get(sender)
        status, outcome, role_id, audit_id, kind = "done", "", None, None, "ignored"
        if source:
            kind = "alert"
            added = 0
            for job in parse_alert(source, msg["body"]):
                if ctx.roles.find_ref(job["ref"]):
                    continue
                try:
                    ctx.roles.create({"company": job["company"], "title": job["title"], "location": job["location"], "url": job["url"],
                                      "source": source}, actor="system", source_ref=job["ref"])
                    added += 1
                except RoleError:
                    continue
            counts["alerts"] += 1
            counts["new_roles"] += added
            outcome = f"{added} new role{'s' if added != 1 else ''} from {source}"
        else:
            info = await classify_email(ctx, msg["from"], msg["subject"], msg["body"])
            if info and info["kind"] != "other":
                kind = "employer"
                status, outcome, role_id, audit_id = apply_email(ctx, msg, info)
                counts["applied" if status == "applied" else "review"] += status in ("applied", "review")
        ctx.db.run("INSERT INTO inbox_items (message_id, received, sender, subject, kind, role_id, outcome, status, audit_id, created) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (message_id) DO NOTHING",
                   (message_id, msg["received"], msg["from"][:200], msg["subject"][:300], kind, role_id, outcome, status, audit_id, time.time()))
    ctx.settings.set("gmail_last_check", time.time())
    return counts


# ------------------------------------------------------------------ boards

def board_jobs(ctx: Ctx, board: str, slug: str) -> list[dict]:
    """Open jobs from a company's public board API."""
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,80}", slug):
        raise ValueError("bad board name")
    if board == "greenhouse":
        data = ctx.http.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", params={"content": "true"}).json()
        return [{"ref": f"greenhouse:{slug}:{j['id']}", "title": j["title"], "url": j.get("absolute_url", ""),
                 "location": (j.get("location") or {}).get("name", ""), "posting": _strip(html.unescape(j.get("content", ""))),
                 "posted_on": str(j.get("updated_at", ""))[:10]} for j in data.get("jobs", [])]
    if board == "lever":
        data = ctx.http.get(f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"}).json()
        return [{"ref": f"lever:{slug}:{j['id']}", "title": j["text"], "url": j.get("hostedUrl", ""),
                 "location": (j.get("categories") or {}).get("location", ""),
                 "posting": j.get("descriptionPlain", "") + "\n"
                 + "\n".join(f"{x.get('text', '')}: {_strip(x.get('content', ''))}" for x in j.get("lists", [])),
                 "posted_on": date.fromtimestamp(j["createdAt"] / 1000).isoformat() if j.get("createdAt") else ""} for j in data]
    if board == "ashby":
        data = ctx.http.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", params={"includeCompensation": "true"}).json()
        return [{"ref": f"ashby:{slug}:{j['id']}", "title": j["title"], "url": j.get("jobUrl", ""), "location": j.get("location", ""),
                 "posting": j.get("descriptionPlain", "") or _strip(j.get("descriptionHtml", "")),
                 "posted_on": str(j.get("publishedAt", ""))[:10]} for j in data.get("jobs", [])]
    raise ValueError("unknown board")


def _strip(text: str) -> str:
    return re.sub(r"\n\s*\n+", "\n\n", html.unescape(re.sub(r"<[^>]+>", "\n", text or ""))).strip()[:60_000]


def check_boards(ctx: Ctx) -> dict:
    added = 0
    for company in ctx.db.all("SELECT * FROM watch_companies WHERE active = 1"):
        try:
            jobs = board_jobs(ctx, company["board"], company["slug"])
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.info("board %s/%s failed: %s", company["board"], company["slug"], type(exc).__name__)
            continue
        words = [w.strip().lower() for w in company["keywords"].split(",") if w.strip()]
        for job in jobs:
            if words and not any(w in job["title"].lower() for w in words):
                continue
            if ctx.roles.find_ref(job["ref"]):
                continue
            try:
                role_id = ctx.roles.create({"company": company["name"], "title": job["title"], "url": job["url"], "location": job["location"],
                                            "posting": job["posting"], "posted_on": job["posted_on"] if _is_date(job["posted_on"]) else "",
                                            "source": company["board"]}, actor="system", source_ref=job["ref"])
                ctx.queue.enqueue("score", {"role_id": role_id}, dedupe=f"score:{role_id}")
                added += 1
            except RoleError:
                continue
        ctx.db.run("UPDATE watch_companies SET last_checked = ? WHERE id = ?", (time.time(), company["id"]))
    return {"added": added}


def _is_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


# ------------------------------------------------------------- safe fetch

class FetchError(Exception):
    pass


def _public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    for info in infos:
        if _blocked_ip(ipaddress.ip_address(info[4][0])):
            return False
    return bool(infos)


# IPv6 ranges that carry an IPv4 address: NAT64 gateways turn 64:ff9b::a9fe:a9fe into a request to
# 169.254.169.254, so they're refused outright; the others are checked by the IPv4 they carry.
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))
_CARRY_V4 = (ipaddress.ip_network("::ffff:0:0/96"), ipaddress.ip_network("::ffff:0:0:0/96"), ipaddress.ip_network("::/96"))


def _blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if not ip.is_global or ip.is_multicast:
        return True
    if ip.version == 4:
        return False
    if any(ip in net for net in _NAT64):
        return True
    carried = [ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)] if any(ip in net for net in _CARRY_V4) else []
    if ip.sixtofour:
        carried.append(ip.sixtofour)
    if ip.teredo:
        carried.extend(ip.teredo)
    return any(not v4.is_global or v4.is_multicast for v4 in carried)


def posting_url(url: str) -> str:
    """A pasted link with https:// added when it has no scheme, as in 'www.linkedin.com/jobs/view/123'."""
    url = (url or "").strip()
    if not url or re.match(r"[A-Za-z][A-Za-z0-9+.-]*://", url):
        return url
    return "https:" + url if url.startswith("//") else "https://" + url


def fetch_posting(ctx: Ctx, url: str, max_bytes: int = 2_000_000) -> str:
    """Fetch a posting page the user pasted, as text. Every failure (a bad link, a timeout, a refused
    connection) is a FetchError with a message the user can act on."""
    try:
        return _fetch(ctx, posting_url(url), max_bytes)
    except httpx.HTTPError as exc:
        log.info("posting fetch failed: %s", type(exc).__name__)
        raise FetchError("The site didn't answer in time or refused the connection. Paste the posting text instead.") from exc
    except (ValueError, httpx.InvalidURL) as exc:  # urlsplit and .port reject malformed links such as https://[::1/x or a non-numeric port
        raise FetchError("That link isn't a valid web address. Check it, or paste the posting text instead.") from exc


def _fetch(ctx: Ctx, url: str, max_bytes: int) -> str:
    """Public https addresses only, so it can't be pointed at the VM's own network."""
    for _ in range(4):
        parts = urlsplit(url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.port not in (None, 443):
            raise FetchError("Only public https links can be fetched.")
        if not _public(parts.hostname):
            raise FetchError("That address isn't on the public internet.")
        with ctx.http.stream("GET", url, headers={"Accept": "text/html,text/plain"}) as response:
            if response.is_redirect:
                url = str(response.next_request.url) if response.next_request else ""
                continue
            if response.status_code != 200:
                raise FetchError(f"The page answered {response.status_code}. Paste the posting text instead.")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    break
                chunks.append(chunk)
            raw = b"".join(chunks).decode(response.encoding or "utf-8", "replace")
            text = _body_text({"mimeType": "text/html", "body": {"data": base64.urlsafe_b64encode(raw.encode()).decode()}})
            text = re.sub(r"[ \t]+", " ", text)
            return re.sub(r"\n\s*\n+", "\n\n", text).strip()[:60_000]
    raise FetchError("Too many redirects.")


def record_dump(value) -> str:
    return dumps(value)
