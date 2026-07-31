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
    assert status == {"order_id": "LIVE-1", "status": "COMPLETE", "reason": None}


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
    assert result == {"order_id": "LIVE-1", "status": "CANCELLED", "cancelled": True}
    assert sdk.cancelled == ["LIVE-1"]


def test_cancel_order_terminal_is_benign():
    """Groww rejects cancelling an already-filled/cancelled order. That's a no-op, not a failure
    (the resting order is gone) — the armed-exit race where a take-profit already filled."""
    class _Sdk(_FakeSdkWithOrders):
        def cancel_order(self, groww_order_id=None, segment=None, timeout=None):
            raise RuntimeError("order not cancellable")
        def get_order_status(self, segment=None, groww_order_id=None, timeout=None):
            return {"order_status": "EXECUTED"}
    client = _authed_client(_Sdk())
    result = client.cancel_order("LIVE-1")
    assert result["status"] == "EXECUTED" and result["cancelled"] is False


def test_cancel_order_not_found_is_benign():
    class _Sdk(_FakeSdkWithOrders):
        def cancel_order(self, groww_order_id=None, segment=None, timeout=None):
            raise RuntimeError("order not found")
        def get_order_status(self, segment=None, groww_order_id=None, timeout=None):
            raise RuntimeError("no such order")
    client = _authed_client(_Sdk())
    result = client.cancel_order("GHOST")
    assert result["status"] == "NOT_FOUND" and result["cancelled"] is False


def test_cancel_order_real_failure_still_raises():
    """A cancel failure on an order that is STILL open (not terminal) is a genuine error."""
    class _Sdk(_FakeSdkWithOrders):
        def cancel_order(self, groww_order_id=None, segment=None, timeout=None):
            raise RuntimeError("broker timeout")
        def get_order_status(self, segment=None, groww_order_id=None, timeout=None):
            return {"order_status": "OPEN"}
    client = _authed_client(_Sdk())
    with pytest.raises(GrowwClientError, match="order cancellation failed"):
        client.cancel_order("LIVE-1")


def test_ensure_ready_live_is_idempotent(monkeypatch):
    """Live auth must happen ONCE and be reused — re-logging-in on every call re-runs TOTP and
    trips Groww's auth rate limit (the gateway calls ensure_ready() per request)."""
    monkeypatch.setenv("GROWW_API_KEY", "key123")
    monkeypatch.setenv("GROWW_TOTP_SECRET", pyotp.random_base32())
    calls = {"n": 0}
    def counting_factory(api_key, totp):
        calls["n"] += 1
        return _FakeSdkWithOrders()
    client = GrowwClient(mode="live", sdk_factory=counting_factory)
    client.ensure_ready()
    client.ensure_ready()
    client.ensure_ready()
    assert calls["n"] == 1
    # a forced re-auth (session expiry) does log in again
    client.reauthenticate()
    assert calls["n"] == 2


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
                 "transaction_type": "BUY", "product": "MIS"},
                {"trading_symbol": "BBB", "groww_order_id": "G2", "order_status": "EXECUTED",
                 "transaction_type": "SELL", "product": "CNC"},
                # product omitted entirely -> None, so reconcile never treats it as MIS
                {"trading_symbol": "CCC", "groww_order_id": "G3", "order_status": "APPROVED",
                 "transaction_type": "SELL"},
            ]}

    import os
    os.environ["GROWW_API_KEY"] = "key123"
    os.environ["GROWW_TOTP_SECRET"] = pyotp.random_base32()
    client = GrowwClient(mode="live", sdk_factory=lambda k, t: _Sdk())
    client.authenticate()
    orders = client.get_open_orders()
    assert orders == [
        {"symbol": "AAA", "order_id": "G1", "status": "APPROVED", "reason": None,
         "transaction_type": "BUY", "product": "MIS"},
        {"symbol": "BBB", "order_id": "G2", "status": "EXECUTED", "reason": None,
         "transaction_type": "SELL", "product": "CNC"},
        {"symbol": "CCC", "order_id": "G3", "status": "APPROVED", "reason": None,
         "transaction_type": "SELL", "product": None},
    ]


