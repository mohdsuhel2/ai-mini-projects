import pyotp
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


def test_authenticate_missing_credentials_raises(monkeypatch):
    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_TOTP_SECRET", raising=False)
    client = GrowwClient(mode="paper", sdk_factory=lambda k, t: "sdk")
    with pytest.raises(GrowwClientError, match="GROWW_API_KEY"):
        client.authenticate()


def test_ensure_ready_paper_needs_no_credentials(monkeypatch):
    """Paper mode must run a full cycle with zero broker credentials: ensure_ready() marks the
    client ready (no Groww login) and the local order simulator then works."""
    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_TOTP_SECRET", raising=False)
    client = GrowwClient(mode="paper", sdk_factory=lambda k, t: "sdk")
    client.ensure_ready()                    # does NOT raise despite missing creds
    client._require_auth()                   # paper is considered authenticated
    order = client.place_order(symbol="RELIANCE", exchange="NSE",
                               transaction_type="BUY", quantity=1, order_type="MARKET",
                               price=100.5)
    assert order["mode"] == "paper"
    assert order["status"] == "COMPLETE"


def test_ensure_ready_live_requires_credentials(monkeypatch):
    """Live mode still authenticates — missing credentials must fail loudly."""
    monkeypatch.delenv("GROWW_API_KEY", raising=False)
    monkeypatch.delenv("GROWW_TOTP_SECRET", raising=False)
    client = GrowwClient(mode="live", sdk_factory=lambda k, t: "sdk")
    with pytest.raises(GrowwClientError, match="GROWW_API_KEY"):
        client.ensure_ready()


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

    def get_ltp(self, exchange_trading_symbols, segment=None, timeout=None):
        return {s: 100.5 for s in exchange_trading_symbols}

    def get_quote(self, trading_symbol=None, exchange=None, segment=None, timeout=None):
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


class _FakeSdkWithPortfolio(_FakeSdk):
    def get_holdings_for_user(self, timeout=None):
        # real Groww fields: trading_symbol / quantity / average_price (no ltp)
        return [{"trading_symbol": "RELIANCE", "quantity": "10", "average_price": "2400.5"}]

    def get_positions_for_user(self, segment=None, timeout=None):
        # real Groww fields: trading_symbol / quantity / product / net_price (no ltp)
        return [{"trading_symbol": "TCS", "quantity": "5", "product": "MIS",
                  "net_price": "3800.0"}]

    def get_available_margin_details(self, timeout=None):
        return {"available": "50000.0", "used": "10000.0", "total": "60000.0"}


def test_get_holdings_returns_normalized_list():
    client = _authed_client(_FakeSdkWithPortfolio())
    result = client.get_holdings()
    assert result == [{"symbol": "RELIANCE", "quantity": 10, "avg_price": 2400.5}]


def test_get_positions_returns_normalized_list():
    client = _authed_client(_FakeSdkWithPortfolio())
    result = client.get_positions()
    assert result == [{"symbol": "TCS", "quantity": 5, "product": "MIS",
                        "avg_price": 3800.0}]


def test_get_margin_returns_normalized_dict():
    client = _authed_client(_FakeSdkWithPortfolio())
    result = client.get_margin()
    assert result == {"available": 50000.0, "used": 10000.0, "total": 60000.0}


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


class _FakeSdkWithOrders(_FakeSdk):
    def __init__(self):
        super().__init__()
        self.placed = []
        self.cancelled = []

    def place_order(self, **kwargs):
        self.placed.append(kwargs)
        return {"groww_order_id": "LIVE-1", "order_status": "PENDING"}

    def get_order_status(self, segment=None, groww_order_id=None, timeout=None):
        return {"order_status": "COMPLETE"}

    def cancel_order(self, groww_order_id=None, segment=None, timeout=None):
        self.cancelled.append(groww_order_id)
        return {"order_status": "CANCELLED"}


