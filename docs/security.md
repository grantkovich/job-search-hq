# Security model

Job Search HQ holds sensitive things: a person's work history, job applications, read access to their Gmail, and an AI key that costs money. This page lists what it protects, from whom, and how. It also lists what it doesn't do.

## What's protected and from whom

| Asset | Threats considered |
|---|---|
| The person's records, profile and documents | Someone on the internet; another person on the same server; a malicious job posting or email |
| Gmail read access (refresh token) | Theft from the database; misuse through the AI (backups hold the key, see Data at rest) |
| Claude API key | Theft; running up costs |
| The server | Takeover through the app; access to Google Cloud from the server |

## Controls

**Sign-in and sessions**
- **Google sign-in:** OpenID Connect authorization code flow with PKCE, a one-time `state` that expires after 10 minutes and is tied to the browser that started it (a 10-minute `__Host-` cookie), a `nonce`, and checks on the token's issuer, audience, authorized party and expiry. A Gmail grant that arrives without a signed-in session is revoked, not just dropped.
- **Who can sign in:** only the owner's email, and only if Google says it's verified (the JSON value `true`, nothing looser). After the first sign-in, the Google account ID (`sub`) is pinned too, so a recycled address can't take over.
- **Session storage:** sessions live on the server. The database keeps only a SHA-256 of each session ID, so a copy of the database can't be used to sign in. They end after 7 days idle or 30 days in total. Every sign-in gets a new ID, so a planted session can't be reused. Settings has **Sign out everywhere**.
- **Session cookie:** `__Host-` prefixed, which means Secure, path `/` and no Domain. It's also HttpOnly and SameSite=Lax. The prefix stops a sibling copy on the same server from setting or reading it.

**Requests**
- **Cross-site requests:** every state-changing request must match the copy's exact origin (`Origin` and `Sec-Fetch-Site`) and carry the session's CSRF token in a header. Sibling copies count as cross-site.
- **Headers:**
  - A strict Content Security Policy: scripts only from the site itself, no inline script or style, no images from other sites, and no framing.
  - HSTS.
  - `Referrer-Policy: no-referrer`.
  - `Cache-Control: no-store` on every page.
  - The microphone is allowed for this site only.
- **Limits:** rate limits on sign-in, on every request that can start an AI run or fetch a link (matched by route pattern), and on uploads. Bodies are limited to 1 MB, except the voice recording upload (25 MB). There's an allowlist of host names, and API docs are turned off.
- **Redirects** go only to paths on the copy itself; `//host`, `/\host` and control characters are refused.
- **Notification buttons** carry a signed token that works once: after Done or Snooze the reminder is handled and the token does nothing.
- **The sign-in page** shows only fixed messages, never text from the link.
- **Output escaping:**
  - Templates escape everything automatically.
  - AI text is rendered as Markdown with raw HTML off, images off, and links limited to http, https, mailto and this site's own paths.
  - Exports guard against spreadsheet formula injection.

**AI safety.** Most of these limits are enforced in code, not left to the prompt.
- **No built-in tools:** Claude gets no shell, no files and no web access. Its only tools are the app's own functions. They're listed per task and checked against an allowlist.
- **No local settings:** no Claude Code settings, skills or CLAUDE.md are loaded from disk.
- **Untrusted text is labeled:** postings, emails, and role titles, companies, places and timeline notes that came from job boards, alert emails or AI summaries of emails are wrapped in `<untrusted>` tags, for chat as well as scoring and drafting. Angle brackets inside are swapped for look-alike characters, so no spelling of a tag can close the wrapper early, and the system prompt says to treat that text as data.
- **Rules live in code:**
  - What an email may change is decided by fixed rules, not by the AI. It can't mark anyone hired. It can't reopen a closed role or move a role backwards.
  - Who an email is from is checked by fixed rules too. The sender's registered domain (`acme.com`, `acme.co.uk`) must be the company's whole name or a domain recorded for it (the role's own link or a contact's work email). A contract hiring system (Greenhouse, Lever, Ashby, Workday, iCIMS, SmartRecruiters and the like) counts only when the company's name is also in the sender name or subject. Tools anyone can sign up for (Calendly, HackerRank, Workable, Google Forms and the like) and every other sender go to review.
  - The chat AI can't mark a role accepted, can't reopen a rejected, withdrawn or otherwise closed role, and can add a role only as saved or applied. It tells the person to make those changes on the role page.
  - Every change is logged with Undo.
