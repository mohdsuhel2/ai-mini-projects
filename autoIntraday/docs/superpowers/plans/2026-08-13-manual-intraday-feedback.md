# Manual Intraday Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Manual Intraday page state what is actually happening — durable receipts, readable errors, timed progress, in-flight order visibility, and detection plus recovery for a run whose job has died (which today locks the page while showing a progress bar).

**Architecture:** All the reasoning lives in pure helpers in `dashboard.py` that take data and return strings or verdicts, so it is testable without Streamlit. One new store method, `stop_analysis_run`, mirrors the existing `stop_swing_run`. The page wiring then just renders what the helpers decide.

**Tech Stack:** Python 3, sqlite3, Streamlit, pytest, `streamlit.testing.v1.AppTest`. Spec: `docs/superpowers/specs/2026-08-13-manual-intraday-feedback-design.md`.

## Global Constraints

- Run tests with: `cd /Users/mohdsuhel/ai-mini-projects/autoIntraday && .venv/bin/python -m pytest tests/<file> -q` (no bare `python`).
- Nothing about order placement, OCO arming or the analysis engine changes. `manual_broker.py`, `manual_order_job.py`, `manual_engine.py` and `analysis_job.py` are NOT touched.
- Unknown stays unknown: a missing timestamp or pid yields `None`/`False`, never a fabricated duration.
- Smoke tests seed a TEMP database via `AUTOINTRADAY_DB`, never `~/.autointraday/autointraday.db`.
- Do NOT `git add`: `groww_client.py`, `swing_job.py`, `observe_job.py`, `tests/test_groww_client.py`, `tests/test_observe.py`, `tests/test_swing_economics.py`, `tests/test_swing_job.py`.

---

### Task 1: Store — stop a run, and report its last activity

**Files:**
- Modify: `store.py` — two methods after `get_analysis_results`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces `Store.stop_analysis_run(run_id) -> int | None` (marks `STOPPED`, resets `ANALYZING` rows to `PENDING`, returns the stored pid) and `Store.analysis_last_activity(run_id) -> str | None` (max `analyzed_at`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_stop_analysis_run_marks_stopped_and_returns_pid():
    store = Store(":memory:")
    rid = store.start_analysis_run("intraday-analyst-2", "symbols")
    store.set_analysis_pid(rid, 4242)
    store.seed_analysis_results(rid, ["KEI", "BSE"])
    store.update_analysis_result(rid, "KEI", "ANALYZING")
    assert store.stop_analysis_run(rid) == 4242
    assert store.latest_analysis_run()["status"] == "STOPPED"
    rows = {r["symbol"]: r for r in store.get_analysis_results(rid)}
    assert rows["KEI"]["status"] == "PENDING"        # mid-flight row reset
    assert rows["BSE"]["status"] == "PENDING"


def test_stop_analysis_run_without_a_pid_returns_none():
    store = Store(":memory:")
    rid = store.start_analysis_run("s", "top5")
    assert store.stop_analysis_run(rid) is None
    assert store.latest_analysis_run()["status"] == "STOPPED"


def test_stop_analysis_run_leaves_finished_rows_alone():
    store = Store(":memory:")
    rid = store.start_analysis_run("s", "symbols")
    store.seed_analysis_results(rid, ["KEI"])
    store.update_analysis_result(rid, "KEI", "DONE", item=_item(), raw="{}")
    store.stop_analysis_run(rid)
    assert store.get_analysis_results(rid)[0]["status"] == "DONE"


def test_analysis_last_activity_is_the_newest_completion():
    store = Store(":memory:")
    rid = store.start_analysis_run("s", "symbols")
    store.seed_analysis_results(rid, ["A", "B"])
    assert store.analysis_last_activity(rid) is None      # nothing finished yet
    store.update_analysis_result(rid, "A", "DONE", item=_item("A"), raw="{}")
    first = store.analysis_last_activity(rid)
    assert first
    store.update_analysis_result(rid, "B", "ERROR", error="boom")
    assert store.analysis_last_activity(rid) >= first
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -q -k "stop_analysis or last_activity"`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'stop_analysis_run'`

- [ ] **Step 3: Implement**

Add after `get_analysis_results` in `store.py`:

```python
    def stop_analysis_run(self, run_id: int) -> int | None:
        """Mark the run STOPPED and reset any mid-flight symbol back to PENDING. Returns the
        stored pid (None if never set) so the caller can signal the process. Mirrors
        stop_swing_run. The page treats anything other than RUNNING as unlocked, so this is
        also the recovery path when a detached job has died."""
        row = self._conn.execute(
            "SELECT pid FROM analysis_runs WHERE id = ?", (run_id,)).fetchone()
        self._conn.execute(
            "UPDATE analysis_results SET status = 'PENDING' "
            "WHERE run_id = ? AND status = 'ANALYZING'", (run_id,))
        self._conn.execute(
            "UPDATE analysis_runs SET status = 'STOPPED', finished_at = ? WHERE id = ?",
            (_utc_now(), run_id))
        self._conn.commit()
        return row["pid"] if row else None

    def analysis_last_activity(self, run_id: int) -> str | None:
        """When a row of this run last reached a terminal state. Used to tell a slow run from
        a stalled one without pulling every report body on a 4-second refresh."""
        r = self._conn.execute(
            "SELECT MAX(analyzed_at) AS t FROM analysis_results WHERE run_id = ?",
            (run_id,)).fetchone()
        return r["t"] if r and r["t"] else None
```

- [ ] **Step 4: Run the store tests**

Run: `.venv/bin/python -m pytest tests/test_store.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "feat(manual-intraday): stop an analysis run and report its last activity"
```

---

### Task 2: The pure feedback helpers

**Files:**
- Modify: `dashboard.py` — helpers beside `_strip_facts`
- Test: `tests/test_dashboard_manual_intraday.py`

**Interfaces:**
- Produces `_fmt_duration(seconds) -> str`, `_age_seconds(iso, now=None) -> float | None`, `_pid_alive(pid) -> bool`, `_run_health(run, last_activity, now, alive) -> str`, `_run_progress_text(run, progress, elapsed_s) -> str`, `_friendly_error(exc) -> tuple[str, str]`, `_inflight_orders(orders) -> list`, `_MANUAL_STATUS_HELP: dict`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dashboard_manual_intraday.py`:

```python
from datetime import datetime, timedelta, timezone


def test_fmt_duration_scales_its_units():
    assert dashboard._fmt_duration(45) == "45s"
    assert dashboard._fmt_duration(100) == "1m40s"
    assert dashboard._fmt_duration(700) == "11m"
    assert dashboard._fmt_duration(3900) == "1h 5m"
    assert dashboard._fmt_duration(-5) == "0s"


def test_age_seconds_and_its_unknowns():
    now = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)
    assert dashboard._age_seconds("2026-08-13T09:58:00+00:00", now) == 120
    assert dashboard._age_seconds(None, now) is None
    assert dashboard._age_seconds("not a date", now) is None


