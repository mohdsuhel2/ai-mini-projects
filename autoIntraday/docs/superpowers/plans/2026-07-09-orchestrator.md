# Orchestrator (Phase 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the hourly-cycle orchestrator — `screener.py` (ranked candidate movers) and `orchestrator.py` (`run_cycle`: manage exits → screen entries → place paper/live orders → persist), wiring Phase 1 (client), Phase 2 (store), Phase 3 (engine).

**Architecture:** `Orchestrator(store, client, engine, get_indicators, get_candidates, now_provider)` — every collaborator injected, so `run_cycle` is fully unit-testable with fakes (no network/LLM/broker). Exits are managed before entries; the engine runs on every open position and on the top-N screener candidates that fit free slots+capital; paper exits mark-to-market against the day high/low.

**Tech Stack:** Python 3.10+, standard library, `pytest`. Reuses (does not vendor) the sibling `StockAnalayze` `groww_intraday_screener.py` via its venv. Depends on the in-repo `groww_client.py`, `store.py`, `decision_engine.py`, `indicators.py`.

## Global Constraints

- Every collaborator is a constructor argument; unit tests inject fakes and never hit the network, the LLM, or the broker. Tests may use a real in-memory `Store(":memory:")`.
- Prices/levels the orchestrator reads come from the indicator JSON: LTP `indicators["price"]["last"]`, day high `["price"]["day_high"]`, day low `["price"]["day_low"]`, session `indicators["session"]["bars_remaining"]` / `["minutes_to_squareoff"]`. The broker client is used only to `authenticate` and to place/close orders.
- Hard caps checked before every entry: never exceed `max_open_positions`, never let `deployed_capital` exceed `total_pool`, never size above `capital_per_position`.
- Entry gate: action ∈ ENTRY_ACTIONS AND `trade_quality ≥ 60` AND `risk_reward ≥ 1.5` AND `entry` and `stop_loss` present.
- Exits before entries. If both target and stop appear breached in one bar, the STOP takes precedence.
- `is_paused` config is an honored kill switch (no trading). Every decision (entered, rejected, skipped) is persisted. A per-name failure is isolated and recorded; an unhandled cycle failure marks the run FAILED and re-raises.
- `ScreenerError` subclasses `DecisionEngineError` (the shared error base from Phase 3).

## Constants (defined in `orchestrator.py`)

`MIN_TRADE_QUALITY = 60`, `MIN_RISK_REWARD = 1.5`, `SQUAREOFF_BARS = 1`, `SQUAREOFF_MINUTES = 15`, `ENTRY_ACTIONS = ("BUY_NOW", "BUY_ON_PULLBACK", "BUY_ON_BREAKOUT", "SHORT_NOW")`, `SHORT_ACTIONS = ("SHORT_NOW",)`, `SLOT_HEADROOM = 3`.

---

### Task 1: `screener.py` — `get_candidates` adapter

**Files:**
- Create: `screener.py`
- Test: `tests/test_screener.py`

**Interfaces:**
- Produces: `ScreenerError(DecisionEngineError)`; `DEFAULT_PYTHON`/`DEFAULT_SCRIPT` (env `SCREENER_PYTHON`/`SCREENER_SCRIPT`, StockAnalayze defaults); `_default_runner(argv, cwd) -> (rc, out, err)`; `get_candidates(direction="up", top=15, min_price=50.0, min_mcap_cr=1000.0, runner=_default_runner) -> list[dict]` — runs the screener, returns the `picks` list from its JSON, raises `ScreenerError` on non-zero exit / empty output / bad JSON / missing `picks`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_screener.py`:

```python
import json

import pytest

from screener import get_candidates, ScreenerError
from decision_engine import DecisionEngineError


def _runner_factory(rc, out, err=""):
    def runner(argv, cwd):
        runner.argv = argv
        runner.cwd = cwd
        return (rc, out, err)
    return runner


def test_screener_error_is_decision_engine_error():
    assert issubclass(ScreenerError, DecisionEngineError)


def test_get_candidates_returns_picks():
    payload = {"picks": [{"symbol": "RELIANCE", "ltp": 2456.7, "change_pct": 2.1},
                         {"symbol": "TCS", "ltp": 3820.0, "change_pct": 1.4}]}
    runner = _runner_factory(0, json.dumps(payload))
    picks = get_candidates(direction="up", top=5, runner=runner)
    assert [p["symbol"] for p in picks] == ["RELIANCE", "TCS"]
    assert "--direction" in runner.argv and "up" in runner.argv
    assert "--top" in runner.argv and "5" in runner.argv


def test_get_candidates_nonzero_exit_raises():
    with pytest.raises(ScreenerError, match="exit"):
        get_candidates(runner=_runner_factory(1, "", "boom"))


