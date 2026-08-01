"""autoIntraday dashboard — Streamlit UI over the SQLite store. Read-only except config
(pause/resume, capital rules, paper/live). No broker/LLM calls. Thin render layer over
dashboard_data view functions. Run: streamlit run dashboard.py

See docs/superpowers/specs/2026-07-10-dashboard-design.md."""
from __future__ import annotations

import os

# PyArrow 25's bundled mimalloc allocator segfaults (mi_thread_init) when Arrow runs on the
# threads Streamlit creates/destroys — which is what `st.dataframe`/`st.table` do under the
# hood. Force Arrow onto the system allocator BEFORE streamlit (and thus pyarrow) is imported.
# Belt-and-suspenders: this module also renders tables as markdown (no Arrow) — see _md_table.
os.environ.setdefault("ARROW_DEFAULT_MEMORY_POOL", "system")

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import streamlit as st

from dashboard_data import (activity_log, closed_positions_for_day, decisions_for_day,
                            header_view, pending_view, pnl_summary, positions_view,
                            realized_for_day, runs_for_day)
from schedule_manager import (ScheduleError, apply_schedule, bar_close_aligned, next_fire,
                              next_primer_fire, primer_time, read_schedule)
from settings import load_settings
from store import Store
from trading_calendar import IST

DB_PATH = load_settings().db_path


@st.cache_resource
def _db_executor():
    """A single, permanent worker thread that owns the SQLite connection. Every DB operation
    runs on it, so Streamlit's (constantly created/destroyed) rerun threads never touch the
    connection directly — the reliable way to use SQLite from a threaded web server."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    ex = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autointraday-db")
    store = ex.submit(lambda: Store(DB_PATH)).result()   # created ON the worker thread
    return ex, store


def _db(fn):
    """Run a store operation on the single DB thread and return its result."""
    ex, store = _db_executor()
    return ex.submit(fn, store).result()


def _ist_day_bounds_utc(day) -> tuple[str, str]:
    """UTC ISO bounds [start, end) of one IST calendar day. DB timestamps are UTC, but the
    user's trading day is IST — an IST day starts at 18:30 UTC the previous evening."""
    start = datetime(day.year, day.month, day.day, tzinfo=IST)
    end = start + timedelta(days=1)
    return (start.astimezone(timezone.utc).isoformat(),
            end.astimezone(timezone.utc).isoformat())


def _fmt_ist(s: str) -> str | None:
    """A UTC ISO timestamp (e.g. '2026-07-16T06:00:02.933295+00:00') -> compact IST
    ('16 Jul 2026, 11:30:02 IST'). Returns None if `s` isn't an ISO timestamp, so callers
    fall back to the raw string. DB timestamps are stored UTC (store._utc_now)."""
    if not (len(s) >= 19 and s[4:5] == "-" and s[7:8] == "-" and "T" in s):
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:                       # naive -> assume UTC (that's how we store)
        dt = dt.replace(tzinfo=timezone.utc)
    return f"{dt.astimezone(IST):%d %b %Y, %H:%M:%S} IST"


def _fmt_ist_short(s: str) -> str | None:
    """UTC ISO timestamp -> compact IST date+time ('22 Jul, 14:32'). None if not an ISO ts."""
    if not (s and len(s) >= 19 and s[4:5] == "-" and s[7:8] == "-" and "T" in s):
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"{dt.astimezone(IST):%d %b, %H:%M}"


def _fmt_ist_time(s: str) -> str | None:
    """UTC ISO timestamp -> 'HH:MM:SS' in IST (time-of-day only; the date is the table's
    already-selected day). None if `s` isn't an ISO timestamp."""
    if not (len(s) >= 19 and s[4:5] == "-" and s[7:8] == "-" and "T" in s):
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return f"{dt.astimezone(IST):%H:%M:%S}"


# Enum values worth a colored badge chip in tables (dot + tinted background; text inherits
# the theme ink so contrast holds in light AND dark).
_BADGES = {
    "OPEN": "green", "SUCCESS": "green", "LONG": "green", "BUY_NOW": "green",
    "BUY_ON_PULLBACK": "green", "BUY_ON_BREAKOUT": "green", "EXECUTED": "green",
    "PENDING": "amber", "ADJUSTED": "amber", "PAUSED": "amber",
    "FAILED": "red", "SHORT": "red", "SELL_NOW": "red", "SHORT_NOW": "red", "EXIT": "red",
    "RUNNING": "blue", "HOLD": "blue", "ADOPTED": "blue", "CANCEL": "blue",
    "CLOSED": "gray", "CANCELLED": "gray", "EXPIRED": "gray", "WAIT": "gray",
    "NO_TRADE": "gray", "SKIP": "gray",
}


def _is_num(s: str) -> bool:
    try:
        float(s.replace("₹", "").replace(",", "").replace("%", "").strip())
        return True
    except ValueError:
        return False


def _cell_html(col: str, v) -> str:
    """One table cell: IST-format timestamps, badge known enums, right-align + sign-color
    numbers in P&L-ish columns, escape everything else."""
    import html as _html
    if v is None:
        return "<td></td>"
    s = str(v)
    if col == "time":
        t = _fmt_ist_time(s)
        if t:
            return f'<td class="t">{_html.escape(t)}</td>'
    ist = _fmt_ist(s)
    if ist:
        return f'<td class="t">{_html.escape(ist)}</td>'
    if s in _BADGES:
        return (f'<td><span class="ai-badge"><span class="ai-bdot ai-bdot--{_BADGES[s]}">'
                f'</span>{_html.escape(s)}</span></td>')
    if _is_num(s):
        cls = "num"
        if "pnl" in col.lower() or "p&l" in col.lower():
            val = float(s.replace("₹", "").replace(",", "").strip())
            if val > 0:
                cls += " pos"
            elif val < 0:
                cls += " neg"
        return f'<td class="{cls}">{_html.escape(s)}</td>'
    return f"<td>{_html.escape(s)}</td>"


def _md_table(rows: list[dict]) -> None:
    """Render rows as a self-built HTML table — deliberately NOT st.dataframe/st.table,
    which serialize via PyArrow (see the module-top note on the mimalloc segfault). Building
    the HTML ourselves also buys per-cell treatment: badges, numeric alignment, P&L color."""
    import html as _html
    if not rows:
        st.caption("— nothing here right now")
        return
    cols = list(rows[0].keys())
    head = "".join(f"<th>{_html.escape(str(c))}</th>" for c in cols)
    body = "".join(
        "<tr>" + "".join(_cell_html(str(c), r.get(c)) for c in cols) + "</tr>"
        for r in rows)
    st.markdown(f'<div class="ai-tblwrap"><table class="ai-tbl">'
                f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>",
                unsafe_allow_html=True)


def _runs_table(rows: list) -> None:
    """Job-runs table with a per-row 🔎 link that opens that run's Claude output. Built as raw HTML
    (like _md_table) because _md_table escapes cells and can't hold a clickable link. The link keeps
    the current tab params and adds vout=<run_id>, so clicking stays on this section and opens the
    dialog (handled where the table is rendered)."""
    import html as _html
    import urllib.parse
    if not rows:
        st.caption("— nothing here right now")
        return
    cols = list(rows[0].keys())
    base = dict(st.query_params)
    head = "".join(f"<th>{_html.escape(str(c))}</th>" for c in cols) + "<th>Output</th>"
    body = ""
    for r in rows:
        cells = "".join(_cell_html(str(c), r.get(c)) for c in cols)
        rid = r.get("id")
        if rid is None:
            cell = "<td></td>"
        else:
            qs = urllib.parse.urlencode({**base, "vout": rid})
            cell = (f'<td><a class="ai-act" href="?{qs}" target="_self" '
                    f'title="View Claude output">🔎</a></td>')
        body += f"<tr>{cells}{cell}</tr>"
    st.markdown(f'<div class="ai-tblwrap"><table class="ai-tbl">'
                f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>",
                unsafe_allow_html=True)


def _url_tabs(param: str, labels: list) -> str:
    """A tab-like selector whose choice is kept in the URL (?param=Label), so a browser refresh
    stays on the same section instead of snapping back to the first tab. Replaces st.tabs where
    the active section must survive a reload (st.tabs is client-side only and can't be persisted)."""
    stored = st.query_params.get(param)
    default = stored if stored in labels else labels[0]
    choice = st.segmented_control(param, labels, default=default, key=f"urltab_{param}",
                                  label_visibility="collapsed")
    if not choice:                       # single-select can be cleared — keep the current section
        choice = default
    if st.query_params.get(param) != choice:
        st.query_params[param] = choice
    return choice


@st.dialog("Claude skill output", width="large")
def _run_output_dialog(run_id: int) -> None:
    """Modal showing the raw Claude JSON recorded for every decision in one job run — the exact
    skill/engine output, per symbol, pretty-printed (falls back to raw text if it isn't JSON)."""
    import json as _json
    decs = _db(lambda s: s.get_decisions_for_run(run_id))
    with_json = [d for d in decs if d.raw_json]
    st.caption(f"Run #{run_id} · {len(decs)} decision(s), {len(with_json)} with Claude output")
    if not with_json:
        st.info("No Claude output recorded for this run (no decisions, or a backend that stores none).")
        return
    syms = sorted({d.symbol for d in with_json})
    pick = st.multiselect("Filter by symbol", syms, key=f"vout_sym_{run_id}",
                          placeholder="All symbols")
    for d in with_json:
        if pick and d.symbol not in pick:
            continue
        label = f"{d.symbol} — {d.action or '—'}"
        if d.score is not None:
            label += f"  ·  q{d.score:g}"
        with st.expander(label, expanded=len(with_json) == 1):
            try:
                st.json(_json.loads(d.raw_json))
            except Exception:
                st.code(d.raw_json)


def _tile(label: str, value: str, sub: str = "", tone: str = "plain") -> str:
    import html as _html
    sub_html = f'<div class="ai-tile-sub">{_html.escape(sub)}</div>' if sub else ""
    return (f'<div class="ai-tile ai-tile--{tone}">'
            f'<div class="ai-tile-label">{_html.escape(label)}</div>'
            f'<div class="ai-tile-value">{_html.escape(value)}</div>{sub_html}</div>')


def _tiles(tiles: list[str]) -> None:
    st.markdown(f'<div class="ai-tiles">{"".join(tiles)}</div>', unsafe_allow_html=True)


def _pnl_tone(v: float) -> str:
    return "pos" if v > 0 else ("neg" if v < 0 else "plain")


