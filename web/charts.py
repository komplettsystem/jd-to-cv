"""Server-side SVG charts and a few display filters, registered as Jinja globals.

No JavaScript chart library: the site runs under a strict Content-Security-Policy and should load fast. Geometry is
written as SVG attributes, colour comes from classes in static/style.css (so light and dark mode both work), and no
`style` attribute is ever produced. Every chart carries a <title> and <desc>; charts with text inside the drawing also
get a table or list next to them in the template. A chart with nothing to draw returns an empty string, and the
template writes the empty state."""
from datetime import date

from flask import current_app, url_for
from markupsafe import Markup, escape

MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# --- small helpers -----------------------------------------------------------------------------------------------

def _n(v):
    """A number for an SVG attribute: short, never scientific notation."""
    return f"{float(v):.2f}".rstrip("0").rstrip(".") or "0"


def _clamp(v, lo=0.0, hi=100.0):
    return max(lo, min(hi, float(v)))


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v


def _clip(text, n):
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def short_model(model_id):
    """'openrouter:anthropic/claude-sonnet-5.5' -> 'claude-sonnet-5.5'. The full id stays available as a tooltip."""
    s = str(model_id or "")
    return s.split(":", 1)[-1].split("/")[-1] or s


def model_provider(model_id):
    s = str(model_id or "")
    return s.split(":", 1)[0] if ":" in s else ""


def day(value):
    """'2026-11-11T20:11:13+00:00' -> '11 Nov 2026'. Anything unreadable is returned as it came."""
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return value or ""
    return f"{d.day} {MONTHS[d.month - 1]} {d.year}"


def day_short(value):
    try:
        d = date.fromisoformat(str(value)[:10])
    except ValueError:
        return value or ""
    return f"{d.day} {MONTHS[d.month - 1]}"


def clock(value):
    """'2026-11-11T20:11:13+00:00' -> '11 Nov, 20:11' (UTC, as stored)."""
    s = str(value or "")
    return f"{day_short(s)}, {s[11:16]}" if len(s) >= 16 else s


def _usd(v):
    return f"${v:.4f}" if v < 0.1 else f"${v:.2f}"


# --- bars: meter, ledger bar, waterfall bar ------------------------------------------------------------------------

def meter(value, label="Score"):
    """A flat track with a filled part, for a 0 to 100 score."""
    if not _is_number(value):
        return Markup("")
    v = _clamp(value)
    return Markup(
        f'<svg class="meter" viewBox="0 0 100 6" preserveAspectRatio="none" role="img" focusable="false">'
        f"<title>{escape(label)}: {v:.0f} of 100</title>"
        f'<rect class="meter-track" x="0" y="0" width="100" height="6"/>'
        f'<rect class="meter-fill" x="0" y="0" width="{_n(v)}" height="6"/>'
        f'<rect class="meter-mid" x="49.75" y="0" width="0.5" height="6"/></svg>')


def bar(value, maximum=100, label=""):
    """A small bar for a table cell. `value` out of `maximum`."""
    if not _is_number(value) or not _is_number(maximum) or maximum <= 0:
        return Markup("")
    share = _clamp(value / maximum * 100)
    return Markup(
        f'<svg class="cellbar" viewBox="0 0 100 4" preserveAspectRatio="none" role="img" focusable="false">'
        f"<title>{escape(label or 'Share')}: {share:.0f}%</title>"
        f'<rect class="meter-track" x="0" y="0" width="100" height="4"/>'
        f'<rect class="cellbar-fill" x="0" y="0" width="{_n(share)}" height="4"/></svg>')


def span_bar(offset_ms, duration_ms, total_ms, kind=""):
    """One row of the trace waterfall: where the span starts and how long it runs, against the whole trace."""
    total = total_ms if _is_number(total_ms) and total_ms > 0 else 0
    if not total:
        x, w = 0.0, 1000.0
    else:
        x = _clamp((offset_ms or 0) / total * 1000, 0, 1000)
        w = _clamp((duration_ms or 0) / total * 1000, 3, 1000 - min(x, 997))
    cls = "wf-fill wf-" + ("".join(c for c in str(kind) if c.isalnum() or c == "_") or "step")
    return Markup(
        f'<svg class="wf-bar" viewBox="0 0 1000 10" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
        f'<rect class="wf-track" x="0" y="4.5" width="1000" height="1"/>'
        f'<rect class="{cls}" x="{_n(x)}" y="1" width="{_n(w)}" height="8"/></svg>')