def test_live_place_order_calls_sdk_and_normalizes():
    sdk = _FakeSdkWithOrders()
    client = _authed_client(sdk)
    client.mode = "live"
    order = client.place_order(symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
                                quantity=10, order_type="MARKET")
    assert order == {"order_id": "LIVE-1", "status": "PENDING", "symbol": "RELIANCE",
                      "transaction_type": "BUY", "quantity": 10, "order_type": "MARKET",
                      "price": None, "mode": "live"}
    assert sdk.placed == [{"trading_symbol": "RELIANCE", "exchange": "NSE", "transaction_type": "BUY",
                            "quantity": 10, "order_type": "MARKET", "price": 0.0, "product": "MIS",
                            "segment": "CASH", "validity": "DAY", "trigger_price": None}]


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
    assert status == {"order_id": order["order_id"], "status": "COMPLETE"}


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
    assert cancelled == {"order_id": order["order_id"], "status": "CANCELLED"}
    assert client._paper_orders[0]["status"] == "CANCELLED"


def test_paper_order_returns_are_not_aliased_to_internal_state():
    client = _authed_client(_FakeSdk())
    order = client.place_order(symbol="RELIANCE", exchange="NSE", transaction_type="BUY",
                                quantity=10, order_type="MARKET")
    # Mutate the dict returned from place_order (_simulate_order) - must not affect internal state.
    order["status"] = "TAMPERED"
    assert client._paper_orders[0]["status"] == "COMPLETE"

    # Mutate the dict returned from get_order_status - must not affect internal state.
    status = client.get_order_status(order["order_id"])
    status["status"] = "TAMPERED"
    assert client._paper_orders[0]["status"] == "COMPLETE"

    # Mutate the dict returned from cancel_order - internal state should still update to
    # CANCELLED via cancel_order itself, but further mutation of the returned dict must not
    # propagate back.
    cancelled = client.cancel_order(order["order_id"])
    cancelled["status"] = "TAMPERED"
    assert client._paper_orders[0]["status"] == "CANCELLED"


def test_cancel_order_live_calls_sdk():
    sdk = _FakeSdkWithOrders()
    client = _authed_client(sdk)
    result = client.cancel_order("LIVE-1")
    assert result == {"order_id": "LIVE-1", "status": "CANCELLED"}
    assert sdk.cancelled == ["LIVE-1"]


class _FakeSdkWithSmartOrders(_FakeSdkWithOrders):
    def create_smart_order(self, **kwargs):
        return {"smart_order_id": "LIVE-OCO-1", "smart_order_status": "ACTIVE"}

    def get_smart_order(self, segment=None, smart_order_type=None, smart_order_id=None, timeout=None):
        return {"smart_order_status": "TRIGGERED"}


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


def test_get_open_orders_paper_is_empty():
    client = _authed_client(_FakeSdk())
    assert client.get_open_orders() == []


def test_get_open_orders_live_maps_fields():
    class _Sdk(_FakeSdk):
        def get_order_list(self, segment=None, page=None, page_size=None, timeout=None):
            return {"order_list": [
                {"trading_symbol": "AAA", "groww_order_id": "G1", "order_status": "APPROVED",
                 "transaction_type": "BUY"},
                {"trading_symbol": "BBB", "groww_order_id": "G2", "order_status": "EXECUTED",
                 "transaction_type": "SELL"},
            ]}

    import os
    os.environ["GROWW_API_KEY"] = "key123"
    os.environ["GROWW_TOTP_SECRET"] = pyotp.random_base32()
    client = GrowwClient(mode="live", sdk_factory=lambda k, t: _Sdk())
    client.authenticate()
    orders = client.get_open_orders()
    assert orders == [
        {"symbol": "AAA", "order_id": "G1", "status": "APPROVED",
         "transaction_type": "BUY"},
        {"symbol": "BBB", "order_id": "G2", "status": "EXECUTED",
         "transaction_type": "SELL"},
    ]