def test_rate_limited_auth_sets_cooldown_and_stops_retrying(monkeypatch):
    """A rate-limited login must NOT be retried on the next call — retrying every request keeps
    Groww's rate-limit window from resetting. Cooldown breaks that self-perpetuating loop."""
    monkeypatch.setenv("GROWW_API_KEY", "k")
    monkeypatch.setenv("GROWW_TOTP_SECRET", pyotp.random_base32())
    calls = {"n": 0}
    def rate_limited_factory(api_key, totp):
        calls["n"] += 1
        raise RuntimeError("The rate limit for the Groww API has been exceeded. Please try later.")
    client = GrowwClient(mode="live", sdk_factory=rate_limited_factory)
    with pytest.raises(GrowwClientError, match="rate limit"):
        client.ensure_ready()                       # attempt 1 -> rate-limited -> cooldown set
    assert calls["n"] == 1 and client._auth_cooldown_until > 0
    with pytest.raises(GrowwClientError, match="cooling down"):
        client.ensure_ready()                       # within cooldown -> NO new login attempt
    assert calls["n"] == 1
    client._auth_cooldown_until = 0.0               # window elapsed -> retry is allowed again
    with pytest.raises(GrowwClientError, match="rate limit"):
        client.ensure_ready()
    assert calls["n"] == 2


def test_non_rate_limit_auth_failure_sets_no_cooldown(monkeypatch):
    monkeypatch.setenv("GROWW_API_KEY", "k")
    monkeypatch.setenv("GROWW_TOTP_SECRET", pyotp.random_base32())
    def bad_factory(api_key, totp):
        raise RuntimeError("invalid TOTP")
    client = GrowwClient(mode="live", sdk_factory=bad_factory)
    with pytest.raises(GrowwClientError, match="authentication failed"):
        client.ensure_ready()
    assert client._auth_cooldown_until == 0.0       # ordinary failure -> retry immediately, no cooldown


# --- Access-token persistence (GROWW_TOKEN_CACHE_PATH) ------------------------------------------

def test_token_cache_roundtrip_and_clear(monkeypatch, tmp_path):
    import groww_client as gc
    monkeypatch.setenv("GROWW_TOKEN_CACHE_PATH", str(tmp_path / "tok.json"))
    assert gc._load_cached_token() is None
    gc._save_cached_token("TKN-1")
    assert gc._load_cached_token() == "TKN-1"          # persisted + reused
    gc._clear_cached_token()
    assert gc._load_cached_token() is None             # dropped on invalidation


def test_token_cache_expired_returns_none(monkeypatch, tmp_path):
    import json as _json
    import groww_client as gc
    cache = tmp_path / "tok.json"
    monkeypatch.setenv("GROWW_TOKEN_CACHE_PATH", str(cache))
    cache.write_text(_json.dumps({"access_token": "OLD", "expiry": 1.0}))   # expiry in the past
    assert gc._load_cached_token() is None             # past 06:00 expiry -> not reused


def test_token_cache_disabled_without_env(monkeypatch):
    import groww_client as gc
    monkeypatch.delenv("GROWW_TOKEN_CACHE_PATH", raising=False)
    assert gc._load_cached_token() is None
    gc._save_cached_token("X")                         # no-op, no error, no file
    gc._clear_cached_token()


def test_next_6am_ist_is_in_the_future():
    import time as _t
    import groww_client as gc
    assert gc._next_6am_ist_epoch() > _t.time()


def test_default_factory_reuses_cached_token(monkeypatch, tmp_path):
    """The whole point: a valid cached token is reused -> NO get_access_token (no login storm)."""
    import sys
    import types
    import groww_client as gc
    monkeypatch.setenv("GROWW_TOKEN_CACHE_PATH", str(tmp_path / "tok.json"))
    gc._save_cached_token("CACHED")
    logins = {"n": 0}
    class FakeAPI:
        def __init__(self, token):
            self.token = token
        @staticmethod
        def get_access_token(api_key=None, totp=None):
            logins["n"] += 1
            return "FRESH"
    mod = types.ModuleType("growwapi")
    mod.GrowwAPI = FakeAPI
    monkeypatch.setitem(sys.modules, "growwapi", mod)
    sdk = gc._default_sdk_factory("k", "123456")
    assert sdk.token == "CACHED" and logins["n"] == 0   # reused, no login
    gc._clear_cached_token()
    sdk2 = gc._default_sdk_factory("k", "123456")
    assert sdk2.token == "FRESH" and logins["n"] == 1    # no cache -> mint once
    assert gc._load_cached_token() == "FRESH"            # and persist it