def test_pid_alive_for_this_process_and_a_dead_one():
    import os
    assert dashboard._pid_alive(os.getpid()) is True
    assert dashboard._pid_alive(None) is False
    assert dashboard._pid_alive(0) is False
    assert dashboard._pid_alive("nonsense") is False
    assert dashboard._pid_alive(999999) is False


_NOW = datetime(2026, 8, 13, 10, 0, tzinfo=timezone.utc)


def _run(status="RUNNING", mode="symbols", started="2026-08-13T09:50:00+00:00"):
    return {"id": 1, "status": status, "mode": mode, "skill_id": "intraday-analyst-2",
            "started_at": started, "pid": 123}


def test_run_health_ok_when_progressing():
    fresh = "2026-08-13T09:59:00+00:00"
    assert dashboard._run_health(_run(), fresh, _NOW, alive=True) == "ok"


def test_run_health_stalled_when_nothing_finished_recently():
    old = "2026-08-13T09:50:00+00:00"          # 10 minutes ago
    assert dashboard._run_health(_run(), old, _NOW, alive=True) == "stalled"


def test_run_health_dead_when_the_process_is_gone():
    assert dashboard._run_health(_run(), "2026-08-13T09:59:00+00:00", _NOW,
                                 alive=False) == "dead"


def test_run_health_ok_for_a_finished_run():
    assert dashboard._run_health(_run(status="SUCCESS"), None, _NOW, alive=False) == "ok"
    assert dashboard._run_health(None, None, _NOW, alive=False) == "ok"


def test_run_health_falls_back_to_started_at_before_anything_finishes():
    """A run with no completions yet is judged from when it started, not called stalled."""
    just_started = _run(started="2026-08-13T09:59:00+00:00")
    assert dashboard._run_health(just_started, None, _NOW, alive=True) == "ok"


def test_progress_text_for_symbols_has_counts_and_time():
    t = dashboard._run_progress_text(_run(), {"done": 3, "total": 5, "errors": 0}, 360)
    assert "3/5" in t and "6m" in t and "left" in t


def test_progress_text_mentions_errors_when_present():
    t = dashboard._run_progress_text(_run(), {"done": 3, "total": 5, "errors": 1}, 360)
    assert "1 errors" in t or "1 error" in t


