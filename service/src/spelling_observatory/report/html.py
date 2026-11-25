"""Self-contained static HTML report (no external resources, no network)."""

from __future__ import annotations

import html
import json
import math

from .build import _f

CSS = """
:root{color-scheme:light;--surface:#fcfcfb;--panel:#f4f3f0;--text:#0b0b0b;--text2:#52514e;--muted:#8a8984;
--grid:#e4e3df;--series-1:#2a78d6;--series-2:#eb6834;--band-1:rgba(42,120,214,.14);--band-2:rgba(235,104,52,.14);
--bar:#b9b8b2}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){color-scheme:dark;--surface:#1a1a19;
--panel:#242422;--text:#f0efec;--text2:#c3c2b7;--muted:#8f8e86;--grid:#34332f;--series-1:#3987e5;--series-2:#d95926;
--band-1:rgba(57,135,229,.2);--band-2:rgba(217,89,38,.2);--bar:#5c5b56}}
:root[data-theme="dark"]{color-scheme:dark;--surface:#1a1a19;--panel:#242422;--text:#f0efec;--text2:#c3c2b7;
--muted:#8f8e86;--grid:#34332f;--series-1:#3987e5;--series-2:#d95926;--band-1:rgba(57,135,229,.2);
--band-2:rgba(217,89,38,.2);--bar:#5c5b56}
body{margin:0;background:var(--surface);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;margin:36px 0 8px}h3{font-size:14px;margin:20px 0 6px}
.sub{color:var(--text2);margin:0 0 16px}.note{color:var(--text2);font-size:13px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.tile{background:var(--panel);border-radius:8px;padding:12px 14px}.tile .k{color:var(--text2);font-size:12px}
.tile .v{font-size:22px;font-weight:600;font-variant-numeric:tabular-nums}.tile .d{color:var(--text2);font-size:12px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums;font-size:13px}
th,td{padding:5px 8px;border-bottom:1px solid var(--grid);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{color:var(--text2);font-weight:600;cursor:default}
th.sort{cursor:pointer}th.sort:hover{color:var(--text)}
.wrap{overflow-x:auto}.scroll{max-height:560px;overflow-y:auto}.scroll thead th{position:sticky;top:0;background:var(--surface)}.insuff{color:var(--muted)}
.legend{display:flex;gap:16px;font-size:13px;color:var(--text2);margin:4px 0}
.sw{display:inline-block;width:14px;height:3px;border-radius:2px;vertical-align:middle;margin-right:6px}
svg text{fill:var(--text2);font-size:11px}
#tip{position:fixed;pointer-events:none;background:var(--panel);color:var(--text);border:1px solid var(--grid);
border-radius:6px;padding:6px 8px;font-size:12px;display:none;box-shadow:0 2px 8px rgba(0,0,0,.15)}
.tiles>div{min-width:0;overflow-x:auto}pre{white-space:pre-wrap;word-break:break-all;font-size:12px}
details{margin-top:8px}summary{cursor:pointer;color:var(--text2)}
"""

JS = """
const tip=document.getElementById('tip');
document.querySelectorAll('[data-tip]').forEach(el=>{
 el.addEventListener('mousemove',e=>{tip.innerHTML=el.dataset.tip;tip.style.display='block';
  tip.style.left=Math.min(e.clientX+12,innerWidth-220)+'px';tip.style.top=(e.clientY+12)+'px'});
 el.addEventListener('mouseleave',()=>{tip.style.display='none'});});
document.querySelectorAll('table.sortable').forEach(t=>{t.querySelectorAll('th').forEach((th,i)=>{th.classList.add('sort');
 th.addEventListener('click',()=>{const rows=[...t.tBodies[0].rows];const asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
  rows.sort((a,b)=>{const x=a.cells[i].dataset.v??a.cells[i].textContent,y=b.cells[i].dataset.v??b.cells[i].textContent;
   const nx=parseFloat(x),ny=parseFloat(y);const c=(isNaN(nx)||isNaN(ny))?x.localeCompare(y):nx-ny;return asc?c:-c});
  rows.forEach(r=>t.tBodies[0].appendChild(r));});});});
"""


def esc(x) -> str:
    return html.escape(str(x))