def test_reauthenticate_clears_cache(monkeypatch, tmp_path):
    import groww_client as gc
    monkeypatch.setenv("GROWW_TOKEN_CACHE_PATH", str(tmp_path / "tok.json"))
    monkeypatch.setenv("GROWW_API_KEY", "k")
    monkeypatch.setenv("GROWW_TOTP_SECRET", pyotp.random_base32())
    gc._save_cached_token("STALE")
    client = gc.GrowwClient(mode="live", sdk_factory=lambda k, t: "sdk")
    client.reauthenticate()                              # forces fresh -> must drop the stale token
    assert gc._load_cached_token() is None
    assert client._sdk == "sdk"


def test_prefer_ipv4_forces_af_inet(monkeypatch):
    """Outbound DNS resolution must be pinned to IPv4 so Groww sees the whitelisted static IPv4,
    not the VPS's IPv6 egress (which Groww rejects as 'unregistered IP address')."""
    import socket
    import groww_client as gc
    monkeypatch.setenv("GROWW_FORCE_IPV4", "1")
    monkeypatch.setattr(socket, "_ai_ipv4_forced", False, raising=False)
    seen = {}
    def fake_gai(host, port, family=0, *a, **k):
        seen["family"] = family
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", port))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_gai)
    gc._prefer_ipv4()
    socket.getaddrinfo("api.groww.in", 443)          # goes through the IPv4-forcing wrapper
    assert seen["family"] == socket.AF_INET


def test_prefer_ipv4_can_be_disabled(monkeypatch):
    import socket
    import groww_client as gc
    monkeypatch.setenv("GROWW_FORCE_IPV4", "0")
    monkeypatch.setattr(socket, "_ai_ipv4_forced", False, raising=False)
    before = socket.getaddrinfo
    gc._prefer_ipv4()
    assert socket.getaddrinfo is before              # no patch applied when disabled


# --- token-rejection detection (gateway self-heal) -------------------------------------------

def test_is_token_invalid_error_matches_real_groww_messages():
    from groww_client import is_token_invalid_error
    # the exact message Groww returned on 2026-07-30 that the gateway used to MISS
    raw = "Authentication failed. Your API token has either expired or is invalid."
    assert is_token_invalid_error(raw)
    # wrapped by the client's read-retry and by a write failure -> still detected
    assert is_token_invalid_error(f"failed after 3 attempts: {raw}")
    assert is_token_invalid_error(f"order placement failed: {raw}")
    # other genuine token-rejection phrasings
    assert is_token_invalid_error("401 Unauthorized")
    assert is_token_invalid_error("Session expired, please log in again")
    assert is_token_invalid_error("Invalid access token")


def test_is_token_invalid_error_ignores_rate_limit_and_unrelated():
    from groww_client import is_token_invalid_error
    # a rate-limit must NEVER count as a token rejection (token is still valid; re-minting hurts)
    assert not is_token_invalid_error("Rate limit exceeded, please retry later")
    assert not is_token_invalid_error("Too many requests — rate limit; authentication failed")
    # unrelated errors
    assert not is_token_invalid_error("gateway unreachable")
    assert not is_token_invalid_error("insufficient funds")
    assert not is_token_invalid_error("")
    assert not is_token_invalid_error(None)


# ---- per-order-type payload construction (2026-07-31) ---------------------------------------
# Root cause of "Price is beyond permissible range": GrowwAPI.place_order defaults price=0.0 and
# hardcodes BOTH "price" and "trigger_price" into its request body, so a stop-loss with no limit
# went out as {"order_type": "SL_M", "trigger_price": 842.10, "price": 0} and the exchange
# validated a limit of 0 against the trigger. The SDK has no SL_M handling and no validation --
# it is a passthrough -- so it does NOT require price == trigger_price. Its own place_smart_order
# builds bodies conditionally and documents a stop-loss leg's price as optional, so an absent
# price is legitimate; place_order simply cannot omit the key, and None serialises to null.

from groww_client import build_order_price_fields


def test_market_keeps_the_proven_zero_price_and_sends_no_trigger():
    """MARKET deliberately keeps price=0.0 rather than null. Conceptually a market order has no
    limit, but 0.0 is empirically proven on this account (every entry and exit) while null is
    unverified — and MARKET is the path that both opens and closes positions."""
    assert build_order_price_fields("MARKET", None, None) == (0.0, None)
    # a caller-supplied price (paper seeds one) is never forwarded as a limit
    assert build_order_price_fields("MARKET", 1234.0, 999.0) == (0.0, None)