def test_get_candidates_empty_raises():
    with pytest.raises(ScreenerError, match="empty"):
        get_candidates(runner=_runner_factory(0, "  "))


def test_get_candidates_bad_json_raises():
    with pytest.raises(ScreenerError, match="parse|JSON"):
        get_candidates(runner=_runner_factory(0, "not json {"))


def test_get_candidates_missing_picks_raises():
    with pytest.raises(ScreenerError, match="picks"):
        get_candidates(runner=_runner_factory(0, json.dumps({"error": "x"})))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_screener.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'screener'`

- [ ] **Step 3: Implement `screener.py`**

```python
"""Candidate provider — runs the sibling StockAnalayze groww movers screener and returns its
ranked picks. Same subprocess pattern as indicators.py. See
docs/superpowers/specs/2026-07-09-orchestrator-design.md."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Callable

from decision_engine import DecisionEngineError

_STOCKANALYZE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze"
DEFAULT_PYTHON = os.environ.get("SCREENER_PYTHON", f"{_STOCKANALYZE}/.venv/bin/python")
DEFAULT_SCRIPT = os.environ.get("SCREENER_SCRIPT", f"{_STOCKANALYZE}/groww_intraday_screener.py")


class ScreenerError(DecisionEngineError):
    """Candidate screen failed: non-zero exit, empty output, bad JSON, or missing picks."""


def _default_runner(argv: list[str], cwd: str) -> tuple[int, str, str]:
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def get_candidates(direction: str = "up", top: int = 15, min_price: float = 50.0,
                   min_mcap_cr: float = 1000.0,
                   runner: Callable[[list[str], str], tuple[int, str, str]] = _default_runner
                   ) -> list[dict[str, Any]]:
    argv = [DEFAULT_PYTHON, DEFAULT_SCRIPT, "--direction", direction, "--top", str(top),
            "--min-price", str(min_price), "--min-mcap-cr", str(min_mcap_cr)]
    cwd = os.path.dirname(DEFAULT_SCRIPT)
    rc, out, err = runner(argv, cwd)
    if rc != 0:
        raise ScreenerError(f"screener exit {rc}: {err.strip()}")
    if not out or not out.strip():
        raise ScreenerError("screener returned empty output")
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        raise ScreenerError(f"could not parse screener JSON: {e}") from e
    if "picks" not in data:
        raise ScreenerError(f"screener output missing 'picks': {list(data)[:5]}")
    return data["picks"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_screener.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add screener.py tests/test_screener.py
git commit -m "Add screener get_candidates adapter"
```

---

### Task 2: Orchestrator scaffolding + pure helpers

**Files:**
- Create: `orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Produces: the constants above; `Orchestrator.__init__(self, store, client, engine, get_indicators, get_candidates, now_provider=_utc_now)`; pure helpers `_passes_entry_gate(decision) -> bool`, `_size_quantity(entry, capital_per_position) -> int`, `_should_square_off(indicators) -> bool`, `_position_side(action) -> str`, `_ltp(indicators)`, `_day_high(indicators)`, `_day_low(indicators)`. `run_cycle` is added in later tasks (a stub returning `{}` here is fine only if no test calls it — but Task 5 fully implements it; scaffold `run_cycle` as raising `NotImplementedError` so Task 5 replaces it).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_orchestrator.py`:

```python
import pytest

from decision_engine import Decision
from orchestrator import (Orchestrator, MIN_TRADE_QUALITY, MIN_RISK_REWARD,
                          _passes_entry_gate, _size_quantity, _should_square_off,
                          _position_side)


def _decision(action="BUY_NOW", tq=80, rr=2.0, entry=100.0, stop=95.0):
    return Decision(action=action, confidence=75, trade_quality=tq, entry=entry,
                    stop_loss=stop, target1=110.0, target2=None, target3=None,
                    risk_reward=rr, expected_move_pct=1.0, invalidation="x",
                    rationale="y", news_catalyst=None, raw_response="{}")


def test_entry_gate_accepts_good_buy():
    assert _passes_entry_gate(_decision(action="BUY_NOW", tq=80, rr=2.0)) is True
    assert _passes_entry_gate(_decision(action="SHORT_NOW", tq=70, rr=1.6)) is True


def test_entry_gate_rejects_low_quality_low_rr_and_wait():
    assert _passes_entry_gate(_decision(tq=50)) is False           # quality < 60
    assert _passes_entry_gate(_decision(rr=1.0)) is False           # R:R < 1.5
    assert _passes_entry_gate(_decision(action="WAIT")) is False    # non-entry action
    assert _passes_entry_gate(_decision(action="HOLD")) is False
    assert _passes_entry_gate(_decision(entry=None)) is False       # missing entry


def test_size_quantity():
    assert _size_quantity(100.0, 1000.0) == 10
    assert _size_quantity(300.0, 1000.0) == 3      # floor
    assert _size_quantity(2000.0, 1000.0) == 0     # too pricey


def test_position_side():
    assert _position_side("BUY_NOW") == "LONG"
    assert _position_side("BUY_ON_PULLBACK") == "LONG"
    assert _position_side("SHORT_NOW") == "SHORT"


def test_should_square_off_near_close():
    assert _should_square_off({"session": {"bars_remaining": 1, "minutes_to_squareoff": 40}}) is True
    assert _should_square_off({"session": {"bars_remaining": 5, "minutes_to_squareoff": 10}}) is True
    assert _should_square_off({"session": {"bars_remaining": 6, "minutes_to_squareoff": 90}}) is False
    assert _should_square_off({}) is False   # missing session → not near close
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'orchestrator'`

- [ ] **Step 3: Implement `orchestrator.py` scaffolding**

```python
"""The hourly-cycle orchestrator: manage exits, screen entries, place paper/live orders,
persist state. Wires Phase 1 (client), Phase 2 (store), Phase 3 (engine). Every collaborator
is injected. See docs/superpowers/specs/2026-07-09-orchestrator-design.md."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

