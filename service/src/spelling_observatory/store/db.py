"""SQLite storage, optionally encrypted with SQLCipher (key held in the OS keychain)."""

from __future__ import annotations

import hashlib
import importlib.resources as ir
import json
import os
import random
import secrets
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .. import SCHEMA_VERSION
from ..config import Config

KEYRING_SERVICE = "spelling-observatory"


def keyring_account(path: Path) -> str:
    """One Keychain entry per database file, so a demo home never touches the real key."""
    return "database-key:" + hashlib.sha256(str(Path(path).resolve()).encode()).hexdigest()[:16]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def source_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _database_key(path: Path) -> str:
    import keyring  # optional dependency ("secure" extra)

    account = keyring_account(path)
    key = keyring.get_password(KEYRING_SERVICE, account)
    if not key:
        if Path(path).exists() and Path(path).stat().st_size > 0:
            raise RuntimeError(f"No Keychain key for existing encrypted database {path}; refusing to create a new one.")
        key = secrets.token_hex(32)
        keyring.set_password(KEYRING_SERVICE, account, key)
    return key


def connect(cfg: Config):
    path = cfg.database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if cfg.storage.encryption == "sqlcipher":
        try:
            import sqlcipher3
        except ImportError as e:
            raise RuntimeError(
                "Encryption is enabled but sqlcipher3 is not installed. Install the 'secure' extra "
                "(pip install 'spelling-observatory[secure]') or set storage.encryption = \"none\"."
            ) from e
        conn = sqlcipher3.connect(str(path))
        conn.execute(f"PRAGMA key = \"x'{_database_key(path)}'\"")
    elif cfg.storage.encryption == "none":
        conn = sqlite3.connect(str(path))
    else:
        raise ValueError(f"Unknown storage.encryption: {cfg.storage.encryption}")
    os.chmod(path, 0o600)
    conn.row_factory = None
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return Store(conn)


