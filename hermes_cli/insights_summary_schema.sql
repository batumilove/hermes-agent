PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA user_version = 1;

CREATE TABLE summary_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE summary_generations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    finished_at REAL,
    status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'failed', 'overlap', 'schema_blocked', 'budget_blocked')),
    producer_sha256 TEXT NOT NULL,
    source_schema_sha256 TEXT NOT NULL,
    source_schema_version INTEGER NOT NULL,
    source_message_high_water INTEGER NOT NULL,
    summary_message_high_water INTEGER NOT NULL,
    bootstrap_complete INTEGER NOT NULL CHECK (bootstrap_complete IN (0, 1)),
    sessions_scanned INTEGER NOT NULL DEFAULT 0,
    dirty_sessions INTEGER NOT NULL DEFAULT 0,
    messages_processed INTEGER NOT NULL DEFAULT 0,
    source_read_seconds REAL NOT NULL DEFAULT 0,
    summary_write_seconds REAL NOT NULL DEFAULT 0,
    db_bytes_before INTEGER,
    db_bytes_after INTEGER,
    error TEXT
);

CREATE TABLE summary_sessions (
    id TEXT PRIMARY KEY,
    source_rowid INTEGER,
    source TEXT NOT NULL,
    user_id TEXT,
    model TEXT,
    parent_session_id TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    end_reason TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens INTEGER NOT NULL DEFAULT 0,
    title TEXT,
    billing_provider TEXT,
    billing_mode TEXT,
    estimated_cost_usd REAL,
    actual_cost_usd REAL,
    cost_status TEXT,
    cost_source TEXT,
    api_call_count INTEGER NOT NULL DEFAULT 0,
    cwd TEXT,
    git_branch TEXT,
    git_repo_root TEXT,
    session_key TEXT,
    chat_id TEXT,
    chat_type TEXT,
    thread_id TEXT,
    display_name TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    rewind_count INTEGER NOT NULL DEFAULT 0,
    fingerprint TEXT NOT NULL
);

CREATE INDEX summary_sessions_started
    ON summary_sessions(started_at DESC);
CREATE INDEX summary_sessions_source_model_started
    ON summary_sessions(source, model, started_at DESC);
CREATE INDEX summary_sessions_model_started
    ON summary_sessions(model, started_at DESC);
CREATE INDEX summary_sessions_active_started
    ON summary_sessions(started_at DESC) WHERE ended_at IS NULL;
CREATE INDEX summary_sessions_source_id
    ON summary_sessions(source, id);
CREATE INDEX summary_sessions_fingerprint
    ON summary_sessions(fingerprint);

CREATE TABLE summary_message_metadata (
    id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES summary_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    tool_name TEXT,
    timestamp REAL NOT NULL,
    token_count INTEGER,
    finish_reason TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    compacted INTEGER NOT NULL DEFAULT 0,
    content_chars INTEGER NOT NULL DEFAULT 0,
    tool_calls_chars INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX summary_messages_session_id
    ON summary_message_metadata(session_id, id);
CREATE INDEX summary_messages_session_timestamp
    ON summary_message_metadata(session_id, timestamp);
CREATE INDEX summary_messages_tool_timestamp
    ON summary_message_metadata(tool_name, timestamp)
    WHERE tool_name IS NOT NULL AND tool_name != '';

CREATE TABLE summary_session_roles (
    session_id TEXT NOT NULL REFERENCES summary_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (session_id, role)
) WITHOUT ROWID;

CREATE TABLE summary_session_tools (
    session_id TEXT NOT NULL REFERENCES summary_sessions(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    calls INTEGER NOT NULL,
    first_ts REAL,
    last_ts REAL,
    PRIMARY KEY (session_id, tool_name)
) WITHOUT ROWID;

CREATE INDEX summary_tools_name_session
    ON summary_session_tools(tool_name, session_id);

CREATE TABLE summary_session_skills (
    session_id TEXT NOT NULL REFERENCES summary_sessions(id) ON DELETE CASCADE,
    skill_name TEXT NOT NULL,
    loads INTEGER NOT NULL DEFAULT 0,
    edits INTEGER NOT NULL DEFAULT 0,
    first_message_id INTEGER NOT NULL,
    first_call_index INTEGER NOT NULL,
    PRIMARY KEY (session_id, skill_name)
) WITHOUT ROWID;

CREATE INDEX summary_skills_name_session
    ON summary_session_skills(skill_name, session_id);

CREATE TABLE summary_scrub_queue (
    session_id TEXT PRIMARY KEY REFERENCES summary_sessions(id) ON DELETE CASCADE,
    last_scrubbed_at REAL,
    last_source_fingerprint TEXT
) WITHOUT ROWID;