MIN_TRADE_QUALITY = 60
MIN_RISK_REWARD = 1.5
SQUAREOFF_BARS = 1
SQUAREOFF_MINUTES = 15
ENTRY_ACTIONS = ("BUY_NOW", "BUY_ON_PULLBACK", "BUY_ON_BREAKOUT", "SHORT_NOW")
SHORT_ACTIONS = ("SHORT_NOW",)
SLOT_HEADROOM = 3


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _passes_entry_gate(decision) -> bool:
    return (decision.action in ENTRY_ACTIONS
            and decision.trade_quality is not None and decision.trade_quality >= MIN_TRADE_QUALITY
            and decision.risk_reward is not None and decision.risk_reward >= MIN_RISK_REWARD
            and decision.entry is not None and decision.stop_loss is not None)


def _size_quantity(entry: float, capital_per_position: float) -> int:
    if entry <= 0:
        return 0
    return int(math.floor(capital_per_position / entry))


def _position_side(action: str) -> str:
    return "SHORT" if action in SHORT_ACTIONS else "LONG"


def _should_square_off(indicators: dict) -> bool:
    session = indicators.get("session") or {}
    bars = session.get("bars_remaining")
    mins = session.get("minutes_to_squareoff")
    if bars is not None and bars <= SQUAREOFF_BARS:
        return True
    if mins is not None and mins <= SQUAREOFF_MINUTES:
        return True
    return False


def _ltp(indicators: dict) -> float:
    return float(indicators["price"]["last"])


def _day_high(indicators: dict) -> float:
    return float(indicators["price"]["day_high"])


def _day_low(indicators: dict) -> float:
    return float(indicators["price"]["day_low"])


class Orchestrator:
    def __init__(self, store, client, engine, get_indicators: Callable[[str], dict],
                 get_candidates: Callable[..., list], now_provider: Callable[[], datetime] = _utc_now):
        self.store = store
        self.client = client
        self.engine = engine
        self.get_indicators = get_indicators
        self.get_candidates = get_candidates
        self.now_provider = now_provider

    def run_cycle(self) -> dict:
        raise NotImplementedError  # implemented in Task 5
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "Scaffold Orchestrator with entry-gate/sizing/square-off helpers"
```

---

### Task 3: `_manage_positions` — exits (square-off, target/stop, signal)

**Files:**
- Modify: `orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: the helpers from Task 2, a Phase 2 `Store`, a `client` with `place_order`, a `engine` with `decide`.
- Produces: `Orchestrator._close_position(self, position, exit_price, reason, indicators) -> None` (computes P&L by side, places the exit order via `client.place_order` for the opposite transaction, `store.record_order`, `store.close_position`); `Orchestrator._realized_pnl(side, entry, exit_price, qty) -> float`; `Orchestrator._manage_positions(self, run_id) -> int` (returns number of exits). For each open position: fetch indicators (isolated try/except → on failure record a skipped decision and continue), square-off check, then stop-before-target mark-to-market, else `engine.decide` with held-position context and exit on an exit action. Records a decision for every managed position.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
from store import Store


class _FakeClient:
    def __init__(self, mode="paper"):
        self.mode = mode
        self.orders = []
        self.oco = []

    def authenticate(self):
        pass

    def place_order(self, **kw):
        self.orders.append(kw)
        oid = f"PAPER-{len(self.orders)}"
        return {"order_id": oid, "status": "COMPLETE", "price": kw.get("price"), "mode": self.mode}

    def place_oco_order(self, **kw):
        self.oco.append(kw)
        return {"order_id": f"PAPER-OCO-{len(self.oco)}", "status": "ACTIVE", "mode": self.mode}