def test_progress_text_for_top5_has_no_meaningless_fraction():
    t = dashboard._run_progress_text(_run(mode="top5"), {"done": 0, "total": 0, "errors": 0},
                                     100)
    assert "single call" in t and "0/0" not in t and "1m40s" in t


def test_progress_text_drops_the_estimate_once_everything_is_done():
    t = dashboard._run_progress_text(_run(), {"done": 5, "total": 5, "errors": 0}, 600)
    assert "left" not in t


def test_friendly_error_recognises_a_groww_auth_failure():
    head, raw = dashboard._friendly_error(RuntimeError("401 Unauthorized: invalid api key"))
    assert "credential" in head.lower() or ".env" in head
    assert "401" in raw


def test_friendly_error_recognises_margin_and_network():
    head, _ = dashboard._friendly_error(RuntimeError("insufficient margin for this order"))
    assert "margin" in head.lower()
    head, _ = dashboard._friendly_error(RuntimeError("Connection timed out"))
    assert "reach" in head.lower() or "network" in head.lower()


def test_friendly_error_passes_a_manual_broker_message_through():
    from manual_broker import ManualBrokerError
    msg = "could not cancel the resting OCO OCO1 — check the broker."
    head, raw = dashboard._friendly_error(ManualBrokerError(msg))
    assert head == msg and raw == msg          # already written for a human


def test_friendly_error_falls_back_without_losing_the_raw_text():
    head, raw = dashboard._friendly_error(ValueError("weird internal thing"))
    assert head and "weird internal thing" in raw


def test_inflight_orders_are_the_ones_still_working():
    orders = [_o("PLACING", 1), _o("ENTRY_PENDING", 2), _o("ARMED", 3), _o("FILLED", 4),
              _o("CLOSED", 5)]
    assert [o["id"] for o in dashboard._inflight_orders(orders)] == [1, 2]
    assert dashboard._inflight_orders([]) == []


def test_every_order_status_has_an_explanation():
    for status in ("PLACING", "ENTRY_PENDING", "FILLED", "ARMED", "REJECTED", "CLOSED",
                   "ERROR"):
        assert dashboard._MANUAL_STATUS_HELP[status].strip()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q`
Expected: the new tests FAIL with `AttributeError: module 'dashboard' has no attribute '_fmt_duration'`

- [ ] **Step 3: Implement**

Add to `dashboard.py` immediately after `_orders_for_display`:

```python
def _fmt_duration(seconds) -> str:
    """A duration a person reads at a glance. Seconds under a minute, minutes-and-seconds up
    to ten, then whole minutes, then hours."""
    try:
        s = int(max(0, float(seconds)))
    except (TypeError, ValueError):
        return "?"
    if s < 60:
        return f"{s}s"
    m, sec = divmod(s, 60)
    if m < 10:
        return f"{m}m{sec:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m" if h else f"{m}m"


def _age_seconds(iso, now=None):
    """Seconds since an ISO timestamp, or None when it is missing or unparseable. Never
    guesses — an unknown age must not render as 0."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso))
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return ((now or datetime.now(timezone.utc)) - dt).total_seconds()


def _pid_alive(pid) -> bool:
    """Is that process still there? Signal 0 checks existence without touching it. A
    PermissionError means it exists but belongs to someone else — still alive."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


# A run with no completed row for this long is reported as stalled rather than silently
# spinning. Generous: a single skill call legitimately takes minutes.
_STALL_AFTER_S = 300


def _run_health(run, last_activity, now, alive) -> str:
    """`ok` | `stalled` | `dead` for the CURRENT run. `dead` means the detached job is gone,
    which matters because the page's buttons stay disabled while a run says RUNNING — without
    this the UI would claim to be working forever and lock the operator out."""
    if not run or run.get("status") != "RUNNING":
        return "ok"
    if not alive:
        return "dead"
    age = _age_seconds(last_activity or run.get("started_at"), now)
    return "stalled" if age is not None and age > _STALL_AFTER_S else "ok"


def _run_progress_text(run, progress, elapsed_s) -> str:
    """The strip's progress line. A top-5 run is ONE call, so a fraction would be meaningless
    and is deliberately absent."""
    skill = (run or {}).get("skill_id") or "skill"
    if (run or {}).get("mode") == "top5":
        return (f"⏳ {skill} — single call, screening the market · 2–4 min typical · "
                f"running {_fmt_duration(elapsed_s)}")
    done = (progress or {}).get("done", 0)
    total = (progress or {}).get("total", 0)
    errors = (progress or {}).get("errors", 0)
    txt = f"⏳ {skill} — {done}/{total} · {_fmt_duration(elapsed_s)} elapsed"
    if total and done < total:
        per = (elapsed_s / done) if done else 120.0
        txt += f" · ~{_fmt_duration((total - done) * per)} left"
    if errors:
        txt += f" · {errors} errors"
    return txt