# --- both-ways quadrant ----------------------------------------------------------------------------------------------

Q_W, Q_H = 460, 410
Q_L, Q_T, Q_R, Q_B = 44, 12, 448, 362  # plot area


def _qx(v):
    return Q_L + _clamp(v) / 100 * (Q_R - Q_L)


def _qy(v):
    return Q_B - _clamp(v) / 100 * (Q_B - Q_T)


def _place_labels(points):
    """Greedy label placement: right of the dot, else left, above, below; first spot that hits no earlier label
    or dot. Returns (x, y, anchor) per point."""
    boxes, out = [], []
    for p in points:
        boxes.append((p["px"] - 9, p["py"] - 9, p["px"] + 9, p["py"] + 9))
    for p in points:
        w, h = 7.4 * len(p["short"]) + 4, 16
        x, y = p["px"], p["py"]
        options = [(x + 12, y + 5, "start"), (x - 12, y + 5, "end"), (x, y - 13, "middle"), (x, y + 24, "middle")]
        chosen = None
        for ox, oy, anchor in options:
            left = ox if anchor == "start" else ox - w if anchor == "end" else ox - w / 2
            box = (left, oy - h + 3, left + w, oy + 3)
            if box[0] < Q_L + 2 or box[2] > Q_R - 2 or box[1] < Q_T or box[3] > Q_B:
                continue
            own = (p["px"] - 9, p["py"] - 9, p["px"] + 9, p["py"] + 9)
            if any(b != own and not (box[2] < b[0] or box[0] > b[2] or box[3] < b[1] or box[1] > b[3])
                   for b in boxes):
                continue
            chosen = (ox, oy, anchor, box)
            break
        if chosen is None:
            ox, oy, anchor = options[0] if x < (Q_L + Q_R) / 2 else options[1]
            left = ox if anchor == "start" else ox - w
            chosen = (ox, oy, anchor, (left, oy - h + 3, left + w, oy + 3))
        boxes.append(chosen[3])
        out.append(chosen[:3])
    return out


