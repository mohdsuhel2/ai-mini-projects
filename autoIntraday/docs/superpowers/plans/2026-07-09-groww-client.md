# Groww API Client (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `GrowwClient`, a single Python module wrapping the official `growwapi` SDK, that every later phase of `autoIntraday` uses to authenticate, read market/portfolio data, and place/track orders — with a structural paper/live mode split so paper testing can never accidentally fire a real order.

**Architecture:** One class, `GrowwClient(mode, sdk_factory=...)`. Reads (quotes, holdings, positions, margin, order status) call the real SDK in both modes and retry a few times on transient failure. Writes (place/cancel order, OCO) call the real SDK only in `live` mode with no retry; in `paper` mode they're simulated locally against the current LTP and logged in-memory. All SDK access goes through an injectable `sdk_factory`, so unit tests use a fake SDK double and never touch the network.

**Tech Stack:** Python 3.10+, `growwapi` (official Groww SDK), `pyotp` (TOTP generation), `pytest`.

## Global Constraints

- Credentials (`GROWW_API_KEY`, `GROWW_TOTP_SECRET`) come only from environment variables — never hardcoded, never committed. `.env` is gitignored; `.env.example` documents the variable names with no values.
- `mode` (`"paper"` or `"live"`) is a required constructor argument, not a global or env-driven toggle.
- Every error raised by this module is a `GrowwClientError` — callers handle exactly one exception type.
- Reads retry a few times with backoff on transient failure. Writes (order placement/cancellation) never retry automatically.
- Auth failure raises immediately with no retry.
- All SDK calls go through the injected `sdk_factory` seam so tests never hit the network.

---

### Task 1: Project scaffolding + `GrowwClientError` + mode validation

**Files:**
- Create: `requirements.txt`
- Create: `.env.example`
- Create: `groww_client.py`
- Test: `tests/test_groww_client.py`

**Interfaces:**
- Produces: `GrowwClientError(Exception)`; `GrowwClient.__init__(self, mode: str, sdk_factory: Callable[[str, str], Any] = _default_sdk_factory)` — raises `GrowwClientError` if `mode` not in `("paper", "live")`; otherwise sets `self.mode`, `self._sdk = None`, `self._paper_orders = []`, `self._paper_order_seq = 0`.

- [ ] **Step 1: Create the venv and requirements file**

```bash
python3 -m venv .venv
```

Write `requirements.txt`:

```
growwapi
pyotp>=2.9.0
pytest>=8.0
```

```bash
.venv/bin/pip install -r requirements.txt
```

- [ ] **Step 2: Write `.env.example`**

```
# Copy to .env and fill in real values. Never commit .env.
GROWW_API_KEY=
GROWW_TOTP_SECRET=
```

- [ ] **Step 3: Write the failing test**

Create `tests/test_groww_client.py`:

```python
import pytest

from groww_client import GrowwClient, GrowwClientError


def test_invalid_mode_raises():
    with pytest.raises(GrowwClientError, match="invalid mode"):
        GrowwClient(mode="turbo")


def test_valid_modes_construct_cleanly():
    for mode in ("paper", "live"):
        client = GrowwClient(mode=mode, sdk_factory=lambda k, t: None)
        assert client.mode == mode
        assert client._sdk is None
        assert client._paper_orders == []
```

