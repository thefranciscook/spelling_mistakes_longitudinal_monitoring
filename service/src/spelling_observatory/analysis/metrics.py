"""Longitudinal error-rate metrics.

All queries are restricted to one classifier version and one (provenance, stage) pair, so
historical submitted text is only compared with submitted text, and classifier changes
never mix silently. Domain filtering keeps writing environments separate.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from .stats import bootstrap_rate_difference, wilson

CATEGORIES = ("A", "B", "C")


@dataclass
class Scope:
    classifier_version_id: int
    provenance: str = "historical_submitted"
    stage: str = "submitted"
    domain: str | None = None

    def where(self, alias: str = "") -> tuple[str, list]:
        p = f"{alias}." if alias else ""
        sql = f"{p}classifier_version_id=? AND {p}provenance=? AND {p}stage=?"
        args: list = [self.classifier_version_id, self.provenance, self.stage]
        if self.domain:
            sql += f" AND {p}domain=?"
            args.append(self.domain)
        return sql, args


@dataclass
class DayRow:
    day: date
    words: int = 0
    errors: int = 0
    A: int = 0
    B: int = 0
    C: int = 0
    resolved: int = 0          # errors with an inferred intended word
    excluded: int = 0
    exclusions: Counter = field(default_factory=Counter)


def daily(conn, scope: Scope) -> list[DayRow]:
    where, args = scope.where()
    rows: dict[str, DayRow] = {}
    for d, n_el, n_ex, ex in conn.execute(
            f"SELECT local_date, n_eligible, n_excluded, exclusions FROM texts WHERE {where}", args):
        r = rows.setdefault(d, DayRow(date.fromisoformat(d)))
        r.words += n_el
        r.excluded += n_ex
        r.exclusions.update(json.loads(ex))
    for d, cat, n, resolved in conn.execute(
            f"SELECT local_date, category, COUNT(*), SUM(intended IS NOT NULL) FROM word_observations "
            f"WHERE {where} AND is_error=1 GROUP BY local_date, category", args):
        r = rows.setdefault(d, DayRow(date.fromisoformat(d)))
        setattr(r, cat, getattr(r, cat) + n)
        r.errors += n
        r.resolved += resolved or 0
    return sorted(rows.values(), key=lambda r: r.day)


@dataclass
class PeriodStats:
    label: str
    start: date
    end: date
    days: int
    words: int
    errors: int
    A: int
    B: int
    C: int
    resolved: int
    excluded: int
    sufficient: bool

    def rate(self, attr: str) -> tuple[float, float, float]:
        """Per 1,000 eligible words, with a Wilson 95% interval."""
        p, lo, hi = wilson(getattr(self, attr), self.words)
        return p * 1000, lo * 1000, hi * 1000

    @property
    def coverage(self) -> float:
        return (self.A + self.B) / self.errors if self.errors else float("nan")

    @property
    def excluded_share(self) -> float:
        tot = self.words + self.excluded
        return self.excluded / tot if tot else float("nan")


def summarise(rows: list[DayRow], label: str, start: date, end: date, min_words: int) -> PeriodStats:
    sel = [r for r in rows if start <= r.day <= end]
    s = PeriodStats(label, start, end, len(sel), sum(r.words for r in sel), sum(r.errors for r in sel),
                    sum(r.A for r in sel), sum(r.B for r in sel), sum(r.C for r in sel),
                    sum(r.resolved for r in sel), sum(r.excluded for r in sel), False)
    s.sufficient = s.words >= min_words
    return s


def month_periods(rows: list[DayRow], min_words: int) -> list[PeriodStats]:
    if not rows:
        return []
    out = []
    y, m = rows[0].day.year, rows[0].day.month
    last = rows[-1].day
    while date(y, m, 1) <= last:
        start = date(y, m, 1)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        end = date(ny, nm, 1) - timedelta(days=1)
        out.append(summarise(rows, start.strftime("%Y-%m"), start, end, min_words))
        y, m = ny, nm
    return out


def week_periods(rows: list[DayRow], min_words: int) -> list[PeriodStats]:
    if not rows:
        return []
    start = rows[0].day - timedelta(days=rows[0].day.weekday())
    out = []
    while start <= rows[-1].day:
        end = start + timedelta(days=6)
        out.append(summarise(rows, start.isoformat(), start, end, min_words))
        start = end + timedelta(days=1)
    return out


def rolling_periods(rows: list[DayRow], window_days: int, min_words: int, step_days: int = 7) -> list[PeriodStats]:
    """Trailing windows evaluated every `step_days`, ending on the last observed day."""
    if not rows:
        return []
    first, last = rows[0].day, rows[-1].day
    ends = []
    end = last
    while end - timedelta(days=window_days - 1) >= first:
        ends.append(end)
        end -= timedelta(days=step_days)
    return [summarise(rows, f"{e.isoformat()} ({window_days}d)", e - timedelta(days=window_days - 1), e, min_words)
            for e in reversed(ends)]


def compare_windows(rows: list[DayRow], base: tuple[date, date], recent: tuple[date, date],
                    iterations: int = 2000) -> dict:
    """Baseline vs recent rate differences (per 1,000 words) with day-clustered bootstrap CIs."""
    b = [r for r in rows if base[0] <= r.day <= base[1] and r.words > 0]
    rc = [r for r in rows if recent[0] <= r.day <= recent[1] and r.words > 0]
    out = {}
    bn = np.array([r.words for r in b], float)
    rn = np.array([r.words for r in rc], float)
    for attr in ("errors", "A", "B", "C"):
        bk = np.array([getattr(r, attr) for r in b], float)
        rk = np.array([getattr(r, attr) for r in rc], float)
        res = bootstrap_rate_difference(bk, bn, rk, rn, iterations)
        out[attr] = {k: v * 1000 for k, v in res.items()}
    # Phonological share of classified errors: does B grow *relative to* A?
    bk = np.array([r.B for r in b], float)
    bd = np.array([r.A + r.B for r in b], float)
    rk = np.array([r.B for r in rc], float)
    rd = np.array([r.A + r.B for r in rc], float)
    out["B_share"] = bootstrap_rate_difference(bk, bd, rk, rd, iterations)
    out["base_days"], out["recent_days"] = len(b), len(rc)
    return out