class Store:
    def __init__(self, conn):
        self.conn = conn
        schema = (ir.files("spelling_observatory.store") / "schema.sql").read_text()
        conn.executescript(schema)
        row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
        elif int(row[0]) != SCHEMA_VERSION:
            raise RuntimeError(f"Database schema {row[0]} != expected {SCHEMA_VERSION}; migration required")
        conn.commit()

    def close(self):
        self.conn.close()

    # --- classifier versions -----------------------------------------------------------

    def classifier_version_id(self, fingerprint: str, components: dict) -> int:
        row = self.conn.execute("SELECT id FROM classifier_versions WHERE fingerprint=?", (fingerprint,)).fetchone()
        if row:
            return row[0]
        cur = self.conn.execute(
            "INSERT INTO classifier_versions (fingerprint, components, created_at) VALUES (?,?,?)",
            (fingerprint, json.dumps(components, sort_keys=True), now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def latest_classifier_version(self) -> int | None:
        row = self.conn.execute(
            "SELECT classifier_version_id FROM texts GROUP BY classifier_version_id "
            "ORDER BY MAX(id) DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def classifier_versions(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT cv.id, cv.fingerprint, cv.created_at, COUNT(t.id), cv.components FROM classifier_versions cv "
            "LEFT JOIN texts t ON t.classifier_version_id = cv.id GROUP BY cv.id ORDER BY cv.id"
        ).fetchall()

    # --- imports -----------------------------------------------------------------------

    def start_import(self, kind: str, sha: str, cv_id: int) -> int:
        cur = self.conn.execute(
            "INSERT INTO imports (kind, source_sha256, classifier_version_id, started_at) VALUES (?,?,?,?)",
            (kind, sha, cv_id, now_iso()),
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_import(self, import_id: int, stats: dict) -> None:
        self.conn.execute("UPDATE imports SET finished_at=?, stats=? WHERE id=?",
                          (now_iso(), json.dumps(stats, sort_keys=True), import_id))
        self.conn.commit()

    # --- texts and words ---------------------------------------------------------------

    def has_text(self, key: str, stage: str, cv_id: int) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM texts WHERE source_key=? AND stage=? AND classifier_version_id=?", (key, stage, cv_id)
        ).fetchone() is not None

    def add_text(self, *, key: str, cv_id: int, provenance: str, stage: str, domain: str, observed_at: float,
                 local_date: str, analysis, import_id: int | None = None, input_source: str = "unknown",
                 input_confidence: float | None = None, typing_wpm: float | None = None,
                 extra_exclusions: Counter | None = None, rng: random.Random | None = None) -> int | None:
        """Insert a text and its word observations. Returns None if it was already stored."""
        exclusions = Counter(analysis.excluded)
        if extra_exclusions:
            exclusions.update(extra_exclusions)
        try:
            cur = self.conn.execute(
                "INSERT INTO texts (source_key, classifier_version_id, provenance, stage, domain, observed_at, "
                "local_date, import_id, input_source, input_confidence, typing_wpm, n_eligible, n_errors, "
                "n_excluded, exclusions) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, cv_id, provenance, stage, domain, observed_at, local_date, import_id, input_source,
                 input_confidence, typing_wpm, analysis.n_eligible, analysis.n_errors,
                 sum(exclusions.values()), json.dumps(dict(sorted(exclusions.items())))),
            )
        except sqlite3.IntegrityError:
            return None
        except Exception as e:  # sqlcipher3 raises its own IntegrityError class
            if type(e).__name__ == "IntegrityError":
                return None
            raise
        text_id = cur.lastrowid
        words = list(analysis.words)
        (rng or random).shuffle(words)
        self.conn.executemany(
            "INSERT INTO word_observations (text_id, classifier_version_id, provenance, stage, domain, local_date, "
            "observed, intended, is_error, category, resolution, reason, edit_distance, edit_ops, phon_match, "
            "candidates, typing_wpm) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(text_id, cv_id, provenance, stage, domain, local_date, w.observed, w.intended, int(w.is_error),
              w.category, w.resolution or None, w.reason or None, w.edit_distance, w.edit_ops or None,
              None if w.phon_match is None else int(w.phon_match),
              json.dumps(w.candidates) if w.is_error else None, typing_wpm) for w in words],
        )
        for token, n in analysis.unknown.items():
            self.conn.execute(
                "INSERT INTO unknown_tokens (classifier_version_id, token, n, first_date, last_date) "
                "VALUES (?,?,?,?,?) ON CONFLICT (classifier_version_id, token) DO UPDATE SET "
                "n = n + excluded.n, first_date = MIN(first_date, excluded.first_date), "
                "last_date = MAX(last_date, excluded.last_date)",
                (cv_id, token, n, local_date, local_date),
            )
        return text_id

    def commit(self):
        self.conn.commit()

    # --- llm cache ---------------------------------------------------------------------

    def get(self, key: str) -> dict | None:
        row = self.conn.execute("SELECT value FROM llm_cache WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: dict) -> None:
        self.conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?)", (key, json.dumps(value), now_iso()))

    # --- maintenance -------------------------------------------------------------------

    def apply_exclusion(self, word: str) -> int:
        """Retroactively remove observations of a newly excluded word (as typed or intended)."""
        word = word.lower()
        rows = self.conn.execute(
            "SELECT text_id, is_error, COUNT(*) FROM word_observations WHERE observed=? OR intended=? "
            "GROUP BY text_id, is_error", (word, word)
        ).fetchall()
        for text_id, is_error, n in rows:
            ex = json.loads(self.conn.execute("SELECT exclusions FROM texts WHERE id=?", (text_id,)).fetchone()[0])
            ex["personal_vocabulary"] = ex.get("personal_vocabulary", 0) + n
            self.conn.execute(
                "UPDATE texts SET n_eligible = n_eligible - ?, n_errors = n_errors - ?, n_excluded = n_excluded + ?, "
                "exclusions = ? WHERE id=?", (n, n if is_error else 0, n, json.dumps(ex), text_id))
        cur = self.conn.execute("DELETE FROM word_observations WHERE observed=? OR intended=?", (word, word))
        self.conn.execute("DELETE FROM unknown_tokens WHERE token=?", (word,))
        self.conn.commit()
        return cur.rowcount

    def prune_versions(self, keep_id: int) -> int:
        n = self.conn.execute("SELECT COUNT(*) FROM texts WHERE classifier_version_id != ?", (keep_id,)).fetchone()[0]
        for table in ("word_observations", "texts", "unknown_tokens", "cohorts"):
            self.conn.execute(f"DELETE FROM {table} WHERE classifier_version_id != ?", (keep_id,))
        self.conn.execute("DELETE FROM cohort_words WHERE cohort_id NOT IN (SELECT id FROM cohorts)")
        self.conn.commit()
        self.conn.execute("VACUUM")
        return n

    def export_json(self, path: Path) -> None:
        tables = ["schema_meta", "classifier_versions", "imports", "sessions", "texts", "word_observations",
                  "unknown_tokens", "cohorts", "cohort_words"]
        out = {}
        for t in tables:
            cur = self.conn.execute(f"SELECT * FROM {t}")
            cols = [d[0] for d in cur.description]
            out[t] = [dict(zip(cols, r)) for r in cur.fetchall()]
        path.write_text(json.dumps(out, indent=1))
        os.chmod(path, 0o600)
