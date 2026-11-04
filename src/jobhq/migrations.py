"""Numbered schema migrations. Never edit one that has shipped; add a new one at the end."""

FOUNDATION = """
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE sessions (
    id_hash TEXT PRIMARY KEY,
    csrf TEXT NOT NULL,
    created REAL NOT NULL,
    last_seen REAL NOT NULL,
    expires REAL NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    ip TEXT NOT NULL DEFAULT ''
);
CREATE TABLE oauth_states (
    state_hash TEXT PRIMARY KEY,
    purpose TEXT NOT NULL CHECK (purpose IN ('login', 'gmail')),
    nonce TEXT NOT NULL,
    verifier TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE TABLE audit (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    actor TEXT NOT NULL CHECK (actor IN ('user', 'ai', 'system')),
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT NOT NULL DEFAULT '',
    before TEXT,
    after TEXT,
    summary TEXT NOT NULL DEFAULT '',
    undone INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX audit_entity ON audit (entity, entity_id);
CREATE TABLE ai_runs (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    task TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    cost_usd REAL NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    ok INTEGER NOT NULL,
    error TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX ai_runs_at ON ai_runs (at);
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    run_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'done', 'failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    locked_until REAL,
    last_error TEXT NOT NULL DEFAULT '',
    result TEXT,
    dedupe TEXT UNIQUE,
    created REAL NOT NULL,
    finished REAL
);
CREATE INDEX jobs_due ON jobs (status, run_at);
CREATE TABLE chat_threads (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'chat' CHECK (kind IN ('chat', 'interview')),
    title TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY,
    thread_id INTEGER NOT NULL REFERENCES chat_threads (id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'done' CHECK (status IN ('pending', 'done', 'failed')),
    meta TEXT NOT NULL DEFAULT '{}',
    at REAL NOT NULL
);
CREATE INDEX chat_messages_thread ON chat_messages (thread_id, id);
CREATE TABLE proposals (
    id INTEGER PRIMARY KEY,
    at REAL NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    payload TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'failed')),
    decided REAL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX proposals_status ON proposals (status, id)
"""

PROFILE = """
CREATE TABLE profile (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    data TEXT NOT NULL DEFAULT '{}',
    updated REAL NOT NULL
);
CREATE TABLE experience (
    id INTEGER PRIMARY KEY,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT NOT NULL DEFAULT '',
    summary TEXT NOT NULL DEFAULT '',
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE education (
    id INTEGER PRIMARY KEY,
    school TEXT NOT NULL,
    degree TEXT NOT NULL DEFAULT '',
    field TEXT NOT NULL DEFAULT '',
    start_date TEXT NOT NULL DEFAULT '',
    end_date TEXT NOT NULL DEFAULT ''
);
CREATE TABLE facts (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    experience_id INTEGER REFERENCES experience (id) ON DELETE SET NULL,
    source TEXT NOT NULL DEFAULT 'user' CHECK (source IN ('user', 'interview', 'import')),
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE TABLE skills (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,
    status TEXT NOT NULL DEFAULT 'have' CHECK (status IN ('have', 'never')),
    level TEXT NOT NULL DEFAULT '',
    years REAL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE profile_questions (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL UNIQUE COLLATE NOCASE,
    question TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    role_id INTEGER,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'answered', 'skipped')),
    answer TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    answered REAL
)
"""

ROLES = """
CREATE TABLE roles (
    id INTEGER PRIMARY KEY,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT UNIQUE,
    location TEXT NOT NULL DEFAULT '',
    work_mode TEXT NOT NULL DEFAULT '' CHECK (work_mode IN ('', 'remote', 'hybrid', 'onsite')),
    pay_min INTEGER,
    pay_max INTEGER,
    posting TEXT NOT NULL DEFAULT '',
    posted_on TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'saved'
        CHECK (status IN ('saved', 'applied', 'interviewing', 'offer', 'accepted', 'rejected', 'withdrawn', 'closed', 'skipped')),
    fit_score INTEGER,
    fit TEXT NOT NULL DEFAULT '{}',
    gates TEXT NOT NULL DEFAULT '{}',
    warm_path TEXT NOT NULL DEFAULT '',
    next_step TEXT NOT NULL DEFAULT '',
    next_step_date TEXT NOT NULL DEFAULT '',
    applied_on TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX roles_status ON roles (status);
CREATE TABLE role_events (
    id INTEGER PRIMARY KEY,
    role_id INTEGER NOT NULL REFERENCES roles (id) ON DELETE CASCADE,
    on_date TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('interview', 'followup', 'note', 'status', 'email', 'deadline')),
    text TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
CREATE INDEX role_events_role ON role_events (role_id, on_date)
"""