# Substrings that identify a failure the operator can actually do something about.
_ERROR_HINTS = (
    (("authenticat", "unauthor", "401", "invalid api", "api key", "token"),
     "Groww login failed — check GROWW_API_KEY / GROWW_API_SECRET in your .env."),
    (("margin", "insufficient funds", "insufficient"),
     "Groww rejected this for insufficient margin."),
    (("timed out", "timeout", "connection", "network", "unreachable", "resolve"),
     "Could not reach Groww — network trouble or the API is down."),
)


def _friendly_error(exc) -> tuple[str, str]:
    """(headline, raw). The raw text is always returned so the caller can keep it visible in
    a collapsed expander — a hidden error is worse than an ugly one."""
    from manual_broker import ManualBrokerError
    raw = str(exc) or exc.__class__.__name__
    if isinstance(exc, ManualBrokerError):
        return raw, raw                      # already written for a human
    low = raw.lower()
    for needles, msg in _ERROR_HINTS:
        if any(n in low for n in needles):
            return msg, raw
    return "Something went wrong — the details are below.", raw


_INFLIGHT_STATES = ("PLACING", "ENTRY_PENDING")


def _inflight_orders(orders) -> list:
    """Orders still working at the broker. A resting LIMIT can sit for hours and is otherwise
    invisible outside the Orders tab."""
    return [o for o in (orders or []) if o.get("status") in _INFLIGHT_STATES]


