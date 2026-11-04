# Design

Job Search HQ began as a personal Claude Code setup: a repository of CSVs and skills, a Slack bot on a VM, and a morning run. This is the same system as a self-hosted web app that anyone can run.

## Decisions (November 2026)

| Question | Decision | Why |
|---|---|---|
| Who runs it | Each person's copy is self-hosted. One server can host several copies, one per person | No Google verification needed for Gmail access, no shared AI bill, and nobody holds a stranger's email |
| Several people on one server | A separate container, database, key and network per person; Caddy routes by subdomain | The app stays single-user and simple, and a mistake in one copy can't expose another |
| Stack | FastAPI and Jinja pages with htmx, SQLite, Python | Few moving parts to deploy; existing Python logic carried over |
| AI | Claude Agent SDK, with either an API key or the person's own Claude plan signed in to Claude Code itself (`jobsctl claude-login`) | Anthropic's terms don't allow an app to collect or store Claude.ai credentials, but do allow a person to sign in to unmodified Claude Code with their own plan. The app only reads "signed in or not". |
| Where jobs come from | Alert emails (LinkedIn, Indeed), public Greenhouse, Lever and Ashby APIs, pasted postings | No scraping; nothing in the repository breaks a site's terms |
| Notifications | Web push from each copy, with its own key | No third-party service; works on ChromeOS, Android, desktop and iOS home-screen apps |
| Documents | Typst instead of LaTeX | A small library with no shell escape and no file access outside its folder; content goes in as data, so it can't run |
| Must work on | A Chromebook with only a browser, for both setup and daily use | The first real user runs a Chromebook |
| Cost | About $20 a month plus about $12 a year for a domain: Google Cloud trial, then the always-free e2-micro; Claude Pro; your own domain (free sslip.io to try it); optional LinkedIn Premium trial | The pitch: an AI job search aid on the cheap (docs/cheap.md) |
| Server sizes | trial (e2-medium) and forever free (e2-micro, standard disk, free regions only, swap, voice off, one AI run at a time) | The free tier's limits; `move-to-free.sh` moves between them with export and import |
| Address | Free `NAME.<ip>.sslip.io` by default; own domain optional | Real HTTPS without buying a domain |
| Images | Built on the server by default; prebuilt multi-arch images on ghcr.io per release, pinned by version or digest | Faster installs, and the e2-micro shouldn't have to build |
| LinkedIn | No API, no scraping. Outreach drafts messages the person sends themselves | Makes a Premium trial useful without breaking LinkedIn's terms |

## Layout

```
VM (Debian 12, Docker)
 ├─ caddy           HTTPS for every copy, routes NAME.domain → app-NAME
 ├─ app-NAME        one per person, its own network, read-only root
 │    ├─ FastAPI pages + htmx          src/jobhq/web
 │    ├─ worker (same process)         src/jobhq/worker.py   inbox hourly, boards 6:30, digest 7:30, reminders each minute
 │    ├─ SQLite /data/app.db            src/jobhq/migrations.py
 │    └─ Claude via Agent SDK           src/jobhq/ai
 └─ /srv/jobhq      vm.env, instances/NAME.env, data/NAME, backups   (managed by scripts/jobsctl)
```

## Modules

| Module | Job |
|---|---|
| `config.py` | Reads and checks the environment, then removes secrets from it |
| `crypto.py` | AES-GCM for stored secrets; signed tokens for notification buttons |
| `db.py`, `migrations.py` | SQLite connection rules and numbered migrations |
| `auth.py` | Google OIDC with PKCE, owner checks, server-side sessions |
| `security.py` | Headers, CSRF and origin checks, rate and size limits |
| `profile.py`, `proposals.py` | The profile and the approval queue for AI suggestions |
| `roles.py` | Roles, timeline, lanes, the due list, audit and undo |
| `intake.py` | Gmail (read only), alert parsing, email rules, public job boards, safe fetch |
| `documents/` | Drafting, claim checks, Typst templates, PDF checks |
| `practice.py` | Practice questions, answers, voice transcription, delivery stats |
| `notify.py` | Reminders and web push |
| `ai/runner.py` | The only way the app runs Claude, with every limit applied |
| `ai/toolbox.py` | The tools Claude may call, per task |
| `ai/tasks.py` | Prompts and answer schemas for each AI task |

## Carried over from the Claude Code version

- **Lanes:** ready, call, check, later and expired, from gates, grade and access.
- **Inbox rules:** no hired from email, no reopening, no moving backwards, unknown senders go to review, and Undo on everything.
- **Profile questions:** asked about tools from scored roles, at most five a day, one per topic; "never used" is recorded too.
- **Practice:** grading out of 20 on structure, evidence, fit and length, with measured pace and filler words.
- **Reminders:** presets, snooze and Done.
- **Facts only:** documents never claim more than the profile says.

## Vendored files

`src/jobhq/static/htmx-2.0.4.min.js` comes from the `htmx.org@2.0.4` npm package (`dist/htmx.min.js`).
SHA-384 (hex): `1c67f3b687e8b5fb21705efef27e382502f6a099a8c150a13d3838f12fa3bd9a33b4f7efc03efd382fcb4ed1ba74ca7e`.
