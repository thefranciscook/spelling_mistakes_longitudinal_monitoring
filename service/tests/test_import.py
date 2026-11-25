"""ChatGPT export import: user-only extraction, provenance, deduplication, repeatability."""

import json
import zipfile

import pytest

from spelling_observatory.importers.chatgpt import import_chatgpt

T0 = 1_717_243_200.0  # 2024-06-01 12:00 UTC


def node(mid, role, text, t, ctype="text", meta=None, parts=None):
    return {mid: {"id": mid, "parent": None, "children": [], "message": {
        "id": mid, "author": {"role": role}, "create_time": t,
        "content": {"content_type": ctype, "parts": parts if parts is not None else [text]}, "metadata": meta or {}}}}


def conversation(cid, *nodes):
    mapping = {}
    for n in nodes:
        mapping.update(n)
    return {"conversation_id": cid, "title": cid, "create_time": T0, "mapping": mapping}


@pytest.fixture
def export(tmp_path):
    convs = [
        conversation(
            "c1",
            node("m1", "user", "the last sentury was full of surprises", T0),
            node("m2", "assistant", "Indeed the twentieth century brought enormous technological and social change.", T0 + 5),
            node("m3", "user", "you said the twentieth century brought enormous technological and social change", T0 + 60),
            node("m4", "user", "the sentury was long", T0 + 70, meta={"voice_mode_message": True}),
            node("m5", "system", "You are ChatGPT, a helpful assistant", T0 - 1),
            node("m6", "user", "custom instructions here", T0 + 1, ctype="user_editable_context"),
            node("m7", "user", "the last sentury was full of surprises", T0 + 90),       # identical resend
            node("m8", "user", "", T0 + 95, ctype="multimodal_text",
                 parts=[{"content_type": "audio_transcription", "text": "dictated sentury words"}]),
        ),
        conversation("c2", node("m9", "user", "szia holnap megnezem", T0 + 86_400)),
    ]
    convs.append(json.loads(json.dumps(convs[0])))  # duplicated conversation within the export
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(convs))
    return path


def test_import_extracts_user_text_only(analyzer, store, cfg, export):
    stats = import_chatgpt(export, analyzer, store, cfg.report.timezone, log=None)
    assert stats["errors"] == 1 and stats["category_B"] == 1
    rows = store.conn.execute("SELECT observed, intended, category FROM word_observations WHERE is_error=1").fetchall()
    assert rows == [("sentury", "century", "B")]
    # The assistant's text and the system prompt never become observations.
    assert store.conn.execute("SELECT COUNT(*) FROM word_observations WHERE observed='enormous'").fetchone()[0] == 0
    texts = dict(store.conn.execute("SELECT input_source, COUNT(*) FROM texts GROUP BY input_source").fetchall())
    assert texts["dictation"] == 2
    ex = {}
    for (e,) in store.conn.execute("SELECT exclusions FROM texts"):
        for k, v in json.loads(e).items():
            ex[k] = ex.get(k, 0) + v
    assert ex["quoted_assistant"] > 0
    assert ex["repeated_text"] > 0
    assert ex["non_text_message"] > 0
    assert ex["non_english"] > 0


def test_local_dates_use_configured_timezone(analyzer, store, cfg, export):
    import_chatgpt(export, analyzer, store, "America/Toronto", log=None)
    dates = {d for (d,) in store.conn.execute("SELECT local_date FROM texts")}
    assert "2024-06-01" in dates and "2024-06-02" in dates


def test_duplicates_within_export_are_skipped(analyzer, store, cfg, export):
    stats = import_chatgpt(export, analyzer, store, cfg.report.timezone, log=None)
    assert stats["duplicates_skipped"] == 6     # the six user messages of the duplicated conversation
    n_texts = store.conn.execute("SELECT COUNT(*) FROM texts").fetchone()[0]
    assert n_texts == 7


def test_reimport_is_idempotent(analyzer, store, cfg, export):
    import_chatgpt(export, analyzer, store, cfg.report.timezone, log=None)
    before = store.conn.execute("SELECT COUNT(*) FROM word_observations").fetchone()[0]
    stats = import_chatgpt(export, analyzer, store, cfg.report.timezone, log=None)
    after = store.conn.execute("SELECT COUNT(*) FROM word_observations").fetchone()[0]
    assert before == after
    assert stats.get("eligible_words", 0) == 0
    assert store.conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0] == 2


def test_zip_and_split_exports(analyzer, store, cfg, export, tmp_path):
    data = json.loads(export.read_text())
    z = tmp_path / "export.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("conversations-000.json", json.dumps(data[:1]))
        zf.writestr("conversations-001.json", json.dumps(data[1:]))
        zf.writestr("chat.html", "<html></html>")
    stats = import_chatgpt(z, analyzer, store, cfg.report.timezone, log=None)
    assert stats["conversations"] == 3 and stats["errors"] == 1


def test_word_order_is_not_stored(analyzer, store, cfg, tmp_path):
    text = " ".join(["alpha", "the", "quick", "brown", "fox", "jumps", "over", "the", "lazy", "dog", "today"] * 3)
    p = tmp_path / "c.json"
    p.write_text(json.dumps([conversation("c", node("m", "user", text, T0))]))
    import_chatgpt(p, analyzer, store, cfg.report.timezone, log=None)
    stored = [w for (w,) in store.conn.execute("SELECT observed FROM word_observations ORDER BY id")]
    assert sorted(stored) == sorted(w.observed for w in analyzer.analyze(text).words)
    assert stored != [w.observed for w in analyzer.analyze(text).words]
    assert store.conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE sql LIKE '%message_text%'").fetchone()[0] == 0
