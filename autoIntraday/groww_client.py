"""Groww API client wrapper — the only module that talks to Groww.

Live mode routes through the VPS gateway when GROWW_GATEWAY_URL is set (recommended on Mac).
Paper mode simulates every write locally. See groww_gateway/README.md and
docs/superpowers/specs/2026-07-09-groww-client-design.md.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

import pyotp

log = logging.getLogger("autointraday.groww_client")

VALID_MODES = ("paper", "live")

# Sentinel stored as the "sdk" in paper mode: satisfies _require_auth for the local order
# simulator without a real broker session. Any live-data/broker read on it fails fast, which is
# correct — paper mode must never reach the real SDK.
_PAPER_READY = "PAPER_READY"
_GATEWAY_READY = "GATEWAY_READY"

# A cancel that fails because the order is already in one of these states is a benign no-op:
# the resting order is gone, which is exactly what cancel wanted. Distinguished from a real error.
_TERMINAL_STATUSES = frozenset({"EXECUTED", "COMPLETED", "CANCELLED", "REJECTED", "FAILED"})

# After a rate-limited login, back off this long before attempting Groww auth again. While rate-
# limited, retrying a login on every request keeps Groww's window from resetting (the login storm
# perpetuates itself) — the cooldown breaks that loop so the window can clear.
_AUTH_COOLDOWN_SECONDS = 120.0


class GrowwClientError(Exception):
    """Wraps every error raised while talking to Groww: auth, SDK, network, rate limit."""


# --- Access-token cache -----------------------------------------------------------------------
# Groww access tokens are valid until 06:00 IST daily, so the intended pattern is generate ONCE
# and reuse — NOT per request or per restart. Groww's auth endpoint has an anti-abuse (brute-force)
# limiter, so a login storm (the old per-request re-auth + every container redeploy re-generating a
# token) trips "rate limit exceeded". Persisting the token to a mounted volume lets restarts and
# redeploys REUSE the day's token instead of minting a new one. Enabled only when
# GROWW_TOKEN_CACHE_PATH is set (the VPS gateway); unset elsewhere -> behave exactly as before.

def _token_cache_path() -> Optional[str]:
    return os.environ.get("GROWW_TOKEN_CACHE_PATH", "").strip() or None


def _next_6am_ist_epoch(now: Optional[datetime] = None) -> float:
    """Epoch of the next 06:00 Asia/Kolkata — a Groww access token's daily expiry."""
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    now = now or datetime.now(ist)
    six = now.replace(hour=6, minute=0, second=0, microsecond=0)
    if now >= six:
        six += timedelta(days=1)
    return six.timestamp()


def _load_cached_token() -> Optional[str]:
    """Return a cached access token if one is stored and not yet at its 06:00 IST expiry, else None."""
    path = _token_cache_path()
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if float(data.get("expiry", 0)) > time.time():
            return data.get("access_token") or None
    except Exception:
        log.warning("could not read token cache %s — will re-authenticate", path, exc_info=True)
    return None


def _save_cached_token(token: str) -> None:
    path = _token_cache_path()
    if not path:
        return
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w") as f:
            json.dump({"access_token": token, "expiry": _next_6am_ist_epoch()}, f)
        os.replace(tmp, path)                       # atomic — never leave a half-written token
        try:
            os.chmod(path, 0o600)                   # it's a live credential
        except OSError:
            pass
    except Exception:
        log.warning("could not write token cache %s", path, exc_info=True)


def _clear_cached_token() -> None:
    """Drop a cached token that Groww rejected mid-day, so the next login mints a fresh one."""
    path = _token_cache_path()
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            log.warning("could not remove token cache %s", path, exc_info=True)


