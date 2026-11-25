"""Previously mastered word tracking.

A word's accuracy counts every eligible occurrence whose intended word is known: correct
spellings (intended = observed) and misspellings whose intended word was inferred.
Misspellings with an unresolved intended word cannot be attributed to a word and are left
out of word-level statistics (they still count in the overall error rates).

A *cohort* is the set of words that met the mastery thresholds in a fixed baseline window.
It is frozen when created and stored, so later errors cannot redefine it.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from ..store.db import now_iso
from .metrics import Scope
from .stats import bootstrap_rate_difference, wilson


@dataclass
class WordWindow:
    n: int = 0
    correct: int = 0
    A: int = 0
    B: int = 0
    C: int = 0
    variants: Counter = field(default_factory=Counter)
    days: set = field(default_factory=set)

    @property
    def accuracy(self) -> float:
        return self.correct / self.n if self.n else float("nan")


def word_stats(conn, scope: Scope, start: date, end: date) -> dict[str, WordWindow]:
    where, args = scope.where()
    out: dict[str, WordWindow] = defaultdict(WordWindow)
    for word, observed, is_error, cat, d, n in conn.execute(
            f"SELECT intended, observed, is_error, category, local_date, COUNT(*) FROM word_observations "
            f"WHERE {where} AND intended IS NOT NULL AND instr(intended, ' ') = 0 AND local_date BETWEEN ? AND ? "
            f"GROUP BY intended, observed, is_error, category, local_date",
            args + [start.isoformat(), end.isoformat()]):
        w = out[word]
        w.n += n
        w.days.add(d)
        if is_error:
            setattr(w, cat, getattr(w, cat) + n)
            w.variants[observed] += n
        else:
            w.correct += n
    return dict(out)


def freeze_cohort(store, scope: Scope, name: str, start: date, end: date, min_n: int, min_acc: float) -> int:
    stats = word_stats(store.conn, scope, start, end)
    params = {"baseline_start": start.isoformat(), "baseline_end": end.isoformat(), "provenance": scope.provenance,
              "stage": scope.stage, "domain": scope.domain, "min_observations": min_n, "min_accuracy": min_acc}
    conn = store.conn
    conn.execute("DELETE FROM cohorts WHERE name=? AND classifier_version_id=?", (name, scope.classifier_version_id))
    cur = conn.execute("INSERT INTO cohorts (name, created_at, classifier_version_id, params) VALUES (?,?,?,?)",
                       (name, now_iso(), scope.classifier_version_id, json.dumps(params, sort_keys=True)))
    cid = cur.lastrowid
    conn.executemany("INSERT INTO cohort_words VALUES (?,?,?,?)",
                     [(cid, w, s.n, s.correct) for w, s in stats.items() if s.n >= min_n and s.accuracy >= min_acc])
    conn.execute("DELETE FROM cohort_words WHERE cohort_id NOT IN (SELECT id FROM cohorts)")
    conn.commit()
    return cid


def load_cohort(conn, name: str, cv_id: int) -> tuple[int, dict, dict[str, tuple[int, int]]] | None:
    row = conn.execute("SELECT id, params FROM cohorts WHERE name=? AND classifier_version_id=?", (name, cv_id)).fetchone()
    if not row:
        return None
    words = {w: (n, c) for w, n, c in conn.execute(
        "SELECT word, baseline_n, baseline_correct FROM cohort_words WHERE cohort_id=?", (row[0],))}
    return row[0], json.loads(row[1]), words


@dataclass
class WordComparison:
    word: str
    base_n: int
    base_correct: int
    recent: WordWindow
    reportable: bool

    @property
    def base_accuracy(self) -> float:
        return self.base_correct / self.base_n if self.base_n else float("nan")

    @property
    def change(self) -> float:
        return self.recent.accuracy - self.base_accuracy if self.recent.n else float("nan")

    def recent_interval(self) -> tuple[float, float, float]:
        return wilson(self.recent.correct, self.recent.n)


def _daily_correct(conn, scope: Scope, words: set[str], start: date, end: date) -> tuple[np.ndarray, np.ndarray]:
    where, args = scope.where()
    by_day: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for word, d, is_error, n in conn.execute(
            f"SELECT intended, local_date, is_error, COUNT(*) FROM word_observations WHERE {where} "
            f"AND intended IS NOT NULL AND local_date BETWEEN ? AND ? GROUP BY intended, local_date, is_error",
            args + [start.isoformat(), end.isoformat()]):
        if word in words:
            by_day[d][1] += n
            if not is_error:
                by_day[d][0] += n
    vals = list(by_day.values())
    return np.array([v[0] for v in vals], float), np.array([v[1] for v in vals], float)


def compare_cohort(conn, scope: Scope, cohort_words: dict[str, tuple[int, int]], base: tuple[date, date],
                   recent: tuple[date, date], min_recent: int, iterations: int = 2000) -> dict:
    recent_stats = word_stats(conn, scope, *recent)
    words = []
    for w, (bn, bc) in cohort_words.items():
        rw = recent_stats.get(w, WordWindow())
        words.append(WordComparison(w, bn, bc, rw, rw.n >= min_recent))
    ws = set(cohort_words)
    bk, bn = _daily_correct(conn, scope, ws, *base)
    rk, rn = _daily_correct(conn, scope, ws, *recent)
    diff = bootstrap_rate_difference(bk, bn, rk, rn, iterations)
    return {
        "words": words,
        "base_accuracy": bk.sum() / bn.sum() if bn.sum() else float("nan"),
        "recent_accuracy": rk.sum() / rn.sum() if rn.sum() else float("nan"),
        "base_n": int(bn.sum()), "recent_n": int(rn.sum()),
        "diff": diff,
        "recent_B": sum(c.recent.B for c in words),
        "recent_A": sum(c.recent.A for c in words),
        "recent_C": sum(c.recent.C for c in words),
    }


def difficult_words(conn, scope: Scope, base: tuple[date, date], recent: tuple[date, date], min_n: int,
                    max_acc: float) -> list[tuple[str, WordWindow, WordWindow]]:
    """Words seen often in the baseline but *not* mastered: consistently difficult words."""
    b = word_stats(conn, scope, *base)
    r = word_stats(conn, scope, *recent)
    out = [(w, s, r.get(w, WordWindow())) for w, s in b.items() if s.n >= min_n and s.accuracy < max_acc]
    return sorted(out, key=lambda x: x[1].accuracy)