# Plain-language names for the raw exit_reason codes stored on positions.
_EXIT_LABELS = {
    "TARGET": "Hit its target", "STOP": "Hit its stop-loss", "SIGNAL": "Engine said exit",
    "SQUARE_OFF": "End-of-day square-off", "BROKER_SYNC": "Closed at broker / manually",
    "EXPIRED": "Resting order expired", "SQUAREOFF": "End-of-day square-off",
    "STALE": "Stale order cleaned up",
}


def _distinct(rows: list[dict], key: str) -> list:
    return sorted({r[key] for r in rows if r.get(key) not in (None, "")})


def _apply_filter(rows: list[dict], key: str, selected) -> list[dict]:
    """Keep rows whose `key` is in `selected`; empty selection means no filter (show all)."""
    return rows if not selected else [r for r in rows if r.get(key) in selected]


_CSS = """
<style>
/* ---- base: one system sans, committed weight contrast, tabular numbers -------------- */
html, body, [data-testid="stAppViewContainer"] {
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto,
               "Helvetica Neue", Arial, sans-serif;
}
.block-container { padding-top: 2.1rem; max-width: 1180px; }
[data-testid="stAppViewContainer"] h3 { font-size: 1rem; letter-spacing: -0.005em;
  font-weight: 650; }

/* theme-neutral surfaces (gray overlays) + ONE accent, reserved for "what happens next" */
:root {
  --ai-line: rgba(128,131,141,.30); --ai-line-soft: rgba(128,131,141,.16);
  --ai-tint: rgba(128,131,141,.07); --ai-tint-hover: rgba(128,131,141,.13);
  --ai-accent: #5a67d8; --ai-accent-tint: rgba(90,103,216,.11);
  --ai-green: #30a46c; --ai-red: #e5484d; --ai-amber: #e79008; --ai-blue: #5a67d8;
  --ai-pos: #217a52; --ai-neg: #c53030;
}
@media (prefers-color-scheme: dark) {
  :root { --ai-accent: #7c88f8; --ai-accent-tint: rgba(124,136,248,.16);
          --ai-pos: #3dd68c; --ai-neg: #ff7b72; }
}

/* ---- brand header ------------------------------------------------------------------- */
.ai-brand { font-size: 1.7rem; font-weight: 750; letter-spacing: -0.02em;
  line-height: 1.15; }
.ai-brand em { font-style: normal; color: var(--ai-accent); }
.ai-pills { display: flex; gap: .45rem; align-items: center; margin: .45rem 0 .1rem;
  flex-wrap: wrap; }
.ai-pill { display: inline-flex; align-items: center; gap: .45rem;
  padding: .24rem .7rem; border-radius: 999px; font-size: .76rem; font-weight: 600;
  border: 1px solid var(--ai-line-soft); background: var(--ai-tint); }
.ai-pill--quiet { font-weight: 500; opacity: .8; }
.ai-dot { width: .53rem; height: .53rem; border-radius: 50%; flex: none; }
.ai-dot--live   { background: var(--ai-red);   box-shadow: 0 0 0 3px rgba(229,72,77,.18); }
.ai-dot--paper  { background: var(--ai-green); box-shadow: 0 0 0 3px rgba(48,164,108,.18); }
.ai-dot--paused { background: var(--ai-amber); box-shadow: 0 0 0 3px rgba(231,144,8,.18); }
.ai-dot--active { background: var(--ai-green); box-shadow: 0 0 0 3px rgba(48,164,108,.18); }
.ai-clock { text-align: right; font-variant-numeric: tabular-nums; opacity: .62;
  font-size: .82rem; padding-top: 1rem; line-height: 1.5; }

/* ---- stat tiles (custom HTML — full hierarchy control) ------------------------------ */
.ai-tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: .6rem; margin: .9rem 0 .4rem; }
.ai-tile { border: 1px solid var(--ai-line-soft); border-radius: 12px;
  padding: .7rem .9rem .65rem; background: var(--ai-tint);
  transition: border-color .15s cubic-bezier(.25,1,.5,1),
              transform .15s cubic-bezier(.25,1,.5,1); }
.ai-tile:hover { border-color: var(--ai-line); transform: translateY(-1px); }
.ai-tile-label { font-size: .66rem; font-weight: 600; text-transform: uppercase;
  letter-spacing: .08em; opacity: .62; margin-bottom: .28rem; }
.ai-tile-value { font-size: 1.42rem; font-weight: 700; letter-spacing: -0.015em;
  font-variant-numeric: tabular-nums; line-height: 1.1; }
.ai-tile-sub { font-size: .74rem; opacity: .62; margin-top: .3rem;
  font-variant-numeric: tabular-nums; }
.ai-tile--accent { border-color: var(--ai-accent-tint); background: var(--ai-accent-tint); }
.ai-tile--accent .ai-tile-value { color: var(--ai-accent); }
.ai-tile--pos .ai-tile-value { color: var(--ai-pos); }
.ai-tile--neg .ai-tile-value { color: var(--ai-neg); }

/* ---- data tables (self-built HTML — the PyArrow-safe path) -------------------------- */
.ai-tblwrap { overflow-x: auto; border: 1px solid var(--ai-line-soft);
  border-radius: 12px; margin: .3rem 0 .9rem; }
.ai-tbl { width: 100%; border-collapse: separate; border-spacing: 0;
  font-size: .855rem; font-variant-numeric: tabular-nums; }
.ai-tbl th { text-align: left; padding: .5rem .8rem; background: var(--ai-tint);
  font-size: .66rem; font-weight: 650; text-transform: uppercase; letter-spacing: .07em;
  opacity: .72; border-bottom: 1px solid var(--ai-line-soft); white-space: nowrap; }
.ai-tbl td { padding: .48rem .8rem; border-bottom: 1px solid var(--ai-line-soft);
  white-space: nowrap; }
.ai-tbl tr:last-child td { border-bottom: none; }
.ai-tbl tbody tr { transition: background .15s cubic-bezier(.25,1,.5,1); }
.ai-tbl tbody tr:hover { background: var(--ai-tint); }
.ai-tbl td.num { text-align: right; }
.ai-tbl td.pos { color: var(--ai-pos); font-weight: 650; }
.ai-tbl td.neg { color: var(--ai-neg); font-weight: 650; }
.ai-tbl td.t { opacity: .78; font-size: .8rem; }
.ai-badge { display: inline-flex; align-items: center; gap: .4rem;
  padding: .14rem .55rem; border-radius: 999px; font-size: .72rem; font-weight: 600;
  border: 1px solid var(--ai-line-soft); background: var(--ai-tint);
  letter-spacing: .01em; }
.ai-bdot { width: .45rem; height: .45rem; border-radius: 50%; flex: none; }
.ai-bdot--green { background: var(--ai-green); }
.ai-bdot--red   { background: var(--ai-red); }
.ai-bdot--amber { background: var(--ai-amber); }
.ai-bdot--blue  { background: var(--ai-blue); }
.ai-bdot--gray  { background: rgba(128,131,141,.7); }

/* ---- swing analysis table (bordered, per-row collapsible reason) --------------------- */
.ai-swt { border: 1px solid var(--ai-line-soft); border-radius: 12px; overflow: hidden;
  margin: .3rem 0 .9rem; font-size: .855rem; font-variant-numeric: tabular-nums; }
.ai-swt-head, .ai-swt summary { display: flex; align-items: center; gap: .7rem;
  padding: .5rem .8rem; }
.ai-swt-head { background: var(--ai-tint); border-bottom: 1px solid var(--ai-line-soft);
  font-size: .66rem; font-weight: 650; text-transform: uppercase; letter-spacing: .07em;
  opacity: .72; }
.ai-swt-row { border-bottom: 1px solid var(--ai-line-soft); }
.ai-swt-row:last-child { border-bottom: none; }
.ai-swt summary { cursor: pointer; list-style: none;
  transition: background .15s cubic-bezier(.25,1,.5,1); }
.ai-swt summary::-webkit-details-marker { display: none; }
.ai-swt summary:hover { background: var(--ai-tint); }
.ai-swt-row[open] > summary { background: var(--ai-tint); }
.ai-caret { flex: 0 0 .8rem; opacity: .5; font-size: .7rem;
  transition: transform .15s cubic-bezier(.25,1,.5,1); }
.ai-swt-row[open] > summary .ai-caret { transform: rotate(90deg); }
.ai-swt-reason { padding: .35rem .8rem .7rem 2.3rem; font-size: .82rem; opacity: .85;
  line-height: 1.55; border-top: 1px dashed var(--ai-line-soft);
  background: rgba(128,131,141,.04); }
.ai-swt .c-sym { flex: 1 1 13%; font-weight: 650; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ai-swt .c-status { flex: 0 0 11%; opacity: .82; }
.ai-swt .c-qty { flex: 0 0 6%; text-align: right; }
.ai-swt .c-avg { flex: 0 0 9%; text-align: right; }
.ai-swt .c-swing, .ai-swt .c-ss { flex: 1 1 20%; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ai-swt .c-when { flex: 0 0 11%; opacity: .68; font-size: .78rem; white-space: nowrap; }
.ai-swt .c-act { flex: 0 0 2.2rem; text-align: right; }
.ai-act { display: inline-block; text-decoration: none; font-size: 1rem; line-height: 1;
  color: var(--ai-accent); border-radius: 7px; padding: .12rem .28rem;
  transition: background .15s cubic-bezier(.25,1,.5,1); }
.ai-act:hover { background: var(--ai-accent-tint); }
.ai-act--off { color: rgba(128,131,141,.55); cursor: default; }

/* ---- tabs: the accent marks where you are ------------------------------------------- */
button[data-baseweb="tab"] p { font-size: .92rem !important; font-weight: 600; }
button[data-baseweb="tab"] { padding-top: .55rem; padding-bottom: .55rem; }
button[data-baseweb="tab"][aria-selected="true"] p { color: var(--ai-accent) !important; }
[data-baseweb="tab-highlight"] { background-color: var(--ai-accent) !important; }

/* ---- buttons + inputs --------------------------------------------------------------- */
.stButton button { border-radius: 10px; font-weight: 600;
  transition: border-color .15s cubic-bezier(.25,1,.5,1); }
.stButton button:hover { border-color: var(--ai-accent); color: var(--ai-accent); }

/* ---- sidebar ------------------------------------------------------------------------ */
[data-testid="stSidebar"] h2 { font-size: 1.02rem; font-weight: 700; }
[data-testid="stSidebar"] h3 { font-size: .72rem; text-transform: uppercase;
  letter-spacing: .08em; opacity: .68; margin-top: 1rem; font-weight: 650; }

@media (prefers-reduced-motion: reduce) { * { transition: none !important;
  transform: none !important; } }

/* ---- compare equity chart (self-built SVG — PyArrow-safe) --------------------------- */
.ai-eqwrap { border: 1px solid var(--ai-line-soft); border-radius: 12px; padding: .6rem .8rem;
  margin: .3rem 0 .9rem; background: var(--ai-tint); }
.ai-eqchart { width: 100%; height: 200px; display: block; }
.ai-eq-zero { stroke: var(--ai-line); stroke-width: 1; stroke-dasharray: 3 3; }
.ai-eq-legend { display: flex; gap: 1rem; flex-wrap: wrap; margin-top: .4rem;
  font-size: .8rem; font-variant-numeric: tabular-nums; }
.ai-eq-key { display: inline-flex; align-items: center; gap: .4rem; opacity: .85; }
.ai-eq-dot { width: .6rem; height: .6rem; border-radius: 2px; flex: none; }
</style>
"""