# Substrings (lower-cased) of a broker error that mean Groww REJECTED the access token — it is
# expired or invalid — so the cached token must be dropped and a fresh one minted. A RATE-LIMIT
# must NEVER match: the token is still valid and re-minting only makes the limit worse (the auth
# cooldown handles rate-limits instead). Kept in sync with the phrasings Groww's API actually
# returns — e.g. "Authentication failed. Your API token has either expired or is invalid." The
# gateway's self-heal missed exactly that message before, so a stale token stuck all session
# (2026-07-30 post-mortem).
_TOKEN_INVALID_MARKERS = (
    "authentication failed",          # Groww's actual auth-rejection prefix
    "expired or is invalid",          # "... token has either expired or is invalid"
    "token expired", "token has expired", "session expired",
    "invalid token", "invalid access token", "access token",
    "unauthorized", "401",
)


def is_token_invalid_error(msg: str) -> bool:
    """True when a broker error means Groww rejected the ACCESS TOKEN (expired/invalid), so it
    must be dropped and re-minted — but never for a rate-limit (the token is still valid; the auth
    cooldown handles that). Central so the gateway self-heal and any client-side reauth agree on
    what counts as a token rejection."""
    low = (msg or "").lower()
    if "rate limit" in low:
        return False
    return any(marker in low for marker in _TOKEN_INVALID_MARKERS)


def _prefer_ipv4() -> None:
    """Force outbound connections to use IPv4, so Groww sees the whitelisted static IPv4 rather
    than the VPS's IPv6 egress. Groww's API is behind Cloudflare (dual-stack); on a dual-stack VPS
    Python would otherwise reach it over IPv6, and Groww's IP whitelist rejects that as an
    'unregistered IP address' on order placement. Set GROWW_FORCE_IPV4=0 to disable. Idempotent."""
    if os.environ.get("GROWW_FORCE_IPV4", "1").strip() in ("0", "false", "no"):
        return
    import socket
    if getattr(socket, "_ai_ipv4_forced", False):
        return
    _orig = socket.getaddrinfo

    def _ipv4_only(host, port, family=0, *args, **kwargs):
        ipv4 = _orig(host, port, socket.AF_INET, *args, **kwargs)
        return ipv4 or _orig(host, port, family, *args, **kwargs)   # fall back if no IPv4 exists

    socket.getaddrinfo = _ipv4_only
    socket._ai_ipv4_forced = True
    log.info("forcing IPv4 for outbound Groww connections (whitelist match)")


def _default_sdk_factory(api_key: str, totp: str) -> Any:
    _prefer_ipv4()                                  # before any Groww connection (token or SDK)
    from growwapi import GrowwAPI
    token = _load_cached_token()
    if token is None:                               # no valid cached token -> mint one and persist
        token = GrowwAPI.get_access_token(api_key=api_key, totp=totp)
        _save_cached_token(token)
    return GrowwAPI(token)


# Real growwapi (v1.5.0) segment/exchange/product constants used at the SDK call sites
# below. Mirrors GrowwAPI.SEGMENT_CASH / EXCHANGE_NSE / PRODUCT_MIS so this module does not
# need a live SDK instance just to read a constant.
_SEGMENT_CASH = "CASH"


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


# Order types we support, and what each one is allowed to carry. Derived from inspecting the
# growwapi 1.5.0 SDK (see build_order_price_fields) — NOT from the published docs.
_ORDER_TYPES = ("MARKET", "LIMIT", "SL", "SL_M")

# NSE tick size. 0.05 is safe universally: where the true tick is 0.01, a 0.05-aligned price is
# still valid, so rounding to 0.05 can never produce an off-tick price.
_TICK = 0.05


def tick_round(px: Optional[float]) -> Optional[float]:
    """Snap a price to the NSE tick grid. Groww rejects off-tick prices with "choose price in
    multiples of the tick size".

    Applied HERE rather than at each call site because this is the single choke point every order
    passes through. The bracket legs were already tick-rounded in the orchestrator, but LIMIT
    ENTRY prices were not — every resting entry on 2026-07-30/31 went out off-tick (2253.98,
    560.9348, 852.396, 442.764, 1126.3876). The final round(_, 2) matters: round(x/0.05)*0.05
    alone yields values like 4350.550000000001, which is off-tick as far as the exchange cares.
    """
    if px is None:
        return None
    return round(round(float(px) / _TICK) * _TICK, 2)


