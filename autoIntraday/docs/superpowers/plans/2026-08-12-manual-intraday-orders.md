# Manual Intraday Orders Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the Analyze page to Manual Intraday and let it place real Groww orders — entry, plus a native OCO carrying the skill's target and stop — prefilled from the analysis, fully editable, independent of autoIntraday's config.

**Architecture:** `manual_broker.py` owns its own `GrowwClient` and its own paper/live mode. Pure helpers (side, sizing, economics, bracket validation) are separated from the I/O so they can be tested without a broker. `manual_order_job.py` runs detached: place entry → poll to fill → arm native OCO. Storage in two new tables, `manual_config` and `manual_orders`.

**Tech Stack:** Python 3, sqlite3, Streamlit, the Groww SDK via `GrowwClient`, pytest. Spec: `docs/superpowers/specs/2026-08-12-manual-intraday-orders-design.md`.

## Global Constraints

- Run tests with: `cd /Users/mohdsuhel/ai-mini-projects/autoIntraday && .venv/bin/python -m pytest tests/<file> -q` (no bare `python` on this machine).
- Manual Intraday NEVER reads autoIntraday's trading config for its mode. Its mode lives in `manual_config.mode` and defaults to `'paper'`.
- Do NOT import `orchestrator.py` from `manual_broker.py` — it would drag the whole trading engine in and defeat the separation. Duplicate the broker status constants with a comment saying why.
- All order construction goes through `GrowwClient.place_order` / `place_oco_order`. Never build an SDK payload here — `place_order` already translates `SL_M` to the buffered `SL` required by the 2026-08-03 NSE 16448 finding.
- New tables only. Never touch `positions` (autoIntraday's trade ledger) — see `tests/test_store.py::test_broker_snapshot_never_touches_the_trade_ledger`.
- Do NOT `git add` these files, which carry unrelated uncommitted work: `groww_client.py`, `swing_job.py`, `observe_job.py`, `tests/test_groww_client.py`, `tests/test_observe.py`, `tests/test_swing_economics.py`, `tests/test_swing_job.py`. Stage only the files each task names.

---

### Task 1: `manual_broker.py` — pure helpers plus the broker wrapper

**Files:**
- Create: `manual_broker.py`
- Test: `tests/test_manual_broker.py`

**Interfaces:**
- Produces:
  - `ManualBrokerError(Exception)`
  - `FILLED_STATES`, `REJECTED_STATES: tuple[str, ...]`; `is_filled(status) -> bool`, `is_rejected(status) -> bool`
  - `entry_txn(side) -> str` (`LONG`→`BUY`, `SHORT`→`SELL`), `exit_txn(side) -> str` (the reverse)
  - `side_from_verdict(verdict) -> str | None` — `LONG`, `SHORT`, or None when the verdict is not an entry instruction
  - `qty_for_capital(capital, price) -> int`
  - `order_economics(side, qty, entry, stop, target) -> dict` with keys `exposure, risk, reward, rr` (None where an input is missing)
  - `validate_bracket(side, entry, stop, target, quantity) -> None`, raising `ManualBrokerError`
  - `ManualBroker(mode="paper", client=None)` with `.place_entry`, `.order_status`, `.arm_oco`, `.modify_exits`, `.square_off`
- Tasks 3 and 4 consume all of the above.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_manual_broker.py`:

```python
"""Manual Intraday's broker layer: the arithmetic and the fat-finger guards are pure and
tested here; the I/O is exercised against a stub client."""
import pytest

from manual_broker import (ManualBroker, ManualBrokerError, entry_txn, exit_txn, is_filled,
                           is_rejected, order_economics, qty_for_capital, side_from_verdict,
                           validate_bracket)


# ---- pure helpers ---------------------------------------------------------------------

def test_entry_and_exit_transactions_per_side():
    assert entry_txn("LONG") == "BUY" and exit_txn("LONG") == "SELL"
    assert entry_txn("SHORT") == "SELL" and exit_txn("SHORT") == "BUY"


def test_unknown_side_raises():
    with pytest.raises(ManualBrokerError):
        entry_txn("SIDEWAYS")


@pytest.mark.parametrize("verdict", ["BUY NOW", "BUY ON PULLBACK", "IGNITION_BUY", "ADD",
                                     "MARKET EXCEPTION LONG"])
def test_buy_verdicts_map_to_long(verdict):
    assert side_from_verdict(verdict) == "LONG"


@pytest.mark.parametrize("verdict", ["SHORT NOW", "SHORT ON BREAKDOWN"])
def test_short_verdicts_map_to_short(verdict):
    assert side_from_verdict(verdict) == "SHORT"


@pytest.mark.parametrize("verdict", ["SELL NOW", "EXIT", "REDUCE", "HOLD", "WAIT", "NO TRADE",
                                     "", None])
def test_ambiguous_verdicts_map_to_nothing(verdict):
    """SELL/EXIT/REDUCE mean 'close the long you hold', NOT 'open a short'. Guessing here
    would open a brand-new short position on an exit instruction."""
    assert side_from_verdict(verdict) is None


def test_qty_for_capital_floors():
    assert qty_for_capital(30000, 1842.0) == 16          # 16.28 -> 16
    assert qty_for_capital(1000, 1842.0) == 0            # cannot afford one share


def test_qty_for_capital_handles_missing_price():
    assert qty_for_capital(30000, None) == 0
    assert qty_for_capital(30000, 0) == 0


def test_economics_for_a_long():
    e = order_economics("LONG", 10, entry=100.0, stop=95.0, target=120.0)
    assert e["exposure"] == 1000.0
    assert e["risk"] == 50.0                              # (100-95)*10
    assert e["reward"] == 200.0                           # (120-100)*10
    assert e["rr"] == pytest.approx(4.0)


def test_economics_for_a_short():
    e = order_economics("SHORT", 10, entry=100.0, stop=105.0, target=80.0)
    assert e["risk"] == 50.0 and e["reward"] == 200.0
    assert e["rr"] == pytest.approx(4.0)


def test_economics_missing_legs_are_none_not_zero():
    e = order_economics("LONG", 10, entry=100.0, stop=None, target=None)
    assert e["exposure"] == 1000.0
    assert e["risk"] is None and e["reward"] is None and e["rr"] is None


def test_validate_accepts_a_sane_long_and_short():
    validate_bracket("LONG", entry=100.0, stop=95.0, target=120.0, quantity=10)
    validate_bracket("SHORT", entry=100.0, stop=105.0, target=80.0, quantity=10)


def test_validate_rejects_inverted_long():
    """A long whose stop sits ABOVE entry fires the instant it arms."""
    with pytest.raises(ManualBrokerError, match="stop"):
        validate_bracket("LONG", entry=100.0, stop=105.0, target=120.0, quantity=10)
    with pytest.raises(ManualBrokerError, match="target"):
        validate_bracket("LONG", entry=100.0, stop=95.0, target=90.0, quantity=10)


def test_validate_rejects_inverted_short():
    with pytest.raises(ManualBrokerError, match="stop"):
        validate_bracket("SHORT", entry=100.0, stop=95.0, target=80.0, quantity=10)
    with pytest.raises(ManualBrokerError, match="target"):
        validate_bracket("SHORT", entry=100.0, stop=105.0, target=120.0, quantity=10)


def test_validate_rejects_nonpositive_quantity_and_entry():
    with pytest.raises(ManualBrokerError, match="quantity"):
        validate_bracket("LONG", entry=100.0, stop=95.0, target=120.0, quantity=0)
    with pytest.raises(ManualBrokerError, match="entry"):
        validate_bracket("LONG", entry=0, stop=95.0, target=120.0, quantity=10)


def test_status_predicates():
    assert is_filled("EXECUTED") and is_filled("complete")
    assert is_rejected("REJECTED") and is_rejected("cancelled")
    assert not is_filled("PENDING") and not is_rejected("PENDING")


# ---- broker wrapper against a stub client ----------------------------------------------

class _Client:
    def __init__(self, status="COMPLETE"):
        self.mode = "paper"
        self.calls = []
        self._status = status

    def place_order(self, **kw):
        self.calls.append(("place_order", kw))
        return {"order_id": "OID1", "status": self._status, "price": kw.get("price")}

    def place_oco_order(self, symbol, entry, target, stop_loss):
        self.calls.append(("place_oco", {"symbol": symbol, "entry": entry, "target": target,
                                         "stop_loss": stop_loss}))
        return {"order_id": "OCO1", "status": "ACTIVE"}

    def modify_oco_order(self, order_id, target, stop_loss):
        self.calls.append(("modify_oco", {"order_id": order_id, "target": target,
                                          "stop_loss": stop_loss}))
        return {"order_id": order_id, "status": "MODIFIED"}

    def cancel_order(self, order_id):
        self.calls.append(("cancel", {"order_id": order_id}))
        return {"order_id": order_id, "status": "CANCELLED"}

    def get_order_status(self, order_id):
        return {"order_id": order_id, "status": self._status, "reason": None}


def test_place_entry_sends_buy_for_a_long():
    c = _Client()
    ManualBroker(client=c).place_entry("KEI", "LONG", 10, "MARKET", None)
    name, kw = c.calls[0]
    assert name == "place_order"
    assert kw["transaction_type"] == "BUY" and kw["order_type"] == "MARKET"
    assert kw["quantity"] == 10 and kw["product"] == "MIS" and kw["symbol"] == "KEI"


def test_place_entry_sends_sell_and_a_limit_price_for_a_short():
    c = _Client()
    ManualBroker(client=c).place_entry("KEI", "SHORT", 5, "LIMIT", 1842.0)
    _, kw = c.calls[0]
    assert kw["transaction_type"] == "SELL" and kw["order_type"] == "LIMIT"
    assert kw["price"] == 1842.0


def test_arm_oco_uses_the_exit_transaction_and_both_legs():
    c = _Client()
    ManualBroker(client=c).arm_oco("KEI", "LONG", 10, target=1905.0, stop=1808.0)
    name, kw = c.calls[0]
    assert name == "place_oco"
    assert kw["entry"]["transaction_type"] == "SELL"      # SELL closes a long
    assert kw["entry"]["quantity"] == 10
    assert kw["target"]["trigger_price"] == 1905.0 and kw["target"]["price"] == 1905.0
    assert kw["stop_loss"]["trigger_price"] == 1808.0


def test_arm_oco_validates_before_sending():
    c = _Client()
    with pytest.raises(ManualBrokerError):
        ManualBroker(client=c).arm_oco("KEI", "LONG", 10, target=1700.0, stop=1808.0)
    assert c.calls == []                                  # nothing was sent


def test_square_off_cancels_the_oco_before_the_market_exit():
    c = _Client()
    ManualBroker(client=c).square_off("KEI", "LONG", 10, oco_order_id="OCO1")
    assert [n for n, _ in c.calls] == ["cancel", "place_order"]
    assert c.calls[1][1]["transaction_type"] == "SELL"
    assert c.calls[1][1]["order_type"] == "MARKET"


def test_square_off_aborts_if_the_oco_cancel_fails():
    """If the OCO survives, a market exit would flatten the position and then let the stop
    fire into a brand-new REVERSE position. Refuse rather than risk that."""
    class Bad(_Client):
        def cancel_order(self, order_id):
            raise RuntimeError("cancel rejected")
    c = Bad()
    with pytest.raises(ManualBrokerError, match="check the broker"):
        ManualBroker(client=c).square_off("KEI", "LONG", 10, oco_order_id="OCO1")
    assert [n for n, _ in c.calls] == []                  # no market exit was sent


def test_square_off_without_an_oco_just_exits():
    c = _Client()
    ManualBroker(client=c).square_off("KEI", "SHORT", 4, oco_order_id=None)
    assert [n for n, _ in c.calls] == ["place_order"]
    assert c.calls[0][1]["transaction_type"] == "BUY"


def test_rejected_entry_raises_with_the_reason():
    c = _Client(status="REJECTED")
    with pytest.raises(ManualBrokerError, match="REJECTED"):
        ManualBroker(client=c).place_entry("KEI", "LONG", 10, "MARKET", None)


def test_mode_defaults_to_paper():
    assert ManualBroker().mode == "paper"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_manual_broker.py -q`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'manual_broker'`

- [ ] **Step 3: Implement**

Create `manual_broker.py`:

```python
"""Manual Intraday's broker layer — places REAL Groww orders from the analysis page.

Deliberately independent of autoIntraday: it builds its OWN GrowwClient and takes its
paper/live mode from `manual_config`, never from the trading config. This page is the
operator's own desk, not part of the bot.

All order construction goes through GrowwClient, which already translates SL_M into the
buffered SL that Groww actually accepts (NSE 16448, proven 2026-08-03). Nothing here builds
an SDK payload.
See docs/superpowers/specs/2026-08-12-manual-intraday-orders-design.md."""
from __future__ import annotations

import math

# Broker status vocabulary. Duplicated from orchestrator rather than imported: these are facts
# about Groww, not autoIntraday policy, and importing the orchestrator would pull the entire
# trading engine into this page and defeat the separation this feature exists for.
REJECTED_STATES = ("REJECTED", "CANCELLED", "CANCELED", "FAILED", "REJECT", "EXPIRED")
FILLED_STATES = ("EXECUTED", "COMPLETE", "COMPLETED", "FILLED")

_ENTRY_TXN = {"LONG": "BUY", "SHORT": "SELL"}
_EXIT_TXN = {"LONG": "SELL", "SHORT": "BUY"}


class ManualBrokerError(Exception):
    """A manual order could not be placed, armed or closed."""


def is_filled(status) -> bool:
    return str(status or "").upper() in FILLED_STATES


def is_rejected(status) -> bool:
    return str(status or "").upper() in REJECTED_STATES


def entry_txn(side: str) -> str:
    try:
        return _ENTRY_TXN[str(side).upper()]
    except KeyError:
        raise ManualBrokerError(f"unknown side {side!r}; expected LONG or SHORT") from None


def exit_txn(side: str) -> str:
    try:
        return _EXIT_TXN[str(side).upper()]
    except KeyError:
        raise ManualBrokerError(f"unknown side {side!r}; expected LONG or SHORT") from None


def side_from_verdict(verdict) -> str | None:
    """The side a verdict instructs you to OPEN, or None when it does not instruct one.

    SELL / EXIT / REDUCE mean "close the position you hold", never "open a short" — mapping
    them to SHORT would open a brand-new position on an exit instruction. Anything ambiguous
    returns None and the operator picks the side explicitly."""
    v = str(verdict or "").upper()
    if not v:
        return None
    if "SHORT" in v:
        return "SHORT"
    if "BUY" in v or "LONG" in v or v.strip() == "ADD":
        return "LONG"
    return None


def qty_for_capital(capital, price) -> int:
    """Whole shares that `capital` buys at `price`. Unknown or unusable price -> 0."""
    try:
        cap, px = float(capital), float(price)
    except (TypeError, ValueError):
        return 0
    if px <= 0 or cap <= 0:
        return 0
    return int(math.floor(cap / px))


def order_economics(side, quantity, entry, stop, target) -> dict:
    """What the ticket is worth: exposure, money at risk to the stop, reward at the target,
    and the resulting R:R. A missing leg yields None — never 0, which would read as 'no risk'."""
    out = {"exposure": None, "risk": None, "reward": None, "rr": None}
    try:
        q, e = int(quantity), float(entry)
    except (TypeError, ValueError):
        return out
    if q <= 0 or e <= 0:
        return out
    long_ = str(side).upper() == "LONG"
    out["exposure"] = e * q
    if stop is not None:
        s = float(stop)
        out["risk"] = (e - s) * q if long_ else (s - e) * q
    if target is not None:
        t = float(target)
        out["reward"] = (t - e) * q if long_ else (e - t) * q
    if out["risk"] and out["reward"] is not None:
        out["rr"] = out["reward"] / out["risk"]
    return out


def validate_bracket(side, entry, stop, target, quantity) -> None:
    """Refuse a geometrically impossible bracket BEFORE anything reaches the broker. A long
    whose stop sits above entry fires the moment it arms; this is a fat-finger guard, not an
    opinion about the trade."""
    s = str(side).upper()
    if s not in _ENTRY_TXN:
        raise ManualBrokerError(f"unknown side {side!r}; expected LONG or SHORT")
    try:
        q = int(quantity)
    except (TypeError, ValueError):
        raise ManualBrokerError("quantity must be a whole number of shares") from None
    if q <= 0:
        raise ManualBrokerError("quantity must be greater than zero")
    try:
        e = float(entry)
    except (TypeError, ValueError):
        raise ManualBrokerError("entry price is required") from None
    if e <= 0:
        raise ManualBrokerError("entry price must be greater than zero")
    if stop is not None:
        st = float(stop)
        if s == "LONG" and st >= e:
            raise ManualBrokerError(
                f"a LONG stop must sit BELOW entry (stop {st} >= entry {e})")
        if s == "SHORT" and st <= e:
            raise ManualBrokerError(
                f"a SHORT stop must sit ABOVE entry (stop {st} <= entry {e})")
    if target is not None:
        t = float(target)
        if s == "LONG" and t <= e:
            raise ManualBrokerError(
                f"a LONG target must sit ABOVE entry (target {t} <= entry {e})")
        if s == "SHORT" and t >= e:
            raise ManualBrokerError(
                f"a SHORT target must sit BELOW entry (target {t} >= entry {e})")


class ManualBroker:
    """Thin, testable wrapper over GrowwClient for the Manual Intraday desk."""

    def __init__(self, mode: str = "paper", client=None):
        self.mode = mode
        self._client = client

    def _c(self):
        if self._client is None:
            from settings import load_settings
            from groww_client import GrowwClient
            load_settings().apply_to_environ()
            client = GrowwClient(mode=self.mode)
            client.authenticate()
            self._client = client
        return self._client

    def place_entry(self, symbol: str, side: str, quantity: int, entry_type: str,
                    price) -> dict:
        """Send the entry. MARKET ignores `price`; LIMIT requires it."""
        etype = str(entry_type or "MARKET").upper()
        if etype not in ("MARKET", "LIMIT"):
            raise ManualBrokerError(f"entry_type must be MARKET or LIMIT, got {entry_type!r}")
        if etype == "LIMIT" and not price:
            raise ManualBrokerError("a LIMIT entry needs a price")
        res = self._c().place_order(
            symbol=symbol, exchange="NSE", transaction_type=entry_txn(side),
            quantity=int(quantity), order_type=etype,
            price=float(price) if etype == "LIMIT" else None, product="MIS")
        if is_rejected(res.get("status")):
            raise ManualBrokerError(
                f"entry {res.get('status')} for {symbol}: {res.get('reason') or 'no reason given'}")
        return res

    def order_status(self, order_id: str) -> dict:
        return self._c().get_order_status(order_id)

    def arm_oco(self, symbol: str, side: str, quantity: int, target, stop) -> dict:
        """Rest both exits as a NATIVE Groww OCO, so the broker owns one-cancels-other and no
        orphan leg can survive to open a reverse position."""
        if target is None or stop is None:
            raise ManualBrokerError("an OCO needs BOTH a target and a stop")
        # The entry price is not available here, so validate the pair's internal geometry
        # instead: for a long the target must sit above the stop, and below it for a short.
        # An inverted pair would have one leg already through the market when it arms.
        t, s = float(target), float(stop)
        if str(side).upper() == "LONG" and t <= s:
            raise ManualBrokerError(f"LONG target {t} must sit above the stop {s}")
        if str(side).upper() == "SHORT" and t >= s:
            raise ManualBrokerError(f"SHORT target {t} must sit below the stop {s}")
        return self._c().place_oco_order(
            symbol,
            entry={"transaction_type": exit_txn(side), "quantity": int(quantity)},
            target={"trigger_price": t, "order_type": "LIMIT", "price": t},
            stop_loss={"trigger_price": s, "order_type": "LIMIT", "price": s})

    def modify_exits(self, oco_order_id: str, target, stop) -> dict:
        return self._c().modify_oco_order(oco_order_id, float(target), float(stop))

    def square_off(self, symbol: str, side: str, quantity: int,
                   oco_order_id: str | None = None) -> dict:
        """Flatten now. The resting OCO is cancelled FIRST: exiting while it still rests would
        flatten the position and then let the stop leg fire into a brand-new REVERSE position.
        If the cancel fails we refuse to exit rather than risk that."""
        if oco_order_id:
            try:
                self._c().cancel_order(oco_order_id)
            except Exception as e:                                   # noqa: BLE001
                raise ManualBrokerError(
                    f"could not cancel the resting OCO {oco_order_id} ({e}) — refusing to "
                    f"square off, because exiting with it still live could open a reverse "
                    f"position. Please check the broker.") from e
        return self._c().place_order(
            symbol=symbol, exchange="NSE", transaction_type=exit_txn(side),
            quantity=int(quantity), order_type="MARKET", price=None, product="MIS")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_manual_broker.py -q`
Expected: all PASS (35 cases — the parametrized verdict tests expand)

- [ ] **Step 5: Commit**

```bash
git add manual_broker.py tests/test_manual_broker.py
git commit -m "feat(manual-intraday): broker layer with sizing, economics and bracket guards"
```

---

### Task 2: Store — `manual_config` and `manual_orders`

**Files:**
- Modify: `store.py` — `_SCHEMA` (beside `broker_positions`), new methods after `get_analysis_results`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces on `Store`:
  - `get_manual_config() -> dict` (keys `mode`, `capital_per_trade`), seeded to `{'paper', 25000.0}`
  - `set_manual_config(**fields) -> None` (accepts `mode`, `capital_per_trade`)
  - `create_manual_order(symbol, side, quantity, entry_type, entry_price, stop, target, mode, skill_id=None, result_id=None) -> int` (status `PLACING`)
  - `update_manual_order(order_id, **fields) -> None` (any of `status, entry_order_id, oco_order_id, fill_price, filled_at, closed_at, error, stop, target, quantity`)
  - `get_manual_orders(limit=50) -> list[dict]` newest first
  - `get_manual_order(order_id) -> dict | None`
- Tasks 3 and 4 call exactly these.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
# ---- Manual Intraday: its own config and order book ------------------------------------

def test_manual_config_defaults_to_paper_and_roundtrips():
    store = Store(":memory:")
    cfg = store.get_manual_config()
    assert cfg["mode"] == "paper" and cfg["capital_per_trade"] == 25000.0
    store.set_manual_config(mode="live", capital_per_trade=40000.0)
    cfg = store.get_manual_config()
    assert cfg["mode"] == "live" and cfg["capital_per_trade"] == 40000.0


def test_manual_config_partial_update_keeps_the_rest():
    store = Store(":memory:")
    store.set_manual_config(mode="live")
    assert store.get_manual_config()["capital_per_trade"] == 25000.0


def test_manual_config_rejects_unknown_field():
    store = Store(":memory:")
    with pytest.raises(StoreError):
        store.set_manual_config(nonsense=1)


def test_manual_order_lifecycle_roundtrips():
    store = Store(":memory:")
    oid = store.create_manual_order(symbol="KEI", side="LONG", quantity=16,
                                    entry_type="MARKET", entry_price=1842.0, stop=1808.0,
                                    target=1905.0, mode="paper", skill_id="intraday-analyst-2",
                                    result_id=7)
    row = store.get_manual_order(oid)
    assert row["status"] == "PLACING" and row["symbol"] == "KEI" and row["quantity"] == 16
    assert row["skill_id"] == "intraday-analyst-2" and row["result_id"] == 7
    assert row["placed_at"]

    store.update_manual_order(oid, status="ENTRY_PENDING", entry_order_id="OID1")
    store.update_manual_order(oid, status="FILLED", fill_price=1843.5,
                              filled_at="2026-08-12T04:00:00+00:00")
    store.update_manual_order(oid, status="ARMED", oco_order_id="OCO1")
    row = store.get_manual_order(oid)
    assert row["status"] == "ARMED" and row["oco_order_id"] == "OCO1"
    assert row["entry_order_id"] == "OID1" and row["fill_price"] == 1843.5


def test_manual_orders_newest_first_and_limited():
    store = Store(":memory:")
    a = store.create_manual_order(symbol="A", side="LONG", quantity=1, entry_type="MARKET",
                                  entry_price=10.0, stop=9.0, target=12.0, mode="paper")
    b = store.create_manual_order(symbol="B", side="SHORT", quantity=1, entry_type="LIMIT",
                                  entry_price=10.0, stop=11.0, target=8.0, mode="live")
    assert [r["id"] for r in store.get_manual_orders()] == [b, a]
    assert len(store.get_manual_orders(limit=1)) == 1


def test_update_manual_order_rejects_unknown_field():
    store = Store(":memory:")
    oid = store.create_manual_order(symbol="A", side="LONG", quantity=1, entry_type="MARKET",
                                    entry_price=10.0, stop=9.0, target=12.0, mode="paper")
    with pytest.raises(StoreError):
        store.update_manual_order(oid, nonsense=1)


def test_get_manual_order_unknown_is_none():
    assert Store(":memory:").get_manual_order(999) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_store.py -q -k manual`
Expected: FAIL — `AttributeError: 'Store' object has no attribute 'get_manual_config'`

- [ ] **Step 3: Implement**

In `_SCHEMA`, immediately after the `broker_positions` table:

```sql
CREATE TABLE IF NOT EXISTS manual_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    mode TEXT NOT NULL DEFAULT 'paper',
    capital_per_trade REAL NOT NULL DEFAULT 25000,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS manual_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity INTEGER NOT NULL,
    entry_type TEXT NOT NULL,
    entry_price REAL,
    stop REAL,
    target REAL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    entry_order_id TEXT,
    oco_order_id TEXT,
    fill_price REAL,
    placed_at TEXT NOT NULL,
    filled_at TEXT,
    closed_at TEXT,
    error TEXT,
    skill_id TEXT,
    result_id INTEGER
);
```

Add these methods to `Store`, after `get_analysis_results`:

```python
    # ---- Manual Intraday: its own config and order book ----------------------------------
    _MANUAL_CONFIG_FIELDS = ("mode", "capital_per_trade")

    def get_manual_config(self) -> dict:
        """Manual Intraday's OWN mode and sizing — never autoIntraday's. Seeded on first read
        so the page works on an existing database with no migration step."""
        r = self._conn.execute("SELECT * FROM manual_config WHERE id = 1").fetchone()
        if r is None:
            self._conn.execute(
                "INSERT INTO manual_config (id, mode, capital_per_trade, updated_at) "
                "VALUES (1, 'paper', 25000, ?)", (_utc_now(),))
            self._conn.commit()
            r = self._conn.execute("SELECT * FROM manual_config WHERE id = 1").fetchone()
        return {"mode": r["mode"], "capital_per_trade": float(r["capital_per_trade"])}

    def set_manual_config(self, **fields) -> None:
        unknown = set(fields) - set(self._MANUAL_CONFIG_FIELDS)
        if unknown:
            raise StoreError(f"unknown manual config field(s): {', '.join(sorted(unknown))}")
        if not fields:
            return
        self.get_manual_config()                 # ensure the row exists
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(f"UPDATE manual_config SET {sets}, updated_at = ? WHERE id = 1",
                           [*fields.values(), _utc_now()])
        self._conn.commit()

    _MANUAL_ORDER_FIELDS = ("status", "entry_order_id", "oco_order_id", "fill_price",
                            "filled_at", "closed_at", "error", "stop", "target", "quantity")

    def create_manual_order(self, symbol: str, side: str, quantity: int, entry_type: str,
                            entry_price, stop, target, mode: str,
                            skill_id: str | None = None,
                            result_id: int | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO manual_orders (symbol, side, quantity, entry_type, entry_price, "
            "stop, target, mode, status, placed_at, skill_id, result_id) "
            "VALUES (?,?,?,?,?,?,?,?, 'PLACING', ?,?,?)",
            (symbol, side, int(quantity), entry_type, entry_price, stop, target, mode,
             _utc_now(), skill_id, result_id))
        self._conn.commit()
        return int(cur.lastrowid)

    def update_manual_order(self, order_id: int, **fields) -> None:
        unknown = set(fields) - set(self._MANUAL_ORDER_FIELDS)
        if unknown:
            raise StoreError(f"unknown manual order field(s): {', '.join(sorted(unknown))}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        self._conn.execute(f"UPDATE manual_orders SET {sets} WHERE id = ?",
                           [*fields.values(), order_id])
        self._conn.commit()

    def get_manual_orders(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM manual_orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_manual_order(self, order_id: int) -> dict | None:
        r = self._conn.execute("SELECT * FROM manual_orders WHERE id = ?",
                               (order_id,)).fetchone()
        return dict(r) if r else None
```

- [ ] **Step 4: Run the store tests**

Run: `.venv/bin/python -m pytest tests/test_store.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add store.py tests/test_store.py
git commit -m "feat(manual-intraday): own config and order book tables"
```

---

### Task 3: `manual_order_job.py` — place, wait for fill, arm the OCO

**Files:**
- Create: `manual_order_job.py`
- Test: `tests/test_manual_order_job.py`

**Interfaces:**
- Consumes: `ManualBroker` (Task 1), `Store` manual methods (Task 2).
- Produces: `run_order(store, broker, order_id, poll_s=10, deadline_s=21600, sleep=time.sleep) -> str` returning the final status; `main()` reading `--order <id>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_manual_order_job.py`:

```python
"""The detached manual-order runner: entry -> fill -> native OCO, and the failure paths that
must never leave a filled position unprotected without saying so."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manual_broker import ManualBrokerError
from manual_order_job import run_order
from store import Store


class _Broker:
    """Stub with the ManualBroker surface the job uses."""

    def __init__(self, entry_status="COMPLETE", statuses=None, oco_error=None,
                 entry_error=None):
        self.entry_status, self.oco_error, self.entry_error = entry_status, oco_error, entry_error
        self.statuses = list(statuses or [])
        self.armed = []

    def place_entry(self, symbol, side, quantity, entry_type, price):
        if self.entry_error:
            raise ManualBrokerError(self.entry_error)
        return {"order_id": "OID1", "status": self.entry_status, "price": price}

    def order_status(self, order_id):
        nxt = self.statuses.pop(0) if self.statuses else self.entry_status
        return {"order_id": order_id, "status": nxt, "reason": "no margin"}

    def arm_oco(self, symbol, side, quantity, target, stop):
        if self.oco_error:
            raise ManualBrokerError(self.oco_error)
        self.armed.append((symbol, side, quantity, target, stop))
        return {"order_id": "OCO1", "status": "ACTIVE"}


def _order(store, **kw):
    base = dict(symbol="KEI", side="LONG", quantity=10, entry_type="MARKET",
                entry_price=1842.0, stop=1808.0, target=1905.0, mode="paper")
    base.update(kw)
    return store.create_manual_order(**base)


def test_market_entry_fills_and_arms_the_oco():
    store = Store(":memory:")
    oid = _order(store)
    b = _Broker()
    assert run_order(store, b, oid, sleep=lambda s: None) == "ARMED"
    row = store.get_manual_order(oid)
    assert row["status"] == "ARMED" and row["entry_order_id"] == "OID1"
    assert row["oco_order_id"] == "OCO1" and row["filled_at"]
    assert b.armed == [("KEI", "LONG", 10, 1905.0, 1808.0)]


def test_rejected_entry_records_the_reason_and_arms_nothing():
    store = Store(":memory:")
    oid = _order(store)
    b = _Broker(entry_error="entry REJECTED for KEI: no margin")
    assert run_order(store, b, oid, sleep=lambda s: None) == "REJECTED"
    row = store.get_manual_order(oid)
    assert row["status"] == "REJECTED" and "no margin" in row["error"]
    assert row["oco_order_id"] is None
    assert b.armed == []


def test_entry_rejected_while_polling_records_the_reason():
    store = Store(":memory:")
    oid = _order(store, entry_type="LIMIT")
    b = _Broker(entry_status="PENDING", statuses=["PENDING", "REJECTED"])
    assert run_order(store, b, oid, sleep=lambda s: None) == "REJECTED"
    assert store.get_manual_order(oid)["status"] == "REJECTED"
    assert b.armed == []


def test_limit_still_pending_at_the_deadline_stays_pending():
    store = Store(":memory:")
    oid = _order(store, entry_type="LIMIT")
    b = _Broker(entry_status="PENDING", statuses=["PENDING"] * 50)
    out = run_order(store, b, oid, poll_s=10, deadline_s=25, sleep=lambda s: None)
    assert out == "ENTRY_PENDING"
    row = store.get_manual_order(oid)
    assert row["status"] == "ENTRY_PENDING" and row["oco_order_id"] is None
    assert b.armed == []


def test_oco_failure_after_a_fill_is_recorded_without_losing_the_fill():
    """The position IS open. Saying so loudly matters more than the OCO failing."""
    store = Store(":memory:")
    oid = _order(store)
    b = _Broker(oco_error="smart order rejected")
    assert run_order(store, b, oid, sleep=lambda s: None) == "FILLED"
    row = store.get_manual_order(oid)
    assert row["status"] == "FILLED"          # filled, NOT armed
    assert "smart order rejected" in row["error"]
    assert row["oco_order_id"] is None and row["filled_at"]


def test_a_fill_price_is_recorded_when_the_broker_reports_one():
    store = Store(":memory:")
    oid = _order(store)

    class B(_Broker):
        def order_status(self, order_id):
            return {"order_id": order_id, "status": "COMPLETE", "price": 1843.25}
    run_order(store, B(), oid, sleep=lambda s: None)
    assert store.get_manual_order(oid)["fill_price"] == 1843.25


def test_unknown_order_id_returns_error():
    assert run_order(Store(":memory:"), _Broker(), 999, sleep=lambda s: None) == "ERROR"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_manual_order_job.py -q`
Expected: collection ERROR — `ModuleNotFoundError: No module named 'manual_order_job'`

- [ ] **Step 3: Implement**

Create `manual_order_job.py`:

```python
#!/usr/bin/env python3
"""Manual Intraday order runner — launched detached by the Manual Intraday page.

Three steps, in order: place the entry, poll until it fills, then arm a NATIVE Groww OCO
carrying the target and stop. The broker owns one-cancels-other, so both exit legs rest
safely and no orphan leg can survive to open a reverse position.

Every failure is recorded on the order row rather than raised, because the operator's next
action depends entirely on WHERE it failed: a rejected entry means nothing happened, while an
OCO failure after a fill means a live unprotected position that needs attention now.
See docs/superpowers/specs/2026-08-12-manual-intraday-orders-design.md."""
from __future__ import annotations

import logging
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from manual_broker import is_filled, is_rejected

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("autointraday.manual_order")

POLL_S = 10
# A LIMIT entry may rest all session; MARKET resolves on the first poll.
DEADLINE_S = 6 * 60 * 60


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_order(store, broker, order_id: int, poll_s: int = POLL_S,
              deadline_s: int = DEADLINE_S, sleep=time.sleep) -> str:
    """Place, wait, arm. Returns the final status string. Never raises."""
    row = store.get_manual_order(order_id)
    if row is None:
        log.error("no manual order %s", order_id)
        return "ERROR"

    try:
        res = broker.place_entry(row["symbol"], row["side"], row["quantity"],
                                 row["entry_type"], row["entry_price"])
    except Exception as e:                                           # noqa: BLE001
        log.warning("manual entry failed for %s: %s", row["symbol"], e)
        store.update_manual_order(order_id, status="REJECTED", error=str(e)[:500])
        return "REJECTED"

    entry_order_id = res.get("order_id")
    store.update_manual_order(order_id, status="ENTRY_PENDING",
                              entry_order_id=entry_order_id)

    status, fill_price, waited = res.get("status"), res.get("price"), 0
    reason = None
    while not is_filled(status) and not is_rejected(status) and waited < deadline_s:
        sleep(poll_s)
        waited += poll_s
        try:
            st = broker.order_status(entry_order_id)
        except Exception as e:                                       # noqa: BLE001
            log.warning("status poll failed for %s: %s", entry_order_id, e)
            continue
        status = st.get("status")
        if st.get("price") is not None:
            fill_price = st.get("price")
        reason = st.get("reason")

    if is_rejected(status):
        store.update_manual_order(order_id, status="REJECTED",
                                  error=f"entry {status}: {reason or ''}"[:500])
        return "REJECTED"
    if not is_filled(status):
        log.info("manual order %s still resting at the deadline", order_id)
        return "ENTRY_PENDING"

    store.update_manual_order(order_id, status="FILLED", filled_at=_now(),
                              fill_price=fill_price)
    try:
        oco = broker.arm_oco(row["symbol"], row["side"], row["quantity"],
                             target=row["target"], stop=row["stop"])
    except Exception as e:                                           # noqa: BLE001
        log.error("OCO arm FAILED for %s — position is OPEN and UNPROTECTED: %s",
                  row["symbol"], e)
        store.update_manual_order(order_id, error=f"OCO arm failed: {e}"[:500])
        return "FILLED"
    store.update_manual_order(order_id, status="ARMED", oco_order_id=oco.get("order_id"))
    log.info("manual order %s armed: %s", order_id, oco.get("order_id"))
    return "ARMED"


def main() -> int:
    from settings import load_settings
    settings = load_settings()
    settings.apply_to_environ()

    from store import Store
    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
    store = Store(settings.db_path)

    order_id = int(sys.argv[sys.argv.index("--order") + 1])
    row = store.get_manual_order(order_id)
    if row is None:
        log.error("no manual order %s", order_id)
        return 1

    from manual_broker import ManualBroker
    run_order(store, ManualBroker(mode=row["mode"]), order_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_manual_order_job.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add manual_order_job.py tests/test_manual_order_job.py
git commit -m "feat(manual-intraday): detached order runner — entry, fill, native OCO"
```

---

### Task 4: The Manual Intraday page

**Files:**
- Modify: `dashboard.py` — rename the page section, add the order ticket and positions section, CSS, nav wiring
- Modify: `tests/test_dashboard_analysis.py` → rename to `tests/test_dashboard_manual_intraday.py`
- Test: `tests/test_dashboard_manual_intraday.py`

**Interfaces:**
- Consumes: `manual_broker` helpers (Task 1), `Store` manual methods (Task 2), `manual_order_job.py` (Task 3).
- Produces: `_manual_intraday_page()` (renamed from `_analysis_page`), `_ticket_defaults(result, capital)`, `_launch_manual_order(order_id)`, `_manual_orders_section()`, `_autointraday_live_warning()`.

- [ ] **Step 1: Rename the existing test file and add the new tests**

```bash
git mv tests/test_dashboard_analysis.py tests/test_dashboard_manual_intraday.py
```

In that file, change the final test to the new url path and add the ticket tests:

```python
def test_page_is_registered_in_navigation():
    src = open(dashboard.__file__, encoding="utf-8").read()
    assert 'url_path="manual-intraday"' in src and "_manual_intraday_page" in src
    assert 'title="Manual Intraday"' in src


def test_ticket_defaults_come_from_the_result():
    d = dashboard._ticket_defaults(R, capital=30000.0)
    assert d["side"] == "LONG"                 # from the BUY NOW verdict
    assert d["entry"] == 1842.0 and d["stop"] == 1808.0 and d["target"] == 1905.0
    assert d["quantity"] == 16                 # floor(30000 / 1842)


def test_ticket_defaults_leave_side_blank_on_an_exit_verdict():
    """EXIT means close what you hold, not open a short — the operator must choose."""
    d = dashboard._ticket_defaults(dict(R, verdict="EXIT"), capital=30000.0)
    assert d["side"] is None


def test_ticket_defaults_survive_a_result_with_no_levels():
    bare = dict(R, entry=None, stop=None, target=None)
    d = dashboard._ticket_defaults(bare, capital=30000.0)
    assert d["entry"] is None and d["quantity"] == 0


def test_ticket_target_comes_from_target1():
    d = dashboard._ticket_defaults(R, capital=30000.0)
    assert d["target"] == R["target1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q`
Expected: the 5 new/changed tests FAIL (`_ticket_defaults` missing, url path still `analysis`)

- [ ] **Step 3: Implement**

(a) Add CSS after the `.ai-aerr` rule:

```css
.ai-mode-live { display: inline-block; padding: .1rem .5rem; border-radius: 999px;
  background: #e5484d; color: #fff; font-size: .7rem; font-weight: 700;
  letter-spacing: .04em; }
.ai-mode-paper { display: inline-block; padding: .1rem .5rem; border-radius: 999px;
  background: rgba(128,131,141,.25); font-size: .7rem; font-weight: 700;
  letter-spacing: .04em; }
```

(b) Add these helpers immediately after `_analysis_card`:

```python
def _ticket_defaults(r: dict, capital: float) -> dict:
    """Prefill an order ticket from one analysis result. The target comes from `target1` — the
    skills' practical first objective, and the only target column an analysis row carries."""
    from manual_broker import qty_for_capital, side_from_verdict
    entry = r.get("entry")
    return {"side": side_from_verdict(r.get("verdict")), "entry": entry,
            "stop": r.get("stop"), "target": r.get("target1"),
            "quantity": qty_for_capital(capital, entry)}


def _launch_manual_order(order_id: int) -> None:
    """Fire the order runner detached: a LIMIT entry can rest for hours."""
    import subprocess
    import sys
    here = os.path.dirname(os.path.abspath(__file__))
    subprocess.Popen([sys.executable, os.path.join(here, "manual_order_job.py"),
                      "--order", str(order_id)],
                     cwd=here, env=dict(os.environ), start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _autointraday_live_warning() -> None:
    """One Groww account: if the BOT is live it adopts MIS positions opened here and cancels
    their exit orders (orchestrator._reconcile_broker / _takeover_foreign_orders). Code
    separation cannot prevent that, so say it out loud instead of letting the two fight."""
    try:
        bot_live = _db(lambda s: s.get_config()).mode == "live"
    except Exception:                                                # noqa: BLE001
        return
    if bot_live:
        st.warning("**autoIntraday is LIVE.** It will adopt any MIS position opened here and "
                   "replace your stop/target with its own analysed levels on its next cycle. "
                   "Pause it, or expect it to take these trades over.", icon="⚠️")
```

(c) Add the order-ticket renderer, called from the Formatted tab of `_analysis_live`:

```python
def _order_ticket(r: dict, cfg: dict, skill_id: str | None = None) -> None:
    """The editable ticket for one analysis result. Everything is prefilled from the skill and
    everything can be changed before anything is sent. `skill_id` comes from the RUN — an
    analysis_results row does not carry it — and is recorded as the order's provenance."""
    from manual_broker import ManualBrokerError, order_economics, validate_bracket
    d = _ticket_defaults(r, cfg["capital_per_trade"])
    key = f"tkt_{r['id']}"
    with st.expander("Place order", expanded=False):
        c1, c2, c3 = st.columns(3)
        sides = ["LONG", "SHORT"]
        side = c1.selectbox("Side", sides, key=f"{key}_side",
                            index=sides.index(d["side"]) if d["side"] in sides else 0,
                            help=None if d["side"] else
                            "This verdict is not an entry instruction — choose the side "
                            "yourself.")
        etype = c2.selectbox("Entry", ["MARKET", "LIMIT"], key=f"{key}_type")
        qty = c3.number_input("Qty", min_value=0, step=1, value=int(d["quantity"]),
                              key=f"{key}_qty")
        p1, p2, p3 = st.columns(3)
        entry = p1.number_input("Entry price", min_value=0.0, step=0.05,
                                value=float(d["entry"] or 0.0), key=f"{key}_entry")
        stop = p2.number_input("Stop", min_value=0.0, step=0.05,
                               value=float(d["stop"] or 0.0), key=f"{key}_stop")
        target = p3.number_input("Target", min_value=0.0, step=0.05,
                                 value=float(d["target"] or 0.0), key=f"{key}_target")

        e = order_economics(side, qty, entry or None, stop or None, target or None)
        bits = []
        if e["exposure"]:
            bits.append(f"exposure ₹{e['exposure']:,.0f}")
        if e["risk"] is not None:
            bits.append(f"risk ₹{e['risk']:,.0f}")
        if e["reward"] is not None:
            bits.append(f"reward ₹{e['reward']:,.0f}")
        if e["rr"] is not None:
            bits.append(f"R:R {e['rr']:.2f}")
        st.caption(" · ".join(bits) or "Fill in the levels to see what this risks.")

        try:
            validate_bracket(side, entry or None, stop or None, target or None, qty)
            problem = None
        except ManualBrokerError as ex:
            problem = str(ex)
        if problem:
            st.error(problem)
        live = cfg["mode"] == "live"
        label = f"⚠ Place LIVE {side} {qty} {r['symbol']}" if live else \
                f"Place PAPER {side} {qty} {r['symbol']}"
        if st.button(label, key=f"{key}_go", type="primary", disabled=bool(problem),
                     use_container_width=True):
            oid = _db(lambda s: s.create_manual_order(
                symbol=r["symbol"], side=side, quantity=int(qty), entry_type=etype,
                entry_price=entry or None, stop=stop or None, target=target or None,
                mode=cfg["mode"], skill_id=skill_id, result_id=r.get("id")))
            _launch_manual_order(oid)
            st.toast(f"{cfg['mode'].upper()} order sent for {r['symbol']}", icon="📤")
            st.rerun()
```

In `_analysis_live`, inside `with t_fmt:` after the card, add:

```python
                if r["status"] == "DONE":
                    _order_ticket(r, _db(lambda s: s.get_manual_config()),
                                  latest["skill_id"])
```

(d) Add the positions section, called from the page:

```python
_MANUAL_STATUS_LABEL = {"PLACING": "· sending", "ENTRY_PENDING": "⏳ waiting for fill",
                        "FILLED": "⚠ filled, exits NOT armed", "ARMED": "✓ armed",
                        "REJECTED": "✗ rejected", "CLOSED": "· closed", "ERROR": "⚠ error"}


@st.fragment(run_every=5)
def _manual_orders_section() -> None:
    """Everything this page has sent, with live status and the two management actions."""
    from manual_broker import ManualBroker, ManualBrokerError
    orders = _db(lambda s: s.get_manual_orders(limit=25))
    if not orders:
        st.caption("No orders placed from this page yet.")
        return
    for o in orders:
        badge = _MANUAL_STATUS_LABEL.get(o["status"], o["status"])
        head = (f"{o['symbol']} · {o['side']} x{o['quantity']} · {o['mode'].upper()} · {badge}")
        with st.expander(head, expanded=o["status"] in ("FILLED", "ERROR")):
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
```

Add this helper next to the other formatters near `_fmt_ist_short`:

```python
def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
```

(e) Rename `_analysis_page` to `_manual_intraday_page`, change its heading and caption, and add the mode control plus the two new sections. Replace the brand line and caption with:

```python
    cfg = _db(lambda s: s.get_manual_config())
    st.markdown('<div class="ai-brand">Manual Intraday<em>.</em></div>',
                unsafe_allow_html=True)
    badge = ('<span class="ai-mode-live">LIVE — REAL ORDERS</span>' if cfg["mode"] == "live"
             else '<span class="ai-mode-paper">PAPER</span>')
    st.markdown(f'{badge} &nbsp; your own desk — independent of autoIntraday\'s mode.',
                unsafe_allow_html=True)
    st.caption("Analyse your live Groww intraday positions with any skill, then place the "
               "trade from the same screen. Orders here go to YOUR Groww account using this "
               "page's own mode below — autoIntraday's paper/live setting does not apply.")
    _autointraday_live_warning()
```

and add a settings expander just above `st.divider()`:

```python
    with st.expander("Manual Intraday settings"):
        s1, s2 = st.columns(2)
        want_live = s1.toggle("LIVE mode (places REAL orders on Groww)",
                              value=cfg["mode"] == "live", key="manual_mode")
        cap = s2.number_input("Capital per trade (₹)", min_value=0.0, step=1000.0,
                              value=float(cfg["capital_per_trade"]), key="manual_cap")
        if st.button("Save settings", use_container_width=True):
            _db(lambda s: s.set_manual_config(mode="live" if want_live else "paper",
                                              capital_per_trade=float(cap)))
            st.rerun()
```

and at the end of the page, after `_analysis_live()`:

```python
    st.divider()
    st.subheader("Orders placed from this page")
    _manual_orders_section()
```

(f) Nav wiring in `main()` — replace the `analyze` page line:

```python
    manual = st.Page(_manual_intraday_page, title="Manual Intraday",
                     url_path="manual-intraday")
    pages = [intraday, swing, live, ashort, lab, manual]
```

- [ ] **Step 4: Run the page tests**

Run: `.venv/bin/python -m pytest tests/test_dashboard_manual_intraday.py -q`
Expected: all PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: all PASS

- [ ] **Step 6: Render the page under AppTest**

Write a driver to the scratchpad that imports dashboard and calls `_manual_intraday_page()`,
then run it through `streamlit.testing.v1.AppTest` and assert `at.exception` is empty. Seed a
`manual_orders` row and an analysis result first so the ticket and positions sections both
render, then delete the seeded rows.

- [ ] **Step 7: Commit**

```bash
git add dashboard.py tests/test_dashboard_manual_intraday.py
git commit -m "feat(manual-intraday): order ticket, positions section, page rename"
```