- **Profile changes wait for approval:** the profile is the source for every resume claim, so the AI can't change it. It files suggestions the person approves.
- **Document checks:** each claim must cite profile facts, tools marked "never used" are blocked, and a second AI pass compares claims with the facts they cite.
- **Spending limits:** a dollar cap per run and per month, and a cap on steps. A run that times out or crashes without Claude's cost report is charged its whole per-run cap, so failures can't slip past the monthly limit.
- **Minimal credentials:** the Claude subprocess gets only its own credential. The app's secrets are removed from the environment at startup and blanked for the subprocess.
- **No Claude.ai credentials in the app:** a person using their own Claude plan signs in to Claude Code itself, in a terminal, through Anthropic's own flow. Claude Code keeps that sign-in in its own folder; the app never reads, stores or passes it on, and only asks Claude Code whether it's signed in. A pasted plan token is refused, and a migration deletes any an early version stored.

**Data at rest**
- **Encryption:** the Claude API key, Gmail refresh token and push signing key are encrypted with AES-256-GCM. The key is derived from `JOBHQ_SECRET` by HKDF. Each value is bound to its field name, so a value moved to another field won't decrypt. This protects **the database file on its own**: a copy of `app.db` without `JOBHQ_SECRET` doesn't reveal those secrets.
- **Backups and export files are not protected by that encryption.** Each backup archive holds the copy's `instance.env`, which contains `JOBHQ_SECRET`, next to the database, so anyone with the archive can decrypt everything in it, including Gmail access. An `export-server` file also holds the Google client secret. Guard backups and export files like passwords: they're written root-only (files 0600, folder 0700), `export-server` prints this warning, and an export should be moved only over SSH/IAP and deleted after the move.
- **Key rotation:** `jobsctl rotate-secret` rotates the key.
- **File permissions:** the database and PDFs are mode 0600, data folders 0700, and secrets files root-only.
- **Voice recordings:** they're processed in a private temporary folder and deleted right after transcription. Only the transcript is kept. ffmpeg may read only local files (`-protocol_whitelist file`) with the one reader that matches the recording's declared type.

**Outbound requests**
- **Pasted links:** fetching a posting from a pasted link accepts only https on port 443 to public addresses. NAT64 addresses (`64:ff9b::/96`, `64:ff9b:1::/48`) are refused, and IPv6 addresses that carry an IPv4 address (mapped, compatible, 6to4, Teredo) are checked by the IPv4 inside. Every redirect is checked again, and there are size limits.
- **Push notifications:** only the known push services are accepted (Google, Apple, Mozilla, Microsoft), so a forged subscription can't point the server elsewhere.
- **Metadata server:** containers can't reach the cloud metadata server, which is blocked with iptables. Only DNS (port 53) is allowed, because on Google Cloud that address is also the VM's DNS resolver. The VM also has no service account, so even a successful request to the metadata server would get nothing.

**Isolation between people on one server**
- Each person gets a separate container, database, encryption key, Docker network and data folder.
- Containers run as an unprivileged user with a read-only root filesystem, no Linux capabilities and no-new-privileges, plus memory, CPU and process limits.
- Only Caddy is reachable from outside.

**Server**
- **Network:** only ports 80 and 443 are open. SSH works only through Google's IAP tunnel, with OS Login, and project-wide SSH keys are blocked.
- **VM:** Shielded VM (secure boot, vTPM, integrity monitoring) and deletion protection.
- **Updates:** automatic security updates, with a reboot window.
- **Docker:** installed from Docker's signed apt repository, with log rotation.
- **Backups:** nightly, root-only (umask 077, files 0600 in a 0700 folder), kept 14 per person. The backup reads the container's snapshot folder without following links, so a compromised container can't make root copy other files into an archive. Each archive holds its copy's key; see Data at rest.
- **Caddy settings:** every host name and email written into the Caddyfile is checked first, including ones that come from an `import-server` file.

**Supply chain**
- **Python packages:** pinned with hashes (`requirements.lock`, installed with `--require-hashes`).
- **htmx:** vendored at a fixed version, with its SHA-384 in `docs/design.md`.
- **CI:** runs the tests, `ruff`, `bandit` and `pip-audit`, and builds the image for both architectures. Dependabot raises updates.

## Known limits