def build_order_price_fields(order_type: str, price: Optional[float],
                             trigger_price: Optional[float]) -> tuple[Optional[float],
                                                                     Optional[float]]:
    """Decide the (price, trigger_price) actually sent, per order type. Raises on an invalid combo.

    Why this exists: GrowwAPI.place_order builds its request body with BOTH keys hardcoded --

        request_body = {..., "price": price, "trigger_price": trigger_price, ...}

    -- and `requests` serialises None as JSON null rather than dropping the key, so the SDK can
    never truly omit a field. Its own signature also defaults `price=0.0`. That default is what
    sent {"order_type": "SL_M", "trigger_price": 842.10, "price": 0} and drew "Price is beyond
    permissible range": the exchange validated a limit of 0 against a trigger of 842.10.

    What the SDK actually tells us:
      * There is NO SL_M-specific handling and NO validation anywhere in it. It is a passthrough,
        so it does not "require" price == trigger_price. That was a guess and is now reverted.
      * The newer place_smart_order path in the SAME client builds its body CONDITIONALLY
        (`if trigger_price is not None: body[...] = ...`), omitting absent fields, and documents
        its stop-loss leg as taking "price (optional)".

    So an absent price is legitimate for a stop-loss order; place_order simply cannot express it.
    None is the closest achievable to omission — it serialises to null instead of a bogus 0.

    Rules:
      MARKET -> no price, no trigger (a market order has neither)
      LIMIT  -> price required, no trigger
      SL     -> both required (stop-loss LIMIT)
      SL_M   -> trigger required, price omitted (null)
    """
    otype = (order_type or "").upper()
    if otype not in _ORDER_TYPES:
        raise GrowwClientError(f"unsupported order_type {order_type!r}; expected one of "
                               f"{', '.join(_ORDER_TYPES)}")
    if otype == "MARKET":
        # 0.0, NOT None. A market order has no limit, so conceptually nothing should be sent — but
        # price=0.0 is EMPIRICALLY PROVEN on this account (every entry and every exit, 127 orders
        # on 2026-07-31 alone, all accepted). Sending null here is unverified, and MARKET is the
        # path that both opens positions and closes them. Changing a working critical path on
        # inference is exactly the reasoning that produced the SL_M bug. Left alone deliberately.
        return 0.0, None
    if otype == "LIMIT":
        if price is None or price <= 0:
            raise GrowwClientError("LIMIT order requires a positive price")
        return tick_round(price), None
    if otype == "SL":
        if trigger_price is None or trigger_price <= 0:
            raise GrowwClientError("SL order requires a positive trigger_price")
        if price is None or price <= 0:
            raise GrowwClientError("SL order requires a positive price (use SL_M for a "
                                   "market stop)")
        return tick_round(price), tick_round(trigger_price)
    # SL_M. price MIRRORS the trigger rather than being null.
    #
    # Sending null was the better-evidenced reading of the SDK (place_smart_order omits absent
    # fields and documents a stop-loss leg's price as optional), but it was REJECTED live on
    # 2026-07-31 with the same "difference between the limit price and trigger price is beyond
    # permissible range". Groww evaluates a null price as 0 server-side, so the gap is the full
    # price of the stock either way. Both null and 0 are therefore unusable, which leaves the
    # trigger itself: a zero gap is always inside the permissible band. Verified against the live
    # API, not inferred.
    if trigger_price is None or trigger_price <= 0:
        raise GrowwClientError("SL_M order requires a positive trigger_price")
    t = tick_round(trigger_price)
    return t, t


