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
from schedule_manager import (ScheduleError, apply_schedule, next_fire, next_primer_fire,
                              primer_time, read_schedule)
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
    t_capital, t_schedule, t_data = st.tabs(["Capital", "Schedule", "Data"])

    with t_capital:
        st.caption("Pause and Paper/Live are on the header. These are the sizing rules.")
        cc1, cc2, cc3 = st.columns(3)
        total_pool = cc1.number_input("Pool (₹)", min_value=0.0, value=float(h["total_pool"]),
                                      step=1000.0)
        max_pos = cc2.number_input("Max positions", min_value=0,
                                   value=int(h["max_open_positions"]), step=1)
        cap_pos = cc3.number_input("Per position (₹)", min_value=0.0,
                                   value=float(h["capital_per_position"]), step=1000.0)
        if st.button("Save capital rules", use_container_width=True):
            _db(lambda s: s.update_config(total_pool=total_pool, max_open_positions=int(max_pos),
                                          capital_per_position=cap_pos))
            st.success("Saved.")
            st.rerun()

    with t_schedule:
        try:
            sched = read_schedule()
        except ScheduleError as e:
            st.error(str(e))
        else:
            from datetime import time as dtime
            s1, s2, s3 = st.columns(3)
            first = s1.time_input("First cycle", value=dtime(*sched["start"]), step=300)
            last = s2.time_input("Last cycle", value=dtime(*sched["last"]), step=300)
            interval = s3.number_input("Every (min)", min_value=5, max_value=120,
                                       value=int(sched["interval_min"]) or 20, step=5)
            st.caption("Square-off stays fixed at 15:18. Applying reloads the scheduler — "
                       "refused while a cycle is running.")
            if st.button("Apply schedule", use_container_width=True):
                try:
                    msg = apply_schedule((first.hour, first.minute), (last.hour, last.minute),
                                         int(interval))
                except ScheduleError as e:
                    st.error(str(e))
                else:
                    st.success(msg)
                    st.rerun()

            ph, pm = primer_time((first.hour, first.minute))
            primer_on = _db(lambda s: s.get_config().primer_enabled)
            new_primer = st.toggle(f"Claude primer — prime the window at {ph:02d}:{pm:02d} IST",
                                   value=primer_on,
                                   help="A throwaway Claude call 2h before the first cycle starts "
                                        "the 5-hour usage window early, so it resets during "
                                        "trading, not after.")
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