class _FakeEngine:
    def __init__(self, decision):
        self.decision = decision
        self.calls = []

    def decide(self, symbol, indicators, position=None):
        self.calls.append((symbol, position))
        return self.decision


def _indic(symbol="RELIANCE", last=100.0, high=100.0, low=100.0, bars=5, mins=120):
    return {"symbol": symbol, "price": {"last": last, "day_high": high, "day_low": low},
            "session": {"bars_remaining": bars, "minutes_to_squareoff": mins}}


def _orch(store, client, engine, indic_map, candidates=None):
    return Orchestrator(store, client, engine,
                        get_indicators=lambda s: indic_map[s],
                        get_candidates=lambda **kw: candidates or [])


def test_manage_closes_long_on_target():
    store = Store(":memory:")
    pid = store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    client, engine = _FakeClient(), _FakeEngine(_decision(action="HOLD"))
    orch = _orch(store, client, engine, {"RELIANCE": _indic(last=108, high=112, low=99)})
    run_id = store.start_run("paper")
    exits = orch._manage_positions(run_id)
    assert exits == 1
    p = store.get_position(pid)
    assert p.status == "CLOSED"
    assert p.exit_price == 110.0 and p.exit_reason == "TARGET"
    assert p.realized_pnl == pytest.approx((110.0 - 100.0) * 10)


def test_manage_stop_takes_precedence_over_target():
    store = Store(":memory:")
    pid = store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"RELIANCE": _indic(last=100, high=112, low=94)})  # both breached
    run_id = store.start_run("paper")
    orch._manage_positions(run_id)
    p = store.get_position(pid)
    assert p.exit_reason == "STOP" and p.exit_price == 95.0


def test_manage_square_off_near_close():
    store = Store(":memory:")
    store.open_position(symbol="TCS", exchange="NSE", side="LONG", quantity=5,
                        entry_price=200.0, target_price=999.0, stop_loss=1.0, mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"TCS": _indic(symbol="TCS", last=205, high=206, low=204, bars=1)})
    run_id = store.start_run("paper")
    orch._manage_positions(run_id)
    p = store.get_open_positions()
    assert p == []  # squared off despite target/stop not breached
    closed = store.get_run(run_id)  # sanity: run exists
    assert closed is not None


def test_manage_signal_exit_on_sell():
    store = Store(":memory:")
    store.open_position(symbol="TCS", exchange="NSE", side="LONG", quantity=5,
                        entry_price=200.0, target_price=999.0, stop_loss=1.0, mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="SELL_NOW")),
                 {"TCS": _indic(symbol="TCS", last=210, high=211, low=209, bars=5)})
    run_id = store.start_run("paper")
    exits = orch._manage_positions(run_id)
    assert exits == 1
    assert store.get_open_positions() == []


def test_manage_hold_keeps_position():
    store = Store(":memory:")
    store.open_position(symbol="TCS", exchange="NSE", side="LONG", quantity=5,
                        entry_price=200.0, target_price=999.0, stop_loss=1.0, mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                 {"TCS": _indic(symbol="TCS", last=205, high=206, low=204, bars=5)})
    run_id = store.start_run("paper")
    exits = orch._manage_positions(run_id)
    assert exits == 0
    assert len(store.get_open_positions()) == 1


def test_manage_indicator_failure_is_isolated():
    store = Store(":memory:")
    store.open_position(symbol="BAD", exchange="NSE", side="LONG", quantity=5,
                        entry_price=200.0, target_price=999.0, stop_loss=1.0, mode="paper")

    def boom(symbol):
        raise RuntimeError("no data")

    orch = Orchestrator(store, _FakeClient(), _FakeEngine(_decision(action="HOLD")),
                        get_indicators=boom, get_candidates=lambda **kw: [])
    run_id = store.start_run("paper")
    exits = orch._manage_positions(run_id)   # must not raise
    assert exits == 0
    assert len(store.get_open_positions()) == 1
    decs = store.get_decisions_for_run(run_id)
    assert any("no data" in (d.reason or "") for d in decs)   # recorded the skip
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL — `AttributeError: 'Orchestrator' object has no attribute '_manage_positions'`

- [ ] **Step 3: Implement**

Add to `orchestrator.py` (inside `Orchestrator`), and `import json` at the top of the module:

```python
    @staticmethod
    def _realized_pnl(side: str, entry: float, exit_price: float, qty: int) -> float:
        return (exit_price - entry) * qty if side == "LONG" else (entry - exit_price) * qty

    def _close_position(self, position, exit_price: float, reason: str) -> None:
        txn = "SELL" if position.side == "LONG" else "BUY"
        order = self.client.place_order(
            symbol=position.symbol, exchange=position.exchange, transaction_type=txn,
            quantity=position.quantity, order_type="MARKET", product="MIS")
        self.store.record_order(
            broker_order_id=order["order_id"], symbol=position.symbol, transaction_type=txn,
            quantity=position.quantity, order_type="MARKET", price=exit_price,
            status=order.get("status", "COMPLETE"), mode=self.client.mode,
            position_id=position.id, raw_json=json.dumps(order, default=str))
        pnl = self._realized_pnl(position.side, position.entry_price, exit_price, position.quantity)
        self.store.close_position(position.id, exit_price=exit_price, exit_reason=reason,
                                  realized_pnl=pnl)

    def _exit_level(self, position, indicators):
        """Return (exit_price, reason) if this position should exit now, else None."""
        if _should_square_off(indicators):
            return _ltp(indicators), "SQUARE_OFF"
        high, low = _day_high(indicators), _day_low(indicators)
        if position.side == "LONG":
            if position.stop_loss is not None and low <= position.stop_loss:
                return position.stop_loss, "STOP"
            if position.target_price is not None and high >= position.target_price:
                return position.target_price, "TARGET"
        else:  # SHORT
            if position.stop_loss is not None and high >= position.stop_loss:
                return position.stop_loss, "STOP"
            if position.target_price is not None and low <= position.target_price:
                return position.target_price, "TARGET"
        return None

    def _manage_positions(self, run_id: int) -> int:
        exits = 0
        for position in self.store.get_open_positions():
            try:
                indicators = self.get_indicators(position.symbol)
            except Exception as e:
                self.store.record_decision(run_id=run_id, symbol=position.symbol,
                                           action="SKIP", reason=f"indicator error: {e}")
                continue
            level = self._exit_level(position, indicators)
            if level is not None:
                exit_price, reason = level
                self.store.record_decision(run_id=run_id, symbol=position.symbol,
                                           action="EXIT", reason=reason, position_id=position.id)
                self._close_position(position, exit_price, reason)
                exits += 1
                continue
            ctx = {"side": position.side, "quantity": position.quantity,
                   "entry_price": position.entry_price,
                   "unrealized_pnl_pct": round(
                       self._realized_pnl(position.side, position.entry_price, _ltp(indicators), 1)
                       / position.entry_price * 100, 2)}
            decision = self.engine.decide(position.symbol, indicators, position=ctx)
            exit_actions = ("SELL_NOW",) if position.side == "LONG" else ("BUY_NOW",)
            is_exit = decision.action in exit_actions
            self.store.record_decision(run_id=run_id, symbol=position.symbol,
                                       action=decision.action, score=decision.trade_quality,
                                       reason=decision.rationale, position_id=position.id,
                                       raw_json=decision.raw_response)
            if is_exit:
                self._close_position(position, _ltp(indicators), "SIGNAL")
                exits += 1
        return exits
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: PASS (11 tests total in this file)

- [ ] **Step 5: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "Add _manage_positions: square-off, target/stop, signal exits"
```

---

### Task 4: `_screen_and_enter` — entry gate, sizing, caps, place entry+OCO

