-- Personal Spelling Observatory schema, version 1.
-- No raw message text and no raw keystroke streams are stored.

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Every distinct combination of rules, resources, thresholds and model versions.
-- Observations from different classifier versions are never pooled in analysis.
CREATE TABLE IF NOT EXISTS classifier_versions (
    id          INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    components  TEXT NOT NULL,          -- JSON
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS imports (
    id                    INTEGER PRIMARY KEY,
    kind                  TEXT NOT NULL,          -- 'chatgpt_export'
    source_sha256         TEXT NOT NULL,
    classifier_version_id INTEGER NOT NULL REFERENCES classifier_versions(id),
    started_at            TEXT NOT NULL,
    finished_at           TEXT,
    stats                 TEXT                    -- JSON
);

-- Phase 2: browser typing sessions.
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY,
    domain      TEXT NOT NULL,
    started_at  REAL NOT NULL,
    ended_at    REAL,
    client      TEXT
);

-- One unit of text: a submitted message (historical) or a typed field submission (prospective).
CREATE TABLE IF NOT EXISTS texts (
    id                    INTEGER PRIMARY KEY,
    source_key            TEXT NOT NULL,          -- sha256 of a stable source identifier
    classifier_version_id INTEGER NOT NULL REFERENCES classifier_versions(id),
    provenance            TEXT NOT NULL CHECK (provenance IN ('historical_submitted', 'prospective')),
    stage                 TEXT NOT NULL CHECK (stage IN ('submitted', 'initial', 'final')),
    domain                TEXT NOT NULL,
    observed_at           REAL NOT NULL,          -- unix seconds, UTC
    local_date            TEXT NOT NULL,          -- YYYY-MM-DD in the configured timezone
    import_id             INTEGER REFERENCES imports(id),
    session_id            INTEGER REFERENCES sessions(id),
    input_source          TEXT NOT NULL DEFAULT 'unknown',   -- keyboard|paste|dictation|programmatic|unknown
    input_confidence      REAL,
    typing_wpm            REAL,
    n_eligible            INTEGER NOT NULL,
    n_errors              INTEGER NOT NULL,
    n_excluded            INTEGER NOT NULL,
    exclusions            TEXT NOT NULL,          -- JSON {reason: words}
    UNIQUE (source_key, stage, classifier_version_id)
);
CREATE INDEX IF NOT EXISTS texts_cv_date ON texts (classifier_version_id, local_date);

-- One row per eligible English word occurrence. Rows are inserted in shuffled order within a
-- text so the original word order cannot be reconstructed from row ids.
CREATE TABLE IF NOT EXISTS word_observations (
    id                    INTEGER PRIMARY KEY,
    text_id               INTEGER NOT NULL REFERENCES texts(id) ON DELETE CASCADE,
    classifier_version_id INTEGER NOT NULL,
    provenance            TEXT NOT NULL,
    stage                 TEXT NOT NULL,
    domain                TEXT NOT NULL,
    local_date            TEXT NOT NULL,
    observed              TEXT NOT NULL,          -- lowercase form as typed
    intended              TEXT,                   -- NULL when not confidently inferred
    is_error              INTEGER NOT NULL,
    category              TEXT CHECK (category IN ('A', 'B', 'C')),
    resolution            TEXT,                   -- deterministic|segmentation|llm_context|ambiguous_intended|llm_abstained
    reason                TEXT,                   -- why the category was assigned
    edit_distance         INTEGER,
    edit_ops              TEXT,
    phon_match            INTEGER,
    candidates            TEXT,                   -- JSON list, errors only
    corrected             INTEGER,                -- prospective: corrected before submission
    typing_wpm            REAL
);
CREATE INDEX IF NOT EXISTS wo_cv_date ON word_observations (classifier_version_id, local_date);
CREATE INDEX IF NOT EXISTS wo_cv_intended ON word_observations (classifier_version_id, intended);

-- Frequent out-of-vocabulary tokens, for optional review of personal vocabulary.
CREATE TABLE IF NOT EXISTS unknown_tokens (
    classifier_version_id INTEGER NOT NULL,
    token                 TEXT NOT NULL,
    n                     INTEGER NOT NULL,
    first_date            TEXT NOT NULL,
    last_date             TEXT NOT NULL,
    PRIMARY KEY (classifier_version_id, token)
);

-- Frozen mastered-word cohorts.
CREATE TABLE IF NOT EXISTS cohorts (
    id                    INTEGER PRIMARY KEY,
    name                  TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    classifier_version_id INTEGER NOT NULL,
    params                TEXT NOT NULL,          -- JSON: window, stage, domain, thresholds
    UNIQUE (name, classifier_version_id)
);
CREATE TABLE IF NOT EXISTS cohort_words (
    cohort_id        INTEGER NOT NULL REFERENCES cohorts(id) ON DELETE CASCADE,
    word             TEXT NOT NULL,
    baseline_n       INTEGER NOT NULL,
    baseline_correct INTEGER NOT NULL,
    PRIMARY KEY (cohort_id, word)
);

CREATE TABLE IF NOT EXISTS llm_cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