def test_limit_requires_a_positive_price_and_sends_no_trigger():
    assert build_order_price_fields("LIMIT", 850.0, None) == (850.0, None)
    assert build_order_price_fields("LIMIT", 850.0, 999.0) == (850.0, None)   # trigger dropped
    for bad in (None, 0, -1):
        with pytest.raises(GrowwClientError, match="LIMIT order requires"):
            build_order_price_fields("LIMIT", bad, None)


def test_sl_requires_both_price_and_trigger():
    assert build_order_price_fields("SL", 841.0, 842.10) == (841.0, 842.10)
    # snapped to the symbol's own grid; with no symbol the safe 0.10 fallback applies
    assert build_order_price_fields("SL", 841.03, 842.107, "63MOONS") == (841.05, 842.10)
    assert build_order_price_fields("SL", 841.03, 842.107) == (841.0, 842.1)
    with pytest.raises(GrowwClientError, match="SL order requires a positive trigger_price"):
        build_order_price_fields("SL", 841.0, None)
    with pytest.raises(GrowwClientError, match="SL order requires a positive price"):
        build_order_price_fields("SL", None, 842.10)


def test_sl_m_mirrors_the_trigger_as_its_price():
    """price=0.0 was rejected ("beyond permissible range"), and so was price=null — Groww
    evaluates null as 0 server-side, so the gap is the stock's full price either way. The trigger
    itself gives a zero gap, always inside the band. Verified live, not inferred."""
    assert build_order_price_fields("SL_M", None, 842.10) == (842.10, 842.10)
    # a caller-supplied limit is ignored: SL_M is a market stop, both fields mirror the trigger
    assert build_order_price_fields("SL_M", 850.0, 842.10) == (842.10, 842.10)
    # off-tick triggers are snapped
    assert build_order_price_fields("SL_M", None, 842.107) == (842.10, 842.10)
    for bad in (None, 0, -1):
        with pytest.raises(GrowwClientError, match="SL_M order requires a positive trigger_price"):
            build_order_price_fields("SL_M", None, bad)


def test_unsupported_order_type_fails_locally_with_a_descriptive_error():
    with pytest.raises(GrowwClientError, match="unsupported order_type"):
        build_order_price_fields("BRACKET", 100.0, None)


def _capture_sdk():
    captured = {}

    class _SDK:
        def place_order(self, **kw):
            captured.update(kw)
            return {"groww_order_id": "X1", "order_status": "NEW"}
    return captured, _SDK()


def test_stop_loss_for_an_existing_position_sends_a_null_price():
    """The reported case end to end: long 10 @ 850, protective stop at 842.10."""
    captured, sdk = _capture_sdk()
    c = GrowwClient(mode="live")
    c._sdk = sdk
    c.place_order(symbol="AAA", exchange="NSE", transaction_type="SELL", quantity=10,
                  order_type="SL_M", price=None, trigger_price=842.10)
    assert captured["order_type"] == "SL_M"
    assert captured["trigger_price"] == 842.10
    assert captured["price"] == 842.10          # NOT 0.0 and NOT null — both were rejected live
    assert captured["transaction_type"] == "SELL" and captured["quantity"] == 10


def test_market_and_limit_still_place_correctly():
    captured, sdk = _capture_sdk()
    c = GrowwClient(mode="live")
    c._sdk = sdk
    c.place_order(symbol="AAA", exchange="NSE", transaction_type="BUY", quantity=10,
                  order_type="MARKET", price=850.0)
    assert captured["price"] == 0.0 and captured["trigger_price"] is None

    c.place_order(symbol="AAA", exchange="NSE", transaction_type="BUY", quantity=10,
                  order_type="LIMIT", price=849.5)
    assert captured["price"] == 849.5 and captured["trigger_price"] is None


def test_invalid_order_fails_before_reaching_the_sdk():
    """Validation must happen locally — the SDK is never called with a malformed order."""
    captured, sdk = _capture_sdk()
    c = GrowwClient(mode="live")
    c._sdk = sdk
    with pytest.raises(GrowwClientError):
        c.place_order(symbol="AAA", exchange="NSE", transaction_type="SELL", quantity=10,
                      order_type="SL_M", price=None, trigger_price=None)
    assert captured == {}                        # nothing was sent