**Files:**
- Modify: `orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: the helpers, `store`, `client` (`place_order`+`place_oco_order`), `engine`, `get_candidates`, `get_indicators`.
- Produces: `Orchestrator._place_entry(self, run_id, symbol, decision) -> None` (sizes, places entry order + OCO, opens the position, records both orders + links the decision's position_id); `Orchestrator._screen_and_enter(self, run_id) -> tuple[int, int]` (returns `(candidates_screened, entries)`). Enforces free-slot and free-capital caps; drops already-held symbols; records every decision.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
def _cands(*syms):
    return [{"symbol": s, "ltp": 100.0, "change_pct": 2.0} for s in syms]


def _cfg(store, **kw):
    store.update_config(**kw)


def test_enter_opens_position_on_good_buy():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    client, engine = _FakeClient(), _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0,
                                                          entry=100.0, stop=95.0))
    orch = _orch(store, client, engine, {"RELIANCE": _indic()}, candidates=_cands("RELIANCE"))
    run_id = store.start_run("paper")
    screened, entries = orch._screen_and_enter(run_id)
    assert screened == 1 and entries == 1
    p = store.get_open_positions()[0]
    assert p.symbol == "RELIANCE" and p.side == "LONG"
    assert p.quantity == 100          # floor(10000 / 100)
    assert len(client.orders) == 1 and len(client.oco) == 1


def test_enter_rejects_failing_gate_but_records_decision():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="WAIT", tq=40)),
                 {"RELIANCE": _indic()}, candidates=_cands("RELIANCE"))
    run_id = store.start_run("paper")
    screened, entries = orch._screen_and_enter(run_id)
    assert entries == 0
    assert store.get_open_positions() == []
    assert len(store.get_decisions_for_run(run_id)) == 1   # still recorded


def test_enter_respects_free_slots():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=1,
         capital_per_position=10000.0)
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0)),
                 {"A": _indic("A"), "B": _indic("B")}, candidates=_cands("A", "B"))
    run_id = store.start_run("paper")
    _, entries = orch._screen_and_enter(run_id)
    assert entries == 1                       # only 1 slot
    assert store.count_open_positions() == 1


def test_enter_skips_when_no_free_capital():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=5000.0, max_open_positions=3,
         capital_per_position=10000.0)   # pool < capital_per_position
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0)),
                 {"A": _indic("A")}, candidates=_cands("A"))
    run_id = store.start_run("paper")
    _, entries = orch._screen_and_enter(run_id)
    assert entries == 0


def test_enter_skips_already_held_symbol():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=1, entry_price=100.0,
                        mode="paper")
    orch = _orch(store, _FakeClient(), _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0)),
                 {"A": _indic("A")}, candidates=_cands("A"))
    run_id = store.start_run("paper")
    screened, entries = orch._screen_and_enter(run_id)
    assert entries == 0   # A already held → not re-entered
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL — `AttributeError: 'Orchestrator' object has no attribute '_screen_and_enter'`

- [ ] **Step 3: Implement**

Add to `orchestrator.py` (inside `Orchestrator`):

```python
    def _place_entry(self, run_id: int, symbol: str, decision, indicators, mode: str) -> bool:
        cfg = self.store.get_config()
        qty = _size_quantity(decision.entry, cfg.capital_per_position)
        free_capital = cfg.total_pool - self.store.deployed_capital()
        if qty < 1 or qty * decision.entry > free_capital:
            self.store.record_decision(run_id=run_id, symbol=symbol, action=decision.action,
                                       score=decision.trade_quality,
                                       reason="rejected: sizing/capital", raw_json=decision.raw_response)
            return False
        side = _position_side(decision.action)
        txn = "BUY" if side == "LONG" else "SELL"
        entry_order = self.client.place_order(
            symbol=symbol, exchange="NSE", transaction_type=txn, quantity=qty,
            order_type="MARKET", product="MIS")
        oco = self.client.place_oco_order(
            symbol=symbol,
            entry={"transaction_type": txn, "quantity": qty, "order_type": "MARKET"},
            target={"trigger_price": decision.target1, "order_type": "LIMIT",
                    "price": decision.target1},
            stop_loss={"trigger_price": decision.stop_loss, "order_type": "LIMIT",
                       "price": decision.stop_loss})
        pid = self.store.open_position(
            symbol=symbol, exchange="NSE", side=side, quantity=qty, entry_price=decision.entry,
            target_price=decision.target1, stop_loss=decision.stop_loss,
            entry_order_id=entry_order["order_id"], oco_order_id=oco["order_id"], mode=mode)
        for o, otype in ((entry_order, "MARKET"), (oco, "OCO")):
            self.store.record_order(
                broker_order_id=o["order_id"], symbol=symbol, transaction_type=txn,
                quantity=qty, order_type=otype, price=decision.entry,
                status=o.get("status", "COMPLETE"), mode=mode, position_id=pid,
                raw_json=json.dumps(o, default=str))
        self.store.record_decision(run_id=run_id, symbol=symbol, action=decision.action,
                                   score=decision.trade_quality, reason=decision.rationale,
                                   entry_price=decision.entry, target_price=decision.target1,
                                   stop_loss=decision.stop_loss, position_id=pid,
                                   raw_json=decision.raw_response)
        return True

    def _screen_and_enter(self, run_id: int) -> tuple[int, int]:
        cfg = self.store.get_config()
        free_slots = cfg.max_open_positions - self.store.count_open_positions()
        free_capital = cfg.total_pool - self.store.deployed_capital()
        if free_slots <= 0 or free_capital < cfg.capital_per_position:
            return 0, 0
        held = {p.symbol for p in self.store.get_open_positions()}
        candidates = self.get_candidates(direction="up", top=free_slots + SLOT_HEADROOM)
        screened = 0
        entries = 0
        for cand in candidates:
            if entries >= free_slots:
                break
            symbol = cand["symbol"]
            if symbol in held:
                continue
            screened += 1
            try:
                indicators = self.get_indicators(symbol)
                decision = self.engine.decide(symbol, indicators, position=None)
            except Exception as e:
                self.store.record_decision(run_id=run_id, symbol=symbol, action="SKIP",
                                           reason=f"decision error: {e}")
                continue
            if not _passes_entry_gate(decision):
                self.store.record_decision(run_id=run_id, symbol=symbol, action=decision.action,
                                           score=decision.trade_quality, reason=decision.rationale,
                                           raw_json=decision.raw_response)
                continue
            if self._place_entry(run_id, symbol, decision, indicators, cfg.mode):
                entries += 1
                held.add(symbol)
        return screened, entries
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: PASS (16 tests total in this file)

