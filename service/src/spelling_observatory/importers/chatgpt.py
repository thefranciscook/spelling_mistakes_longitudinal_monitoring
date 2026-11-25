"""Importer for the ChatGPT data export (Settings -> Data controls -> Export data).

Accepts the export .zip, the extracted directory, or a ``conversations.json`` file (newer
exports may split it into ``conversations-000.json`` etc.).

Only user-authored messages are analysed. Historical messages are *submitted* text: typing
speed, corrected mistakes and dictation are mostly unobservable, so they are stored with
provenance ``historical_submitted`` / stage ``submitted`` and are never compared with
prospective initial-typing metrics.
"""

from __future__ import annotations

import hashlib
import json
import random
import zipfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from ..classify.pipeline import Analyzer, TextAnalysis
from ..store.db import Store, source_key
from ..text.segment import word_ngrams

DOMAIN = "chatgpt.com"
DICTATION_HINTS = ("voice", "dictation", "audio", "speech")


@dataclass
class UserMessage:
    conversation_id: str
    message_id: str
    created: float
    text: str
    flag: str | None            # exclusion reason for the whole message, if any
    # Word n-grams of earlier assistant replies in the conversation. A live reference (not a
    # copy): the consumer must analyse the message before advancing the generator.
    prior_assistant: set


def _read_conversations(path: Path) -> tuple[list[dict], str]:
    h = hashlib.sha256()
    convs: list[dict] = []

    def take(name: str, blob: bytes):
        h.update(name.encode())
        h.update(blob)
        data = json.loads(blob)
        convs.extend(data if isinstance(data, list) else data.get("conversations", []))

    if path.is_file() and path.suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            names = sorted(n for n in zf.namelist() if Path(n).name.startswith("conversations") and n.endswith(".json"))
            for n in names:
                take(Path(n).name, zf.read(n))
    elif path.is_dir():
        for p in sorted(path.glob("conversations*.json")):
            take(p.name, p.read_bytes())
    else:
        take(path.name, path.read_bytes())
    if not convs:
        raise ValueError(f"No conversations found in {path}")
    return convs, h.hexdigest()


def _text_parts(content: dict) -> tuple[str, bool]:
    """Return (text, contains_audio_transcription)."""
    parts = content.get("parts") or []
    texts, audio = [], False
    for p in parts:
        if isinstance(p, str):
            texts.append(p)
        elif isinstance(p, dict):
            ctype = str(p.get("content_type", ""))
            if "audio" in ctype or "transcription" in ctype:
                audio = True
    if not parts and isinstance(content.get("text"), str):
        texts.append(content["text"])
    return "\n\n".join(t for t in texts if t), audio


def _dictation_flag(meta: dict) -> bool:
    for k, v in (meta or {}).items():
        if any(h in k.lower() for h in DICTATION_HINTS) and v not in (None, False, "", 0, [], {}):
            return True
    return False


def iter_user_messages(conv: dict, ngram: int, max_chars: int) -> Iterator[UserMessage]:
    conv_id = str(conv.get("conversation_id") or conv.get("id") or conv.get("title", ""))
    conv_time = float(conv.get("create_time") or 0)
    nodes = []
    for node in (conv.get("mapping") or {}).values():
        msg = node.get("message")
        if not msg:
            continue
        role = (msg.get("author") or {}).get("role")
        created = float(msg.get("create_time") or conv_time or 0)
        nodes.append((created, role, msg))
    nodes.sort(key=lambda x: x[0])
    assistant_ngrams: set = set()
    user_texts: set[str] = set()
    for created, role, msg in nodes:
        content = msg.get("content") or {}
        ctype = content.get("content_type", "")
        meta = msg.get("metadata") or {}
        text, audio = _text_parts(content) if ctype in ("text", "multimodal_text") else ("", False)
        if role == "assistant":
            if text:
                assistant_ngrams |= word_ngrams(text, ngram)
            continue
        if role != "user":
            continue
        flag = None
        if meta.get("is_visually_hidden_from_conversation") or meta.get("is_user_system_message"):
            flag = "hidden_message"
        elif ctype not in ("text", "multimodal_text"):
            flag = "non_text_message"
        elif audio or _dictation_flag(meta):
            flag = "dictation"
        elif len(text) > max_chars:
            flag = "suspected_paste"
        if not text.strip() and flag is None:
            continue
        normalised = " ".join(text.split()).lower()
        if flag is None and normalised in user_texts:
            # Exact re-submission. Only *identical* repeats are dropped: dropping near-repeats
            # would remove error-free copies while keeping copies with a new typo (bias).
            flag = "repeated_text"
        user_texts.add(normalised)
        yield UserMessage(conv_id, str(msg.get("id")), created, text, flag, assistant_ngrams)