def test_every_outgoing_price_is_snapped_to_the_tick_grid():
    """Groww rejects off-tick prices with "choose price in multiples of the tick size". Every
    LIMIT entry placed on 2026-07-30/31 went out off-tick (2253.98, 560.9348, 852.396, 442.764,
    1126.3876) because only the bracket legs were rounded, never the entry."""
    from groww_client import tick_round
    from instrument_master import tick_size_for
    for sym in ("NETWEB", "63MOONS", "UNKNOWNSYM"):
        tick = tick_size_for(sym)
        for raw in (2253.98, 560.9348, 852.396, 442.764, 1126.3876, 4350.528):
            snapped = tick_round(raw, sym)
            assert abs(snapped / tick - round(snapped / tick)) < 1e-9, \
                f"{sym}: {raw} -> {snapped} off its {tick} grid"
    assert tick_round(None) is None
    # float noise must not survive: round(x/tick)*tick alone gives e.g. 4350.550000000001
    assert tick_round(4350.528, "63MOONS") == 4350.55


def test_limit_entry_price_is_snapped_before_it_reaches_the_sdk():
    captured, sdk = _capture_sdk()
    c = GrowwClient(mode="live")
    c._sdk = sdk
    c.place_order(symbol="AAA", exchange="NSE", transaction_type="BUY", quantity=10,
                  order_type="LIMIT", price=2253.98)
    assert captured["price"] == 2254.0          # was sent off-tick and rejected before


def test_tick_size_is_per_symbol_not_a_hardcoded_005():
    """Groww's instrument master: BAJFINANCE/MANKIND/NETWEB/TDPOWERSYS/RELIANCE tick 0.10,
    63MOONS 0.05. A 0.05-aligned price like 4399.95 is INVALID on a 0.10 grid — that is what
    "choose price in multiples of the tick size" was reporting (verified 2026-07-31)."""
    from instrument_master import DEFAULT_TICK, round_to_tick, tick_size_for
    assert tick_size_for("NETWEB") == 0.10
    assert tick_size_for("63MOONS") == 0.05
    assert round_to_tick(4399.95, "NETWEB") == 4399.9        # was rejected as 4399.95
    assert round_to_tick(1126.85, "BAJFINANCE") == 1126.8
    assert round_to_tick(1112.55, "TDPOWERSYS") == 1112.5
    assert round_to_tick(888.4, "63MOONS") == 888.4          # already valid on its finer grid
    # unknown symbol falls back to the COARSER tick: a 0.10 price is also a valid 0.05 price,
    # so rounding to 0.05 under uncertainty is the bug, not the fix
    assert DEFAULT_TICK == 0.10
    assert round_to_tick(100.05, "NOTALISTEDSYMBOL") == 100.0
    assert round_to_tick(None, "NETWEB") is None


def test_sl_m_payload_uses_the_symbols_own_tick():
    captured, sdk = _capture_sdk()
    c = GrowwClient(mode="live")
    c._sdk = sdk
    c.place_order(symbol="NETWEB", exchange="NSE", transaction_type="SELL", quantity=10,
                  order_type="SL_M", price=None, trigger_price=4399.95)
    assert captured["price"] == 4399.9 and captured["trigger_price"] == 4399.9


def test_order_reason_extracts_growws_text_from_any_known_key():
    """Groww's rejection text arrives under different keys (docs say `remark`, community SDKs
    report `status_message`). Losing it is what left 76 rejected stops invisible on 2026-07-31."""
    from groww_client import _order_reason
    assert _order_reason({"remark": "Order placed successfully"}) == "Order placed successfully"
    assert _order_reason({"status_message": "choose price in multiples of the tick size"}) \
        == "choose price in multiples of the tick size"
    assert _order_reason({"rejection_reason": "beyond permissible range"}) \
        == "beyond permissible range"
    assert _order_reason({"remark": "   "}) is None       # blank is not a reason
    assert _order_reason({}) is None
    assert _order_reason(None) is None


def test_get_order_status_surfaces_the_rejection_reason():
    class _SDK:
        def get_order_status(self, **kw):
            return {"order_status": "REJECTED",
                    "remark": "choose price in multiples of the tick size"}

    c = GrowwClient(mode="live")
    c._sdk = _SDK()
    out = c.get_order_status("GSM123")
    assert out["status"] == "REJECTED"
    assert out["reason"] == "choose price in multiples of the tick size"
