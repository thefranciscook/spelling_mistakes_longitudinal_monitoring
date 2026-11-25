"""Retrospective report: computes all results once, renders text and HTML."""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from ..analysis.mastered import compare_cohort, difficult_words, freeze_cohort, load_cohort
from ..analysis.metrics import Scope, compare_windows, daily, month_periods, rolling_periods, summarise, week_periods
from ..config import Config


@dataclass
class Windows:
    first: date
    last: date
    base: tuple[date, date]
    recent: tuple[date, date]
    comparable: bool


def choose_windows(cfg: Config, first: date, last: date) -> Windows:
    r = cfg.report
    bs = date.fromisoformat(r.baseline_start) if r.baseline_start else first
    be = date.fromisoformat(r.baseline_end) if r.baseline_end else bs + timedelta(days=r.default_baseline_days - 1)
    rs = last - timedelta(days=r.recent_days - 1)
    return Windows(first, last, (bs, be), (rs, last), comparable=rs > be)


def build(store, cfg: Config, scope: Scope) -> dict:
    conn = store.conn
    rows = daily(conn, scope)
    if not rows:
        raise RuntimeError("No observations for this scope. Import data first.")
    mw = cfg.report.min_period_words
    win = choose_windows(cfg, rows[0].day, rows[-1].day)
    res: dict = {"scope": scope, "windows": win}
    res["overall"] = summarise(rows, "All data", win.first, win.last, mw)
    res["baseline"] = summarise(rows, "Baseline", *win.base, mw)
    res["recent"] = summarise(rows, f"Last {cfg.report.recent_days} days", *win.recent, mw)
    res["months"] = month_periods(rows, mw)
    res["weeks"] = week_periods(rows, max(1, mw // 4))
    res["rolling30"] = rolling_periods(rows, 30, mw)
    res["rolling90"] = rolling_periods(rows, 90, mw)
    res["comparison"] = compare_windows(rows, win.base, win.recent, cfg.report.bootstrap_iterations) if win.comparable else None

    m = cfg.mastered
    name = f"baseline:{win.base[0]}:{win.base[1]}:{scope.domain or 'all'}"
    cohort = load_cohort(conn, name, scope.classifier_version_id)
    if cohort is None:
        freeze_cohort(store, scope, name, *win.base, m.min_baseline_observations, m.min_baseline_accuracy)
        cohort = load_cohort(conn, name, scope.classifier_version_id)
    _, params, words = cohort
    res["cohort"] = {"name": name, "params": params, "size": len(words)}
    res["mastered"] = compare_cohort(conn, scope, words, win.base, win.recent, m.min_recent_observations,
                                     cfg.report.bootstrap_iterations) if win.comparable and words else None
    res["difficult"] = difficult_words(conn, scope, win.base, win.recent, m.min_baseline_observations,
                                       m.min_baseline_accuracy)[:30]

    where, args = scope.where()
    res["variants"] = {
        cat: conn.execute(
            f"SELECT observed, intended, COUNT(*) c FROM word_observations WHERE {where} AND is_error=1 "
            f"AND category=? AND intended IS NOT NULL GROUP BY observed, intended ORDER BY c DESC LIMIT 15",
            args + [cat]).fetchall()
        for cat in ("B", "A", "C")
    }
    res["reasons"] = conn.execute(
        f"SELECT category, reason, COUNT(*) FROM word_observations WHERE {where} AND is_error=1 "
        f"GROUP BY category, reason ORDER BY category, COUNT(*) DESC", args).fetchall()
    res["resolutions"] = conn.execute(
        f"SELECT resolution, COUNT(*) FROM word_observations WHERE {where} AND is_error=1 GROUP BY resolution "
        f"ORDER BY COUNT(*) DESC", args).fetchall()
    exclusions: Counter = Counter()
    for r in rows:
        exclusions.update(r.exclusions)
    res["exclusions"] = exclusions.most_common()
    res["domains"] = conn.execute(
        "SELECT domain, provenance, stage, COUNT(*), SUM(n_eligible) FROM texts WHERE classifier_version_id=? "
        "GROUP BY domain, provenance, stage", (scope.classifier_version_id,)).fetchall()
    comp = conn.execute("SELECT fingerprint, components FROM classifier_versions WHERE id=?",
                        (scope.classifier_version_id,)).fetchone()
    res["classifier"] = {"id": scope.classifier_version_id, "fingerprint": comp[0], "components": json.loads(comp[1])}
    res["other_versions"] = [v for v in store.classifier_versions() if v[0] != scope.classifier_version_id and v[3]]
    return res


def _f(x: float, nd: int = 2) -> str:
    return "–" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.{nd}f}"


def text_summary(res: dict) -> str:
    o, b, r, w = res["overall"], res["baseline"], res["recent"], res["windows"]
    lines = [
        f"Data: {w.first} to {w.last}  ({o.words:,} eligible words, {o.errors:,} errors; "
        f"{_f(100 * o.excluded_share, 1)}% of words excluded)",
        f"Classifier version #{res['classifier']['id']} ({res['classifier']['fingerprint']}); "
        f"scope: {res['scope'].provenance}/{res['scope'].stage}, domain={res['scope'].domain or 'all'}",
        "",
        f"{'':28}{'words':>10}{'errors/1k':>11}{'B/1k':>8}{'A/1k':>8}{'C/1k':>8}{'coverage':>10}",
    ]
    for p in (o, b, r):
        lines.append(f"{p.label + ' ' + str(p.start) + '..' + str(p.end):28.28}{p.words:>10,}"
                     f"{_f(p.rate('errors')[0]):>11}{_f(p.rate('B')[0]):>8}{_f(p.rate('A')[0]):>8}"
                     f"{_f(p.rate('C')[0]):>8}{_f(100 * p.coverage, 0) + '%':>10}")
    c = res["comparison"]
    lines.append("")
    if c:
        lines.append(f"Recent minus baseline, per 1,000 words (95% day-bootstrap interval; "
                     f"{c['base_days']} vs {c['recent_days']} days):")
        for k, label in (("errors", "All errors"), ("B", "Phonological (B)"), ("A", "Mechanical (A)"), ("C", "Unclassified (C)")):
            lines.append(f"  {label:20} {_f(c[k]['diff']):>7}  [{_f(c[k]['lo'])}, {_f(c[k]['hi'])}]")
        s = c["B_share"]
        lines.append(f"  {'B share of A+B':20} {_f(100 * s['diff'], 1):>6}%  [{_f(100 * s['lo'], 1)}, {_f(100 * s['hi'], 1)}] pp")
    else:
        lines.append("Baseline and recent windows overlap: not enough history for a comparison.")
    mm = res["mastered"]
    lines.append("")
    lines.append(f"Mastered-word cohort: {res['cohort']['size']} words (frozen: {res['cohort']['name']})")
    if mm:
        d = mm["diff"]
        lines.append(f"  accuracy baseline {_f(100 * mm['base_accuracy'], 2)}% (n={mm['base_n']:,}) -> recent "
                     f"{_f(100 * mm['recent_accuracy'], 2)}% (n={mm['recent_n']:,}); change "
                     f"{_f(100 * d['diff'], 2)} pp [{_f(100 * d['lo'], 2)}, {_f(100 * d['hi'], 2)}]")
        worst = sorted([x for x in mm["words"] if x.reportable], key=lambda x: x.change)[:10]
        for x in worst:
            if x.change < 0:
                lines.append(f"    {x.word:18} {x.base_correct}/{x.base_n} -> {x.recent.correct}/{x.recent.n}  "
                             f"variants: {', '.join(v for v, _ in x.recent.variants.most_common(3))}")
    return "\n".join(lines)
