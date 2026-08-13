# Manual Intraday Results Master–Detail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Results tab's stack of full cards with a compact selectable table plus one detail panel, cutting a five-result run from 50 interactive widgets to about 11.

**Architecture:** Two pure helpers build the table rows and resolve the selection by symbol; `_mi_results_tab` becomes table → detail. Nothing outside the Results tab changes.

**Tech Stack:** Python 3, Streamlit (`st.dataframe(on_select=...)`, `st.tabs`), pytest, AppTest. Spec: `docs/superpowers/specs/2026-08-13-manual-intraday-results-master-detail-design.md`.

## Global Constraints

- Run tests with: `cd /Users/mohdsuhel/ai-mini-projects/autoIntraday && .venv/bin/python -m pytest tests/<file> -q`.
- Order placement, OCO arming, the analysis engine and the other three tabs are NOT touched. `_order_ticket_body` keeps its signature and behaviour.
- Row order is the run's own order. Do NOT sort by conviction — a top-5 run returns picks best-first and that ranking is information.
- Unknown values are `None`, never `0`.
- Selection is tracked by SYMBOL, never by row index: the tab auto-refreshes every 4s and rows appear as a run progresses, so an index would drift onto a different stock.
- Smoke tests use a TEMP database via `AUTOINTRADAY_DB`, never the live one.
- Do NOT `git add`: `groww_client.py`, `swing_job.py`, `observe_job.py`, `tests/test_groww_client.py`, `tests/test_observe.py`, `tests/test_swing_economics.py`, `tests/test_swing_job.py`.

---

### Task 1: The two pure helpers

**Files:**
- Modify: `dashboard.py` — add beside `_orders_for_display`
- Test: `tests/test_dashboard_manual_intraday.py`

**Interfaces:**
- `_results_table_rows(results) -> list[dict]` with keys `Symbol, Verdict, Conv, Entry, Stop, T1, R:R, Status`.
- `_pick_result(results, symbol) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard_manual_intraday.py`:

```python
def _res(symbol="KEI", status="DONE", verdict="BUY NOW", **kw):
    base = {"symbol": symbol, "status": status, "verdict": verdict, "conviction": 73,
            "entry": 103.0, "stop": 98.0, "target1": 123.0, "risk_reward": 2.0,
            "summary": "s", "report": "r", "raw_json": "{}", "error": None, "id": 1}
    base.update(kw)
    return base


def test_table_rows_carry_the_comparable_numbers():
    rows = dashboard._results_table_rows([_res()])
    assert rows[0]["Symbol"] == "KEI" and rows[0]["Verdict"] == "BUY NOW"
    assert rows[0]["Conv"] == 73 and rows[0]["Entry"] == 103.0
    assert rows[0]["Stop"] == 98.0 and rows[0]["T1"] == 123.0 and rows[0]["R:R"] == 2.0


def test_table_rows_preserve_the_runs_own_order():
    """A top-5 run returns its picks best-first — re-sorting would discard that ranking."""
    results = [_res("CELLO", conviction=70), _res("OMNI", conviction=90),
               _res("BSE", conviction=60)]
    assert [r["Symbol"] for r in dashboard._results_table_rows(results)] == \
        ["CELLO", "OMNI", "BSE"]


def test_table_rows_leave_unknowns_blank_not_zero():
    rows = dashboard._results_table_rows([_res(entry=None, stop=None, target1=None,
                                               risk_reward=None, conviction=None)])
    for col in ("Entry", "Stop", "T1", "R:R", "Conv"):
        assert rows[0][col] is None


def test_table_rows_label_work_in_progress_by_status():
    rows = dashboard._results_table_rows([
        _res("A", status="ANALYZING", verdict=None),
        _res("B", status="PENDING", verdict=None),
        _res("C", status="ERROR", verdict=None)])
    assert "analyz" in rows[0]["Verdict"].lower()
    assert "wait" in rows[1]["Verdict"].lower()
    assert "error" in rows[2]["Verdict"].lower()


def test_table_rows_report_status_separately():
    rows = dashboard._results_table_rows([_res(), _res("B", status="ERROR", verdict=None)])
    assert rows[0]["Status"] == "DONE" and rows[1]["Status"] == "ERROR"


def test_pick_result_finds_the_named_symbol():
    results = [_res("CELLO"), _res("OMNI"), _res("BSE")]
    assert dashboard._pick_result(results, "OMNI")["symbol"] == "OMNI"


def test_pick_result_falls_back_to_the_first_with_a_verdict():
    """A stale selection must not blank the panel — and must not pick a row still analysing."""
    results = [_res("A", status="ANALYZING", verdict=None), _res("B"), _res("C")]
    assert dashboard._pick_result(results, "GONE")["symbol"] == "B"
    assert dashboard._pick_result(results, None)["symbol"] == "B"


def test_pick_result_falls_back_to_the_first_row_when_none_have_verdicts():
    results = [_res("A", status="ANALYZING", verdict=None)]
    assert dashboard._pick_result(results, None)["symbol"] == "A"


def test_pick_result_on_an_empty_run_is_none():
    assert dashboard._pick_result([], "KEI") is None
    assert dashboard._pick_result(None, None) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q -k "table_rows or pick_result"`
Expected: FAIL — `AttributeError: module 'dashboard' has no attribute '_results_table_rows'`

- [ ] **Step 3: Implement**

Add to `dashboard.py` after `_orders_for_display`:

```python
_RESULT_WIP_LABEL = {"ANALYZING": "⏳ analyzing", "PENDING": "· waiting",
                     "ERROR": "⚠ error"}


def _results_table_rows(results) -> list[dict]:
    """One comparable row per result, in the RUN'S OWN ORDER — a top-5 run returns its picks
    best-first and re-sorting here would silently throw that ranking away. Unknown numbers stay
    None so the table shows a blank rather than a misleading zero."""
    rows = []
    for r in (results or []):
        rows.append({
            "Symbol": r.get("symbol"),
            "Verdict": r.get("verdict") or _RESULT_WIP_LABEL.get(r.get("status"), "—"),
            "Conv": r.get("conviction"),
            "Entry": r.get("entry"), "Stop": r.get("stop"), "T1": r.get("target1"),
            "R:R": r.get("risk_reward"),
            "Status": r.get("status"),
        })
    return rows


def _pick_result(results, symbol):
    """The result to show in the detail panel. Selection travels as a SYMBOL because the table
    refreshes while a run fills in — a row index would drift onto a different stock. A stale or
    missing selection falls back to the first row that actually has a verdict."""
    rows = list(results or [])
    if not rows:
        return None
    if symbol:
        for r in rows:
            if r.get("symbol") == symbol:
                return r
    return next((r for r in rows if r.get("verdict")), rows[0])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard.py tests/test_dashboard_manual_intraday.py
git commit -m "feat(manual-intraday): result table rows and selection helpers"
```

---

### Task 2: Rewrite the Results tab as table plus detail

**Files:**
- Modify: `dashboard.py` — `_mi_results_tab`
- Test: `tests/test_dashboard_manual_intraday.py` (AppTest)

- [ ] **Step 1: Implement**

Replace the whole body of `_mi_results_tab` with:

```python
@st.fragment(run_every=4)
def _mi_results_tab() -> None:
    """A compact table of every result, then ONE detail panel for the selected row.

    Every result used to render as a full card with its trade ticket already open, which put 50
    interactive widgets on screen for a five-pick run and made the stocks impossible to compare.
    Now the numbers sit side by side and only the row you pick builds a ticket."""
    import json as _json
    import pandas as pd
    latest = _db(lambda s: s.latest_analysis_run())
    if latest is None:
        st.caption("No analysis run yet — pick a skill and some stocks under **Positions**.")
        return
    results = _db(lambda s: s.get_analysis_results(latest["id"]))
    st.caption(f"Run #{latest['id']} · {latest['skill_id']} · "
               f"{'top 5' if latest['mode'] == 'top5' else 'selected positions'} · "
               f"{_fmt_ist_short(latest['started_at']) or ''}")
    if latest["status"] == "FAILED":
        st.error(latest["error"] or "The run failed.")
    if not results:
        st.caption("Nothing to show yet — the skill has not returned anything for this run.")
        return

    rows = _results_table_rows(results)
    st.caption("Click a row to open it below. Order is the skill's own ranking — click a "
               "column header to sort differently.")
    event = st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True,
                         on_select="rerun", selection_mode="single-row",
                         key=f"mi_res_tbl_{latest['id']}")
    picked = list(getattr(event, "selection", {}).get("rows") or [])
    if picked and picked[0] < len(rows):
        st.session_state["mi_result_symbol"] = rows[picked[0]]["Symbol"]

    r = _pick_result(results, st.session_state.get("mi_result_symbol"))
    if r is None:
        return
    st.divider()
    st.markdown(_analysis_card(r), unsafe_allow_html=True)
    if r["status"] != "DONE":
        st.caption(_MANUAL_STATUS_HELP.get(r["status"], "")
                   or f"{r['symbol']} is {str(r['status']).lower()} — nothing to trade yet.")
        if r["error"]:
            st.error(r["error"])
        return
    t_trade, t_an, t_raw = st.tabs(["Trade", "Analysis", "Raw"])
    with t_trade:
        _order_ticket_body(r, _db(lambda s: s.get_manual_config()), latest["skill_id"])
    with t_an:
        st.markdown(r["report"] or "_No analysis recorded for this stock._")
    with t_raw:
        if r["raw_json"]:
            try:
                st.json(_json.loads(r["raw_json"]))
            except Exception:                                        # noqa: BLE001
                st.code(r["raw_json"])
        else:
            st.caption("No raw output recorded.")
```

Note: `_MANUAL_STATUS_HELP` is keyed by ORDER status, not result status, so `.get` returns
nothing for `ANALYZING`/`PENDING` — hence the `or` fallback sentence. That is intentional; do
not add result statuses to the order help map, which is used by the Orders tab.

- [ ] **Step 2: Run the page tests**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q`
Expected: all PASS

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 4: Verify the widget collapse under AppTest, against a TEMP database**

Reuse the five-result driver from the audit (`drive_res.py`) with `AUTOINTRADAY_DB` pointed at a
scratchpad file. Assert: no exception; `len(at.number_input) == 6` and `len(at.selectbox) == 2`
(exactly ONE trade ticket); `len(at.dataframe) == 1`; total interactive widgets under 15. Then
set `st.session_state["mi_result_symbol"]` to a different symbol, re-run, and assert the detail
panel's Place button names that symbol. Delete the temp DB afterwards.

- [ ] **Step 5: Commit**

```bash
git add dashboard.py tests/test_dashboard_manual_intraday.py
git commit -m "feat(manual-intraday): results as a table plus one detail panel"
```