def quadrant(points):
    """x = how well the CV fits the posting, y = how well the posting fits the owner's terms. One dot per posting,
    linked to its run. A posting that breaks a dealbreaker gets a crossed dot."""
    pts = []
    for p in points or []:
        if not _is_number(p.get("x")) or not _is_number(p.get("y")):
            continue
        pts.append({**p, "px": _qx(p["x"]), "py": _qy(p["y"]), "short": _clip(p.get("label") or "Posting", 22)})
    if not pts:
        return Markup("")
    mx, my = _qx(50), _qy(50)
    n_conf = sum(1 for p in pts if p.get("conflicts"))
    o = [f'<svg class="chart chart-quadrant" viewBox="0 0 {Q_W} {Q_H}" role="group" aria-labelledby="q-title q-desc">',
         '<title id="q-title">Both ways: CV fit against fit to your terms</title>',
         f'<desc id="q-desc">{len(pts)} posting{"" if len(pts) == 1 else "s"}. Across: how well the CV fits the '
         f'posting. Up: how well the posting fits your terms. {n_conf} with a dealbreaker conflict. '
         'The same numbers are in the list next to the chart.</desc>',
         f'<rect class="q-zone q-both" x="{_n(mx)}" y="{Q_T}" width="{_n(Q_R - mx)}" height="{_n(my - Q_T)}"/>',
         f'<rect class="q-zone q-plain" x="{Q_L}" y="{Q_T}" width="{_n(mx - Q_L)}" height="{_n(my - Q_T)}"/>',
         f'<rect class="q-zone q-plain" x="{_n(mx)}" y="{_n(my)}" width="{_n(Q_R - mx)}" height="{_n(Q_B - my)}"/>',
         f'<rect class="q-zone q-neither" x="{Q_L}" y="{_n(my)}" width="{_n(mx - Q_L)}" height="{_n(Q_B - my)}"/>',
         f'<rect class="q-frame" x="{Q_L}" y="{Q_T}" width="{Q_R - Q_L}" height="{Q_B - Q_T}"/>',
         f'<line class="q-mid" x1="{_n(mx)}" y1="{Q_T}" x2="{_n(mx)}" y2="{Q_B}"/>',
         f'<line class="q-mid" x1="{Q_L}" y1="{_n(my)}" x2="{Q_R}" y2="{_n(my)}"/>',
         f'<text class="q-name" x="{Q_R - 8}" y="{Q_T + 20}" text-anchor="end">Fits both ways</text>',
         f'<text class="q-name" x="{Q_L + 8}" y="{Q_T + 20}">The job suits you, the CV does not show it</text>',
         f'<text class="q-name" x="{Q_R - 8}" y="{Q_B - 10}" text-anchor="end">The CV fits, the job does not suit you</text>',
         f'<text class="q-name" x="{Q_L + 8}" y="{Q_B - 10}">Neither</text>']
    for v in (0, 50, 100):
        o.append(f'<text class="q-tick" x="{_n(_qx(v))}" y="{Q_B + 17}" text-anchor="middle">{v}</text>')
        o.append(f'<text class="q-tick" x="{Q_L - 7}" y="{_n(_qy(v) + 5)}" text-anchor="end">{v}</text>')
    o.append(f'<text class="q-axis" x="{_n((Q_L + Q_R) / 2)}" y="{Q_H - 8}" text-anchor="middle">'
             'CV fit to the posting, %</text>')
    o.append(f'<text class="q-axis" x="13" y="{_n((Q_T + Q_B) / 2)}" text-anchor="middle" '
             f'transform="rotate(-90 13 {_n((Q_T + Q_B) / 2)})">Posting fit to your terms, %</text>')
    for p, (lx, ly, anchor) in zip(pts, _place_labels(pts)):
        conflict = bool(p.get("conflicts"))
        tip = f'{p.get("label") or "Posting"}: CV fit {p["x"]:.0f}%, terms {p["y"]:.0f}%'
        if p.get("overall"):
            tip += f', overall reading {p["overall"]}'
        if conflict:
            tip += ", dealbreaker conflict"
        inner = [f"<title>{escape(tip)}</title>",
                 f'<circle class="q-dot{" q-dot-conflict" if conflict else ""}" cx="{_n(p["px"])}" cy="{_n(p["py"])}" r="7"/>']
        if conflict:
            x, y = p["px"], p["py"]
            inner.append(f'<path class="q-cross" d="M{_n(x - 3.5)} {_n(y - 3.5)}L{_n(x + 3.5)} {_n(y + 3.5)}'
                         f'M{_n(x - 3.5)} {_n(y + 3.5)}L{_n(x + 3.5)} {_n(y - 3.5)}"/>')
        inner.append(f'<text class="q-label" x="{_n(lx)}" y="{_n(ly)}" text-anchor="{anchor}">{escape(p["short"])}</text>')
        if p.get("run_id") is not None:
            href = url_for("admin.run_view", run_id=p["run_id"])
            name = f'{p["short"]}: CV fit {p["x"]:.0f}%, fit to your terms {p["y"]:.0f}%'
            if p.get("overall"):
                name += f', overall reading {p["overall"]}'
            o.append(f'<a href="{escape(href)}" aria-label="{escape(name)}">{"".join(inner)}</a>')
        else:
            o.append(f'<g>{"".join(inner)}</g>')
    o.append("</svg>")
    return Markup("".join(o))


# --- 14-day trend ----------------------------------------------------------------------------------------------------