- [ ] **Step 5: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "Add _screen_and_enter: entry gate, sizing, caps, entry+OCO"
```

---

### Task 5: `run_cycle` — wire it together with the paused check and FAILED guard

**Files:**
- Modify: `orchestrator.py`
- Test: `tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `_manage_positions`, `_screen_and_enter`, the store's config/run methods, `client.authenticate`.
- Produces: `Orchestrator.run_cycle(self) -> dict` (replaces the `NotImplementedError` stub). Starts a run, honors `is_paused`, authenticates, manages positions then screens entries, finishes the run SUCCESS with counts; on any unhandled exception marks the run FAILED and re-raises. Returns a summary dict `{"run_id", "status", "exits", "entries", "candidates"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_orchestrator.py`:

```python
def test_run_cycle_paused_does_nothing():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=3,
         capital_per_position=10000.0, is_paused=True)
    client = _FakeClient()
    orch = _orch(store, client, _FakeEngine(_decision()), {}, candidates=_cands("A"))
    summary = orch.run_cycle()
    assert summary["status"] == "SUCCESS"
    assert summary["entries"] == 0 and summary["exits"] == 0
    assert client.orders == []                     # no trading while paused
    assert store.get_run(summary["run_id"]).summary == "paused"


def test_run_cycle_enters_then_reports():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=2,
         capital_per_position=10000.0)
    client = _FakeClient()
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, entry=100.0, stop=95.0))
    orch = _orch(store, client, engine, {"A": _indic("A")}, candidates=_cands("A"))
    summary = orch.run_cycle()
    assert summary["status"] == "SUCCESS"
    assert summary["entries"] == 1
    run = store.get_run(summary["run_id"])
    assert run.status == "SUCCESS" and run.num_actions == 1


def test_run_cycle_exit_frees_slot_for_entry():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=1,
         capital_per_position=10000.0)
    # hold one position that will square off this cycle (bars=1)
    store.open_position(symbol="OLD", exchange="NSE", side="LONG", quantity=5,
                        entry_price=200.0, target_price=999.0, stop_loss=1.0, mode="paper")
    engine = _FakeEngine(_decision(action="BUY_NOW", tq=80, rr=2.0, entry=100.0, stop=95.0))
    indic = {"OLD": _indic("OLD", bars=1), "NEW": _indic("NEW", bars=5)}
    orch = _orch(store, _FakeClient(), engine, indic, candidates=_cands("NEW"))
    summary = orch.run_cycle()
    assert summary["exits"] == 1 and summary["entries"] == 1   # OLD squared off → NEW entered
    open_syms = {p.symbol for p in store.get_open_positions()}
    assert open_syms == {"NEW"}


def test_run_cycle_marks_failed_and_reraises():
    store = Store(":memory:")
    _cfg(store, mode="paper", total_pool=100000.0, max_open_positions=1,
         capital_per_position=10000.0)

    class BoomClient(_FakeClient):
        def authenticate(self):
            raise RuntimeError("auth blew up")

    orch = _orch(store, BoomClient(), _FakeEngine(_decision()), {}, candidates=[])
    with pytest.raises(RuntimeError, match="auth blew up"):
        orch.run_cycle()
    # the run was marked FAILED, not left RUNNING
    runs_failed = [r for r in [store.get_run(1)] if r.status == "FAILED"]
    assert len(runs_failed) == 1
    assert "auth blew up" in (store.get_run(1).error or "")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: FAIL — `NotImplementedError` from the stub

- [ ] **Step 3: Implement `run_cycle`**

Replace the `run_cycle` stub in `orchestrator.py` with:

```python
    def run_cycle(self) -> dict:
        run_id = self.store.start_run(self.client.mode)
        cfg = self.store.get_config()
        if cfg.is_paused:
            self.store.finish_run(run_id, "SUCCESS", num_candidates=0, num_actions=0,
                                  summary="paused")
            return {"run_id": run_id, "status": "SUCCESS", "exits": 0, "entries": 0,
                    "candidates": 0}
        try:
            self.client.authenticate()
            exits = self._manage_positions(run_id)
            candidates, entries = self._screen_and_enter(run_id)
            self.store.finish_run(run_id, "SUCCESS", num_candidates=candidates,
                                  num_actions=exits + entries,
                                  summary=f"{entries} entries, {exits} exits")
            return {"run_id": run_id, "status": "SUCCESS", "exits": exits, "entries": entries,
                    "candidates": candidates}
        except Exception as e:
            self.store.finish_run(run_id, "FAILED", error=str(e))
            raise
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_orchestrator.py -v`
Expected: PASS (20 tests total in this file)

- [ ] **Step 5: Commit**

```bash
git add orchestrator.py tests/test_orchestrator.py
git commit -m "Add run_cycle: paused check, manage+screen, FAILED guard"
```

---

### Task 6: Manual smoke script (one real paper cycle) + README

**Files:**
- Create: `scripts/smoke_test_cycle.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `Orchestrator`, `Store`, `GrowwClient`, `DecisionEngine`, `get_indicators`, `get_candidates`.
- Produces: nothing new for later phases; a manual end-to-end paper-cycle runner + docs.