INTAKE = """
CREATE TABLE inbox_items (
    id INTEGER PRIMARY KEY,
    message_id TEXT NOT NULL UNIQUE,
    received REAL NOT NULL,
    sender TEXT NOT NULL DEFAULT '',
    subject TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL CHECK (kind IN ('alert', 'employer', 'ignored')),
    role_id INTEGER REFERENCES roles (id) ON DELETE SET NULL,
    outcome TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'done' CHECK (status IN ('done', 'review', 'applied', 'undone')),
    audit_id INTEGER,
    created REAL NOT NULL
);
CREATE TABLE watch_companies (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    board TEXT NOT NULL CHECK (board IN ('greenhouse', 'lever', 'ashby')),
    slug TEXT NOT NULL,
    keywords TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    last_checked REAL,
    UNIQUE (board, slug)
)
"""

DOCUMENTS = """
CREATE TABLE documents (
    id INTEGER PRIMARY KEY,
    role_id INTEGER REFERENCES roles (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('resume', 'cover_letter')),
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'approved', 'rejected')),
    content TEXT NOT NULL,
    pdf_file TEXT NOT NULL DEFAULT '',
    pages INTEGER,
    checks TEXT NOT NULL DEFAULT '[]',
    created REAL NOT NULL,
    updated REAL NOT NULL
)
"""

PRACTICE_REMINDERS_PUSH = """
CREATE TABLE practice_questions (
    id INTEGER PRIMARY KEY,
    role_id INTEGER REFERENCES roles (id) ON DELETE SET NULL,
    question TEXT NOT NULL,
    focus TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
CREATE TABLE practice_attempts (
    id INTEGER PRIMARY KEY,
    question_id INTEGER NOT NULL REFERENCES practice_questions (id) ON DELETE CASCADE,
    at REAL NOT NULL,
    answer TEXT NOT NULL,
    via TEXT NOT NULL DEFAULT 'typed' CHECK (via IN ('typed', 'voice')),
    seconds REAL,
    words INTEGER NOT NULL DEFAULT 0,
    wpm REAL,
    fillers INTEGER NOT NULL DEFAULT 0,
    grade TEXT NOT NULL DEFAULT '{}',
    total INTEGER,
    status TEXT NOT NULL DEFAULT 'grading' CHECK (status IN ('grading', 'graded', 'failed'))
);
CREATE TABLE reminders (
    id INTEGER PRIMARY KEY,
    text TEXT NOT NULL,
    due REAL NOT NULL,
    role_id INTEGER REFERENCES roles (id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'done', 'cancelled')),
    created REAL NOT NULL,
    sent REAL
);
CREATE INDEX reminders_due ON reminders (status, due);
CREATE TABLE push_subscriptions (
    id INTEGER PRIMARY KEY,
    endpoint TEXT NOT NULL UNIQUE,
    keys TEXT NOT NULL,
    user_agent TEXT NOT NULL DEFAULT '',
    failures INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL
)
"""

# Claude.ai tokens are never stored any more (see ai/claude_code.py); remove any an early version kept.
DROP_CLAUDE_TOKENS = """
DELETE FROM settings WHERE key = 'claude_oauth_token';
UPDATE settings SET value = '"claude_login"' WHERE key = 'claude_mode' AND value = '"subscription"'
"""

OUTREACH = """
CREATE TABLE contacts (
    id INTEGER PRIMARY KEY,
    role_id INTEGER REFERENCES roles (id) ON DELETE SET NULL,
    name TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    relation TEXT NOT NULL DEFAULT 'cold' CHECK (relation IN ('warm', 'cold')),
    how_known TEXT NOT NULL DEFAULT '',
    linkedin_url TEXT NOT NULL DEFAULT '',
    email TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'to_contact' CHECK (status IN ('to_contact', 'sent', 'replied', 'no_reply')),
    last_contacted TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX contacts_role ON contacts (role_id);
CREATE TABLE outreach_messages (
    id INTEGER PRIMARY KEY,
    contact_id INTEGER NOT NULL REFERENCES contacts (id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK (kind IN ('connection_note', 'inmail', 'email', 'follow_up')),
    subject TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'sent')),
    created REAL NOT NULL,
    sent_on TEXT NOT NULL DEFAULT ''
)
"""

# Certifications, licenses and projects for the resume; the role's pay currency; tool questions waiting past the daily cap.
PROFILE_EXTRAS = """
CREATE TABLE certifications (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    issuer TEXT NOT NULL DEFAULT '',
    on_date TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
CREATE TABLE projects (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    what TEXT NOT NULL DEFAULT '',
    on_date TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL
);
CREATE TABLE question_queue (
    id INTEGER PRIMARY KEY,
    topic TEXT NOT NULL UNIQUE COLLATE NOCASE,
    reason TEXT NOT NULL DEFAULT '',
    role_id INTEGER,
    created REAL NOT NULL
);
ALTER TABLE roles ADD COLUMN pay_currency TEXT NOT NULL DEFAULT ''
"""

MIGRATIONS = [FOUNDATION, PROFILE, ROLES, INTAKE, DOCUMENTS, PRACTICE_REMINDERS_PUSH, DROP_CLAUDE_TOKENS, OUTREACH, PROFILE_EXTRAS]