- [ ] **Step 4: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'groww_client'`

- [ ] **Step 5: Write minimal implementation**

Create `groww_client.py`:

```python
"""Groww API client wrapper — the only module that talks to the growwapi SDK.

Paper mode intercepts every write (order placement/cancellation) and simulates it
locally instead of calling Groww, so the same code path later phases use can run
against a live account without risk. See
docs/superpowers/specs/2026-07-09-groww-client-design.md.
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable, Optional

import pyotp

VALID_MODES = ("paper", "live")


class GrowwClientError(Exception):
    """Wraps every error raised while talking to Groww: auth, SDK, network, rate limit."""


def _default_sdk_factory(api_key: str, totp: str) -> Any:
    from growwapi import GrowwAPI
    access_token = GrowwAPI.get_access_token(api_key=api_key, totp=totp)
    return GrowwAPI(access_token)


class GrowwClient:
    def __init__(self, mode: str, sdk_factory: Callable[[str, str], Any] = _default_sdk_factory):
        if mode not in VALID_MODES:
            raise GrowwClientError(f"invalid mode {mode!r}, must be one of {VALID_MODES}")
        self.mode = mode
        self._sdk_factory = sdk_factory
        self._sdk: Any = None
        self._paper_orders: list[dict] = []
        self._paper_order_seq = 0
```

- [ ] **Step 6: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add requirements.txt .env.example groww_client.py tests/test_groww_client.py
git commit -m "Scaffold GrowwClient with mode validation"
```

---

### Task 2: Authentication

**Files:**
- Modify: `groww_client.py`
- Test: `tests/test_groww_client.py`

**Interfaces:**
- Consumes: `GrowwClientError`, `GrowwClient.__init__` from Task 1.
- Produces: `GrowwClient.authenticate(self) -> None` — reads `GROWW_API_KEY`/`GROWW_TOTP_SECRET` from env, raises `GrowwClientError` if either is missing, generates a TOTP via `pyotp.TOTP(secret).now()`, calls `self._sdk_factory(api_key, totp)` and stores the result on `self._sdk`; wraps any factory exception in `GrowwClientError`. `GrowwClient._require_auth(self) -> None` — raises `GrowwClientError` if `self._sdk is None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_groww_client.py`:

```python
import pyotp


def test_authenticate_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_TOTP_SECRET", raising=False)
    client = GrowwClient(mode="paper", sdk_factory=lambda k, t: "sdk")
    with pytest.raises(GrowwClientError, match="GROWW_API_KEY"):
        client.authenticate()


def test_authenticate_success_stores_sdk(monkeypatch):
    monkeypatch.setenv("GROWW_API_KEY", "key123")
    secret = pyotp.random_base32()
    monkeypatch.setenv("GROWW_TOTP_SECRET", secret)

    seen = {}

    def fake_factory(api_key, totp):
        seen["api_key"] = api_key
        seen["totp"] = totp
        return "fake-sdk"

    client = GrowwClient(mode="paper", sdk_factory=fake_factory)
    client.authenticate()

    assert client._sdk == "fake-sdk"
    assert seen["api_key"] == "key123"
    assert len(seen["totp"]) == 6 and seen["totp"].isdigit()


def test_authenticate_factory_error_wrapped(monkeypatch):
    monkeypatch.setenv("GROWW_API_KEY", "key123")
    monkeypatch.setenv("GROWW_TOTP_SECRET", pyotp.random_base32())

    def failing_factory(api_key, totp):
        raise RuntimeError("bad creds")

    client = GrowwClient(mode="paper", sdk_factory=failing_factory)
    with pytest.raises(GrowwClientError, match="authentication failed"):
        client.authenticate()


def test_require_auth_raises_before_authenticate():
    client = GrowwClient(mode="paper", sdk_factory=lambda k, t: "sdk")
    with pytest.raises(GrowwClientError, match="not authenticated"):
        client._require_auth()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: FAIL — `AttributeError: 'GrowwClient' object has no attribute 'authenticate'`

- [ ] **Step 3: Implement `authenticate` and `_require_auth`**

Add to `groww_client.py` inside `GrowwClient`:

```python
    def authenticate(self) -> None:
        api_key = os.environ.get("GROWW_API_KEY")
        totp_secret = os.environ.get("GROWW_TOTP_SECRET")
        if not api_key or not totp_secret:
            raise GrowwClientError("GROWW_API_KEY and GROWW_TOTP_SECRET must be set in the environment")
        totp = pyotp.TOTP(totp_secret).now()
        try:
            self._sdk = self._sdk_factory(api_key, totp)
        except Exception as e:
            raise GrowwClientError(f"authentication failed: {e}") from e

    def _require_auth(self) -> None:
        if self._sdk is None:
            raise GrowwClientError("not authenticated - call authenticate() first")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add groww_client.py tests/test_groww_client.py
git commit -m "Add GrowwClient authentication via TOTP"
```

---

### Task 3: Retry helper + market data reads

**Files:**
- Modify: `groww_client.py`
- Test: `tests/test_groww_client.py`

**Interfaces:**
- Consumes: `GrowwClientError`, `GrowwClient._require_auth`.
- Produces: `_retry(fn: Callable[[], Any], attempts: int = 3, backoff_seconds: float = 0.5) -> Any` — calls `fn()`, retrying on any exception up to `attempts` times with a `time.sleep(backoff_seconds)` between attempts, then raises `GrowwClientError` wrapping the last exception. `GrowwClient.get_ltp(self, symbols: list[str]) -> dict[str, float]`. `GrowwClient.get_quote(self, symbol: str) -> dict[str, Any]` with keys `symbol, ltp, open, high, low, close, volume`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_groww_client.py`:

```python
def _authed_client(sdk):
    client = GrowwClient(mode="paper", sdk_factory=lambda k, t: sdk)
    import os
    os.environ["GROWW_API_KEY"] = "key123"
    os.environ["GROWW_TOTP_SECRET"] = pyotp.random_base32()
    client.authenticate()
    return client


class _FakeSdk:
    def __init__(self):
        self.ltp_calls = 0

    def get_ltp(self, symbols):
        return {s: 100.5 for s in symbols}

    def get_quote(self, symbol):
        return {"ltp": 100.5, "open": 99.0, "high": 101.0, "low": 98.5,
                "close": 99.5, "volume": 12345}


def test_get_ltp_returns_normalized_floats():
    client = _authed_client(_FakeSdk())
    result = client.get_ltp(["RELIANCE", "TCS"])
    assert result == {"RELIANCE": 100.5, "TCS": 100.5}


def test_get_quote_returns_normalized_dict():
    client = _authed_client(_FakeSdk())
    result = client.get_quote("RELIANCE")
    assert result == {"symbol": "RELIANCE", "ltp": 100.5, "open": 99.0,
                       "high": 101.0, "low": 98.5, "close": 99.5, "volume": 12345}


def test_get_ltp_without_auth_raises():
    client = GrowwClient(mode="paper", sdk_factory=lambda k, t: _FakeSdk())
    with pytest.raises(GrowwClientError, match="not authenticated"):
        client.get_ltp(["RELIANCE"])


def test_retry_succeeds_after_transient_failures():
    from groww_client import _retry
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("timeout")
        return "ok"

    assert _retry(flaky, attempts=3, backoff_seconds=0) == "ok"
    assert calls["n"] == 3


def test_retry_exhausts_and_raises_growwclienterror():
    from groww_client import _retry

    def always_fails():
        raise RuntimeError("still down")

    with pytest.raises(GrowwClientError, match="failed after 2 attempts"):
        _retry(always_fails, attempts=2, backoff_seconds=0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: FAIL — `AttributeError: 'GrowwClient' object has no attribute 'get_ltp'` (and `ImportError` for `_retry`)

- [ ] **Step 3: Implement `_retry`, `get_ltp`, `get_quote`**

Add module-level function to `groww_client.py` (after `_default_sdk_factory`):

```python
def _retry(fn: Callable[[], Any], attempts: int = 3, backoff_seconds: float = 0.5) -> Any:
    """Retry a read-only call a few times with linear backoff. Never use for writes."""
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_error = e
            if attempt < attempts - 1:
                time.sleep(backoff_seconds)
    raise GrowwClientError(f"failed after {attempts} attempts: {last_error}") from last_error
```

Add methods to `GrowwClient`:

```python
    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        self._require_auth()
        raw = _retry(lambda: self._sdk.get_ltp(symbols))
        return {symbol: float(price) for symbol, price in raw.items()}

    def get_quote(self, symbol: str) -> dict[str, Any]:
        self._require_auth()
        raw = _retry(lambda: self._sdk.get_quote(symbol))
        return {
            "symbol": symbol,
            "ltp": float(raw["ltp"]),
            "open": float(raw["open"]),
            "high": float(raw["high"]),
            "low": float(raw["low"]),
            "close": float(raw["close"]),
            "volume": int(raw["volume"]),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add groww_client.py tests/test_groww_client.py
git commit -m "Add retry helper and market data reads (get_ltp, get_quote)"
```

---

### Task 4: Portfolio reads

**Files:**
- Modify: `groww_client.py`
- Test: `tests/test_groww_client.py`

**Interfaces:**
- Consumes: `_retry`, `GrowwClient._require_auth`.
- Produces: `GrowwClient.get_holdings(self) -> list[dict]` (keys `symbol, quantity, avg_price, ltp`); `GrowwClient.get_positions(self) -> list[dict]` (keys `symbol, quantity, product, avg_price, ltp`); `GrowwClient.get_margin(self) -> dict` (keys `available, used, total`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_groww_client.py`:

```python
class _FakeSdkWithPortfolio(_FakeSdk):
    def get_holdings(self):
        return [{"symbol": "RELIANCE", "quantity": "10", "avg_price": "2400.5", "ltp": "2456.7"}]

    def get_positions(self):
        return [{"symbol": "TCS", "quantity": "5", "product": "MIS",
                  "avg_price": "3800.0", "ltp": "3820.5"}]

    def get_margin(self):
        return {"available": "50000.0", "used": "10000.0", "total": "60000.0"}


def test_get_holdings_returns_normalized_list():
    client = _authed_client(_FakeSdkWithPortfolio())
    result = client.get_holdings()
    assert result == [{"symbol": "RELIANCE", "quantity": 10, "avg_price": 2400.5, "ltp": 2456.7}]


def test_get_positions_returns_normalized_list():
    client = _authed_client(_FakeSdkWithPortfolio())
    result = client.get_positions()
    assert result == [{"symbol": "TCS", "quantity": 5, "product": "MIS",
                        "avg_price": 3800.0, "ltp": 3820.5}]


def test_get_margin_returns_normalized_dict():
    client = _authed_client(_FakeSdkWithPortfolio())
    result = client.get_margin()
    assert result == {"available": 50000.0, "used": 10000.0, "total": 60000.0}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: FAIL — `AttributeError: 'GrowwClient' object has no attribute 'get_holdings'`

- [ ] **Step 3: Implement the three methods**

Add to `GrowwClient`:

```python
    def get_holdings(self) -> list[dict]:
        self._require_auth()
        raw = _retry(lambda: self._sdk.get_holdings())
        return [
            {"symbol": h["symbol"], "quantity": int(h["quantity"]),
             "avg_price": float(h["avg_price"]), "ltp": float(h["ltp"])}
            for h in raw
        ]

    def get_positions(self) -> list[dict]:
        self._require_auth()
        raw = _retry(lambda: self._sdk.get_positions())
        return [
            {"symbol": p["symbol"], "quantity": int(p["quantity"]), "product": p["product"],
             "avg_price": float(p["avg_price"]), "ltp": float(p["ltp"])}
            for p in raw
        ]

    def get_margin(self) -> dict:
        self._require_auth()
        raw = _retry(lambda: self._sdk.get_margin())
        return {"available": float(raw["available"]), "used": float(raw["used"]),
                "total": float(raw["total"])}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: PASS (15 tests)

- [ ] **Step 5: Commit**

```bash
git add groww_client.py tests/test_groww_client.py
git commit -m "Add portfolio reads (get_holdings, get_positions, get_margin)"
```

---

### Task 5: Paper-mode order placement

**Files:**
- Modify: `groww_client.py`
- Test: `tests/test_groww_client.py`

**Interfaces:**
- Consumes: `GrowwClient.get_ltp`, `GrowwClient._require_auth`.
- Produces: `GrowwClient.place_order(self, symbol: str, exchange: str, transaction_type: str, quantity: int, order_type: str, price: Optional[float] = None, product: str = "MIS") -> dict` (paper path). `GrowwClient._simulate_order(self, symbol, transaction_type, quantity, order_type, price) -> dict` — appends to `self._paper_orders`, returns keys `order_id, status, symbol, transaction_type, quantity, order_type, price, mode`. Order IDs are `f"PAPER-{n}"`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_groww_client.py`:

```python
def test_paper_market_order_fills_at_ltp():
    client = _authed_client(_FakeSdk())
    order = client.place_order(symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
                                quantity=10, order_type="MARKET")
    assert order == {"order_id": "PAPER-1", "status": "COMPLETE", "symbol": "RELIANCE",
                      "transaction_type": "BUY", "quantity": 10, "order_type": "MARKET",
                      "price": 100.5, "mode": "paper"}
    assert client._paper_orders == [order]


def test_paper_limit_order_fills_at_limit_price():
    client = _authed_client(_FakeSdk())
    order = client.place_order(symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
                                quantity=10, order_type="LIMIT", price=95.0)
    assert order["price"] == 95.0
    assert order["order_id"] == "PAPER-1"


def test_paper_order_ids_increment():
    client = _authed_client(_FakeSdk())
    first = client.place_order(symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
                                quantity=1, order_type="MARKET")
    second = client.place_order(symbol="TCS", exchange="NSE", transaction_type="SELL",
                                 quantity=1, order_type="MARKET")
    assert first["order_id"] == "PAPER-1"
    assert second["order_id"] == "PAPER-2"
    assert len(client._paper_orders) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: FAIL — `AttributeError: 'GrowwClient' object has no attribute 'place_order'`

- [ ] **Step 3: Implement `place_order` (paper path) and `_simulate_order`**

Add to `GrowwClient`:

```python
    def place_order(self, symbol: str, exchange: str, transaction_type: str, quantity: int,
                     order_type: str, price: Optional[float] = None, product: str = "MIS") -> dict:
        self._require_auth()
        if self.mode == "paper":
            return self._simulate_order(symbol, transaction_type, quantity, order_type, price)
        raise NotImplementedError  # live path added in Task 6

    def _simulate_order(self, symbol: str, transaction_type: str, quantity: int,
                         order_type: str, price: Optional[float]) -> dict:
        if order_type == "LIMIT" and price is not None:
            fill_price = price
        else:
            fill_price = self.get_ltp([symbol])[symbol]
        self._paper_order_seq += 1
        order = {
            "order_id": f"PAPER-{self._paper_order_seq}", "status": "COMPLETE", "symbol": symbol,
            "transaction_type": transaction_type, "quantity": quantity,
            "order_type": order_type, "price": fill_price, "mode": "paper",
        }
        self._paper_orders.append(order)
        return order
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: PASS (18 tests)

- [ ] **Step 5: Commit**

```bash
git add groww_client.py tests/test_groww_client.py
git commit -m "Add paper-mode order simulation"
```

---

### Task 6: Live-mode orders, order status, cancel

**Files:**
- Modify: `groww_client.py`
- Test: `tests/test_groww_client.py`

**Interfaces:**
- Consumes: `GrowwClient._require_auth`, `_retry`.
- Produces: `GrowwClient.place_order` live path (replaces the `NotImplementedError`) — returns keys `order_id, status, symbol, transaction_type, quantity, order_type, price, mode`. `GrowwClient.get_order_status(self, order_id: str) -> dict` (keys `order_id, status`; paper IDs read from `self._paper_orders`, live IDs call the SDK with retry). `GrowwClient.cancel_order(self, order_id: str) -> dict` (paper: marks the stored order `CANCELLED`; live: calls SDK, no retry).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_groww_client.py`:

```python
class _FakeSdkWithOrders(_FakeSdk):
    def __init__(self):
        super().__init__()
        self.placed = []
        self.cancelled = []

    def place_order(self, **kwargs):
        self.placed.append(kwargs)
        return {"order_id": "LIVE-1", "status": "PENDING"}

    def get_order_status(self, order_id):
        return {"status": "COMPLETE"}

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        return {"status": "CANCELLED"}


def test_live_place_order_calls_sdk_and_normalizes():
    sdk = _FakeSdkWithOrders()
    client = _authed_client(sdk)
    client.mode = "live"
    order = client.place_order(symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
                                quantity=10, order_type="MARKET")
    assert order == {"order_id": "LIVE-1", "status": "PENDING", "symbol": "RELIANCE",
                      "transaction_type": "BUY", "quantity": 10, "order_type": "MARKET",
                      "price": None, "mode": "live"}
    assert sdk.placed == [{"symbol": "RELIANCE", "exchange": "NSE", "transaction_type": "BUY",
                            "quantity": 10, "order_type": "MARKET", "price": None, "product": "MIS"}]


def test_live_place_order_error_wrapped_no_retry():
    class FailingSdk(_FakeSdkWithOrders):
        def place_order(self, **kwargs):
            self.placed.append(kwargs)
            raise RuntimeError("exchange rejected")

    sdk = FailingSdk()
    client = _authed_client(sdk)
    client.mode = "live"
    with pytest.raises(GrowwClientError, match="order placement failed"):
        client.place_order(symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
                            quantity=10, order_type="MARKET")
    assert len(sdk.placed) == 1  # exactly one attempt, no retry


def test_get_order_status_paper_reads_local_log():
    client = _authed_client(_FakeSdk())
    order = client.place_order(symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
                                quantity=10, order_type="MARKET")
    status = client.get_order_status(order["order_id"])
    assert status["status"] == "COMPLETE"


def test_get_order_status_unknown_paper_id_raises():
    client = _authed_client(_FakeSdk())
    with pytest.raises(GrowwClientError, match="unknown paper order id"):
        client.get_order_status("PAPER-999")


def test_get_order_status_live_calls_sdk():
    sdk = _FakeSdkWithOrders()
    client = _authed_client(sdk)
    status = client.get_order_status("LIVE-1")
    assert status == {"order_id": "LIVE-1", "status": "COMPLETE"}


def test_cancel_order_paper_marks_cancelled():
    client = _authed_client(_FakeSdk())
    order = client.place_order(symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
                                quantity=10, order_type="MARKET")
    cancelled = client.cancel_order(order["order_id"])
    assert cancelled["status"] == "CANCELLED"
    assert client._paper_orders[0]["status"] == "CANCELLED"


def test_cancel_order_live_calls_sdk():
    sdk = _FakeSdkWithOrders()
    client = _authed_client(sdk)
    result = client.cancel_order("LIVE-1")
    assert result == {"order_id": "LIVE-1", "status": "CANCELLED"}
    assert sdk.cancelled == ["LIVE-1"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: FAIL — live `place_order` raises `NotImplementedError`; `get_order_status`/`cancel_order` don't exist yet

- [ ] **Step 3: Implement the live path, `get_order_status`, `cancel_order`**

Replace the `raise NotImplementedError` line in `place_order` with:

```python
        try:
            raw = self._sdk.place_order(
                symbol=symbol, exchange=exchange, transaction_type=transaction_type,
                quantity=quantity, order_type=order_type, price=price, product=product,
            )
        except Exception as e:
            raise GrowwClientError(f"order placement failed: {e}") from e
        return {"order_id": raw["order_id"], "status": raw["status"], "symbol": symbol,
                "transaction_type": transaction_type, "quantity": quantity,
                "order_type": order_type, "price": price, "mode": "live"}
```

Add to `GrowwClient`:

```python
    def get_order_status(self, order_id: str) -> dict:
        self._require_auth()
        if order_id.startswith("PAPER-"):
            for order in self._paper_orders:
                if order["order_id"] == order_id:
                    return order
            raise GrowwClientError(f"unknown paper order id: {order_id}")
        raw = _retry(lambda: self._sdk.get_order_status(order_id))
        return {"order_id": order_id, "status": raw["status"]}

    def cancel_order(self, order_id: str) -> dict:
        self._require_auth()
        if order_id.startswith("PAPER-"):
            for order in self._paper_orders:
                if order["order_id"] == order_id:
                    order["status"] = "CANCELLED"
                    return order
            raise GrowwClientError(f"unknown paper order id: {order_id}")
        try:
            raw = self._sdk.cancel_order(order_id)
        except Exception as e:
            raise GrowwClientError(f"order cancellation failed: {e}") from e
        return {"order_id": order_id, "status": raw["status"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: PASS (25 tests)

- [ ] **Step 5: Commit**

```bash
git add groww_client.py tests/test_groww_client.py
git commit -m "Add live order placement, order status, and cancellation"
```

---

### Task 7: OCO smart orders (target + stop-loss)

**Files:**
- Modify: `groww_client.py`
- Test: `tests/test_groww_client.py`

**Interfaces:**
- Consumes: `GrowwClient._require_auth`, `_retry`.
- Produces: `GrowwClient.place_oco_order(self, symbol: str, entry: dict, target: dict, stop_loss: dict) -> dict` (keys `order_id, status, symbol, target, stop_loss, mode`; paper status starts `"ACTIVE"`). `GrowwClient.get_smart_order_status(self, order_id: str) -> dict` (paper IDs delegate to `get_order_status`; live IDs call `self._sdk.get_smart_order(order_id)` with retry).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_groww_client.py`:

```python
class _FakeSdkWithSmartOrders(_FakeSdkWithOrders):
    def place_oco_order(self, **kwargs):
        return {"order_id": "LIVE-OCO-1", "status": "ACTIVE"}

    def get_smart_order(self, order_id):
        return {"status": "TRIGGERED"}


def test_paper_oco_order_is_active_and_logged():
    client = _authed_client(_FakeSdkWithSmartOrders())
    order = client.place_oco_order(
        symbol="RELIANCE",
        entry={"transaction_type": "BUY", "quantity": 10, "order_type": "MARKET"},
        target={"trigger_price": 2500.0, "order_type": "LIMIT", "price": 2500.0},
        stop_loss={"trigger_price": 2400.0, "order_type": "LIMIT", "price": 2395.0},
    )
    assert order["order_id"] == "PAPER-OCO-1"
    assert order["status"] == "ACTIVE"
    assert order["mode"] == "paper"
    assert client._paper_orders[-1] == order


def test_live_oco_order_calls_sdk():
    sdk = _FakeSdkWithSmartOrders()
    client = _authed_client(sdk)
    client.mode = "live"
    target = {"trigger_price": 2500.0, "order_type": "LIMIT", "price": 2500.0}
    stop_loss = {"trigger_price": 2400.0, "order_type": "LIMIT", "price": 2395.0}
    order = client.place_oco_order(
        symbol="RELIANCE",
        entry={"transaction_type": "BUY", "quantity": 10, "order_type": "MARKET"},
        target=target, stop_loss=stop_loss,
    )
    assert order == {"order_id": "LIVE-OCO-1", "status": "ACTIVE", "symbol": "RELIANCE",
                      "target": target, "stop_loss": stop_loss, "mode": "live"}


def test_get_smart_order_status_paper_delegates():
    client = _authed_client(_FakeSdkWithSmartOrders())
    order = client.place_oco_order(
        symbol="RELIANCE",
        entry={"transaction_type": "BUY", "quantity": 10, "order_type": "MARKET"},
        target={"trigger_price": 2500.0, "order_type": "LIMIT", "price": 2500.0},
        stop_loss={"trigger_price": 2400.0, "order_type": "LIMIT", "price": 2395.0},
    )
    status = client.get_smart_order_status(order["order_id"])
    assert status["status"] == "ACTIVE"


def test_get_smart_order_status_live_calls_sdk():
    client = _authed_client(_FakeSdkWithSmartOrders())
    status = client.get_smart_order_status("LIVE-OCO-1")
    assert status == {"order_id": "LIVE-OCO-1", "status": "TRIGGERED"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: FAIL — `AttributeError: 'GrowwClient' object has no attribute 'place_oco_order'`

- [ ] **Step 3: Implement `place_oco_order` and `get_smart_order_status`**

Add to `GrowwClient`:

```python
    def place_oco_order(self, symbol: str, entry: dict, target: dict, stop_loss: dict) -> dict:
        self._require_auth()
        if self.mode == "paper":
            self._paper_order_seq += 1
            order = {
                "order_id": f"PAPER-OCO-{self._paper_order_seq}", "status": "ACTIVE",
                "symbol": symbol, "target": target, "stop_loss": stop_loss, "mode": "paper",
            }
            self._paper_orders.append(order)
            return order
        try:
            raw = self._sdk.place_oco_order(symbol=symbol, entry=entry, target=target, stop_loss=stop_loss)
        except Exception as e:
            raise GrowwClientError(f"OCO order placement failed: {e}") from e
        return {"order_id": raw["order_id"], "status": raw["status"], "symbol": symbol,
                "target": target, "stop_loss": stop_loss, "mode": "live"}

    def get_smart_order_status(self, order_id: str) -> dict:
        self._require_auth()
        if order_id.startswith("PAPER-"):
            return self.get_order_status(order_id)
        raw = _retry(lambda: self._sdk.get_smart_order(order_id))
        return {"order_id": order_id, "status": raw["status"]}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_groww_client.py -v`
Expected: PASS (29 tests)

- [ ] **Step 5: Commit**

```bash
git add groww_client.py tests/test_groww_client.py
git commit -m "Add OCO smart orders (target + stop-loss)"
```

---

### Task 8: Manual auth smoke script + verify real SDK surface

**Files:**
- Create: `scripts/smoke_test_groww_auth.py`
- Modify: `groww_client.py` (only if the real `growwapi` SDK's actual method names differ from the assumptions below — see Step 1)
- Create: `README.md`

**Interfaces:**
- Consumes: `GrowwClient`, `GrowwClient.authenticate`, `GrowwClient.get_holdings`, `GrowwClient.get_ltp`.
- Produces: nothing new consumed by later phases; this task validates Task 1–7's assumptions against the real SDK and documents usage.

- [ ] **Step 1: Verify the real `growwapi` SDK surface**

This plan's `_default_sdk_factory` and the live-mode SDK calls (`place_order`, `get_holdings`, `get_positions`, `get_margin`, `get_ltp`, `get_quote`, `get_order_status`, `place_oco_order`, `get_smart_order`, `cancel_order`) were designed from Groww's published API docs (`https://groww.in/trade-api/docs/python-sdk`, `.../orders`, `.../smart-orders`, `.../feed`), not from direct inspection of the installed package. Before running the smoke test, confirm the installed SDK matches:

```bash
.venv/bin/pip show growwapi
.venv/bin/python -c "from growwapi import GrowwAPI; print([m for m in dir(GrowwAPI) if not m.startswith('_')])"
```

Compare the printed method names against the calls listed above. If any name or parameter differs (e.g. `get_positions` vs `positions`, `trading_symbol` vs `symbol`), update the corresponding `self._sdk.*` call site(s) in `groww_client.py` to match, and re-run the full test suite (`.venv/bin/python -m pytest tests/test_groww_client.py -v`) to confirm nothing broke — the unit tests use a fake SDK double so they won't catch a mismatch themselves, only manual comparison against the real SDK will.

- [ ] **Step 2: Write the smoke test script**

Create `scripts/smoke_test_groww_auth.py`:

```python
#!/usr/bin/env python3
"""Manual, not-CI smoke test: authenticate for real and call read-only endpoints only.