- [ ] **Step 1: Run the full suite to confirm nothing regressed**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — all prior phases (Groww client, store, decision engine, indicators, screener) plus the orchestrator's tests, all green.

- [ ] **Step 2: Write the smoke script**

Create `scripts/smoke_test_cycle.py`:

```python
#!/usr/bin/env python3
"""Manual, not-CI smoke test: run ONE real paper trading cycle end to end against a temp DB,
the real GrowwClient in PAPER mode (no real orders), the real decision engine, and the real
screener/indicators. Prints the run summary.

Usage: .venv/bin/python scripts/smoke_test_cycle.py
Requires ANTHROPIC_API_KEY (or `ant` login), Groww creds for read-only auth, and the sibling
StockAnalayze venv. PLACES NO REAL ORDERS (paper mode).
"""
from __future__ import annotations

import sys
import tempfile

sys.path.insert(0, ".")

from decision_engine import DecisionEngine
from groww_client import GrowwClient
from indicators import get_indicators
from orchestrator import Orchestrator
from screener import get_candidates
from store import Store


def main() -> None:
    db_path = tempfile.mktemp(suffix=".db")
    store = Store(db_path)
    store.update_config(mode="paper", total_pool=100000.0, max_open_positions=3,
                        capital_per_position=20000.0, is_paused=False)
    orch = Orchestrator(
        store=store, client=GrowwClient(mode="paper"), engine=DecisionEngine(use_web_search=True),
        get_indicators=get_indicators, get_candidates=get_candidates)
    print(f"running one paper cycle (temp db {db_path}) ...")
    summary = orch.run_cycle()
    print(f"cycle: {summary['status']} - {summary['entries']} entries, "
          f"{summary['exits']} exits, {summary['candidates']} candidates screened")
    for p in store.get_open_positions():
        print(f"  OPEN {p.side} {p.quantity} {p.symbol} @ {p.entry_price} "
              f"target {p.target_price} stop {p.stop_loss}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Add a Phase 4 section to `README.md`**

Append to `README.md`:

```markdown
## Phase 4: Orchestrator

`orchestrator.py` runs one trading cycle: manage open positions (square-off / target-stop /
signal exits) → screen candidates (`screener.py`) → decide (Phase 3) → size + place paper/live
orders (Phase 1) → persist everything (Phase 2). `Orchestrator.run_cycle()` is the entry point
Phase 5's cron will call hourly. See `docs/superpowers/specs/2026-07-09-orchestrator-design.md`.

### Test

\`\`\`bash
.venv/bin/python -m pytest tests/test_orchestrator.py tests/test_screener.py -v
\`\`\`

### Run one real paper cycle (no real orders)

\`\`\`bash
.venv/bin/python scripts/smoke_test_cycle.py
\`\`\`
```

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_test_cycle.py README.md
git commit -m "Add one-paper-cycle smoke script and Phase 4 README"
```

---

## Self-Review Notes

- **Spec coverage:** screener adapter (Task 1) · orchestrator scaffolding + entry-gate/sizing/square-off helpers (Task 2) · exits with square-off priority, stop-before-target, signal exit, fault isolation (Task 3) · entry gate + sizing + slot/capital caps + already-held drop + entry/OCO placement (Task 4) · `run_cycle` paused-check + manage-then-screen + SUCCESS/FAILED accounting + whole-cycle guard (Task 5) · one-paper-cycle smoke + README (Task 6). All spec sections map to a task.
- **Type consistency:** `Orchestrator.__init__` collaborators are used with the exact Phase 1/2/3 signatures (`store.open_position`/`close_position`/`record_order`/`record_decision`/`get_open_positions`/`count_open_positions`/`deployed_capital`/`get_config`/`start_run`/`finish_run`, `client.authenticate`/`place_order`/`place_oco_order`, `engine.decide`); constants defined once in Task 2 and reused; `_realized_pnl`/`_exit_level`/`_place_entry` signatures match across tasks.
- **No placeholders:** every step has complete runnable code and exact expected test counts (screener 6; orchestrator 5 → 11 → 16 → 20). The `run_cycle` stub raises `NotImplementedError` in Task 2 and is fully implemented in Task 5 — no test calls it before then.
- **Store-method check:** `record_decision` is called with `action`/`reason`/`position_id`/`score`/`entry_price`/`target_price`/`stop_loss`/`raw_json` — all optional-with-defaults params on the Phase 2 signature; `SKIP`/`EXIT` are free-text action strings (the store does not constrain the action enum), consistent with the store's schema.