def _strip(days, key, name, fmt):
    values = [d.get(key) or 0 for d in days]
    peak = max(values) if values else 0
    n = len(days)
    o = [f'<div class="trend-strip"><p class="trend-name">{escape(name)} '
         f'<span class="trend-peak">most in a day: {escape(fmt(peak))}</span></p>',
         f'<svg class="trend-svg" viewBox="0 0 {n * 10} 40" preserveAspectRatio="none" role="img" focusable="false">',
         f"<title>{escape(name)}, last {n} days</title>",
         f"<desc>Total {escape(fmt(sum(values)))}; most in one day {escape(fmt(peak))}.</desc>",
         f'<rect class="trend-base" x="0" y="39" width="{n * 10}" height="1"/>']
    for i, (d, v) in enumerate(zip(days, values)):
        h = (v / peak * 36) if peak else 0
        if v and h < 1.5:
            h = 1.5
        o.append(f'<rect class="trend-bar" x="{i * 10 + 1.5}" y="{_n(39 - h)}" width="7" height="{_n(h)}">'
                 f'<title>{escape(day_short(d.get("day")))}: {escape(fmt(v))}</title></rect>')
    o.append("</svg></div>")
    return "".join(o)


def trend(days):
    """Two strips over the same days, each on its own scale: checks started, and spend."""
    days = list(days or [])
    if not days:
        return Markup("")
    html = ['<div class="trend">',
            _strip(days, "runs", "Checks started", lambda v: f"{v:,.0f}"),
            _strip(days, "requests", "Model requests", lambda v: f"{v:,.0f}"),
            _strip(days, "spend", "Spend", _usd),
            f'<p class="trend-axis"><span>{escape(day_short(days[0].get("day")))}</span>'
            f'<span>{escape(day_short(days[-1].get("day")))}</span></p></div>']
    return Markup("".join(html))


# --- talent pool funnel -----------------------------------------------------------------------------------------------

def funnel(steps):
    """Counted search steps as shrinking bars on a linear scale. The list is its own text alternative."""
    steps = [s for s in (steps or []) if _is_number(s.get("count"))]
    if not steps:
        return Markup("")
    first = max(steps[0]["count"], 1)
    o = ['<ol class="funnel">']
    for i, s in enumerate(steps):
        share = _clamp(s["count"] / first * 100)
        capped = "+" in (s.get("count_text") or "")
        count = f'{s["count"]:,.0f}{"+" if capped else ""}'
        note = ""
        prev = s.get("share_of_previous")
        if i and _is_number(prev):
            pct = prev * 100
            note = f'{"under 1" if 0 < pct < 1 else f"{pct:.0f}"}% of the step above'
        source = ", ".join(x for x in (str(s.get("source") or ""), str(day(s.get("measured")) or "")) if x)
        o.append('<li class="funnel-step">'
                 f'<span class="funnel-label">{escape(s.get("label") or "Step")}</span>'
                 f'<span class="funnel-count">{escape(count)}</span>'
                 f'<svg class="funnel-bar" viewBox="0 0 100 8" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
                 f'<rect class="meter-track" x="0" y="0" width="100" height="8"/>'
                 f'<rect class="funnel-fill" x="0" y="0" width="{_n(max(share, 0.6))}" height="8"/></svg>'
                 f'<span class="funnel-note">{escape(note)}</span>'
                 f'<span class="funnel-source">{escape(source)}</span></li>')
    o.append("</ol>")
    return Markup("".join(o))


# --- cost against quality -----------------------------------------------------------------------------------------------

S_W, S_H = 460, 330
S_L, S_T, S_R, S_B = 48, 14, 446, 280