def _pill(label: str, dot: str) -> str:
    return f'<span class="ai-pill"><span class="ai-dot ai-dot--{dot}"></span>{label}</span>'


def _render_performance(start_iso, end_iso) -> None:
    """Render the plain-language performance block for a window (both None = all-time)."""
    perf = _db(lambda s: s.performance_summary(start_iso, end_iso))
    if perf["trades"] == 0:
        st.info("No closed trades in this period yet.")
        return
    total = perf["total_pnl"]
    verb = "made" if total >= 0 else "lost"
    st.markdown(
        f"**Across {perf['trades']} closed trades, the bot {verb} ₹{abs(total):,.2f} "
        f"in total** — winning {perf['win_rate_pct']}% of them ({perf['wins']} up, "
        f"{perf['losses']} down), about ₹{perf['expectancy_per_trade']:,.2f} on a typical trade.")
    _tiles([
        _tile("Total profit / loss", f"₹{total:,.2f}",
              "all closed trades added up", tone=_pnl_tone(total)),
        _tile("Closed trades", str(perf["trades"]),
              f"{perf['wins']} winners · {perf['losses']} losers"),
        _tile("Win rate", f"{perf['win_rate_pct']}%",
              f"{perf['wins']} of {perf['trades']} made money"),
        _tile("Average winner", f"₹{perf['avg_win']:,.2f}",
              "typical profit when it wins", tone="pos"),
        _tile("Average loser", f"₹{perf['avg_loss']:,.2f}",
              "typical loss when it loses", tone="neg"),
        _tile("Average per trade", f"₹{perf['expectancy_per_trade']:,.2f}",
              "winners & losers blended", tone=_pnl_tone(perf["expectancy_per_trade"])),
    ])
    st.subheader("How trades closed")
    st.caption("Each way a trade can end, how many closed that way, and the P&L it produced.")
    _md_table([
        {"How it closed": _EXIT_LABELS.get(r["exit_reason"], r["exit_reason"] or "—"),
         "Trades": r["count"], "P&L": f"₹{r['total_pnl']:,.2f}"}
        for r in _db(lambda s: s.exit_reason_breakdown(start_iso, end_iso))])