def _render() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)

    h = _db(header_view)
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
        live = st.toggle("Live mode", value=(h["mode"] == "live"),
                         help="ON = REAL orders on Groww. OFF = paper (simulated). "
                              "The next cycle acts on the new mode.")
        if live != (h["mode"] == "live"):
            _db(lambda s: s.update_config(mode="live" if live else "paper"))
            st.rerun()
    with cC:
        if st.button("⚙ Settings", use_container_width=True):
            _settings_dialog()

    today_iso = datetime.now(timezone.utc).date().isoformat()
    pnl = _db(lambda s: pnl_summary(s, today_iso))
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
        _tile("Pool used", f"₹{h['deployed_capital']:,.0f}",
              f"{h['utilization_pct']}% of ₹{h['total_pool']:,.0f}"),
        _tile("Open positions", f"{h['open_count']} / {h['max_open_positions']}"),
        _tile("Resting orders", str(h["pending_count"])),
        next_tile,
        primer_tile,
        _tile("P&L today", f"₹{pnl['realized_today']:,.2f}",
              tone=_pnl_tone(pnl["realized_today"])),
        _tile("P&L total", f"₹{pnl['realized_total']:,.2f}",
              tone=_pnl_tone(pnl["realized_total"])),
    ])

    tab_overview, tab_perf, tab_history = st.tabs(["Overview", "Performance", "History"])

    with tab_overview:
        st.subheader("Pending / resting orders")
        st.caption("Placed but not yet filled — each fills when price reaches `rest_at`, then "
                   "arms its target/stop. Cancelled at square-off if never reached.")
        _md_table(_db(pending_view))

        st.subheader("Open positions")
        _md_table([r for r in _db(positions_view) if r["status"] == "OPEN"])

    with tab_perf:
        st.caption("How the bot's finished trades have actually done. A trade counts here only "
                   "once it's closed.")
        today_bounds = _ist_day_bounds_utc(datetime.now(IST).date())
        perf_today, perf_all = st.tabs(["Today", "All-time"])
        with perf_today:
            _render_performance(*today_bounds)
        with perf_all:
            _render_performance(None, None)

    with tab_history:
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
        day_pnl = _db(lambda s: realized_for_day(s, start_iso, end_iso))
        with head_l:
            st.markdown(f"#### History — {day_label}")
            st.caption(f"Realized P&L: ₹{day_pnl:,.2f}")

        op_tab, job_tab, dec_tab, pos_tab = st.tabs(
            ["Operations", "Job runs", "Decisions", "Closed positions"])

        with op_tab:
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
            log = _db(lambda s: activity_log(s, start_iso, end_iso))
            f1, f2 = st.columns(2)
            ev = f1.multiselect("Event", _distinct(log, "event"), key="op_ev")
            sym = f2.multiselect("Symbol", _distinct(log, "symbol"), key="op_sym")
            _md_table(_apply_filter(_apply_filter(log, "event", ev), "symbol", sym))

        with job_tab:
            runs = _db(lambda s: runs_for_day(s, start_iso, end_iso))
            stt = st.multiselect("Status", _distinct(runs, "status"), key="job_status")
            _md_table(_apply_filter(runs, "status", stt))

        with dec_tab:
            decs = _db(lambda s: decisions_for_day(s, start_iso, end_iso))
            d1, d2 = st.columns(2)
            act_f = d1.multiselect("Action", _distinct(decs, "action"), key="dec_act")
            sym_f = d2.multiselect("Symbol", _distinct(decs, "symbol"), key="dec_sym")
            _md_table(_apply_filter(_apply_filter(decs, "action", act_f), "symbol", sym_f))

        with pos_tab:
            closed = _db(lambda s: closed_positions_for_day(s, start_iso, end_iso))
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
        shown = [v for v in verdicts if q in v["symbol"].lower()] if q else verdicts
        new_count = sum(1 for v in verdicts if v.get("status") == "NEW")
        if q:
            st.caption(f"Showing {len(shown)} of {len(verdicts)} — filtered by “{q}”.")
        elif new_count:
            st.caption(f"Click a row for the reasoning · ↻ analyzes in place · {new_count} newly "
                       "held stock(s) not analyzed yet — hit ↻ on those rows.")
        else:
            st.caption("Click a row to see the reasoning · ↻ re-analyzes that stock in place.")
        if shown:
            _swing_verdicts_table(shown, running)
        else:
            st.caption("No stock matches your search.")

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
    st.markdown(_CSS, unsafe_allow_html=True)
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

    # Search — filters both the analysis table (read from session_state inside the fragment) and
    # the pre-analysis holdings list below.
    if holdings:
        st.text_input("Search stock", key="swing_search", label_visibility="collapsed",
                      placeholder="🔍  Search a stock by symbol…")
    query = st.session_state.get("swing_search", "").strip().lower()

    if holdings and not (latest and _db(lambda s: s.get_swing_verdicts(latest["id"]))):
        # show the raw holdings until there's an analysis table to show instead
        shown = [h for h in holdings if query in h["symbol"].lower()] if query else holdings
        if query:
            st.caption(f"Showing {len(shown)} of {len(holdings)} — filtered by “{query}”.")
        _md_table([{"Symbol": h["symbol"], "Qty": h["quantity"], "Avg price": h["avg_price"]}
                   for h in shown])

    _swing_live()


def main() -> None:
    st.set_page_config(page_title="autoIntraday", layout="wide",
                       initial_sidebar_state="collapsed")
    intraday = st.Page(_render, title="Intraday", url_path="intraday", default=True)
    swing = st.Page(_swing_page, title="Swing", url_path="swing")
    st.navigation([intraday, swing], position="top").run()


if __name__ == "__main__":
    main()