def cost_quality(models, kind=""):
    """One dot per model: mean cost of a report across, eval pass rate up. Models without both are left out and
    counted in the description. `models` is a list of {label, cost, quality (0..1)}."""
    pts = [m for m in (models or []) if _is_number(m.get("cost")) and _is_number(m.get("quality"))]
    if not pts:
        return Markup("")
    left_out = len(models) - len(pts)
    top = max(m["cost"] for m in pts) or 0.01
    top *= 1.15

    def sx(v):
        return S_L + _clamp(v / top * 100) / 100 * (S_R - S_L)

    def sy(q):
        return S_B - _clamp(q * 100) / 100 * (S_B - S_T)

    o = [f'<svg class="chart chart-scatter" viewBox="0 0 {S_W} {S_H}" role="img" aria-labelledby="s-title s-desc">',
         '<title id="s-title">Cost against quality, by model</title>',
         f'<desc id="s-desc">{len(pts)} model{"" if len(pts) == 1 else "s"}. Across: mean cost of one finished check in '
         f'US dollars. Up: share of {escape(kind)} eval cases passed. {left_out} left out because a cost or an eval result is missing. '
         'The same numbers are in the catalog table.</desc>',
         f'<rect class="q-frame" x="{S_L}" y="{S_T}" width="{S_R - S_L}" height="{S_B - S_T}"/>']
    for q in (0, 0.5, 1):
        y = sy(q)
        o.append(f'<line class="q-mid" x1="{S_L}" y1="{_n(y)}" x2="{S_R}" y2="{_n(y)}"/>')
        o.append(f'<text class="q-tick" x="{S_L - 7}" y="{_n(y + 5)}" text-anchor="end">{q * 100:.0f}</text>')
    for frac in (0, 0.5, 1):
        v = top * frac
        o.append(f'<text class="q-tick" x="{_n(sx(v))}" y="{S_B + 17}" text-anchor="{"start" if frac == 0 else "end" if frac == 1 else "middle"}">'
                 f'{escape(_usd(v) if v else "$0")}</text>')
    o.append(f'<text class="q-axis" x="{_n((S_L + S_R) / 2)}" y="{S_H - 8}" text-anchor="middle">Mean cost of one finished check</text>')
    o.append(f'<text class="q-axis" x="13" y="{_n((S_T + S_B) / 2)}" text-anchor="middle" '
             f'transform="rotate(-90 13 {_n((S_T + S_B) / 2)})">{escape((kind + " eval").strip().capitalize())} cases passed, %</text>')
    for m in sorted(pts, key=lambda m: m["cost"]):
        x, y = sx(m["cost"]), sy(m["quality"])
        label = _clip(m.get("label") or "Model", 24)
        right = x < (S_L + S_R) * 0.6
        o.append(f'<g><title>{escape(m.get("label") or "Model")}: {escape(_usd(m["cost"]))} a report, '
                 f'{m["quality"] * 100:.0f}% of eval cases passed</title>'
                 f'<circle class="q-dot" cx="{_n(x)}" cy="{_n(y)}" r="7"/>'
                 f'<text class="q-label" x="{_n(x + 12 if right else x - 12)}" y="{_n(y + 5)}" '
                 f'text-anchor="{"start" if right else "end"}">{escape(label)}</text></g>')
    o.append("</svg>")
    return Markup("".join(o))


# --- eval pass-rate matrix ------------------------------------------------------------------------------------------------

def eval_matrix(eval_runs):
    """Models down, datasets across; each cell is the newest finished run's pass rate, linked to that run.
    This is a real table, so it is its own text alternative."""
    latest, datasets, models = {}, [], []
    for e in eval_runs or []:
        if e.get("status") != "done":
            continue
        rate = (e.get("summary") or {}).get("pass_rate")
        if not _is_number(rate):
            continue
        key = (e.get("backend"), e.get("dataset"))
        if key not in latest or (e.get("id") or 0) > (latest[key].get("id") or 0):
            latest[key] = e
        if e.get("dataset") not in datasets:
            datasets.append(e.get("dataset"))
        if e.get("backend") not in models:
            models.append(e.get("backend"))
    if not latest:
        return Markup("")
    kinds = {e.get("dataset"): e.get("dataset_kind") for e in latest.values()}
    datasets.sort(key=str)
    models.sort(key=str)
    o = ['<div class="table-scroll"><table class="matrix"><caption>Share of cases passed, newest finished run of '
         'each model on each dataset</caption><thead><tr><th scope="col">Model</th>']
    for d in datasets:
        o.append(f'<th scope="col">{escape(d)}<span class="matrix-kind">{escape(kinds.get(d) or "")}</span></th>')
    o.append("</tr></thead><tbody>")
    for m in models:
        o.append(f'<tr><th scope="row" title="{escape(m)}">{escape(short_model(m))}</th>')
        for d in datasets:
            e = latest.get((m, d))
            if not e:
                o.append('<td class="matrix-empty">not run</td>')
                continue
            rate = _clamp(e["summary"]["pass_rate"] * 100)
            href = url_for("admin.eval_view", eval_run_id=e["id"])
            o.append(f'<td><a class="matrix-cell" href="{escape(href)}"><span class="matrix-rate">{rate:.0f}%</span>'
                     f'<svg class="cellbar" viewBox="0 0 100 4" preserveAspectRatio="none" aria-hidden="true" focusable="false">'
                     f'<rect class="meter-track" x="0" y="0" width="100" height="4"/>'
                     f'<rect class="cellbar-fill" x="0" y="0" width="{_n(rate)}" height="4"/></svg>'
                     f'<span class="matrix-n">{escape(e.get("n_cases") or 0)} cases</span></a></td>')
        o.append("</tr>")
    o.append("</tbody></table></div>")
    return Markup("".join(o))