Safe to run against a live Groww account — places no orders. Confirms GROWW_API_KEY /
GROWW_TOTP_SECRET are valid and the SDK is reachable end-to-end.

Usage: GROWW_API_KEY=... GROWW_TOTP_SECRET=... .venv/bin/python scripts/smoke_test_groww_auth.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from groww_client import GrowwClient, GrowwClientError


def main() -> None:
    client = GrowwClient(mode="paper")
    try:
        client.authenticate()
        print("auth: OK")
        holdings = client.get_holdings()
        print(f"get_holdings: OK ({len(holdings)} holdings)")
        if holdings:
            symbol = holdings[0]["symbol"]
            ltp = client.get_ltp([symbol])
            print(f"get_ltp: OK ({symbol} = {ltp[symbol]})")
    except GrowwClientError as e:
        print(f"FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the smoke test against the real account**

```bash
GROWW_API_KEY=$GROWW_API_KEY GROWW_TOTP_SECRET=$GROWW_TOTP_SECRET .venv/bin/python scripts/smoke_test_groww_auth.py
```

Expected: `auth: OK`, `get_holdings: OK (...)`, and (if any holdings exist) `get_ltp: OK (...)`. If this fails, fix Step 1's SDK-surface mismatches or credentials before proceeding — do not move on to Phase 2 with unverified auth.

- [ ] **Step 4: Write `README.md`**

```markdown
# autoIntraday

Automated intraday trading on Groww. See `docs/superpowers/specs/` for the full system
design and `docs/superpowers/plans/` for implementation plans.

## Phase 1: Groww API client

`groww_client.py` wraps the official `growwapi` SDK with a `paper`/`live` mode split —
paper mode simulates every order locally against live prices; live mode calls Groww for
real. See `docs/superpowers/specs/2026-07-09-groww-client-design.md` for the full design.

### Setup

\`\`\`bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env  # fill in GROWW_API_KEY and GROWW_TOTP_SECRET
\`\`\`

### Test

\`\`\`bash
.venv/bin/python -m pytest tests/test_groww_client.py -v
\`\`\`

### Verify real credentials (read-only, safe to run against a live account)

\`\`\`bash
export $(cat .env | xargs) && .venv/bin/python scripts/smoke_test_groww_auth.py
\`\`\`
```

- [ ] **Step 5: Commit**

```bash
git add scripts/smoke_test_groww_auth.py README.md groww_client.py
git commit -m "Add auth smoke test script and README"
```

---

## Self-Review Notes

- **Spec coverage:** TOTP auth (Task 2) · market data reads (Task 3) · portfolio reads (Task 4) · paper order simulation (Task 5) · live orders/status/cancel (Task 6) · OCO smart orders (Task 7) · single `GrowwClientError` type, retry-on-read/no-retry-on-write, structural paper/live split (throughout) · unit tests mocking the SDK boundary + manual smoke script (Task 8) · env-var-only credentials with `.env.example` (Task 1). All spec sections have a corresponding task.
- **Type consistency checked:** `get_ltp` return shape (`dict[str, float]`) used identically in `_simulate_order`; order dict keys (`order_id, status, symbol, transaction_type, quantity, order_type, price, mode`) match across `_simulate_order` and the live path in Task 6; `get_order_status`/`cancel_order` paper-vs-live branching by `order_id.startswith("PAPER-")` is consistent everywhere it's used (Tasks 6–7).
- **No placeholders:** every step has complete, runnable code; the one open unknown (exact real SDK method names) is explicitly called out as a verification step in Task 8 rather than silently assumed.