_MANUAL_STATUS_HELP = {
    "PLACING": "Being sent to Groww now.",
    "ENTRY_PENDING": "Sent — waiting for the entry to fill. A LIMIT can rest all session.",
    "FILLED": "The position is OPEN but the exits did NOT arm — it has no protection "
              "resting against it. Re-place the exits or square off now.",
    "ARMED": "Filled, and both exits are resting at Groww as a native OCO. One filling "
             "cancels the other.",
    "REJECTED": "Groww refused the entry — nothing reached the market, so there is no "
                "position and nothing to unwind.",
    "CLOSED": "Squared off from this page.",
    "ERROR": "Something failed after the order was created — read the message and check the "
             "broker before acting.",
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard.py tests/test_dashboard_manual_intraday.py
git commit -m "feat(manual-intraday): duration, health, progress and error helpers"
```

---

### Task 3: Wire the feedback into the page

**Files:**
- Modify: `dashboard.py` — `_manual_status_strip`, `_order_ticket_body`, `_mi_positions_tab`, `_mi_orders_tab`, plus two new helpers
- Test: `tests/test_dashboard_manual_intraday.py` (AppTest render)

**Interfaces:**
- Consumes every helper from Tasks 1 and 2.
- Produces `_stop_analysis_job(run_id)`, `_notice(kind, text)`, `_render_notice()`, `_show_error(prefix, exc)`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_dashboard_manual_intraday.py`:

```python
def test_notice_helpers_round_trip_through_session_state():
    dashboard.st.session_state.clear()
    dashboard._notice("ok", "Order #12 sent")
    assert dashboard.st.session_state["mi_notice"]["text"] == "Order #12 sent"
    assert dashboard.st.session_state["mi_notice"]["kind"] == "ok"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q -k notice`
Expected: FAIL — `_notice` missing

- [ ] **Step 3: Implement**

(a) Add next to `_launch_manual_order`:

```python
def _stop_analysis_job(run_id: int) -> None:
    """Mark the run STOPPED and best-effort signal its process. The DB state is what unlocks
    the page, so it is written first and the kill is allowed to fail — the process may already
    be gone, which is exactly the case this exists for."""
    import signal
    pid = _db(lambda s: s.stop_analysis_run(run_id))
    if pid:
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, TypeError, ValueError):
            pass


def _notice(kind: str, text: str) -> None:
    """Record a durable receipt for the next render. Toasts vanish in seconds and placing an
    order also switches tabs, so a toast alone can be missed entirely."""
    st.session_state["mi_notice"] = {"kind": kind, "text": text}


def _render_notice() -> None:
    n = st.session_state.get("mi_notice")
    if not n:
        return
    {"ok": st.success, "warn": st.warning, "err": st.error}.get(n["kind"], st.info)(n["text"])


def _show_error(prefix: str, exc: Exception) -> None:
    """Headline in plain language, raw text kept one click away — never hidden."""
    head, raw = _friendly_error(exc)
    st.error(f"{prefix}: {head}")
    if raw and raw != head:
        with st.expander("Technical detail"):
            st.code(raw)
```

(b) In `_manual_status_strip`, replace the run-status branch and add the new lines. After
computing `f`, add:

```python
    health = _run_health(run, _db(lambda s: s.analysis_last_activity(run["id"])) if run else None,
                         datetime.now(timezone.utc), _pid_alive(run.get("pid")) if run else False)
    elapsed = _age_seconds((run or {}).get("started_at")) or 0
```

Replace the `if f["run_status"] == "RUNNING":` block's body with:

```python
        if f["run_status"] == "RUNNING" and health != "dead":
            total = f["total"] or 1
            c3.progress(min(1.0, f["done"] / total) if f["run_mode"] != "top5" else 0.0,
                        text=_run_progress_text(run, prog, elapsed))
```

and after the existing `else` branch of that chain, add these blocks inside the container:

```python
        if health == "dead":
            st.error(f"The analysis job for run #{run['id']} is no longer running "
                     f"(started {_fmt_duration(elapsed)} ago). Nothing is being analysed, and "
                     f"the buttons stay locked until this run is cleared.")
        elif health == "stalled":
            last = _db(lambda s: s.analysis_last_activity(run["id"]))
            st.info(f"No stock has finished for {_fmt_duration(_age_seconds(last) or elapsed)}"
                    " — the skill may still be thinking. Stop the run if it looks wedged.")
        if f["run_status"] == "RUNNING":
            if st.button("⏹ Stop run", key=f"mi_stop_{run['id']}"):
                _stop_analysis_job(run["id"])
                _notice("warn", f"Run #{run['id']} stopped.")
                st.rerun(scope="app")
        inflight = _inflight_orders(_db(lambda s: s.get_manual_orders(limit=50)))
        if inflight:
            bits = ", ".join(
                f"{o['symbol']} ({_fmt_duration(_age_seconds(o['placed_at']) or 0)})"
                for o in inflight)
            st.info(f"{len(inflight)} order(s) still working at the broker — {bits}.")
```

Note `st.rerun(scope="app")`: the strip is a fragment, and a plain `st.rerun()` would refresh
only the strip, leaving the Positions tab's buttons still disabled.

(c) In `_order_ticket_body`, replace the toast-only confirmation with a receipt as well:

```python
            _notice("ok", f"Order #{oid} sent — {cfg['mode'].upper()} {side} {int(qty)} "
                          f"{r['symbol']}, {etype} entry. Track it under **Orders**.")
            st.toast(f"{cfg['mode'].upper()} order sent for {r['symbol']}", icon="📤")
```

(d) In `_mi_positions_tab`, replace the fetch error handler:

```python
            except Exception as e:                                   # noqa: BLE001
                _show_error("Could not load positions", e)
                st.stop()
```

and add a receipt on success, inside the `try` after `_refresh_positions_from_groww()`:

```python
                n = len(_db(lambda s: s.get_broker_positions()))
                _notice("ok", f"Fetched {n} intraday position(s) from Groww.")
```

(e) In `_mi_orders_tab`, add the status explanation and swap both error handlers:

after the existing `st.caption(...)` of order details, add

```python
            st.caption(_MANUAL_STATUS_HELP.get(o["status"], ""))
```

and in the two action handlers replace `st.error(f"Could not modify: {ex}")` with
`_show_error("Could not modify the exits", ex)` and `st.error(f"Could not square off: {ex}")`
with `_show_error("Could not square off", ex)`; on each success path add a `_notice(...)`
receipt alongside the toast, and change both `st.rerun()` calls to `st.rerun(scope="app")`.

(f) In `_manual_intraday_page`, render the receipt right after the strip:

```python
    _manual_status_strip()
    _render_notice()
```

- [ ] **Step 4: Run the page tests**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q`
Expected: all PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 6: Render the new states under AppTest against a TEMP database**

Extend the scratchpad driver so `AUTOINTRADAY_DB` points at a temp file and seed, per scenario:
a RUNNING run whose `pid` is a definitely-dead number (999999) plus a `PLACING` order. Render
the page and assert: no exception; the "no longer running" error appears; a "Stop run" button
exists; and the in-flight order line appears. Then seed a RUNNING run with a live pid
(`os.getpid()`) and a stale `analyzed_at` and assert the stalled note appears instead. Delete
the temp DB afterwards.

- [ ] **Step 7: Commit**

```bash
git add dashboard.py tests/test_dashboard_manual_intraday.py
git commit -m "feat(manual-intraday): receipts, readable errors, timed progress, dead-run recovery"
```