# --- the benchmark over time ----------------------------------------------------------------------------------------------

T_W, T_H = 640, 300
T_L, T_T, T_R, T_B = 44, 18, 620, 236
MARKS = ("circle", "square", "triangle")


def _mark(kind, x, y, cls):
    if kind == "square":
        return f'<rect class="{cls}" x="{_n(x - 5)}" y="{_n(y - 5)}" width="10" height="10"/>'
    if kind == "triangle":
        return f'<path class="{cls}" d="M{_n(x)} {_n(y - 6)} L{_n(x + 6)} {_n(y + 5)} L{_n(x - 6)} {_n(y + 5)} Z"/>'
    return f'<circle class="{cls}" cx="{_n(x)}" cy="{_n(y)}" r="5"/>'


def benchmark_timeline(tl):
    """One line per model through the deployed states of the app, cases passed on the benchmark. A state that was tried and
    not deployed is a hollow point off the line. A bar through a point is the spread of its repeat runs."""
    if not tl or not tl.get("states"):
        return Markup("")
    states, models = tl["states"], tl["models"]
    planned = max((p["planned"] for m in models for p in tl["points"][m].values()), default=0)
    if not planned:
        return Markup("")
    vals = [p["lo"] / p["planned"] * 100 for m in models for p in tl["points"][m].values()]
    lo = max(0, int(min(vals) // 10 * 10) - 10)
    n = len(states)

    def px(i):
        return T_L + (T_R - T_L) * (i + 0.5) / n

    def py(v):
        return T_B - (T_B - T_T) * (v - lo) / (100 - lo)
    o = [f'<svg class="chart bench-chart" viewBox="0 0 {T_W} {T_H}" role="img" focusable="false">',
         "<title>The benchmark over time</title>",
         "<desc>Share of benchmark cases passed by each model in each state of the app. "
         + " ".join(f"{short_model(m)}: " + ", ".join(f"{states[i]['label']} {tl['points'][m][s['id']]['mean']:.1f} of {tl['points'][m][s['id']]['planned']}"
                                                     for i, s in enumerate(states) if s['id'] in tl['points'][m]) + "." for m in models) + "</desc>"]
    for v in range(int(lo), 101, 10):
        o.append(f'<line class="tl-grid" x1="{T_L}" x2="{T_R}" y1="{_n(py(v))}" y2="{_n(py(v))}"/>'
                 f'<text class="q-tick" x="{T_L - 6}" y="{_n(py(v) + 4)}" text-anchor="end">{v}%</text>')
    for i, s in enumerate(states):
        o.append(f'<text class="q-name" x="{_n(px(i))}" y="{T_B + 18}" text-anchor="middle">{escape(_clip(s["label"], 26))}</text>')
        if s["date"]:
            o.append(f'<text class="q-tick" x="{_n(px(i))}" y="{T_B + 34}" text-anchor="middle">{escape(day_short(s["date"]))}</text>')
    for k, m in enumerate(models):
        cls = f"tl-m{k % 3}"
        pts = [(i, tl["points"][m][s["id"]]) for i, s in enumerate(states) if s["id"] in tl["points"][m]]
        line = [(px(i), py(p["mean"] / p["planned"] * 100)) for i, p in pts if states[i]["deployed"]]
        if len(line) > 1:
            o.append(f'<polyline class="tl-line {cls}" points="{" ".join(f"{_n(x)},{_n(y)}" for x, y in line)}"/>')
        for i, p in pts:
            x, y = px(i), py(p["mean"] / p["planned"] * 100)
            if p["runs"] > 1:
                o.append(f'<line class="tl-spread {cls}" x1="{_n(x)}" x2="{_n(x)}" y1="{_n(py(p["lo"] / p["planned"] * 100))}" y2="{_n(py(p["hi"] / p["planned"] * 100))}"/>')
            klass = f"tl-pt {cls}" + ("" if states[i]["deployed"] else " tl-hollow")
            o.append(f'<g><title>{escape(short_model(m))}, {escape(states[i]["label"])}: {p["mean"]:.1f} of {p["planned"]} cases, {p["runs"]} run(s)</title>'
                     + _mark(MARKS[k % 3], x, y, klass) + "</g>")
    o.append("</svg>")
    legend = "".join(f'<li><svg viewBox="0 0 28 14" aria-hidden="true" focusable="false"><line class="tl-line tl-m{k % 3}" x1="2" x2="26" y1="7" y2="7"/>'
                     + _mark(MARKS[k % 3], 14, 7, f"tl-pt tl-m{k % 3}") + f'</svg>{escape(short_model(m))}</li>' for k, m in enumerate(models))
    return Markup("".join(o) + f'<ul class="tl-legend">{legend}<li><svg viewBox="0 0 28 14" aria-hidden="true" focusable="false">'
                  f'<circle class="tl-pt tl-hollow tl-m0" cx="14" cy="7" r="5"/></svg>tried, not deployed</li></ul>')


KIND_ORDER = ("synthetic", "labels", "judge", "scope")


def by_kind(eval_runs):
    """{model id: [(kind, pass rate 0..1, cases), ...]} from finished runs: per dataset the newest run, then the
    mean within each kind. Kinds are never blended: a pass on built cases and a pass from a model judge are
    different claims."""
    latest = {}
    for e in eval_runs or []:
        rate = (e.get("summary") or {}).get("pass_rate")
        if e.get("status") != "done" or not _is_number(rate):
            continue
        key = (e.get("backend"), e.get("dataset"))
        if key not in latest or (e.get("id") or 0) > (latest[key].get("id") or 0):
            latest[key] = e
    groups = {}
    for (backend, _), e in latest.items():
        g = groups.setdefault(backend, {}).setdefault(e.get("dataset_kind") or "other", [0.0, 0, 0])
        g[0] += e["summary"]["pass_rate"]
        g[1] += 1
        g[2] += e.get("n_cases") or 0
    order = {k: i for i, k in enumerate(KIND_ORDER)}
    return {backend: [(k, v[0] / v[1], v[2]) for k, v in sorted(kinds.items(), key=lambda kv: (order.get(kv[0], 9), kv[0]))]
            for backend, kinds in groups.items()}


def eval_kinds():
    """Per-kind eval results for the model tables, read from the app's own database."""
    return by_kind(current_app.extensions["svc"].db.eval_runs())


def register(app):
    app.jinja_env.globals.update(
        chart_quadrant=quadrant, chart_trend=trend, chart_funnel=funnel, chart_meter=meter, chart_bar=bar,
        chart_span=span_bar, chart_cost_quality=cost_quality, chart_eval_matrix=eval_matrix, chart_benchmark_timeline=benchmark_timeline, eval_kinds=eval_kinds)
    app.jinja_env.filters.update(short_model=short_model, model_provider=model_provider, day=day,
                                 day_short=day_short, clock=clock)