- **Backups hold their own key:** see Data at rest. Encrypting backups with a key kept apart from the server (or keeping secrets as separate root-only files outside the archive) is a planned improvement.
- **Email sender rules match names, not ownership:** a sender's domain counts as the company's when its name is the company's whole name, on any ending. So mail from a look-alike registered under another ending (`acme.xyz` for Acme) could still update Acme's role. What it can do is limited to the fixed email rules (never hired, never reopened, never backwards), and every change has Undo. A company whose mail comes from a differently named domain goes to review until that domain is recorded for it (the role's link or a contact's email).
- **DNS rebinding:** a pasted link is checked by resolving its name before the request. A domain that changes its answer between the check and the request could still reach a private address. The metadata block and the missing service account limit what that could reach.
- **Prompt injection:** it can't be fully prevented. The defenses limit what a successful injection can do: no exfiltration path, no file or shell access, rule-bound record changes with Undo, and approval for profile changes. But a crafted email or posting could still make the AI write something misleading in a chat reply or a draft. Review drafts before sending them.
- **The free sslip.io address:** with the free address, `NAME.<ip>.sslip.io`, a free, open-source DNS service turns the name into your server's IP. Brian Cunnie and Kenneth Lakin run it, together with nip.io, on three name servers (Singapore, the US and Poland). It has been running for more than ten years, with no terms of service, privacy policy or uptime promise. (Checked November 2026: [nip.io](https://nip.io/), [github.com/cunnie/sslip.io](https://github.com/cunnie/sslip.io).)
  - **Your data doesn't pass through it.** Browsers connect straight to your server, and the HTTPS certificate is your server's own, from Let's Encrypt.
  - **What it sees:** lookups of the name, which it logs by default. The name contains the person's short name and the server's IP. That name is public anyway, because every HTTPS certificate is listed in public certificate logs; that's also true with your own domain.
  - **If it goes down,** addresses stop working until it's back. Your data is untouched.
  - **Certificates are shared:** Let's Encrypt counts every sslip.io user against one shared limit, which it has raised to 250,000 certificates a week, and it has declined to raise it further. If that limit runs out, new and renewed certificates fail until it resets. Certificates last 90 days and renew about 30 days early, so a short shortage has slack.
  - **Its blocklist:** to fight phishing, sslip.io refuses to resolve IP addresses on [its blocklist](https://github.com/cunnie/sslip.io-blocklist), which are mostly single IPs plus a few whole ranges. Google Cloud reuses IPs, so an address someone else abused could be on it. Check yours there after the installer prints it.
  - **All sslip.io addresses count as one "site":** sslip.io isn't on the Public Suffix List, so browsers treat every `*.sslip.io` address as the same site. That weakens protections that rely on SameSite cookies. Job Search HQ doesn't rely on them: it uses `__Host-` cookies, which another address can't set or overwrite, and it rejects any change that doesn't come from its own exact address (same-origin, not just same-site).
  - **Some networks may block it:** filters on school, work or other managed networks sometimes block services like this. That's unconfirmed for any particular product, so open the address once from each network the person uses.
  - **The real risk:** whoever runs sslip.io, or anyone who took over its name servers, could point a name at a different server, get a certificate for it, and catch a signed-in browser's session cookie. Gmail access would stay safe, because using it needs the client secret that only your server has. In 2015, the project accidentally published the private key of the wildcard certificate it held then; it was revoked within a week, and it holds no such certificate now.
  - **The comparison:** your own domain carries the same kind of trust, placed in the registrar you pay instead of two volunteers. It also avoids the shared certificate limit, the blocklist and the shared "site". sslip.io is fine for trying Job Search HQ, at your own risk. For a long-running server, use your own domain (about $12 a year): [install.md, step 7](install.md#7-your-own-domain-recommended).
- **Unverified Google app:** each copy uses its own unverified Google app, which is allowed for personal use by fewer than 100 people. Hosting this for the public would need Google's verification and a security assessment for Gmail access.
- **Your Google account:** the person who runs the server, and anyone with access to their Google Cloud project, can read every copy's data on that server. Only host people who trust you, and protect your Google account with a passkey or 2-step verification.
- **Names in some scripts:** resumes and cover letters can't yet show names written in Chinese, Japanese or Korean characters, or emoji, because the PDF font has no glyphs for them and system fonts are turned off on purpose. Accented Latin names work, and the PDF's file name keeps the real name.
- **Not independently tested:** this hasn't had an outside security review or penetration test. Please report problems as described in [SECURITY.md](../SECURITY.md).