@st.dialog("Settings")
def _settings_dialog() -> None:
    """All controls, in one compact modal opened from the top-right. Grouped into tabs so only
    the relevant section shows at a time: Trading, Schedule, Data."""
    h = _db(header_view)
    t_capital, t_strategies, t_schedule, t_data = st.tabs(
        ["Capital", "Strategies", "Schedule", "Data"])

    with t_strategies:
        cfg = _db(lambda s: s.get_config())
        reg = _strategy_registry()
        names = {x.id: x.name for x in reg.all()}
        ids = reg.ids()

        def _idx(val):
            return ids.index(val) if val in ids else 0

        st.caption("Pick which strategy generates decisions in each mode, or turn on Compare "
                   "Testing to run several side by side (paper-only).")
        live_sel = st.radio("Live strategy", ids, index=_idx(cfg.live_strategy),
                            format_func=lambda i: names.get(i, i), horizontal=True, key="cfg_live_strat")
        paper_sel = st.radio("Paper strategy", ids, index=_idx(cfg.paper_strategy),
                             format_func=lambda i: names.get(i, i), horizontal=True, key="cfg_paper_strat")
        st.divider()
        comp_on = st.toggle("Enable Compare Testing", value=cfg.compare_enabled)
        comp_sel = st.multiselect("Strategies to compare", ids,
                                  default=[i for i in cfg.compare_strategies if i in ids] or ids,
                                  format_func=lambda i: names.get(i, i))
        if comp_on:
            st.warning("Compare Testing runs PAPER trades only — Live mode is turned OFF while "
                       "it's on, and no broker orders are placed.")
        if st.button("Save strategy settings", use_container_width=True):
            fields = {"live_strategy": live_sel, "paper_strategy": paper_sel,
                      "compare_enabled": comp_on, "compare_strategies": comp_sel or ids}
            if comp_on:
                fields["mode"] = "paper"          # compare is paper-only -> disable live
            _db(lambda s: s.update_config(**fields))
            st.success("Saved.")
            st.rerun()

    with t_capital:
        sub_capital, sub_exits, sub_exec = st.tabs(["Capital", "Exits", "Execution"])

        with sub_capital:
            st.caption("Pause and Paper/Live are on the header. These are the sizing rules.")
            cc1, cc2, cc3 = st.columns(3)
            total_pool = cc1.number_input("Pool (₹)", min_value=0.0, value=float(h["total_pool"]),
                                          step=1000.0)
            max_pos = cc2.number_input("Max positions", min_value=0,
                                       value=int(h["max_open_positions"]), step=1)
            cap_pos = cc3.number_input("Per position (₹)", min_value=0.0,
                                       value=float(h["capital_per_position"]), step=1000.0)
            if st.button("Save capital rules", use_container_width=True):
                _db(lambda s: s.update_config(total_pool=total_pool,
                                              max_open_positions=int(max_pos),
                                              capital_per_position=cap_pos))
                st.success("Saved.")
                st.rerun()

        with sub_exits:
            st.caption("Early profit-taking — secure the win before the far target reverts. "
                       "Percentages are RETURN ON MARGIN (at 5x, 7% ≈ a 1.4% move, 15% ≈ 3%).")
            pb = _db(lambda s: s.get_config())
            pb_on = st.toggle("Take profit early", value=pb.profit_book_enabled,
                              help="Off = let winners ride all the way to the structural target.")
            pc1, pc2 = st.columns(2)
            partial_pct = pc1.number_input("Book half at (% on margin)", min_value=0.0,
                                           max_value=100.0, value=float(pb.profit_book_partial_pct),
                                           step=1.0, disabled=not pb_on)
            full_pct = pc2.number_input("Exit all at (% on margin)", min_value=0.0, max_value=300.0,
                                        value=float(pb.profit_book_full_pct), step=1.0,
                                        disabled=not pb_on)
            if st.button("Save profit-taking", use_container_width=True):
                if pb_on and full_pct and partial_pct and full_pct < partial_pct:
                    st.error("'Exit all' % must be ≥ 'Book half' %.")
                else:
                    _db(lambda s: s.update_config(profit_book_enabled=pb_on,
                                                  profit_book_partial_pct=partial_pct,
                                                  profit_book_full_pct=full_pct))
                    st.success("Saved.")
                    st.rerun()

            st.divider()
            st.caption("Exit placement (LIVE only) — where the stop + target actually live. "
                       "DB-only keeps them soft (the 5-min cycle market-exits when hit). The eager "
                       "modes rest a REAL Groww bracket (stop SL_M + target LIMIT, one-cancels-other).")
            ex = _db(lambda s: s.get_config())
            _EXIT_MODES = [("db_only", "DB-only (soft levels)"),
                           ("armed", "Armed (place near a level)"),
                           ("on_fill", "On fill (bracket at Groww)")]
            _mode_ids = [m for m, _ in _EXIT_MODES]
            _labels = {m: lbl for m, lbl in _EXIT_MODES}
            cur_mode = ex.exit_mode if ex.exit_mode in _mode_ids else "db_only"
            em = st.radio("Exit order placement", _mode_ids, index=_mode_ids.index(cur_mode),
                          format_func=lambda m: _labels[m],
                          help="DB-only = market exit at the 5-min cycle (default, today's behavior). "
                               "Armed = rest the bracket at Groww once price nears a level. "
                               "On fill = rest the bracket the moment the entry fills (most protective, "
                               "most API calls).")
            ex_band = st.number_input("Armed: place within (% of the level)", min_value=0.1,
                                      max_value=5.0, value=float(ex.arm_exit_band_pct), step=0.1,
                                      format="%.1f", disabled=(em != "armed"),
                                      help="Only used in Armed mode — how close price must get to the "
                                           "stop or target before the bracket is placed.")
            fallback_pct = st.number_input(
                "Fallback stop for unprotected positions (% from entry)", min_value=0.0,
                max_value=10.0, value=float(ex.adopt_fallback_stop_pct), step=0.1,
                format="%.1f",
                help="Applied to any OPEN position that still has no stop after the engine's "
                     "read — an adopted manual position, or a read that returned WAIT. The "
                     "engine's structural stop replaces it as soon as it arrives and never "
                     "widens it. 0 disables the floor.")
            if em != "db_only":
                st.warning("Eager modes place REAL Groww orders. LIVE only (paper stays soft). Don't "
                           "switch this on the live account until the orphaned ledgers are cleaned and "
                           "cancel is verified in production.")
            if st.button("Save exit placement", use_container_width=True):
                _db(lambda s: s.update_config(exit_mode=em, arm_exit_band_pct=ex_band,
                                              adopt_fallback_stop_pct=fallback_pct))
                st.success("Saved.")
                st.rerun()

        with sub_exec:
            st.caption("Execution breathing space — applied to Claude's levels before the order goes "
                       "to Groww. Entry is nudged toward price (fills easier); the stop is placed a "
                       "little WIDER (a long's SL goes lower, so noise doesn't knock it out); the "
                       "target keeps (100 − shave)% of the move.")
            m = _db(lambda s: s.get_config())
            m1, m2, m3 = st.columns(3)
            entry_tol = m1.number_input("Entry nudge %", min_value=0.0, max_value=5.0,
                                        value=float(m.entry_tolerance_pct), step=0.05, format="%.2f")
            stop_tol = m2.number_input("Stop widen %", min_value=0.0, max_value=5.0,
                                       value=float(m.stop_tolerance_pct), step=0.05, format="%.2f")
            target_shave = m3.number_input("Target shave %", min_value=0.0, max_value=90.0,
                                           value=float(m.target_shave_pct), step=1.0, format="%.1f")
            rr_enabled = st.checkbox(
                "Enable the R:R gate", value=bool(m.rr_gate_enabled),
                help="ON (default): a trade must clear the reward:risk floor (1.5:1) — both the "
                     "skill's self-reported R:R and the recomputed geometric R:R. OFF: skip the "
                     "R:R floor entirely and take entries on quality + confidence alone (still "
                     "needs valid levels, a real stop distance, sizing room, no trend veto, and a "
                     "trade with actual upside). Looser — takes more trades, including poor-R:R ones.")
            rr_pre_margin = st.checkbox(
                "Gate R:R on raw levels (before margins)", value=bool(m.rr_gate_pre_margin),
                disabled=not rr_enabled,
                help="Only matters when the R:R gate is ON. ON (default): the gate judges Claude's "
                     "RAW entry/stop/target — the margins above only shape the actual orders, so a "
                     "wide target-shave books less profit but never rejects the trade for 'low "
                     "R:R'. OFF: the shaved target / widened stop must still clear the R:R floor "
                     "AFTER margins (stricter, fewer trades).")
            if st.button("Save execution margins", use_container_width=True):
                _db(lambda s: s.update_config(entry_tolerance_pct=entry_tol,
                                              stop_tolerance_pct=stop_tol,
                                              target_shave_pct=target_shave,
                                              rr_gate_enabled=rr_enabled,
                                              rr_gate_pre_margin=rr_pre_margin))
                st.success("Saved.")
                st.rerun()

            st.divider()
            st.caption("Scale into strength — pyramid extra capital into a position while the engine "
                       "keeps re-affirming a STRONG same-side entry for N consecutive cycles. OFF by "
                       "default. Each add is add% of the per-position capital, up to max-adds; a "
                       "pyramided position's full-book rises to the pyramid full-book so the added "
                       "capital can chase a bigger move. The structural stop is never widened.")
            pc = _db(lambda s: s.get_config())
            pyr_on = st.checkbox("Enable scale-into-strength", value=bool(pc.pyramid_enabled))
            pk1, pk2, pk3 = st.columns(3)
            pyr_add = pk1.number_input("Add % of capital", min_value=10.0, max_value=100.0,
                                       value=float(pc.pyramid_add_pct), step=5.0, format="%.0f")
            pyr_max = pk2.number_input("Max adds", min_value=1, max_value=5,
                                       value=int(pc.pyramid_max_adds), step=1)
            pyr_full = pk3.number_input("Pyramid full-book % (on margin)", min_value=0.0,
                                        max_value=100.0, value=float(pc.pyramid_full_pct),
                                        step=1.0, format="%.0f")
            pk4, pk5, pk6 = st.columns(3)
            pyr_conf = pk4.number_input("Confirm cycles", min_value=1, max_value=6,
                                        value=int(pc.pyramid_confirm_cycles), step=1)
            pyr_q = pk5.number_input("Min quality", min_value=0.0, max_value=100.0,
                                     value=float(pc.pyramid_min_quality), step=1.0, format="%.0f")
            pyr_c = pk6.number_input("Min confidence", min_value=0.0, max_value=100.0,
                                     value=float(pc.pyramid_min_confidence), step=1.0, format="%.0f")
            if pyr_on:
                st.warning(f"Adds REAL capital to winners in LIVE and can push a position to "
                           f"{1 + pyr_add * pyr_max / 100:.1f}x its base size. Test in paper first.")
            if st.button("Save scale-into-strength", use_container_width=True):
                _db(lambda s: s.update_config(
                    pyramid_enabled=pyr_on, pyramid_add_pct=pyr_add,
                    pyramid_max_adds=int(pyr_max), pyramid_full_pct=pyr_full,
                    pyramid_confirm_cycles=int(pyr_conf), pyramid_min_quality=pyr_q,
                    pyramid_min_confidence=pyr_c))
                st.success("Saved.")
                st.rerun()

    with t_schedule:
        try:
            sched = read_schedule()
        except ScheduleError as e:
            st.error(str(e))
        else:
            from datetime import time as dtime
            try:
                _ph, _pm = (int(x) for x in _db(lambda s: s.get_config()).primer_time.split(":"))
                primer_default = dtime(_ph, _pm)
            except Exception:
                primer_default = dtime(7, 30)
            s1, s2, s3 = st.columns(3)
            # step=60 (not 300): the bar-close-aligned fires land on :01/:16/:31/:46, which a
            # 5-minute picker cannot express.
            first = s1.time_input("First cycle", value=dtime(*sched["start"]), step=60,
                                  help="Best at one minute past a 15m bar close — 09:31 or 09:36.")
            last = s2.time_input("Last cycle", value=dtime(*sched["last"]), step=60)
            interval = s3.number_input("Every (min)", min_value=5, max_value=120,
                                       value=int(sched["interval_min"]) or 20, step=5)
            if bar_close_aligned((first.hour, first.minute), (last.hour, last.minute),
                                 int(interval)):
                st.caption("✅ Bar-close aligned — every cycle fires 1 min after a 15m candle "
                           "closes, so the engine never races the data feed.")
            else:
                st.warning(
                    "⚠️ Not bar-close aligned. The engine decides on COMPLETED 15m candles "
                    "(Yahoo buckets NSE from 09:15, so they close at 09:30 / 09:45 / 10:00 …). "
                    "Firing on the close itself can read a bar the feed hasn't finished writing "
                    "— stale price, understated volume/RVOL. Pick a first cycle whose minute is "
                    "one past a close (09:31, 09:36 …) with an interval that divides 15.")
            primer_in = st.time_input("Claude primer time (IST)", value=primer_default, step=300,
                                      help="Throwaway Claude call that starts the 5-hour usage "
                                           "window early so it resets during trading. Default 07:30.")
            st.caption("No square-off cycle — Groww auto-flattens intraday at close. Applying "
                       "reloads the scheduler; refused while a cycle is running.")
            if st.button("Apply schedule", use_container_width=True):
                try:
                    msg = apply_schedule((first.hour, first.minute), (last.hour, last.minute),
                                         int(interval),
                                         primer_hm=(primer_in.hour, primer_in.minute))
                except ScheduleError as e:
                    st.error(str(e))
                else:
                    _db(lambda s: s.update_config(
                        primer_time=f"{primer_in.hour:02d}:{primer_in.minute:02d}"))
                    st.success(msg)
                    st.rerun()

            primer_on = _db(lambda s: s.get_config().primer_enabled)
            new_primer = st.toggle(
                f"Claude primer — prime the window at {primer_in.hour:02d}:{primer_in.minute:02d} IST",
                value=primer_on,
                help="Runs a throwaway Claude call at the primer time above to start the 5-hour "
                     "usage window early, so it resets during trading, not after.")
            if new_primer != primer_on:
                _db(lambda s: s.update_config(primer_enabled=new_primer))
                st.rerun()

    with t_data:
        st.caption("Delete history older than 30 days. Keeps the last 30 days, every open/resting "
                   "position, and your settings.")
        purge_ok = st.checkbox("I understand this permanently deletes data older than 30 days")
        if st.button("Clear old data (> 30 days)", disabled=not purge_ok,
                     use_container_width=True):
            counts = _db(lambda s: s.purge_old_history())
            deleted = sum(counts.values())
            if deleted == 0:
                st.info("Nothing older than 30 days — nothing deleted.")
            else:
                st.success(f"Cleared {deleted} old rows "
                           f"({counts['positions']} trades, {counts['decisions']} decisions, "
                           f"{counts['orders']} orders, {counts['job_runs']} runs).")


def _intraday_strategy_view() -> str | None:
    """The strategy_id to scope the Intraday page to, or None when only one strategy has traded
    (then the page shows everything, exactly as before). The selector appears only with >1."""
    from strategies import is_compare_ledger
    # Only the live/paper (base) ledgers belong on the Intraday page; compare ledgers ("cmp:*")
    # live on the Compare page.
    present = [p for p in _db(lambda s: s.strategy_ids_present()) if not is_compare_ledger(p)]
    if len(present) <= 1:
        return None
    reg = _strategy_registry()
    names = {x.id: x.name for x in reg.all()}
    cfg = _db(lambda s: s.get_config())
    default = cfg.paper_strategy if cfg.paper_strategy in present else present[0]
    return st.radio("Strategy view", present, index=present.index(default),
                    format_func=lambda i: names.get(i, i), horizontal=True,
                    key="intraday_strat_view")