QUOTE_OVERLAP = 0.5


def _overlap_checker(m: UserMessage, n: int):
    """Exclude sentences mostly copied from earlier assistant replies in the conversation."""
    def check(sentence: str) -> str | None:
        grams = word_ngrams(sentence, n)
        if grams and len(grams & m.prior_assistant) / len(grams) >= QUOTE_OVERLAP:
            return "quoted_assistant"
        return None
    return check


def import_chatgpt(path: Path, analyzer: Analyzer, store: Store, timezone_name: str,
                   batch_size: int = 200, log=print) -> dict:
    convs, sha = _read_conversations(Path(path))
    cv_id = store.classifier_version_id(analyzer.fingerprint(), analyzer.components())
    import_id = store.start_import("chatgpt_export", sha, cv_id)
    tz = ZoneInfo(timezone_name)
    ccfg = analyzer.cfg.classifier
    stats: Counter = Counter(conversations=len(convs))
    rng = random.Random(0)
    batch: list[tuple[UserMessage, TextAnalysis | None]] = []
    seen: set[str] = set()

    def flush():
        analyses = [a for _, a in batch if a is not None]
        stats.update(analyzer.resolve_pending(analyses, cache=store, log=log))
        for m, a in batch:
            key = source_key("chatgpt", m.message_id)
            dt = datetime.fromtimestamp(m.created, tz=timezone.utc)
            if a is None:  # whole message excluded
                a = TextAnalysis()
                a.excluded[m.flag] += max(1, len(m.text.split()))
            input_source = "dictation" if m.flag == "dictation" else "unknown"
            if store.add_text(key=key, cv_id=cv_id, provenance="historical_submitted", stage="submitted",
                              domain=DOMAIN, observed_at=m.created, local_date=dt.astimezone(tz).date().isoformat(),
                              analysis=a, import_id=import_id, input_source=input_source, rng=rng) is None:
                stats["duplicates_skipped"] += 1
                continue
            stats["eligible_words"] += a.n_eligible
            stats["errors"] += a.n_errors
            for w in a.words:
                if w.is_error:
                    stats[f"category_{w.category}"] += 1
        store.commit()
        batch.clear()

    for conv in convs:
        for m in iter_user_messages(conv, ccfg.assistant_overlap_ngram, ccfg.max_message_chars):
            stats["user_messages"] += 1
            key = source_key("chatgpt", m.message_id)
            if key in seen or store.has_text(key, "submitted", cv_id):
                stats["duplicates_skipped"] += 1
                continue
            seen.add(key)
            if m.flag:
                stats[f"messages_excluded_{m.flag}"] += 1
                batch.append((m, None))
            else:
                batch.append((m, analyzer.analyze(m.text, skip_sentence=_overlap_checker(m, ccfg.assistant_overlap_ngram))))
            if len(batch) >= batch_size:
                flush()
                if log:
                    log(f"  {stats['user_messages']} user messages processed ...")
    flush()
    result = dict(sorted(stats.items()))
    result["classifier_version_id"] = cv_id
    store.finish_import(import_id, result)
    return result