class GrowwClient:
    def __init__(self, mode: str, sdk_factory: Callable[[str, str], Any] = _default_sdk_factory):
        if mode not in VALID_MODES:
            raise GrowwClientError(f"invalid mode {mode!r}, must be one of {VALID_MODES}")
        self.mode = mode
        self._sdk_factory = sdk_factory
        self._sdk: Any = None
        self._gateway: Any = None
        self._paper_orders: list[dict] = []
        self._paper_order_seq = 0
        self._auth_cooldown_until = 0.0    # monotonic deadline; set on a rate-limited auth failure

    def _gateway_enabled(self) -> bool:
        return self.mode == "live" and bool(os.environ.get("GROWW_GATEWAY_URL", "").strip())

    def authenticate(self) -> None:
        if self._gateway_enabled():
            from groww_gateway_transport import GrowwGatewayTransport
            self._gateway = GrowwGatewayTransport()
            try:
                self._gateway.ensure_session()
            except Exception as e:
                raise GrowwClientError(f"gateway authentication failed: {e}") from e
            self._sdk = _GATEWAY_READY
            return
        api_key = os.environ.get("GROWW_API_KEY")
        totp_secret = os.environ.get("GROWW_TOTP_SECRET")
        if not api_key or not totp_secret:
            raise GrowwClientError(
                "live mode requires GROWW_GATEWAY_URL + GROWW_GATEWAY_TOKEN (recommended) "
                "or GROWW_API_KEY + GROWW_TOTP_SECRET on a Groww-whitelisted static IP"
            )
        totp = pyotp.TOTP(totp_secret).now()
        try:
            self._sdk = self._sdk_factory(api_key, totp)
        except Exception as e:
            raise GrowwClientError(f"authentication failed: {e}") from e

    def ensure_ready(self) -> None:
        """Prepare the client to run a cycle. Live mode must authenticate against Groww; paper
        mode simulates every order locally and reads no broker data (the orchestrator prices from
        the indicator feed and tracks positions in the DB), so it needs no credentials — it just
        marks itself ready so `_require_auth` passes for the local order simulator.

        Live auth is IDEMPOTENT: authenticate once, then reuse the session for the rest of the
        day. Re-authenticating on every call re-runs the TOTP login, which trips Groww's auth
        rate limit — that was the root cause of intermittent gateway 500/502s (the gateway server
        calls ensure_ready() on every request). Use reauthenticate() to force a fresh login when
        the session actually expires."""
        if self.mode == "live":
            if self._sdk is None:
                if self._auth_cooldown_until and time.monotonic() < self._auth_cooldown_until:
                    raise GrowwClientError(
                        "auth cooling down after a Groww rate-limit — not re-attempting login yet "
                        "so the rate-limit window can reset")
                try:
                    self.authenticate()
                except GrowwClientError as e:
                    if "rate limit" in str(e).lower():
                        self._auth_cooldown_until = time.monotonic() + _AUTH_COOLDOWN_SECONDS
                    raise
        elif self._sdk is None:
            self._sdk = _PAPER_READY

    def reauthenticate(self) -> None:
        """Force a fresh login — call this only when a live call fails with a session-expiry /
        auth error, so we don't re-run TOTP (and hit the rate limit) on healthy requests. Drops any
        cached token first so a token Groww rejected mid-day is replaced, not reloaded."""
        self._sdk = None
        self._gateway = None
        _clear_cached_token()
        self.authenticate()

    def _via_gateway(self) -> Any:
        if self._gateway is None:
            raise GrowwClientError("gateway transport is not initialized")
        return self._gateway

    def _require_auth(self) -> None:
        if self._sdk is None:
            raise GrowwClientError("not authenticated - call authenticate() first")

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        self._require_auth()
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().get_ltp(symbols)
        # Real SDK: get_ltp(exchange_trading_symbols: Tuple[str], segment: str), and keys
        # in the response are "EXCHANGE_SYMBOL" (e.g. "NSE_RELIANCE"), not bare symbols.
        exchange_symbols = tuple(f"NSE_{symbol}" for symbol in symbols)
        raw = _retry(lambda: self._sdk.get_ltp(exchange_symbols, segment=_SEGMENT_CASH))
        return {symbol: float(raw[f"NSE_{symbol}"]) for symbol in symbols}

    def get_quote(self, symbol: str) -> dict[str, Any]:
        self._require_auth()
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().get_quote(symbol)
        # Real SDK: get_quote(trading_symbol: str, exchange: str, segment: str).
        raw = _retry(lambda: self._sdk.get_quote(
            trading_symbol=symbol, exchange="NSE", segment=_SEGMENT_CASH))
        return {
            "symbol": symbol,
            "ltp": float(raw["ltp"]),
            "open": float(raw["open"]),
            "high": float(raw["high"]),
            "low": float(raw["low"]),
            "close": float(raw["close"]),
            "volume": int(raw["volume"]),
        }

    def get_historical_candles(self, symbol: str, start_time: str, end_time: str,
                               interval_minutes: int = 1) -> dict[str, Any]:
        """Historical OHLCV — used to seed the Live Intraday indicators at session start.

        The GrowwFeed WebSocket carries no candles, so without this a fresh session cannot compute
        VWAP/EMA/ATR until it has polled for an hour. Groww limits 1-min data to 7 days per request
        and 3 months back.

        Uses the SDK's get_historical_candle_data: it takes `trading_symbol`, matching the symbol
        convention used everywhere else here. The newer get_historical_candles wants a differently
        formatted `groww_symbol`, so switching to it would need a symbol-mapping layer first.

        Returns {"candles": [[epoch_seconds, o, h, l, c, v], ...]}.
        """
        self._require_auth()
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().get_historical_candles(
                symbol, start_time, end_time, interval_minutes)
        raw = _retry(lambda: self._sdk.get_historical_candle_data(
            trading_symbol=symbol, exchange="NSE", segment=_SEGMENT_CASH,
            start_time=start_time, end_time=end_time, interval_in_minutes=interval_minutes))
        return {"candles": list(raw.get("candles") or [])}

    def get_holdings(self) -> list[dict]:
        self._require_auth()
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().get_holdings()
        # Real SDK: get_holdings_for_user() -> dict payload (not a bare list); the holdings
        # list is under the "holdings" key. Verified field names on a live account:
        # trading_symbol / quantity / average_price. There is NO ltp in this response —
        # fetch it separately via get_ltp if a mark is needed.
        raw = _retry(lambda: self._sdk.get_holdings_for_user())
        holdings = raw["holdings"] if isinstance(raw, dict) and "holdings" in raw else raw
        return [
            {"symbol": h["trading_symbol"], "quantity": int(h["quantity"]),
             "avg_price": float(h["average_price"])}
            for h in holdings
        ]

    def get_positions(self) -> list[dict]:
        self._require_auth()
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().get_positions()
        # Real SDK: get_positions_for_user(segment=...) -> dict payload; positions under the
        # "positions" key. Verified fields: trading_symbol / quantity / product / net_price
        # (net_price is the position's net entry price). No ltp in this response.
        raw = _retry(lambda: self._sdk.get_positions_for_user(segment=_SEGMENT_CASH))
        positions = raw["positions"] if isinstance(raw, dict) and "positions" in raw else raw
        out = []
        for p in (positions or []):
            # Tolerate field-name variation and skip the empty aggregate row Groww can return
            # (no symbol / zero qty) — defensive so reconcile never crashes on an odd payload.
            sym = p.get("trading_symbol") or p.get("symbol")
            qty = p.get("quantity", p.get("net_quantity", 0)) or 0
            if not sym or int(qty) == 0:
                continue
            px = p.get("net_price", p.get("average_price", 0)) or 0
            out.append({"symbol": sym, "quantity": int(qty), "product": p.get("product", "MIS"),
                        "avg_price": float(px)})
        return out

    def get_open_orders(self) -> list[dict]:
        """Today's broker order book (ALL statuses — the caller filters terminal ones out),
        including each order's product (MIS/CNC). Lets the orchestrator see manually placed
        orders and tell an intraday order apart from a delivery one. Paper mode has no broker
        book (paper orders live in the DB), so it returns []."""
        if self.mode == "paper":
            return []
        self._require_auth()
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().get_open_orders()
        raw = _retry(lambda: self._sdk.get_order_list(segment=_SEGMENT_CASH))
        orders = raw.get("order_list", raw) if isinstance(raw, dict) else raw
        return [
            {"symbol": o.get("trading_symbol") or o.get("symbol"),
             "order_id": o.get("groww_order_id") or o.get("order_id"),
             "status": o.get("order_status") or o.get("status"),
             "transaction_type": o.get("transaction_type"),
             # MIS vs CNC. Reconcile only ever cancels MIS orders — a resting CNC sell belongs
             # to the user's delivery portfolio. Absent -> None, which fails safe (not MIS).
             "product": o.get("product") or None}
            for o in (orders or [])
        ]

    def get_margin(self) -> dict:
        self._require_auth()
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().get_margin()
        # Real SDK: get_available_margin_details() (not get_margin()).
        raw = _retry(lambda: self._sdk.get_available_margin_details())
        return {"available": float(raw["available"]), "used": float(raw["used"]),
                "total": float(raw["total"])}

    def place_order(self, symbol: str, exchange: str, transaction_type: str, quantity: int,
                     order_type: str, price: Optional[float] = None, product: str = "MIS",
                     trigger_price: Optional[float] = None) -> dict:
        """order_type: MARKET | LIMIT | SL | SL_M (the SDK's ORDER_TYPE_* values). SL/SL_M carry
        a trigger_price — as a BUY with the trigger above market they are stop-ENTRY orders: the
        broker arms them and fires the moment LTP touches the trigger, with no polling latency.
        That is how resting breakout entries live at Groww instead of only in our DB."""
        self._require_auth()
        if self.mode == "paper":
            return self._simulate_order(symbol, transaction_type, quantity, order_type, price)
        if self._sdk is _GATEWAY_READY:
            # Validate BEFORE the network hop so a malformed order fails here with a descriptive
            # message instead of costing a round trip and a broker-side rejection.
            gw_price, gw_trigger = build_order_price_fields(order_type, price, trigger_price)
            return self._via_gateway().place_order(
                symbol=symbol,
                exchange=exchange,
                transaction_type=transaction_type,
                quantity=quantity,
                order_type=order_type,
                price=gw_price,
                product=product,
                trigger_price=gw_trigger,
            )
        try:
            # Real SDK: place_order(validity, exchange, order_type, product, quantity,
            # segment, trading_symbol, transaction_type, price=..., trigger_price=..., ...)
            # -- param is `trading_symbol`, not `symbol`; `segment` and `validity` are
            # required and were entirely missing from the original assumption; the
            # response is a raw `groww_order_id` key, not `order_id`.
            # Price/trigger are decided PER ORDER TYPE and validated locally first — no generic
            # "or 0.0" fallback. See build_order_price_fields for what the SDK actually does and
            # why a bare 0.0 was rejected. Passing price=None makes the SDK serialise JSON null
            # rather than a bogus limit of 0.
            live_price, live_trigger = build_order_price_fields(order_type, price, trigger_price)
            raw = self._sdk.place_order(
                trading_symbol=symbol, exchange=exchange, transaction_type=transaction_type,
                quantity=quantity, order_type=order_type, price=live_price,
                product=product, segment=_SEGMENT_CASH, validity="DAY",
                trigger_price=live_trigger,
            )
        except Exception as e:
            raise GrowwClientError(f"order placement failed: {e}") from e
        return {"order_id": raw["groww_order_id"], "status": raw["order_status"], "symbol": symbol,
                "transaction_type": transaction_type, "quantity": quantity,
                "order_type": order_type, "price": price, "mode": "live"}

    def get_order_status(self, order_id: str) -> dict:
        self._require_auth()
        if order_id.startswith("PAPER-"):
            for order in self._paper_orders:
                if order["order_id"] == order_id:
                    return {"order_id": order["order_id"], "status": order["status"]}
            raise GrowwClientError(f"unknown paper order id: {order_id}")
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().get_order_status(order_id)
        # Real SDK: get_order_status(segment, groww_order_id) - `segment` is required and
        # the id param is named `groww_order_id`, not a positional `order_id`.
        raw = _retry(lambda: self._sdk.get_order_status(segment=_SEGMENT_CASH, groww_order_id=order_id))
        return {"order_id": order_id, "status": raw["order_status"]}

    def cancel_order(self, order_id: str) -> dict:
        self._require_auth()
        if order_id.startswith("PAPER-"):
            for order in self._paper_orders:
                if order["order_id"] == order_id:
                    order["status"] = "CANCELLED"
                    return {"order_id": order["order_id"], "status": order["status"]}
            raise GrowwClientError(f"unknown paper order id: {order_id}")
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().cancel_order(order_id)
        try:
            # Real SDK: cancel_order(groww_order_id, segment) - same naming/segment gap as above.
            raw = self._sdk.cancel_order(groww_order_id=order_id, segment=_SEGMENT_CASH)
        except Exception as e:
            # Groww rejects cancelling an order that is already terminal (filled/cancelled/
            # rejected) or unknown. That is NOT a real failure for us — the resting order is
            # gone, which is what cancel wanted. This is the armed-exit race: we try to cancel
            # a take-profit order that already filled. Report it benignly so callers don't alarm.
            status = self._safe_order_status(order_id)
            if status in _TERMINAL_STATUSES:
                return {"order_id": order_id, "status": status, "cancelled": False,
                        "note": "order already terminal — nothing to cancel"}
            if status is None:
                return {"order_id": order_id, "status": "NOT_FOUND", "cancelled": False,
                        "note": "order not found — nothing to cancel"}
            raise GrowwClientError(f"order cancellation failed: {e}") from e
        return {"order_id": order_id, "status": raw["order_status"], "cancelled": True}

    def _safe_order_status(self, order_id: str) -> Optional[str]:
        """Best-effort order status for cancel's benign-terminal check. Returns the status string,
        or None if the order can't be found / status can't be read. Never raises."""
        try:
            return self.get_order_status(order_id).get("status")
        except Exception:
            return None

    def place_oco_order(self, symbol: str, entry: dict, target: dict, stop_loss: dict) -> dict:
        self._require_auth()
        if self.mode == "paper":
            self._paper_order_seq += 1
            order = {
                "order_id": f"PAPER-OCO-{self._paper_order_seq}", "status": "ACTIVE",
                "symbol": symbol, "target": target, "stop_loss": stop_loss, "mode": "paper",
            }
            self._paper_orders.append(order)
            return dict(order)
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().place_oco_order(symbol, entry, target, stop_loss)
        try:
            # Real SDK: there is no `place_oco_order`; smart orders (OCO/GTT) go through
            # `create_smart_order`. Per its docs, OCO wants net_position_quantity + target +
            # stop_loss + transaction_type — `order=` is a GTT-only field (sending it with a
            # missing net_position_quantity failed live 2026-07-20: "Net position quantity is
            # required for OCO orders"). transaction_type here is the EXIT direction (SELL
            # closes a long, BUY closes a short), so net position is +qty long / -qty short.
            txn = entry.get("transaction_type")
            qty = int(entry["quantity"])
            net_qty = qty if txn == "SELL" else -qty
            raw = self._sdk.create_smart_order(
                smart_order_type="OCO", segment=_SEGMENT_CASH, trading_symbol=symbol,
                quantity=qty, product_type="MIS", exchange="NSE", duration="DAY",
                net_position_quantity=net_qty, target=target, stop_loss=stop_loss,
                transaction_type=txn,
            )
        except Exception as e:
            raise GrowwClientError(f"OCO order placement failed: {e}") from e
        # Verified live 2026-07-20: the create response carries `status` ("ACTIVE"), not
        # `smart_order_status` (the old assumption KeyError'd after a SUCCESSFUL creation).
        return {"order_id": raw["smart_order_id"],
                "status": raw.get("status") or raw.get("smart_order_status") or "UNKNOWN",
                "symbol": symbol, "target": target, "stop_loss": stop_loss, "mode": "live"}

    def modify_oco_order(self, order_id: str, target: float, stop_loss: float) -> dict:
        """Update the target/stop legs of a resting OCO at the broker — this is how a trailed
        stop actually protects the position in real time instead of only existing in our DB.
        Paper OCOs are simulated: update the local record."""
        if self.mode == "paper" or order_id.startswith("PAPER-"):
            for order in self._paper_orders:
                if order["order_id"] == order_id:
                    order["target"] = {"trigger_price": target, "order_type": "LIMIT",
                                       "price": target}
                    order["stop_loss"] = {"trigger_price": stop_loss, "order_type": "LIMIT",
                                          "price": stop_loss}
            return {"order_id": order_id, "status": "MODIFIED"}
        self._require_auth()
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().modify_oco_order(order_id, target, stop_loss)
        try:
            # Real SDK: modify_smart_order(smart_order_id, smart_order_type, segment,
            # target={trigger_price, order_type, price}, stop_loss={...}) — OCO legs are
            # modifiable in place. UNVERIFIED against the live API; confirm in the 1-share test.
            raw = self._sdk.modify_smart_order(
                smart_order_id=order_id, smart_order_type="OCO", segment=_SEGMENT_CASH,
                target={"trigger_price": target, "order_type": "LIMIT", "price": target},
                stop_loss={"trigger_price": stop_loss, "order_type": "LIMIT",
                           "price": stop_loss})
        except Exception as e:
            raise GrowwClientError(f"OCO modification failed: {e}") from e
        return {"order_id": order_id,
                "status": raw.get("smart_order_status", "MODIFIED") if isinstance(raw, dict)
                else "MODIFIED"}

    def cancel_oco_order(self, order_id: str) -> dict:
        """Cancel a resting OCO (smart order). MUST be called before manually exiting a position
        the OCO protects — otherwise the OCO legs stay armed at the broker and can fire after
        we're already flat, leaving a naked position. Paper OCOs are simulated, so cancelling is
        a local bookkeeping mark (and a no-op if this client instance never saw the order — each
        cycle runs a fresh process)."""
        if self.mode == "paper" or order_id.startswith("PAPER-"):
            for order in self._paper_orders:
                if order["order_id"] == order_id:
                    order["status"] = "CANCELLED"
            return {"order_id": order_id, "status": "CANCELLED"}
        self._require_auth()
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().cancel_oco_order(order_id)
        try:
            # Real SDK: smart orders are cancelled via cancel_smart_order(smart_order_id, segment,
            # smart_order_type) — UNVERIFIED against the live API; confirm in the 1-share test.
            raw = self._sdk.cancel_smart_order(
                smart_order_id=order_id, segment=_SEGMENT_CASH, smart_order_type="OCO")
        except Exception as e:
            raise GrowwClientError(f"OCO cancellation failed: {e}") from e
        return {"order_id": order_id, "status": raw.get("smart_order_status", "CANCELLED")}

    def get_smart_order_status(self, order_id: str) -> dict:
        self._require_auth()
        if order_id.startswith("PAPER-"):
            return self.get_order_status(order_id)
        if self._sdk is _GATEWAY_READY:
            return self._via_gateway().get_smart_order_status(order_id)
        # Real SDK: get_smart_order(segment, smart_order_type, smart_order_id) - no
        # single-arg `get_smart_order(order_id)` overload; `segment`/`smart_order_type`
        # are required and were missing from the original assumption.
        raw = _retry(lambda: self._sdk.get_smart_order(
            segment=_SEGMENT_CASH, smart_order_type="OCO", smart_order_id=order_id))
        # Same live-verified key fix as place_oco_order: the response uses `status`. NOTE this
        # endpoint served stale ACTIVE for orders modify/cancel called terminated — treat it
        # as advisory, never as proof a bracket is armed.
        return {"order_id": order_id,
                "status": raw.get("status") or raw.get("smart_order_status") or "UNKNOWN"}

    def _simulate_order(self, symbol: str, transaction_type: str, quantity: int,
                         order_type: str, price: Optional[float]) -> dict:
        # Fill at the caller-supplied price when there is one (the orchestrator passes the
        # indicator-derived entry/exit price it already computed) — that keeps paper mode fully
        # self-contained: no broker session, no get_ltp (which this account can't call anyway).
        # Only when no price is given do we fall back to the live-quote SDK.
        if price is not None:
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
        return dict(order)
