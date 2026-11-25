"""Storage: encryption at rest, file permissions, export."""

import json
import os
import sqlite3
import stat

import pytest

from spelling_observatory.store import db


def test_encrypted_database_is_unreadable_without_key(cfg, monkeypatch):
    pytest.importorskip("sqlcipher3")
    monkeypatch.setattr(db, "_database_key", lambda path: "ab" * 32)
    cfg.storage.encryption = "sqlcipher"
    s = db.connect(cfg)
    s.classifier_version_id("fp", {"x": 1})
    s.close()
    with pytest.raises(sqlite3.DatabaseError):
        sqlite3.connect(str(cfg.database_path)).execute("SELECT * FROM classifier_versions").fetchall()
    s = db.connect(cfg)   # reopens with the key
    assert s.classifier_versions()[0][1] == "fp"
    s.close()


def test_database_file_is_private(store, cfg):
    mode = stat.S_IMODE(os.stat(cfg.database_path).st_mode)
    assert mode == 0o600
    assert stat.S_IMODE(os.stat(cfg.home).st_mode) == 0o700


def test_export_contains_all_tables(store, tmp_path):
    store.classifier_version_id("fp", {})
    out = tmp_path / "export.json"
    store.export_json(out)
    data = json.loads(out.read_text())
    assert {"texts", "word_observations", "cohorts", "classifier_versions"} <= set(data)
    assert stat.S_IMODE(os.stat(out).st_mode) == 0o600