def _render() -> None:
    from store import ScopedStore

    sid = _intraday_strategy_view()

    def _sdb(fn):
        """Run a view function against the store, scoped to the selected strategy (or the raw
        store when a single strategy has traded — identical to the pre-multi-strategy behaviour)."""
        return _db(lambda s: fn(ScopedStore(s, sid))) if sid else _db(fn)

    h = _sdb(header_view)
    left, right = st.columns([3, 1])
    with left:
        st.markdown('<div class="ai-brand">autoIntraday<em>.</em></div>',
                    unsafe_allow_html=True)
        try:
            sched = read_schedule()
            sched_chip = (f'<span class="ai-pill ai-pill--quiet">every '
                          f'{sched["interval_min"]}m · '
                          f'{sched["start"][0]:02d}:{sched["start"][1]:02d}–'
                          f'{sched["last"][0]:02d}:{sched["last"][1]:02d}</span>')
        except ScheduleError:
            sched_chip = ""
        pills = [_pill("LIVE", "live") if h["mode"] == "live" else _pill("PAPER", "paper"),
                 _pill("PAUSED", "paused") if h["is_paused"] else _pill("ACTIVE", "active"),
                 sched_chip]
        st.markdown(f'<div class="ai-pills">{"".join(pills)}</div>', unsafe_allow_html=True)
    with right:
        st.markdown(f'<div class="ai-clock">{datetime.now(IST):%a %d %b %Y · %H:%M:%S} IST'
                    f'</div>', unsafe_allow_html=True)

    # Quick controls on the header — two aligned toggles + Settings.
    cA, cB, _sp, cC = st.columns([1.3, 1.5, 3.7, 1.3], vertical_alignment="center")
    with cA:
        paused = st.toggle("Paused", value=h["is_paused"],
                           help="Stops NEW entries; open positions are still managed.")
        if paused != h["is_paused"]:
            _db(lambda s: s.update_config(is_paused=paused))
            st.rerun()
    with cB:
        compare_on = _db(lambda s: s.get_config().compare_enabled)
        live = st.toggle("Live mode", value=(h["mode"] == "live"), disabled=compare_on,
                         help=("Disabled during Compare Testing (paper-only)." if compare_on else
                               "ON = REAL orders on Groww. OFF = paper (simulated). "
                               "The next cycle acts on the new mode."))
        if not compare_on and live != (h["mode"] == "live"):
            _db(lambda s: s.update_config(mode="live" if live else "paper"))
            st.rerun()
    with cC:
        if st.button("⚙ Settings", use_container_width=True):
            _settings_dialog()

    today_iso = datetime.now(timezone.utc).date().isoformat()
    pnl = _sdb(lambda s: pnl_summary(s, today_iso))
    primer_on = _db(lambda s: s.get_config().primer_enabled)
    nf = next_fire()
    if nf is None:
        next_tile = _tile("Next cycle", "—", "scheduler not loaded")
    else:
        mins = int((nf - datetime.now(IST)).total_seconds() // 60)
        when = f"in {mins} min" if mins < 24 * 60 else f"{nf:%a %d %b}"
        next_tile = _tile("Next cycle", f"{nf:%H:%M}", when, tone="accent")
    if not primer_on:
        primer_tile = _tile("Claude primer", "off", "enable in Settings")
    else:
        pf = next_primer_fire()
        if pf is None:
            primer_tile = _tile("Claude primer", "on", "agent not installed")
        else:
            pmin = int((pf - datetime.now(IST)).total_seconds() // 60)
            pwhen = f"in {pmin} min" if pmin < 24 * 60 else f"{pf:%a %d %b}"
            primer_tile = _tile("Claude primer", f"{pf:%H:%M}", pwhen)
    _tiles([
        _tile("Margin used", f"₹{h['deployed_capital']:,.0f}",
              f"{h['utilization_pct']}% of ₹{h['total_pool']:,.0f} pool · "
              f"₹{h.get('deployed_notional', 0):,.0f} notional"),
        _tile("Open positions", f"{h['open_count']} / {h['max_open_positions']}"),
        _tile("Resting orders", str(h["pending_count"])),
        next_tile,
        primer_tile,
        _tile("P&L today", f"₹{pnl['realized_today']:,.2f}",
              tone=_pnl_tone(pnl["realized_today"])),
        _tile("P&L total", f"₹{pnl['realized_total']:,.2f}",
              tone=_pnl_tone(pnl["realized_total"])),
    ])

    section = _url_tabs("tab", ["Overview", "Performance", "History"])

    if section == "Overview":
        st.subheader("Pending / resting orders")
        st.caption("Placed but not yet filled — each fills when price reaches `rest_at`, then "
                   "arms its target/stop. Cancelled at square-off if never reached.")
        _md_table(_sdb(pending_view))

        st.subheader("Open positions")
        _md_table([r for r in _sdb(positions_view) if r["status"] == "OPEN"])

    elif section == "Performance":
        st.caption("How the bot's finished trades have actually done. A trade counts here only "
                   "once it's closed.")
        today_bounds = _ist_day_bounds_utc(datetime.now(IST).date())
        perf_today, perf_all = st.tabs(["Today", "All-time"])
        with perf_today:
            _render_performance(*today_bounds)
        with perf_all:
            _render_performance(None, None)

    elif section == "History":
        today_ist = datetime.now(IST).date()
        # Selected day lives in session so the Prev/Today/Next buttons and the calendar all
        # drive the same value. Never past today.
        sel = st.session_state.get("hist_day", today_ist)
        if sel > today_ist:
            sel = today_ist
        # Date navigation, top-right; drives every nested tab below.
        head_l, head_r = st.columns([2, 3], vertical_alignment="center")
        with head_r:
            b_prev, b_today, b_next, b_cal = st.columns([1, 1, 1, 2.4],
                                                        vertical_alignment="center")
            if b_prev.button("◀ Prev", use_container_width=True):
                st.session_state["hist_day"] = sel - timedelta(days=1)
                st.rerun()
            if b_today.button("Today", use_container_width=True,
                              disabled=(sel == today_ist)):
                st.session_state["hist_day"] = today_ist
                st.rerun()
            if b_next.button("Next ▶", use_container_width=True,
                             disabled=(sel >= today_ist)):
                st.session_state["hist_day"] = sel + timedelta(days=1)
                st.rerun()
            day = b_cal.date_input("Day", value=sel, max_value=today_ist,
                                   label_visibility="collapsed",
                                   help="Pick any past date to review an older session.")
        st.session_state["hist_day"] = day        # keep buttons in sync with a calendar pick
        start_iso, end_iso = _ist_day_bounds_utc(day)
        day_label = "today" if day == today_ist else day.strftime("%a %d %b %Y")
        day_pnl = _sdb(lambda s: realized_for_day(s, start_iso, end_iso))
        with head_l:
            st.markdown(f"#### History — {day_label}")
            st.caption(f"Realized P&L: ₹{day_pnl:,.2f}")

        htab = _url_tabs("htab", ["Operations", "Job runs", "Decisions", "Closed positions"])

        if htab == "Operations":
            act = _db(lambda s: s.activity_summary(start_iso, end_iso))
            _tiles([
                _tile("Buy orders", str(act["buys"])),
                _tile("Sell orders", str(act["sells"])),
                _tile("Entries opened", str(act["entries"])),
                _tile("Exits", str(act["exits"])),
                _tile("Added more", str(act["added"])),
                _tile("SL / target adj.", str(act["adjusted"])),
                _tile("Cancelled", str(act["cancels"])),
                _tile("Adopted", str(act["adopted"])),
            ])
            log = _sdb(lambda s: activity_log(s, start_iso, end_iso))
            f1, f2 = st.columns(2)
            ev = f1.multiselect("Event", _distinct(log, "event"), key="op_ev")
            sym = f2.multiselect("Symbol", _distinct(log, "symbol"), key="op_sym")
            _md_table(_apply_filter(_apply_filter(log, "event", ev), "symbol", sym))

        elif htab == "Job runs":
            runs = _sdb(lambda s: runs_for_day(s, start_iso, end_iso))
            stt = st.multiselect("Status", _distinct(runs, "status"), key="job_status")
            filtered = _apply_filter(runs, "status", stt)
            st.caption("Click 🔎 on a row to view that run's Claude output.")
            _runs_table(filtered)
            # A row's 🔎 sets ?vout=<run_id>; open the modal once, then consume the param so a
            # refresh or dialog-dismiss doesn't reopen it (tab/htab in the URL keep this section).
            if "vout" in st.query_params:
                raw = st.query_params.get("vout")
                del st.query_params["vout"]
                try:
                    _run_output_dialog(int(raw))
                except (TypeError, ValueError):
                    pass

        elif htab == "Decisions":
            decs = _sdb(lambda s: decisions_for_day(s, start_iso, end_iso))
            d1, d2 = st.columns(2)
            act_f = d1.multiselect("Action", _distinct(decs, "action"), key="dec_act")
            sym_f = d2.multiselect("Symbol", _distinct(decs, "symbol"), key="dec_sym")
            _md_table(_apply_filter(_apply_filter(decs, "action", act_f), "symbol", sym_f))

        elif htab == "Closed positions":
            closed = _sdb(lambda s: closed_positions_for_day(s, start_iso, end_iso))
            r1, r2 = st.columns(2)
            side_f = r1.multiselect("Side", _distinct(closed, "side"), key="pos_side")
            reason_f = r2.multiselect("Exit reason", _distinct(closed, "exit_reason"),
                                      key="pos_reason")
            _md_table(_apply_filter(_apply_filter(closed, "side", side_f),
                                    "exit_reason", reason_f))

    if st.button("Refresh"):
        st.rerun()


def _launch_swing_job(resume_run_id: int | None = None) -> None:
    """Fire the swing analysis as a detached subprocess so the UI never blocks. When
    `resume_run_id` is given, the job continues that stopped run instead of starting fresh."""
    import subprocess
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(here, "swing_job.py")]
    if resume_run_id is not None:
        cmd += ["--resume", str(resume_run_id)]
    subprocess.Popen(cmd, cwd=here, env=dict(os.environ), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _launch_swing_one(symbol: str, run_id: int | None = None) -> None:
    """Re-analyze a single holding in a detached subprocess so the UI never blocks. With
    `run_id`, update that stock's row in the existing run in place; without it, run the stock as
    its own fresh single-stock run."""
    import subprocess
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    cmd = [sys.executable, os.path.join(here, "swing_job.py"), "--symbol", symbol]
    if run_id is not None:
        cmd += ["--run", str(run_id)]
    subprocess.Popen(cmd, cwd=here, env=dict(os.environ), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _stop_swing_job(run_id: int) -> None:
    """Stop a running analysis: mark it STOPPED + reset the mid-flight stock in the DB, then
    best-effort signal the subprocess. The kill is guarded — the process may already be gone,
    and the DB is left consistent either way."""
    import signal
    pid = _db(lambda s: s.stop_swing_run(run_id))
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass


def _swing_verdict_cell(action: str, conviction, target, stop) -> str:
    conv = f" ({conviction})" if conviction is not None else ""
    lv = ""
    if target is not None or stop is not None:
        lv = f" · T {target if target is not None else '—'} / S {stop if stop is not None else '—'}"
    return f"{action or '—'}{conv}{lv}"


def _num(x) -> str:
    return "—" if x is None else str(x)


_SWING_STATUS_LABEL = {"PENDING": "· waiting", "ANALYZING": "⏳ analyzing",
                       "DONE": "✓ done", "ERROR": "⚠ error", "NEW": "· not analyzed yet"}

_SWING_VERDICT_FIELDS = ("swing_action", "swing_conviction", "swing_target", "swing_stop",
                         "swing_rationale", "ss_action", "ss_conviction", "ss_target", "ss_stop",
                         "ss_rationale")


def _swre_link(symbol: str, busy: bool) -> str:
    """A ↻ re-analyze control for one row. When not busy it's an internal query-param link
    (?swre=SYMBOL) handled at the top of _swing_page; when busy it's a dimmed, inert glyph."""
    import html
    import urllib.parse
    title = f"Re-analyze {symbol}"
    if busy:
        return f'<span class="ai-act ai-act--off" title="{html.escape(title)}">↻</span>'
    q = urllib.parse.quote(symbol)
    return (f'<a class="ai-act" href="?swre={q}" target="_self" '
            f'title="{html.escape(title)}">↻</a>')


def _swing_verdicts_table(verdicts: list[dict], running: bool) -> None:
    """Analysis results as a bordered table (self-built HTML — the PyArrow-safe path). Each row
    is a <details>: the summary shows Symbol/Status/Qty/Avg/Swing/Short-swing + a ↻ re-analyze
    link and stays visible; clicking the row expands the swing + short-swing rationale. The ↻
    is disabled (dimmed) while the run is RUNNING or that row is ANALYZING."""
    import html
    head = ('<div class="ai-swt-head">'
            '<span class="ai-caret"></span>'
            '<span class="c-sym">Symbol</span><span class="c-status">Status</span>'
            '<span class="c-qty">Qty</span><span class="c-avg">Avg</span>'
            '<span class="c-swing">Swing</span><span class="c-ss">Short-swing</span>'
            '<span class="c-when">Analyzed</span><span class="c-act"></span></div>')
    rows = []
    for v in verdicts:
        busy = running or v.get("status") == "ANALYZING"
        summary = (
            '<summary>'
            '<span class="ai-caret">▸</span>'
            f'<span class="c-sym">{html.escape(v["symbol"])}</span>'
            f'<span class="c-status">'
            f'{html.escape(_SWING_STATUS_LABEL.get(v.get("status"), v.get("status") or "—"))}'
            '</span>'
            f'<span class="c-qty">{_num(v["quantity"])}</span>'
            f'<span class="c-avg">{_num(v["avg_price"])}</span>'
            f'<span class="c-swing">{html.escape(_swing_verdict_cell(v["swing_action"], v["swing_conviction"], v["swing_target"], v["swing_stop"]))}</span>'
            f'<span class="c-ss">{html.escape(_swing_verdict_cell(v["ss_action"], v["ss_conviction"], v["ss_target"], v["ss_stop"]))}</span>'
            f'<span class="c-when">{_fmt_ist_short(v.get("analyzed_at")) or "—"}</span>'
            f'<span class="c-act">{_swre_link(v["symbol"], busy)}</span>'
            '</summary>')
        parts = []
        if v.get("swing_rationale"):
            parts.append(f'<b>Swing:</b> {html.escape(v["swing_rationale"])}')
        if v.get("ss_rationale"):
            parts.append(f'<b>Short-swing:</b> {html.escape(v["ss_rationale"])}')
        reason = "<br>".join(parts) or "No rationale recorded for this stock yet."
        rows.append(f'<details class="ai-swt-row">{summary}'
                    f'<div class="ai-swt-reason">{reason}</div></details>')
    st.markdown(f'<div class="ai-swt">{head}{"".join(rows)}</div>', unsafe_allow_html=True)


def _refresh_holdings_from_groww() -> None:
    """Fetch holdings from Groww (same creds as intraday, via settings/.env) and persist the
    snapshot so the page shows the last-loaded set without re-hitting Groww every open."""
    from settings import load_settings
    from groww_client import GrowwClient
    load_settings().apply_to_environ()
    client = GrowwClient(mode="live")
    client.authenticate()
    _db(lambda s: s.replace_holdings(client.get_holdings()))


@st.fragment(run_every=4)
def _swing_live() -> None:
    """Live status + progress + results — auto-refreshes every few seconds so a running analysis
    updates without a manual reload. Renders the latest run's per-stock table as it fills in."""
    latest = _db(lambda s: s.latest_swing_run())
    if latest is None:
        st.caption("No analysis run yet.")
        return
    running = latest["status"] == "RUNNING"
    verdicts = _db(lambda s: s.get_swing_verdicts(latest["id"]))
    # Union in holdings bought AFTER this run started — they're in the snapshot but not the run's
    # verdicts, so they'd otherwise never appear. Show them as "not analyzed yet" rows the user
    # can analyze in place with ↻.
    analyzed = {v["symbol"] for v in verdicts}
    extras = [dict({"symbol": h["symbol"], "quantity": h.get("quantity"),
                    "avg_price": h.get("avg_price"), "status": "NEW", "analyzed_at": None},
                   **{f: None for f in _SWING_VERDICT_FIELDS})
              for h in _db(lambda s: s.get_holdings()) if h["symbol"] not in analyzed]
    verdicts = verdicts + extras
    if running:
        prog = _db(lambda s: s.swing_progress(latest["id"]))
        done, total = prog["done"], prog["total"] or 1
        st.progress(done / total,
                    text=f"⏳ Analyzing — {done}/{prog['total']} done"
                         + (f" · {prog['errors']} errors" if prog["errors"] else ""))
    elif latest["status"] == "STOPPED":
        prog = _db(lambda s: s.swing_progress(latest["id"]))
        st.warning(f"⏸ Stopped — {prog['done']}/{prog['total']} done · "
                   f"{prog['pending']} remaining")
    elif latest["status"] == "FAILED":
        st.error(f"Last run FAILED: {latest.get('error') or 'unknown error'}")
    else:
        st.success(f"Last analysis: {_fmt_ist(latest['finished_at']) or latest['finished_at']}"
                   f" · {latest['num_holdings']} holdings")

    if verdicts:
        st.subheader("Analysis")
        # The search box lives in _swing_page (main flow); read it here so the fragment's own
        # auto-refresh keeps filtering to the current query.
        q = st.session_state.get("swing_search", "").strip().lower()
        vfilter = set(st.session_state.get("swing_verdict_filter", []) or [])
        shown = verdicts
        if q:
            shown = [v for v in shown if q in v["symbol"].lower()]
        if vfilter:                            # keep rows whose swing OR short-swing verdict matches
            shown = [v for v in shown
                     if v.get("swing_action") in vfilter or v.get("ss_action") in vfilter]
        new_count = sum(1 for v in verdicts if v.get("status") == "NEW")
        if q or vfilter:
            bits = []
            if q:
                bits.append(f"“{q}”")
            if vfilter:
                bits.append("verdict " + "/".join(sorted(vfilter)))
            st.caption(f"Showing {len(shown)} of {len(verdicts)} — filtered by {', '.join(bits)}.")
        elif new_count:
            st.caption(f"Click a row for the reasoning · ↻ analyzes in place · {new_count} newly "
                       "held stock(s) not analyzed yet — hit ↻ on those rows.")
        else:
            st.caption("Click a row to see the reasoning · ↻ re-analyzes that stock in place.")
        if shown:
            _swing_verdicts_table(shown, running)
        else:
            st.caption("No stock matches your filters.")

    # Compare vs an earlier successful run.
    if not running and latest["status"] == "SUCCESS":
        past = [r for r in _db(lambda s: s.get_swing_runs())
                if r["status"] == "SUCCESS" and r["id"] != latest["id"]]
        if past:
            st.subheader("Compare with a previous run")
            labels = {f"{_fmt_ist(r['finished_at']) or r['finished_at']} (#{r['id']})": r["id"]
                      for r in past}
            pick = st.selectbox("Earlier run", list(labels), key="swing_cmp")
            prev = {v["symbol"]: v for v in _db(lambda s: s.get_swing_verdicts(labels[pick]))}
            changes = [{"Symbol": v["symbol"],
                        "Swing": f"{prev[v['symbol']]['swing_action']} → {v['swing_action']}",
                        "Short-swing": f"{prev[v['symbol']]['ss_action']} → {v['ss_action']}"}
                       for v in verdicts if v["symbol"] in prev
                       and (v["swing_action"] != prev[v["symbol"]]["swing_action"]
                            or v["ss_action"] != prev[v["symbol"]]["ss_action"])]
            st.caption("Only holdings whose verdict changed:")
            _md_table(changes)


def _swing_page() -> None:
    st.markdown('<div class="ai-brand">Swing<em>.</em></div>', unsafe_allow_html=True)
    st.caption("Loads your Groww holdings, then runs a Claude swing analysis on each (both the "
               "days-to-a-month and the 3–5 day view), one stock at a time so you can watch the "
               "progress. Analysis only, no orders. Uses the same Groww credentials as intraday.")

    # Holdings — persisted; shown on open, refreshable from Groww.
    holdings = _db(lambda s: s.get_holdings())
    fetched_at = _db(lambda s: s.holdings_fetched_at())
    latest = _db(lambda s: s.latest_swing_run())
    running = bool(latest and latest["status"] == "RUNNING")
    stopped = bool(latest and latest["status"] == "STOPPED")

    # A ↻ row link sets ?swre=SYMBOL — re-analyze that stock in place in the latest run, then
    # clear the param so the action fires once (not on every fragment auto-refresh).
    if "swre" in st.query_params:
        sym = st.query_params["swre"]
        del st.query_params["swre"]
        if latest and not running:
            _launch_swing_one(sym, latest["id"])
        st.rerun()

    top = st.columns([1.3, 1.7, 3], vertical_alignment="center")
    with top[0]:
        if st.button("Refresh holdings", use_container_width=True, disabled=running):
            try:
                with st.spinner("Fetching from Groww…"):
                    _refresh_holdings_from_groww()
            except Exception as e:
                st.error(f"Could not load holdings: {e}")
            st.rerun()
    with top[1]:
        if running:
            if st.button("⏹ Stop", use_container_width=True, type="secondary"):
                _stop_swing_job(latest["id"])
                st.rerun()
        elif holdings:
            if st.button(f"Analyze {len(holdings)} holdings", use_container_width=True,
                         type="primary"):
                _launch_swing_job()
                st.rerun()
    with top[2]:
        if fetched_at:
            st.caption(f"Holdings as of {_fmt_ist(fetched_at) or fetched_at} · {len(holdings)} "
                       "stocks")
        else:
            st.caption("No holdings loaded yet — click **Refresh holdings**.")

    # After a Stop: offer to restart from scratch or resume where it left off.
    if stopped:
        remaining = _db(lambda s: s.swing_progress(latest["id"]))["pending"]
        ctl = st.columns([1.7, 1.7, 3], vertical_alignment="center")
        with ctl[0]:
            if holdings and st.button("↻ Restart from start", use_container_width=True):
                _launch_swing_job()
                st.rerun()
        with ctl[1]:
            if st.button(f"▶ Resume ({remaining} remaining)", use_container_width=True,
                         type="primary", disabled=remaining == 0):
                _launch_swing_job(resume_run_id=latest["id"])
                st.rerun()
        with ctl[2]:
            st.caption("Restart re-analyzes every holding in a fresh run · Resume keeps the "
                       "done ones and continues.")

    # Search + verdict filter — both read from session_state inside the fragment (and the search
    # also filters the pre-analysis holdings list below). Verdict options are swing_engine's fixed
    # vocabulary; a row matches if EITHER its swing or short-swing verdict is selected.
    if holdings:
        fcols = st.columns([2, 1.4], vertical_alignment="center")
        with fcols[0]:
            st.text_input("Search stock", key="swing_search", label_visibility="collapsed",
                          placeholder="🔍  Search a stock by symbol…")
        with fcols[1]:
            st.multiselect("Verdict", ["HOLD", "ADD", "REDUCE", "EXIT"],
                           key="swing_verdict_filter", label_visibility="collapsed",
                           placeholder="Filter by verdict…")
    query = st.session_state.get("swing_search", "").strip().lower()

    if holdings and not (latest and _db(lambda s: s.get_swing_verdicts(latest["id"]))):
        # show the raw holdings until there's an analysis table to show instead
        shown = [h for h in holdings if query in h["symbol"].lower()] if query else holdings
        if query:
            st.caption(f"Showing {len(shown)} of {len(holdings)} — filtered by “{query}”.")
        _md_table([{"Symbol": h["symbol"], "Qty": h["quantity"], "Avg price": h["avg_price"]}
                   for h in shown])

    _swing_live()


@st.cache_resource
def _strategy_registry():
    from settings import load_settings
    from strategies import StrategyRegistry
    return StrategyRegistry.from_config(load_settings().strategies)


_SERIES_COLORS = ["var(--ai-accent)", "#e79008", "#30a46c", "#e5484d"]


def _fmt_money(v, pct=False) -> str:
    if v is None:
        return "—"
    if v == float("inf"):
        return "∞"
    if pct:
        return f"{v:.1f}%"
    return f"₹{v:,.0f}"


def _svg_equity_chart(series: dict, names: dict) -> None:
    """Multi-series cumulative-P&L curve as self-built inline SVG (the app avoids PyArrow, so no
    st.line_chart). One polyline per strategy over its closed-trade equity, a zero baseline, and
    a legend showing each strategy's final P&L. Colours come from the CSS accent vars."""
    import html as _html
    active = {sid: pts for sid, pts in series.items() if pts}
    if not active:
        st.caption("— no closed trades yet to chart")
        return
    W, H, P = 640, 200, 26
    n = max(len(p) for p in active.values())
    allv = [v for pts in active.values() for v in pts] + [0.0]
    lo, hi = min(allv), max(allv)
    if hi == lo:
        hi = lo + 1.0

    def sx(i):
        return P + (W - 2 * P) * (i / max(n - 1, 1))

    def sy(v):
        return P + (H - 2 * P) * (1 - (v - lo) / (hi - lo))

    zero_y = sy(0.0)
    parts = [f'<svg viewBox="0 0 {W} {H}" class="ai-eqchart" preserveAspectRatio="none">',
             f'<line x1="{P}" y1="{zero_y:.1f}" x2="{W - P}" y2="{zero_y:.1f}" class="ai-eq-zero"/>']
    legend = []
    for k, (sid, pts) in enumerate(active.items()):
        color = _SERIES_COLORS[k % len(_SERIES_COLORS)]
        pts_attr = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(pts))
        parts.append(f'<polyline points="{pts_attr}" fill="none" stroke="{color}" '
                     f'stroke-width="2" vector-effect="non-scaling-stroke"/>')
        legend.append(f'<span class="ai-eq-key"><span class="ai-eq-dot" '
                      f'style="background:{color}"></span>'
                      f'{_html.escape(names.get(sid, sid))} ({pts[-1]:+,.0f})</span>')
    parts.append("</svg>")
    st.markdown(f'<div class="ai-eqwrap">{"".join(parts)}'
                f'<div class="ai-eq-legend">{"".join(legend)}</div></div>',
                unsafe_allow_html=True)


def _svg_daily_bars(rows: list, strat_ids: list, names: dict) -> None:
    """Grouped daily-P&L bars (self-built SVG). One bar per strategy per IST day; a zero baseline."""
    import html as _html
    if not rows:
        st.caption("— no daily P&L yet")
        return
    W, H, P = 640, 200, 26
    vals = [r[sid] for r in rows for sid in strat_ids] + [0.0]
    lo, hi = min(vals), max(vals)
    if hi == lo:
        hi = lo + 1.0

    def sy(v):
        return P + (H - 2 * P) * (1 - (v - lo) / (hi - lo))

    zero_y = sy(0.0)
    gw = (W - 2 * P) / max(len(rows), 1)
    bw = gw * 0.72 / max(len(strat_ids), 1)
    parts = [f'<svg viewBox="0 0 {W} {H}" class="ai-eqchart" preserveAspectRatio="none">',
             f'<line x1="{P}" y1="{zero_y:.1f}" x2="{W - P}" y2="{zero_y:.1f}" class="ai-eq-zero"/>']
    for gi, r in enumerate(rows):
        gx = P + gw * gi + gw * 0.14
        for si, sid in enumerate(strat_ids):
            v = r[sid]
            color = _SERIES_COLORS[si % len(_SERIES_COLORS)]
            x, top = gx + si * bw, min(sy(v), zero_y)
            parts.append(f'<rect x="{x:.1f}" y="{top:.1f}" width="{bw * 0.88:.1f}" '
                         f'height="{abs(sy(v) - zero_y):.1f}" fill="{color}" opacity="0.85"/>')
    parts.append("</svg>")
    legend = "".join(f'<span class="ai-eq-key"><span class="ai-eq-dot" '
                     f'style="background:{_SERIES_COLORS[i % len(_SERIES_COLORS)]}"></span>'
                     f'{_html.escape(names.get(sid, sid))}</span>'
                     for i, sid in enumerate(strat_ids))
    st.markdown(f'<div class="ai-eqwrap">{"".join(parts)}'
                f'<div class="ai-eq-legend">{legend}</div></div>', unsafe_allow_html=True)


def _drawdown_from_equity(equity: list) -> list:
    peak, out = 0.0, []
    for e in equity:
        peak = max(peak, e)
        out.append(round(e - peak, 2))
    return out


def _compare_page() -> None:
    import compare_data as cd
    from orchestrator import LEVERAGE
    st.markdown('<div class="ai-brand">Compare<em>.</em></div>', unsafe_allow_html=True)
    st.caption("Two strategies, identical market data, isolated paper ledgers. Paper-only — no "
               "broker orders are ever placed in Compare Testing.")

    from strategies import compare_ledger_id
    cfg = _db(lambda s: s.get_config())
    reg = _strategy_registry()
    base_ids = [i for i in cfg.compare_strategies if i in reg] or reg.ids()
    # Compare reads the ISOLATED compare ledgers ("cmp:<id>") so P&L is a clean-slate head-to-head,
    # not contaminated by each strategy's live/paper history. Display uses the readable base name.
    strat_ids = [compare_ledger_id(i) for i in base_ids]
    names = {compare_ledger_id(x.id): x.name for x in reg.all()}
    today_iso = datetime.now(timezone.utc).date().isoformat()
    perfs = _db(lambda s: cd.all_performance(s, strat_ids, cfg.total_pool, today_iso))

    # Leaderboard — best strategy per headline metric.
    lb = cd.leaderboard(perfs)
    _tiles([_tile(metric, names.get(sid, sid) if sid else "—", tone="accent")
            for metric, sid in lb.items()])

    st.subheader("Performance summary")
    _md_table([{
        "Strategy": names.get(p["strategy_id"], p["strategy_id"]),
        "Net P&L": _fmt_money(p["net_profit"]),
        "Today": _fmt_money(p["today_profit"]),
        "Trades": p["total_trades"],
        "Win %": _fmt_money(p["win_pct"], pct=True),
        "Avg win": _fmt_money(p["avg_profit"]),
        "Avg loss": _fmt_money(p["avg_loss"]),
        "Profit factor": ("∞" if p["profit_factor"] == float("inf") else f'{p["profit_factor"]:.2f}'),
        "R:R": f'{p["risk_reward"]:.2f}',
        "ROI": _fmt_money(p["roi_pct"], pct=True),
        "Max DD": _fmt_money(p["max_drawdown"]),
        "Streak W/L": f'{p["consecutive_wins"]}/{p["consecutive_losses"]}',
    } for p in perfs])

    ce1, ce2 = st.columns(2)
    with ce1:
        st.subheader("Equity curve")
        _svg_equity_chart({p["strategy_id"]: p["equity"] for p in perfs}, names)
    with ce2:
        st.subheader("Drawdown")
        _svg_equity_chart({p["strategy_id"]: _drawdown_from_equity(p["equity"]) for p in perfs},
                          names)

    st.subheader("Daily P&L")
    _svg_daily_bars(_db(lambda s: cd.daily_pnl(s, strat_ids)), strat_ids, names)

    st.subheader("Decision comparison")
    st.caption("What each strategy decided for the same name at the same time.")
    dc = _db(lambda s: cd.decision_comparison(s, strat_ids))
    if dc:
        _md_table([dict({"Time": _fmt_ist_short(r["time"]) or r["time"], "Symbol": r["symbol"]},
                        **{names.get(sid, sid): r[sid] for sid in strat_ids}) for r in dc[:40]])
    else:
        st.caption("— no decisions recorded yet")

    st.subheader("Trade comparison")

    def _trade_cell(p):
        if p is None or p.exit_price is None:
            return "—"
        return f"{p.entry_price:g}→{p.exit_price:g} ({(p.realized_pnl or 0):+,.0f})"

    tc = _db(lambda s: cd.trade_comparison(s, strat_ids))
    if tc:
        _md_table([dict({"Time": _fmt_ist_short(r["time"]) or r["time"], "Symbol": r["symbol"]},
                        **{names.get(sid, sid): _trade_cell(r["positions"][sid])
                           for sid in strat_ids}) for r in tc[:40]])
    else:
        st.caption("— no closed trades yet")

    st.subheader("Portfolio")
    ports = [_db(lambda s, sid=sid: cd.portfolio_comparison(s, sid, LEVERAGE)) for sid in strat_ids]
    _md_table([{
        "Strategy": names.get(pt["strategy_id"], pt["strategy_id"]),
        "Open positions": pt["open_positions"],
        "Deployed notional": _fmt_money(pt["deployed_notional"]),
        "Used margin": _fmt_money(pt["used_margin"]),
        "Unrealized": "—",           # no live price feed in the dashboard
        "Realized": _fmt_money(pt["realized_pnl"]),
    } for pt in ports])

    st.subheader("Recent compare cycles")
    st.caption("Each compare cycle runs every strategy on the same data — one row per strategy per "
               "cycle. A RUNNING row still in progress means the cycle hasn't finished.")
    runs = []
    for ledger in strat_ids:
        for r in _db(lambda s, l=ledger: s.get_recent_runs(15, strategy_id=l)):
            runs.append({"time": r.started_at, "Strategy": names.get(ledger, ledger),
                         "Status": r.status, "Candidates": r.num_candidates,
                         "Summary": r.summary or ("…running" if r.status == "RUNNING" else "")})
    runs.sort(key=lambda x: x["time"], reverse=True)
    if runs:
        _md_table([dict({"Time": _fmt_ist_short(r["time"]) or r["time"]},
                        **{k: v for k, v in r.items() if k != "time"}) for r in runs[:24]])
    else:
        st.caption("— no compare cycles yet")


def _live_page() -> None:
    """Live Intraday — the deterministic (no-LLM) single-stock trader.

    This page NEVER runs the strategy: the trader is a separate long-lived daemon, and Streamlit
    re-runs this script on every interaction. The two share only the database — the page writes
    control flags, the loop re-reads them each tick. So DISARM works even with this page closed.
    """
    from live_store import LiveStore

    st.markdown('<div class="ai-brand">Live Intraday<em>.</em></div>', unsafe_allow_html=True)
    st.caption("Rule-based, no AI. One stock, one position, long only. The trader runs as its own "
               "daemon — this page only reads its state and sets its flags.")
    try:
        ls = LiveStore(DB_PATH)
    except Exception as e:
        st.error(f"live store unavailable: {e}")
        return
    cfg, state = ls.get_config(), ls.get_state()
    today = datetime.now(IST).date().isoformat()
    trades = ls.trades_for(today)
    realized = ls.realized_pnl(today)
    open_trade = ls.get_open_trade()

    # --- is the daemon actually alive? A stale heartbeat means nothing is trading. -------------
    hb, hb_txt, hb_tone = state.get("heartbeat_at"), "never", "neg"
    if hb:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(hb)).total_seconds()
            hb_txt = f"{int(age)}s ago"
            hb_tone = "pos" if age < 30 else "neg"
        except Exception:
            hb_txt = "unparseable"
    armed = bool(cfg["armed"])
    _tiles([
        _tile("Trader", "ARMED" if armed else "DISARMED", cfg["mode"].upper(),
              "pos" if armed else "plain"),
        _tile("Heartbeat", hb_txt, "daemon last tick", hb_tone),
        _tile("Symbol", state.get("symbol") or "—", state.get("selected_reason") or ""),
        _tile("Realized today", f"₹{realized:,.0f}", f"{len(trades)} trade(s)",
              _pnl_tone(realized)),
    ])
    if not armed and state.get("disarmed_reason"):
        st.warning(f"Disarmed: {state['disarmed_reason']}")
    if hb_tone == "neg":
        st.info("No recent heartbeat — the livetrader daemon does not appear to be running. "
                "Arming here has no effect until it is started.")

    st.markdown(f"**Signal:** `{state.get('signal_action') or '—'}` — "
                f"{state.get('signal_reason') or 'no read yet'}")

    if open_trade:
        st.markdown("**Open position**")
        st.dataframe([{"symbol": open_trade.symbol, "qty": open_trade.quantity,
                       "entry": open_trade.entry_price, "stop": open_trade.stop,
                       "target": open_trade.target, "mode": open_trade.mode}],
                     use_container_width=True, hide_index=True)
    else:
        st.caption("No open position.")

    c1, c2 = st.columns(2)
    if c1.button("🔴 DISARM" if armed else "🟢 ARM", use_container_width=True):
        if armed:
            ls.disarm("disarmed from the dashboard")
        else:
            ls.set_config(armed=1)
        st.rerun()
    if c2.button("Drop symbol & re-select", use_container_width=True,
                 disabled=open_trade is not None,
                 help="Blocked while a position is open."):
        ls.update_state(symbol=None, selected_reason="manual re-select requested")
        st.rerun()

    with st.expander("Settings"):
        live_mode = st.toggle("LIVE mode (places REAL Groww orders)", value=cfg["mode"] == "live")
        cap = st.number_input("Capital per trade (₹)", min_value=1000.0,
                              value=float(cfg["capital_per_trade"]), step=1000.0)
        rr = st.number_input("Minimum R:R", min_value=1.0, value=float(cfg["min_rr"]), step=0.1)
        atr_m = st.number_input("ATR multiple (stop)", min_value=0.5,
                                value=float(cfg["atr_mult"]), step=0.1)
        cap_loss = st.number_input("Daily loss cap (₹)", min_value=0.0,
                                   value=float(cfg["daily_loss_cap"]), step=500.0)
        if st.button("Save settings", use_container_width=True):
            ls.set_config(mode="live" if live_mode else "paper", capital_per_trade=cap,
                          min_rr=rr, atr_mult=atr_m, daily_loss_cap=cap_loss)
            st.success("Saved.")
            st.rerun()
        st.caption(f"Stop floor {cfg['min_stop_pct']}% of price · select at {cfg['select_at']} · "
                   f"no new entry after {cfg['no_new_entry_after']} · "
                   f"square-off {cfg['squareoff_at']}")

    if trades:
        st.markdown("**Today's trades**")
        st.dataframe([{"symbol": t.symbol, "qty": t.quantity, "entry": t.entry_price,
                       "exit": t.exit_price, "reason": t.exit_reason,
                       "pnl": t.pnl, "mode": t.mode} for t in trades],
                     use_container_width=True, hide_index=True)


def _active_short_page() -> None:
    """activeShort — tonight's fall candidates and tomorrow's armed shorts.

    Read-only over the store, like every other page: the scan and arm jobs run on their own
    schedule and this only reflects what they recorded.
    """
    from active_short_store import ActiveShortStore

    st.markdown('<div class="ai-brand">Active Short<em>.</em></div>', unsafe_allow_html=True)
    st.caption("Scans after the close for stocks likely to fall next session, then arms "
               "conditional short entries below each confirmation level at the open. Shorts are "
               "intraday-only in India — everything squares off the same day.")
    try:
        ss = ActiveShortStore(DB_PATH)
    except Exception as e:
        st.error(f"activeShort store unavailable: {e}")
        return
    cfg = ss.get_config()
    live_ok, live_why = ss.live_allowed()
    done = ss.completed_paper_sessions()
    need = int(cfg["paper_sessions_required"])
    enabled = bool(cfg["active_short_enabled"])

    _tiles([
        _tile("Mode", "ENABLED" if enabled else "DISABLED", cfg["active_short_mode"].upper(),
              "pos" if enabled else "plain"),
        _tile("Paper gate", f"{done}/{need}", "sessions recorded",
              "pos" if done >= need else "plain"),
        _tile("Max shorts", str(cfg["max_shorts"]), f"₹{cfg['capital_per_short']:,.0f} each"),
        _tile("Arm at", cfg["arm_at"], f"expire {cfg['arm_expiry']} · flat {cfg['squareoff_at']}"),
    ])
    if not live_ok:
        st.info(f"Live trading refused — {live_why}. Next-session direction is close to a coin "
                f"flip; the paper period exists to measure whether this signal has an edge "
                f"before real money is committed.")

    today = datetime.now(IST).date().isoformat()
    picks = ss.picks_for(today)
    st.markdown(f"**Picks for {today}**")
    if picks:
        st.dataframe([{"rank": p.rank, "symbol": p.symbol, "confidence": p.confidence,
                       "short below": p.confirmation_level, "stop": p.stop, "target": p.target,
                       "rvol": p.rvol, "status": p.status, "fill": p.fill_price,
                       "pnl": p.pnl, "why": p.reason} for p in picks],
                     use_container_width=True, hide_index=True)
    else:
        st.caption("No picks recorded for today. An empty night is a valid result — the scanner "
                   "returns nothing when the regime is bullish or nothing clears its bar.")

    sessions = ss.sessions(limit=20)
    if sessions:
        st.markdown("**Session history** — the hit rate, not a claim about it")
        st.dataframe([{"date": s["trade_date"], "mode": s["mode"], "picks": s["picks"],
                       "triggered": s["triggered"], "pnl": s["realized_pnl"],
                       "complete": bool(s["completed_at"])} for s in sessions],
                     use_container_width=True, hide_index=True)

    with st.expander("Settings"):
        on = st.toggle("Enable activeShort", value=enabled)
        want_live = st.toggle("LIVE mode (places REAL short orders)",
                              value=cfg["active_short_mode"] == "live",
                              disabled=done < need,
                              help=None if done >= need else
                              f"Locked until {need} paper sessions are recorded ({done} so far).")
        n = st.number_input("Max shorts per session", 1, 10, int(cfg["max_shorts"]))
        cap = st.number_input("Capital per short (₹)", 1000.0,
                              value=float(cfg["capital_per_short"]), step=1000.0)
        conf = st.number_input("Minimum confidence", 50.0, 100.0,
                               float(cfg["min_confidence"]), step=1.0)
        if st.button("Save activeShort settings", use_container_width=True):
            ss.set_config(active_short_enabled=1 if on else 0,
                          active_short_mode="live" if (want_live and done >= need) else "paper",
                          max_shorts=int(n), capital_per_short=cap, min_confidence=conf)
            st.success("Saved.")
            st.rerun()
        st.caption(f"Stop {cfg['stop_pct']}% above fill · target {cfg['target_pct']}% below · "
                   f"skip gaps beyond {cfg['max_gap_pct']}% · RVOL floor {cfg['min_rvol']}")


def main() -> None:
    st.set_page_config(page_title="autoIntraday", layout="wide",
                       initial_sidebar_state="collapsed")
    # Injected ONCE here rather than per page. It used to live inside _render and _swing_page, so
    # every new page silently rendered its tiles as unstyled raw divs (Live Intraday and Active
    # Short both shipped that way). A <style> block emits nothing visible, so hoisting it above
    # navigation is safe and makes the styling impossible to forget.
    st.markdown(_CSS, unsafe_allow_html=True)
    intraday = st.Page(_render, title="Intraday", url_path="intraday", default=True)
    swing = st.Page(_swing_page, title="Swing", url_path="swing")
    live = st.Page(_live_page, title="Live Intraday", url_path="live-intraday")
    ashort = st.Page(_active_short_page, title="Active Short", url_path="active-short")
    pages = [intraday, swing, live, ashort]
    # The Compare tab appears only when Compare Testing is on, so the app looks exactly like today
    # when it's off (enable it from Settings ▸ Strategies).
    try:
        if _db(lambda s: s.get_config().compare_enabled):
            pages.append(st.Page(_compare_page, title="Compare", url_path="compare"))
    except Exception:
        pass
    st.navigation(pages, position="top").run()


if __name__ == "__main__":
    main()
