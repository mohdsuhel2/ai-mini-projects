# Manual Intraday Page Structure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the Manual Intraday page into a persistent status strip plus four tabs, promoting the order ticket from three clicks deep to visible, and raising unprotected-position alerts to every tab.

**Architecture:** Four pure helpers carry the logic out of Streamlit so it can be tested directly. The page becomes a fragment-refreshed status strip plus a `_url_tabs` dispatch to four tab renderers. `_analysis_card` gains a tone class rather than being replaced, so existing tests keep their meaning.

**Tech Stack:** Python 3, Streamlit (`st.container(border=True)`, `st.fragment`, `st.tabs`, the repo's `_url_tabs`), pytest, `streamlit.testing.v1.AppTest`. Spec: `docs/superpowers/specs/2026-08-12-manual-intraday-page-structure-design.md`.

## Global Constraints

- Run tests with: `cd /Users/mohdsuhel/ai-mini-projects/autoIntraday && .venv/bin/python -m pytest tests/<file> -q` (no bare `python` on this machine).
- NOTHING about order placement, OCO arming, sizing or the analysis engine changes. This is layout only. `manual_broker.py`, `manual_order_job.py`, `manual_engine.py` and `store.py` are not touched.
- Keep `_analysis_card`, `_analysis_levels`, `_ticket_defaults`, `_selected_symbols` — they have tests that must keep passing.
- Tab labels are `Positions`, `Results`, `Orders`, `Settings` — single words, so the `?mi=` query parameter stays clean.
- Do NOT `git add` these files, which carry unrelated uncommitted work: `groww_client.py`, `swing_job.py`, `observe_job.py`, `tests/test_groww_client.py`, `tests/test_observe.py`, `tests/test_swing_economics.py`, `tests/test_swing_job.py`. Stage only the files each task names.
- Smoke-test seeding must write to a TEMP database, never `~/.autointraday/autointraday.db` — a blanket cleanup against the live DB previously destroyed a real analysis run.

---

### Task 1: The four pure helpers

**Files:**
- Modify: `dashboard.py` — add helpers beside `_analysis_card`; add a tone class to `_analysis_card`
- Test: `tests/test_dashboard_manual_intraday.py`

**Interfaces:**
- Produces:
  - `_verdict_tone(verdict) -> str` — `"good"` | `"bad"` | `"flat"`
  - `_ticket_open_default(verdict) -> bool`
  - `_strip_facts(positions, fetched_at, run, progress, orders) -> dict` with keys `positions, fetched_at, run_status, run_skill, run_mode, done, total, errors, unprotected, errored, attention`
  - `_orders_for_display(orders) -> list[dict]`
  - `_analysis_card` now emits `ai-averdict ai-tone-<tone>` on the verdict span.
- Task 2 consumes all of these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard_manual_intraday.py`:

```python
# ---- page structure helpers -------------------------------------------------------------

import pytest


@pytest.mark.parametrize("verdict", ["BUY NOW", "BUY ON PULLBACK", "IGNITION_BUY", "ADD",
                                     "MARKET EXCEPTION LONG"])
def test_entry_verdicts_tone_good(verdict):
    assert dashboard._verdict_tone(verdict) == "good"


@pytest.mark.parametrize("verdict", ["SHORT NOW", "SELL NOW", "EXIT", "REDUCE"])
def test_exit_and_short_verdicts_tone_bad(verdict):
    """EXIT/REDUCE are red because they instruct you to get OUT, not to stand still."""
    assert dashboard._verdict_tone(verdict) == "bad"


@pytest.mark.parametrize("verdict", ["WAIT", "HOLD", "NO TRADE", "", None])
def test_standstill_verdicts_tone_flat(verdict):
    assert dashboard._verdict_tone(verdict) == "flat"


def test_card_carries_the_tone_class():
    assert "ai-tone-good" in dashboard._analysis_card(R)
    assert "ai-tone-bad" in dashboard._analysis_card(dict(R, verdict="SHORT NOW"))
    assert "ai-tone-flat" in dashboard._analysis_card(dict(R, verdict="WAIT"))


def test_ticket_opens_only_for_an_actionable_entry():
    assert dashboard._ticket_open_default("BUY NOW") is True
    assert dashboard._ticket_open_default("SHORT NOW") is True
    assert dashboard._ticket_open_default("WAIT") is False
    assert dashboard._ticket_open_default("EXIT") is False


_RUN = {"id": 3, "status": "RUNNING", "skill_id": "intraday-analyst-2", "mode": "symbols"}
_PROG = {"done": 3, "total": 5, "errors": 1, "pending": 1, "analyzing": 1}


def _o(status, oid=1):
    return {"id": oid, "status": status, "symbol": "KEI"}


def test_strip_facts_summarises_positions_and_run():
    f = dashboard._strip_facts([{"symbol": "KEI"}, {"symbol": "BSE"}], "2026-08-12T04:00:00Z",
                               _RUN, _PROG, [])
    assert f["positions"] == 2 and f["fetched_at"] == "2026-08-12T04:00:00Z"
    assert f["run_status"] == "RUNNING" and f["run_skill"] == "intraday-analyst-2"
    assert f["done"] == 3 and f["total"] == 5 and f["errors"] == 1


def test_strip_facts_with_no_run_at_all():
    f = dashboard._strip_facts([], None, None, {"done": 0, "total": 0, "errors": 0}, [])
    assert f["positions"] == 0 and f["run_status"] is None and f["run_skill"] is None
    assert f["attention"] == 0


def test_strip_facts_counts_unprotected_and_errored():
    """FILLED means the entry filled but the OCO did NOT arm — a live, unprotected position."""
    orders = [_o("ARMED", 1), _o("FILLED", 2), _o("ERROR", 3), _o("FILLED", 4)]
    f = dashboard._strip_facts([], None, None, _PROG, orders)
    assert len(f["unprotected"]) == 2 and len(f["errored"]) == 1
    assert f["attention"] == 3


def test_strip_facts_does_not_flag_rejected_or_closed():
    """A REJECTED order never reached the market and a CLOSED one is done — neither is urgent."""
    orders = [_o("REJECTED", 1), _o("CLOSED", 2), _o("ARMED", 3), _o("ENTRY_PENDING", 4)]
    f = dashboard._strip_facts([], None, None, _PROG, orders)
    assert f["attention"] == 0


def test_orders_for_display_pins_attention_first():
    orders = [_o("ARMED", 1), _o("CLOSED", 2), _o("FILLED", 3), _o("REJECTED", 4),
              _o("ERROR", 5)]
    out = dashboard._orders_for_display(orders)
    assert [o["id"] for o in out[:2]] == [3, 5]          # FILLED and ERROR pinned
    assert [o["id"] for o in out[2:]] == [1, 2, 4]       # rest keep their order


def test_orders_for_display_loses_nothing():
    orders = [_o(s, i) for i, s in enumerate(
        ["ARMED", "FILLED", "CLOSED", "ERROR", "REJECTED", "ENTRY_PENDING", "PLACING"])]
    out = dashboard._orders_for_display(orders)
    assert sorted(o["id"] for o in out) == sorted(o["id"] for o in orders)
    assert len(out) == len(orders)


def test_orders_for_display_handles_empty():
    assert dashboard._orders_for_display([]) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q`
Expected: the new tests FAIL with `AttributeError: module 'dashboard' has no attribute '_verdict_tone'`

- [ ] **Step 3: Implement**

Add to `dashboard.py` immediately before `_analysis_card`:

```python
# A verdict's colour. Exits are RED, not neutral: "EXIT"/"REDUCE" instruct you to get out, and
# showing that in the same grey as "WAIT" would flatten an instruction into a non-event.
_TONE_BAD = ("SHORT", "EXIT", "REDUCE", "SELL")
_TONE_GOOD = ("BUY", "LONG", "IGNITION")


def _verdict_tone(verdict) -> str:
    v = str(verdict or "").upper()
    if not v:
        return "flat"
    if any(w in v for w in _TONE_BAD):
        return "bad"
    if any(w in v for w in _TONE_GOOD) or v.strip() == "ADD":
        return "good"
    return "flat"


def _ticket_open_default(verdict) -> bool:
    """Open the ticket only when the verdict actually instructs an entry, so a WAIT does not
    push the other results off the screen."""
    from manual_broker import side_from_verdict
    return side_from_verdict(verdict) is not None


# Orders that need the operator NOW. FILLED means the entry filled but the OCO did not arm —
# a live position with no protection resting against it. ERROR is anything else that broke.
# REJECTED is deliberately absent: nothing reached the market, so there is nothing to fix.
_ATTENTION_STATES = ("FILLED", "ERROR")


def _strip_facts(positions, fetched_at, run, progress, orders) -> dict:
    """Everything the status strip renders, as plain data."""
    unprotected = [o for o in (orders or []) if o.get("status") == "FILLED"]
    errored = [o for o in (orders or []) if o.get("status") == "ERROR"]
    return {"positions": len(positions or []), "fetched_at": fetched_at,
            "run_status": run["status"] if run else None,
            "run_skill": run["skill_id"] if run else None,
            "run_mode": run["mode"] if run else None,
            "done": (progress or {}).get("done", 0),
            "total": (progress or {}).get("total", 0),
            "errors": (progress or {}).get("errors", 0),
            "unprotected": unprotected, "errored": errored,
            "attention": len(unprotected) + len(errored)}


def _orders_for_display(orders) -> list:
    """Attention-first, then the caller's order (the store already returns newest-first)."""
    att = [o for o in (orders or []) if o.get("status") in _ATTENTION_STATES]
    rest = [o for o in (orders or []) if o.get("status") not in _ATTENTION_STATES]
    return att + rest
```

In `_analysis_card`, change the verdict span to carry the tone:

```python
    bits = [f'<span class="ai-averdict ai-tone-{_verdict_tone(r.get("verdict"))}">'
            f'{html.escape(str(r.get("verdict") or "—"))}</span>']
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q`
Expected: all PASS, including the pre-existing card and ticket tests

- [ ] **Step 5: Commit**

```bash
git add dashboard.py tests/test_dashboard_manual_intraday.py
git commit -m "feat(manual-intraday): verdict tone, strip facts and order ordering helpers"
```

---

### Task 2: Status strip and four tabs

**Files:**
- Modify: `dashboard.py` — CSS, split `_order_ticket` into a body, add the strip and four tab renderers, rewrite `_manual_intraday_page`
- Test: `tests/test_dashboard_manual_intraday.py`

**Interfaces:**
- Consumes: every helper from Task 1.
- Produces: `_mode_pill(mode)`, `_manual_status_strip()`, `_mi_positions_tab(skills)`, `_mi_results_tab()`, `_mi_orders_tab()`, `_mi_settings_tab()`, `_order_ticket_body(r, cfg, skill_id)`, and a rewritten `_manual_intraday_page()`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_manual_intraday.py`:

```python
def test_page_uses_url_persisted_tabs():
    src = open(dashboard.__file__, encoding="utf-8").read()
    assert '_url_tabs("mi"' in src
    for label in ('"Positions"', '"Results"', '"Orders"', '"Settings"'):
        assert label in src


def test_mode_pill_distinguishes_live_from_paper():
    assert "ai-mode-live" in dashboard._mode_pill("live")
    assert "LIVE" in dashboard._mode_pill("live")
    assert "ai-mode-paper" in dashboard._mode_pill("paper")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q -k "url_persisted or mode_pill"`
Expected: FAIL — `_url_tabs("mi"` absent, `_mode_pill` missing

- [ ] **Step 3: Implement**

(a) CSS — add after the `.ai-mode-paper` rule:

```css
.ai-tone-good { color: #30a46c; }
.ai-tone-bad  { color: #e5484d; }
.ai-tone-flat { opacity: .7; }
.ai-strip-fact { font-size: .82rem; opacity: .8; }
.ai-strip-fact b { opacity: 1; font-weight: 650; }
```

(b) Replace the `_order_ticket` wrapper with a body-only function. Rename `def _order_ticket(r, cfg, skill_id=None)` to `def _order_ticket_body(r, cfg, skill_id=None)` and DELETE its `with st.expander("Place order", expanded=False):` line, de-indenting the whole body by four spaces. The caller now owns the expander.

(c) Add `_mode_pill` next to `_autointraday_live_warning`:

```python
def _mode_pill(mode: str) -> str:
    return ('<span class="ai-mode-live">LIVE — REAL ORDERS</span>' if mode == "live"
            else '<span class="ai-mode-paper">PAPER</span>')
```

(d) Add the status strip, replacing nothing (new function):

```python
@st.fragment(run_every=4)
def _manual_status_strip() -> None:
    """Mode, positions, run progress and alerts — visible from EVERY tab. An unprotected live
    position must not depend on which tab happens to be open."""
    import html
    cfg = _db(lambda s: s.get_manual_config())
    run = _db(lambda s: s.latest_analysis_run())
    prog = (_db(lambda s: s.analysis_progress(run["id"]))
            if run else {"done": 0, "total": 0, "errors": 0})
    f = _strip_facts(_db(lambda s: s.get_broker_positions()),
                     _db(lambda s: s.broker_positions_fetched_at()),
                     run, prog, _db(lambda s: s.get_manual_orders(limit=50)))
    with st.container(border=True):
        c1, c2, c3 = st.columns([1.1, 1.3, 2.6], vertical_alignment="center")
        c1.markdown(_mode_pill(cfg["mode"]), unsafe_allow_html=True)
        when = _fmt_ist_short(f["fetched_at"]) if f["fetched_at"] else None
        c2.markdown(f'<span class="ai-strip-fact"><b>{f["positions"]}</b> positions'
                    + (f' · {when}' if when else ' · not fetched')
                    + '</span>', unsafe_allow_html=True)
        if f["run_status"] == "RUNNING":
            total = f["total"] or 1
            c3.progress(f["done"] / total,
                        text=f"⏳ {f['run_skill']} — {f['done']}/{f['total'] or '?'}"
                             + (f" · {f['errors']} errors" if f["errors"] else ""))
        elif f["run_skill"]:
            c3.markdown(f'<span class="ai-strip-fact">last run '
                        f'<b>{html.escape(str(f["run_skill"]))}</b> · '
                        f'{str(f["run_status"]).lower()}</span>', unsafe_allow_html=True)
        else:
            c3.markdown('<span class="ai-strip-fact">no analysis run yet</span>',
                        unsafe_allow_html=True)
        if f["unprotected"]:
            syms = ", ".join(sorted({o["symbol"] for o in f["unprotected"]}))
            st.error(f"⚠ {len(f['unprotected'])} position(s) FILLED but exits NOT armed "
                     f"({syms}) — live and UNPROTECTED. Open **Orders** and fix now.")
        if f["errored"]:
            st.warning(f"{len(f['errored'])} order(s) errored — see **Orders**.")
    _autointraday_live_warning()
```

(e) Add the four tab renderers:

```python
def _mi_positions_tab(skills: list) -> None:
    import pandas as pd
    positions = _db(lambda s: s.get_broker_positions())
    fetched_at = _db(lambda s: s.broker_positions_fetched_at())
    latest = _db(lambda s: s.latest_analysis_run())
    running = bool(latest and latest["status"] == "RUNNING")

    top = st.columns([1.4, 2.2, 2.4], vertical_alignment="center")
    with top[0]:
        if st.button("Fetch positions", use_container_width=True, disabled=running):
            try:
                with st.spinner("Fetching from Groww…"):
                    _refresh_positions_from_groww()
            except Exception as e:                                   # noqa: BLE001
                st.error(f"Could not load positions: {e}")
            st.rerun()
    with top[1]:
        skill = st.selectbox("Skill", skills, key="analysis_skill",
                             label_visibility="collapsed")
    with top[2]:
        if fetched_at:
            st.caption(f"Positions as of {_fmt_ist(fetched_at) or fetched_at}")
        else:
            st.caption("No positions loaded — click **Fetch positions**.")

    picked: list[str] = []
    if positions:
        table = [{"Analyze": False, "Symbol": p["symbol"], "Qty": p.get("quantity"),
                  "Avg": p.get("avg_price"), "Product": p.get("product")}
                 for p in positions]
        edited = st.data_editor(
            pd.DataFrame(table), hide_index=True, use_container_width=True,
            disabled=["Symbol", "Qty", "Avg", "Product"], key="analysis_pick",
            column_config={"Analyze": st.column_config.CheckboxColumn(
                "Analyze", help="Tick the stocks to send to the chosen skill.")})
        picked = _selected_symbols(edited.to_dict("records"))

    act = st.columns([1.6, 1.6, 3], vertical_alignment="center")
    with act[0]:
        if st.button(f"Analyze {len(picked)} selected", use_container_width=True,
                     type="primary", disabled=running or not picked):
            rid = _db(lambda s: s.start_analysis_run(skill, "symbols"))
            _db(lambda s: s.seed_analysis_results(rid, picked))
            _launch_analysis(rid)
            st.query_params["mi"] = "Results"
            st.rerun()
    with act[1]:
        if st.button("Top 5 from this skill", use_container_width=True, disabled=running):
            rid = _db(lambda s: s.start_analysis_run(skill, "top5"))
            _launch_analysis(rid)
            st.query_params["mi"] = "Results"
            st.rerun()
    with act[2]:
        if picked:
            st.caption(f"{len(picked)} stock(s) x ~2 min ≈ **{len(picked) * 2} minutes**. "
                       "The run continues if you navigate away.")
        else:
            st.caption("Top 5 is a single call — the skill runs its own screen and picks its "
                       "own names.")


@st.fragment(run_every=4)
def _mi_results_tab() -> None:
    import json as _json
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
    cfg = _db(lambda s: s.get_manual_config())
    waiting = [r for r in results if r["status"] in ("PENDING", "ANALYZING")]
    for r in results:
        if r["status"] in ("PENDING", "ANALYZING"):
            continue
        with st.container(border=True):
            st.markdown(_analysis_card(r), unsafe_allow_html=True)
            if r["status"] == "DONE":
                with st.expander(f"Trade {r['symbol']}",
                                 expanded=_ticket_open_default(r["verdict"])):
                    _order_ticket_body(r, cfg, latest["skill_id"])
            t_an, t_raw = st.tabs(["Analysis", "Raw"])
            with t_an:
                st.markdown(r["report"] or "_No analysis recorded for this stock._")
            with t_raw:
                if r["raw_json"]:
                    try:
                        st.json(_json.loads(r["raw_json"]))
                    except Exception:                                # noqa: BLE001
                        st.code(r["raw_json"])
                else:
                    st.caption("No raw output recorded.")
    for r in waiting:
        st.caption(f"{r['symbol']} — "
                   f"{'⏳ analyzing' if r['status'] == 'ANALYZING' else '· waiting'}")


@st.fragment(run_every=5)
def _mi_orders_tab() -> None:
    from manual_broker import ManualBroker
    orders = _orders_for_display(_db(lambda s: s.get_manual_orders(limit=25)))
    if not orders:
        st.caption("No orders placed from this page yet.")
        return
    for o in orders:
        badge = _MANUAL_STATUS_LABEL.get(o["status"], o["status"])
        head = f"{o['symbol']} · {o['side']} x{o['quantity']} · {o['mode'].upper()} · {badge}"
        with st.expander(head, expanded=o["status"] in _ATTENTION_STATES):
            st.caption(f"{o['entry_type']} entry {o['entry_price'] or '—'} · stop {o['stop']} "
                       f"· target {o['target']} · placed {_fmt_ist_short(o['placed_at']) or ''}"
                       + (f" · filled @ {o['fill_price']}" if o["fill_price"] else ""))
            if o["error"]:
                st.error(o["error"])
            if o["status"] != "ARMED":
                continue
            m1, m2, m3 = st.columns([1.2, 1.2, 1.6])
            new_t = m1.number_input("Target", min_value=0.0, step=0.05,
                                    value=float(o["target"] or 0.0), key=f"mo{o['id']}_t")
            new_s = m2.number_input("Stop", min_value=0.0, step=0.05,
                                    value=float(o["stop"] or 0.0), key=f"mo{o['id']}_s")
            b1, b2 = m3.columns(2)
            if b1.button("Modify exits", key=f"mo{o['id']}_mod", use_container_width=True):
                try:
                    ManualBroker(mode=o["mode"]).modify_exits(o["oco_order_id"], new_t, new_s)
                    _db(lambda s: s.update_manual_order(o["id"], target=new_t, stop=new_s))
                    st.toast("Exits updated", icon="✅")
                except Exception as ex:                              # noqa: BLE001
                    st.error(f"Could not modify: {ex}")
                st.rerun()
            if b2.button("Square off", key=f"mo{o['id']}_sq", use_container_width=True):
                try:
                    ManualBroker(mode=o["mode"]).square_off(
                        o["symbol"], o["side"], o["quantity"], o["oco_order_id"])
                    _db(lambda s: s.update_manual_order(o["id"], status="CLOSED",
                                                        closed_at=_utc_iso()))
                    st.toast("Squared off", icon="✅")
                except Exception as ex:                              # noqa: BLE001
                    st.error(f"Could not square off: {ex}")
                st.rerun()


def _mi_settings_tab() -> None:
    cfg = _db(lambda s: s.get_manual_config())
    s1, s2 = st.columns(2)
    want_live = s1.toggle("LIVE mode (places REAL orders on Groww)",
                          value=cfg["mode"] == "live", key="manual_mode")
    cap = s2.number_input("Capital per trade (₹)", min_value=0.0, step=1000.0,
                          value=float(cfg["capital_per_trade"]), key="manual_cap")
    if want_live and cfg["mode"] != "live":
        st.warning("Saving this sends REAL orders to your Groww account from the Trade "
                   "tickets. autoIntraday's own paper/live setting does not apply here.")
    st.caption("Quantity prefills as capital ÷ entry price, and is editable on every ticket.")
    if st.button("Save settings", use_container_width=True):
        _db(lambda s: s.set_manual_config(mode="live" if want_live else "paper",
                                          capital_per_trade=float(cap)))
        st.rerun()
```

(f) Replace the whole body of `_manual_intraday_page` with:

```python
def _manual_intraday_page() -> None:
    from observe import available_skills

    st.markdown('<div class="ai-brand">Manual Intraday<em>.</em></div>',
                unsafe_allow_html=True)
    st.caption("Analyse your live Groww intraday positions with any skill, then place the "
               "trade from the same screen. Orders use THIS page's mode — autoIntraday's "
               "paper/live setting does not apply.")
    _manual_status_strip()

    skills = available_skills()
    if not skills:
        st.error("No skills found in ~/.claude/skills")
        return

    section = _url_tabs("mi", ["Positions", "Results", "Orders", "Settings"])
    if section == "Positions":
        _mi_positions_tab(skills)
    elif section == "Results":
        _mi_results_tab()
    elif section == "Orders":
        _mi_orders_tab()
    else:
        _mi_settings_tab()
```

(g) Delete the two functions this replaces, which are now unreachable: `_analysis_live` (superseded by `_mi_results_tab`) and `_manual_orders_section` (superseded by `_mi_orders_tab`). Verify with `grep -n "_analysis_live\|_manual_orders_section" dashboard.py` returning nothing.

- [ ] **Step 4: Run the page tests**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q`
Expected: all PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 6: Render every tab under AppTest against a TEMP database**

The live database must NOT be touched — a previous blanket cleanup against it destroyed a real
analysis run. `settings.load_settings()` reads the path from the `AUTOINTRADAY_DB` environment
variable, so the driver sets it to a scratchpad file BEFORE importing dashboard.

Write `<scratchpad>/drive_mi.py` that:
1. sets `os.environ["AUTOINTRADAY_DB"]` to a temp path under the scratchpad,
2. imports `Store`, seeds one broker position, one SUCCESS analysis run holding one DONE
   result with a `BUY NOW` verdict, one `ARMED` manual order and one `FILLED` one,
3. imports dashboard, sets `st.query_params["mi"]` from `os.environ["MI_SECTION"]`, and calls
   `dashboard._manual_intraday_page()`.

Then run it through `AppTest` once per section with `MI_SECTION` set to each of `Positions`,
`Results`, `Orders`, `Settings`. Assert for every section that `at.exception` is empty; assert
the unprotected-position alert text appears in `at.error` on all four (it lives in the strip);
assert the Results section exposes a `Trade …` expander; assert the Orders section exposes
`Modify exits` and `Square off` buttons. Delete the temp DB afterwards.

- [ ] **Step 7: Commit**

```bash
git add dashboard.py tests/test_dashboard_manual_intraday.py
git commit -m "feat(manual-intraday): status strip and four-tab layout"
```
