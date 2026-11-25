"""Statistics, mastered-word cohorts, version separation and the end-to-end report."""

import json
import math
from collections import Counter
from datetime import date

import numpy as np
import pytest

from spelling_observatory.analysis.mastered import difficult_words, freeze_cohort, load_cohort, word_stats
from spelling_observatory.analysis.metrics import Scope, daily
from spelling_observatory.analysis.stats import bootstrap_rate_difference, wilson
from spelling_observatory.classify.pipeline import TextAnalysis, WordObs
from spelling_observatory.importers.chatgpt import import_chatgpt
from spelling_observatory.report.build import build, text_summary
from spelling_observatory.report.html import render


def test_wilson_interval():
    p, lo, hi = wilson(5, 100)
    assert p == 0.05 and 0.02 < lo < 0.025 and 0.11 < hi < 0.115
    assert wilson(0, 50)[1] == 0.0
    assert all(math.isnan(x) for x in wilson(0, 0))


def test_bootstrap_covers_zero_without_change():
    rng = np.random.default_rng(1)
    n = np.full(60, 500.0)
    k1, k2 = rng.binomial(500, 0.005, 60).astype(float), rng.binomial(500, 0.005, 60).astype(float)
    r = bootstrap_rate_difference(k1, n, k2, n)
    assert r["lo"] < 0 < r["hi"]
    k3 = rng.binomial(500, 0.02, 60).astype(float)
    assert bootstrap_rate_difference(k1, n, k3, n)["lo"] > 0


def add(store, cv, day, words, key):
    a = TextAnalysis(words=words)
    store.add_text(key=key, cv_id=cv, provenance="historical_submitted", stage="submitted", domain="chatgpt.com",
                   observed_at=0.0, local_date=day, analysis=a)


def ok(w):
    return WordObs(w, w, False)


def err(obs, intended, cat="B"):
    return WordObs(obs, intended, True, cat, "deterministic", "pronunciation_preserved")


def test_cohort_is_frozen_and_distinguishes_difficult_words(store):
    cv = store.classifier_version_id("fp", {})
    for i in range(25):
        add(store, cv, f"2024-01-{i + 1:02d}", [ok("century"), ok("necessary"),
            err("definately", "definitely") if i % 4 == 0 else ok("definitely")], f"b{i}")
    scope = Scope(cv)
    cid = freeze_cohort(store, scope, "base", date(2024, 1, 1), date(2024, 1, 31), 20, 0.95)
    _, _, words = load_cohort(store.conn, "base", cv)
    assert set(words) == {"century", "necessary"}            # definitely: 76% accuracy, not mastered
    # Later errors must not change the frozen cohort.
    for i in range(10):
        add(store, cv, f"2024-06-{i + 1:02d}", [err("sentury", "century")], f"r{i}")
    _, _, again = load_cohort(store.conn, "base", cv)
    assert again == words
    diff = difficult_words(store.conn, scope, (date(2024, 1, 1), date(2024, 1, 31)),
                           (date(2024, 6, 1), date(2024, 6, 30)), 20, 0.95)
    assert [d[0] for d in diff] == ["definitely"]
    recent = word_stats(store.conn, scope, date(2024, 6, 1), date(2024, 6, 30))
    assert recent["century"].n == 10 and recent["century"].B == 10
    assert recent["century"].variants == Counter({"sentury": 10})


def test_classifier_versions_are_not_mixed(store):
    v1 = store.classifier_version_id("fp1", {"a": 1})
    v2 = store.classifier_version_id("fp2", {"a": 2})
    add(store, v1, "2024-01-01", [ok("a"), err("sentury", "century")], "k")
    add(store, v2, "2024-01-01", [ok("a"), ok("century")], "k")     # same source, new version
    d1, d2 = daily(store.conn, Scope(v1)), daily(store.conn, Scope(v2))
    assert (d1[0].words, d1[0].errors) == (2, 1)
    assert (d2[0].words, d2[0].errors) == (2, 0)
    assert store.latest_classifier_version() == v2


def test_domains_are_kept_separate(store):
    cv = store.classifier_version_id("fp", {})
    add(store, cv, "2024-01-01", [ok("a")], "x")
    store.add_text(key="y", cv_id=cv, provenance="historical_submitted", stage="submitted", domain="mail.google.com",
                   observed_at=0.0, local_date="2024-01-01", analysis=TextAnalysis(words=[ok("b"), ok("c")]))
    assert daily(store.conn, Scope(cv, domain="chatgpt.com"))[0].words == 1
    assert daily(store.conn, Scope(cv, domain="mail.google.com"))[0].words == 2
    assert daily(store.conn, Scope(cv))[0].words == 3


def test_retroactive_exclusion(store):
    cv = store.classifier_version_id("fp", {})
    add(store, cv, "2024-01-01", [ok("a"), err("kubectl", "kubect", "C"), ok("kubectl")], "x")
    assert store.apply_exclusion("kubectl") == 2
    n_el, n_err, ex = store.conn.execute("SELECT n_eligible, n_errors, exclusions FROM texts").fetchone()
    assert (n_el, n_err) == (1, 0) and json.loads(ex)["personal_vocabulary"] == 2


def test_end_to_end_synthetic_drift(analyzer, store, cfg, tmp_path):
    from make_synthetic_export import make_export
    path = tmp_path / "conversations.json"
    path.write_text(json.dumps(make_export(seed=3, days=730)))
    stats = import_chatgpt(path, analyzer, store, cfg.report.timezone, log=None)
    assert stats["duplicates_skipped"] > 0 and stats["messages_excluded_dictation"] > 0
    scope = Scope(store.latest_classifier_version())
    res = build(store, cfg, scope)
    c = res["comparison"]
    assert c["B"]["lo"] > 0                       # injected phonological drift is detected
    assert c["A"]["lo"] < 0 < c["A"]["hi"]        # stable mechanical rate is not flagged
    assert res["mastered"]["diff"]["hi"] < 0
    html = render(res)
    assert "<svg" in html and "Previously mastered words" in html and "http" not in html.split("<body>")[1]
    assert "Baseline" in text_summary(res)
    # Re-building reuses the frozen cohort.
    assert build(store, cfg, scope)["cohort"]["size"] == res["cohort"]["size"]