def _num(x: float) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def rate_chart(periods, title: str) -> str:
    """Line chart of B and A rates per 1,000 words with Wilson bands, plus a sample-size strip."""
    if not periods:
        return ""
    W, H, L, R, T, B = 1000, 300, 48, 90, 12, 28
    n = len(periods)
    xs = [L + (W - L - R) * (i / max(1, n - 1)) for i in range(n)]
    series = [("B", "Phonologically plausible (B)", "--series-1", "--band-1"),
              ("A", "Mechanical-like (A)", "--series-2", "--band-2")]
    ymax = 0.0
    for p in periods:
        if p.sufficient:
            for key, *_ in series:
                ymax = max(ymax, p.rate(key)[2])
    ymax = ymax * 1.1 or 1.0
    y = lambda v: T + (H - T - B) * (1 - v / ymax)
    parts = [f'<svg viewBox="0 0 {W} {H + 90}" width="100%" role="img" aria-label="{esc(title)}">']
    for i in range(5):
        v = ymax * i / 4
        parts.append(f'<line x1="{L}" x2="{W - R}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="var(--grid)" stroke-width="1"/>'
                     f'<text x="{L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{v:.1f}</text>')
    step = max(1, n // 12)
    for i in range(0, n, step):
        parts.append(f'<text x="{xs[i]:.1f}" y="{H - 8}" text-anchor="middle">{esc(periods[i].label)}</text>')
    for key, name, color, band in series:
        segs, cur = [], []
        for i, p in enumerate(periods):
            if p.sufficient:
                cur.append(i)
            elif cur:
                segs.append(cur)
                cur = []
        if cur:
            segs.append(cur)
        for seg in segs:
            if len(seg) > 1:
                top = " ".join(f"{xs[i]:.1f},{y(periods[i].rate(key)[2]):.1f}" for i in seg)
                bot = " ".join(f"{xs[i]:.1f},{y(periods[i].rate(key)[1]):.1f}" for i in reversed(seg))
                parts.append(f'<polygon points="{top} {bot}" fill="var({band})"/>')
                line = " ".join(f"{xs[i]:.1f},{y(periods[i].rate(key)[0]):.1f}" for i in seg)
                parts.append(f'<polyline points="{line}" fill="none" stroke="var({color})" stroke-width="2" '
                             f'stroke-linejoin="round" stroke-linecap="round"/>')
        for i, p in enumerate(periods):
            v = p.rate(key)[0]
            if not _num(v):
                continue
            fill = f"var({color})" if p.sufficient else "var(--surface)"
            parts.append(f'<circle cx="{xs[i]:.1f}" cy="{y(min(v, ymax)):.1f}" r="6" fill="var(--surface)"/>'
                         f'<circle cx="{xs[i]:.1f}" cy="{y(min(v, ymax)):.1f}" r="4" fill="{fill}" '
                         f'stroke="var({color})" stroke-width="2"/>')
        last = max((i for i, p in enumerate(periods) if p.sufficient), default=None)
        if last is not None:
            parts.append(f'<text x="{xs[last] + 10:.1f}" y="{y(periods[last].rate(key)[0]) + 4:.1f}" '
                         f'style="fill:var(--text)">{key}</text>')
    # Sample-size strip (separate scale, separate plot).
    top0 = H + 10
    wmax = max(p.words for p in periods) or 1
    bw = max(2.0, (W - L - R) / max(1, n) * 0.6)
    parts.append(f'<text x="{L - 6}" y="{top0 + 10}" text-anchor="end">words</text>')
    for i, p in enumerate(periods):
        h = 60 * p.words / wmax
        parts.append(f'<rect x="{xs[i] - bw / 2:.1f}" y="{top0 + 70 - h:.1f}" width="{bw:.1f}" height="{h:.1f}" '
                     f'rx="2" fill="var(--bar)"/>')
    for i, p in enumerate(periods):
        b, a = p.rate("B"), p.rate("A")
        tipt = (f"<b>{esc(p.label)}</b>{'' if p.sufficient else ' (insufficient data)'}<br>"
                f"{p.words:,} eligible words · {p.errors:,} errors<br>"
                f"B {_f(b[0])} /1k [{_f(b[1])}–{_f(b[2])}]<br>A {_f(a[0])} /1k [{_f(a[1])}–{_f(a[2])}]<br>"
                f"coverage {_f(100 * p.coverage, 0)}%")
        x0 = xs[i] - (W - L - R) / max(1, n) / 2
        parts.append(f'<rect x="{x0:.1f}" y="0" width="{(W - L - R) / max(1, n):.1f}" height="{H + 80}" '
                     f'fill="transparent" data-tip="{esc(tipt)}"/>')
    parts.append("</svg>")
    legend = ('<div class="legend"><span><span class="sw" style="background:var(--series-1)"></span>'
              'Phonologically plausible (B)</span><span><span class="sw" style="background:var(--series-2)"></span>'
              'Mechanical-like (A)</span><span>○ hollow = too few words</span></div>')
    return f"<h3>{esc(title)}</h3>{legend}{''.join(parts)}"


def period_table(periods, caption: str) -> str:
    rows = []
    for p in periods:
        cls = "" if p.sufficient else ' class="insuff"'
        rows.append(f"<tr{cls}><td>{esc(p.label)}</td><td>{p.words:,}</td><td>{p.errors:,}</td>"
                    f"<td>{_f(p.rate('errors')[0])}</td><td>{_f(p.rate('B')[0])}</td><td>{_f(p.rate('A')[0])}</td>"
                    f"<td>{_f(p.rate('C')[0])}</td><td>{_f(100 * p.coverage, 0)}%</td>"
                    f"<td>{_f(100 * p.excluded_share, 1)}%</td></tr>")
    return (f"<details><summary>{esc(caption)} (table)</summary><div class='wrap'><table><thead><tr><th>Period</th>"
            "<th>Eligible words</th><th>Errors</th><th>All /1k</th><th>B /1k</th><th>A /1k</th><th>C /1k</th>"
            "<th>Classified</th><th>Excluded</th></tr></thead><tbody>" + "".join(rows) + "</tbody></table></div></details>")


def tile(k: str, v: str, d: str = "") -> str:
    return f'<div class="tile"><div class="k">{esc(k)}</div><div class="v">{esc(v)}</div><div class="d">{esc(d)}</div></div>'


def render(res: dict) -> str:
    o, b, r, w, sc = res["overall"], res["baseline"], res["recent"], res["windows"], res["scope"]
    mm = res["mastered"]
    out = [f"<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' "
           f"content='width=device-width,initial-scale=1'><title>Spelling Observatory Report</title><style>{CSS}</style>"
           f"</head><body><main>"]
    out.append("<h1>Spelling Observatory: retrospective report</h1>")
    out.append(f"<p class='sub'>{w.first} – {w.last} · {esc(sc.provenance)}/{esc(sc.stage)} · domain "
               f"{esc(sc.domain or 'all')} · classifier #{res['classifier']['id']} "
               f"<code>{esc(res['classifier']['fingerprint'])}</code></p>")
    out.append("<p class='note'>An observational measurement of one person's spelling, not a diagnostic test. "
               "Categories describe the shape of an error, not its cause. Historical data is submitted text: "
               "errors corrected before sending are invisible.</p>")

    out.append("<h2>Overview</h2><div class='tiles'>")
    out.append(tile("Eligible words", f"{o.words:,}", f"{o.days} days with data"))
    out.append(tile("All errors /1k words", _f(o.rate("errors")[0]), f"recent {_f(r.rate('errors')[0])} · baseline {_f(b.rate('errors')[0])}"))
    out.append(tile("Phonological (B) /1k", _f(o.rate("B")[0]), f"recent {_f(r.rate('B')[0])} · baseline {_f(b.rate('B')[0])}"))
    out.append(tile("Mechanical (A) /1k", _f(o.rate("A")[0]), f"recent {_f(r.rate('A')[0])} · baseline {_f(b.rate('A')[0])}"))
    if mm:
        out.append(tile("Mastered-word accuracy", f"{_f(100 * mm['recent_accuracy'], 2)}%",
                        f"baseline {_f(100 * mm['base_accuracy'], 2)}% · {res['cohort']['size']} words"))
    else:
        out.append(tile("Mastered words", f"{res['cohort']['size']}", "no comparison window yet"))
    out.append(tile("Classification coverage", f"{_f(100 * o.coverage, 0)}%", "errors assigned A or B"))
    out.append(tile("Typing speed", "n/a", "not available for submitted text"))
    out.append("</div>")

    out.append("<h2>Error rates over time</h2>")
    out.append(rate_chart(res["months"], "Monthly errors per 1,000 eligible words (95% Wilson band)"))
    out.append(period_table(res["months"], "Monthly"))
    out.append(period_table(res["weeks"], "Weekly"))
    out.append(rate_chart(res["rolling90"], "Rolling 90-day windows (weekly steps)"))
    out.append(period_table(res["rolling30"], "Rolling 30-day windows"))
    out.append("<p class='note'>Wilson bands assume independent words and are too narrow when errors cluster in "
               "days; use the day-bootstrap comparison below for inference.</p>")

    out.append("<h2>Baseline vs recent</h2>")
    out.append(f"<p class='note'>Baseline {w.base[0]} – {w.base[1]} ({b.words:,} words); "
               f"recent {w.recent[0]} – {w.recent[1]} ({r.words:,} words).</p>")
    c = res["comparison"]
    if c:
        rows = []
        for k, label in (("errors", "All errors"), ("B", "Phonologically plausible (B)"),
                         ("A", "Mechanical-like (A)"), ("C", "Unclassified (C)")):
            rows.append(f"<tr><td>{label}</td><td>{_f(b.rate(k)[0])}</td><td>{_f(r.rate(k)[0])}</td>"
                        f"<td>{_f(c[k]['diff'])}</td><td>[{_f(c[k]['lo'])}, {_f(c[k]['hi'])}]</td></tr>")
        s = c["B_share"]
        bs = b.B / (b.A + b.B) if (b.A + b.B) else float("nan")
        rs = r.B / (r.A + r.B) if (r.A + r.B) else float("nan")
        rows.append(f"<tr><td>B as share of A+B</td><td>{_f(100 * bs, 1)}%</td><td>{_f(100 * rs, 1)}%</td>"
                    f"<td>{_f(100 * s['diff'], 1)} pp</td><td>[{_f(100 * s['lo'], 1)}, {_f(100 * s['hi'], 1)}]</td></tr>")
        out.append("<div class='wrap'><table><thead><tr><th>Per 1,000 eligible words</th><th>Baseline</th><th>Recent</th>"
                   "<th>Difference</th><th>95% interval (days resampled)</th></tr></thead><tbody>" + "".join(rows) +
                   f"</tbody></table></div><p class='note'>{c['base_days']} baseline days, {c['recent_days']} recent days.</p>")
    else:
        out.append("<p>Baseline and recent windows overlap; there is not yet enough history for a comparison.</p>")

    out.append("<h2>Previously mastered words</h2>")
    p = res["cohort"]["params"]
    out.append(f"<p class='note'>Cohort frozen from the baseline window: words with ≥{p['min_observations']} "
               f"observations and ≥{100 * p['min_accuracy']:.0f}% accuracy ({res['cohort']['size']} words). "
               "Per-word changes are shown only with enough recent observations.</p>")
    if mm:
        d = mm["diff"]
        out.append(f"<p>Pooled accuracy {_f(100 * mm['base_accuracy'], 2)}% → {_f(100 * mm['recent_accuracy'], 2)}% "
                   f"(change {_f(100 * d['diff'], 2)} pp, 95% interval [{_f(100 * d['lo'], 2)}, {_f(100 * d['hi'], 2)}]). "
                   f"Recent cohort errors: B {mm['recent_B']}, A {mm['recent_A']}, C {mm['recent_C']}.</p>")
        rows = []
        for x in sorted(mm["words"], key=lambda x: (not x.reportable, x.change if _num(x.change) else 9)):
            if not x.recent.n:
                continue
            lo_hi = x.recent_interval()
            cls = "" if x.reportable else ' class="insuff"'
            ch = x.change
            rows.append(f"<tr{cls}><td>{esc(x.word)}</td><td data-v='{x.base_accuracy:.4f}'>{x.base_correct}/{x.base_n}</td>"
                        f"<td data-v='{x.recent.accuracy:.4f}'>{x.recent.correct}/{x.recent.n}</td>"
                        f"<td data-v='{ch:.4f}'>{_f(100 * ch, 1)} pp</td>"
                        f"<td>[{_f(100 * lo_hi[1], 0)}–{_f(100 * lo_hi[2], 0)}%]</td>"
                        f"<td>{x.recent.B}/{x.recent.A}/{x.recent.C}</td>"
                        f"<td style='text-align:left'>{esc(', '.join(f'{v} ({k})' for v, k in x.recent.variants.most_common(3)))}</td></tr>")
        out.append("<div class='wrap scroll'><table class='sortable'><thead><tr><th>Word</th><th>Baseline</th><th>Recent</th>"
                   "<th>Change</th><th>Recent 95%</th><th>B/A/C</th><th>Misspellings</th></tr></thead><tbody>"
                   + "".join(rows) + "</tbody></table></div><p class='note'>Grey rows: too few recent observations "
                   "to report a change. Click a header to sort.</p>")
    out.append("<h3>Consistently difficult words (not mastered in the baseline)</h3>")
    rows = [f"<tr><td>{esc(wd)}</td><td>{bs.correct}/{bs.n}</td><td>{rs.correct}/{rs.n}</td>"
            f"<td style='text-align:left'>{esc(', '.join(v for v, _ in bs.variants.most_common(3)))}</td></tr>"
            for wd, bs, rs in res["difficult"]]
    out.append("<div class='wrap'><table><thead><tr><th>Word</th><th>Baseline</th><th>Recent</th><th>Baseline misspellings</th>"
               "</tr></thead><tbody>" + ("".join(rows) or "<tr><td colspan=4>None</td></tr>") + "</tbody></table></div>")

    out.append("<h2>Examples by category</h2><p class='note'>Most frequent misspelling → inferred word pairs. "
               "Scan these to judge whether the classifier behaves sensibly on your writing.</p><div class='tiles'>")
    for cat, label in (("B", "Phonologically plausible (B)"), ("A", "Mechanical-like (A)"), ("C", "Unclassified (C)")):
        items = "".join(f"<tr><td>{esc(o_)}</td><td>{esc(i_)}</td><td>{n_}</td></tr>" for o_, i_, n_ in res["variants"][cat])
        out.append(f"<div><h3>{label}</h3><table><tbody>{items or '<tr><td>None</td></tr>'}</tbody></table></div>")
    out.append("</div>")

    out.append("<h2>Data quality</h2><div class='tiles'><div><h3>Excluded words by reason</h3><table><tbody>")
    out.append("".join(f"<tr><td>{esc(k)}</td><td>{v:,}</td></tr>" for k, v in res["exclusions"]))
    out.append("</tbody></table></div><div><h3>Why errors got their category</h3><table><tbody>")
    out.append("".join(f"<tr><td>{esc(c_)} · {esc(r_)}</td><td>{n_:,}</td></tr>" for c_, r_, n_ in res["reasons"]))
    out.append("</tbody></table><h3>How the intended word was found</h3><table><tbody>")
    out.append("".join(f"<tr><td>{esc(r_)}</td><td>{n_:,}</td></tr>" for r_, n_ in res["resolutions"]))
    out.append("</tbody></table></div><div><h3>Writing environments</h3><table><tbody>")
    out.append("".join(f"<tr><td>{esc(d_)} · {esc(p_)}/{esc(s_)}</td><td>{t_:,} texts</td><td>{(wn or 0):,} words</td></tr>"
                       for d_, p_, s_, t_, wn in res["domains"]))
    out.append("</tbody></table></div></div>")
    if res["other_versions"]:
        out.append("<p class='note'>Other classifier versions exist in the database and are <b>not</b> included: "
                   + ", ".join(f"#{v[0]} ({v[3]} texts)" for v in res["other_versions"]) + ".</p>")
    out.append("<details><summary>Classifier version components</summary><pre>"
               + esc(json.dumps(res["classifier"]["components"], indent=2)) + "</pre></details>")
    out.append(f"<div id='tip'></div><script>{JS}</script></main></body></html>")
    return "".join(out)
