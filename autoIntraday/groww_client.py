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

# Sentinel stored as the "sdk" in paper mode: satisfies _require_auth for the local order
# simulator without a real broker session. Any live-data/broker read on it fails fast, which is
# correct — paper mode must never reach the real SDK.
_PAPER_READY = "PAPER_READY"


class GrowwClientError(Exception):
    """Wraps every error raised while talking to Groww: auth, SDK, network, rate limit."""


def _default_sdk_factory(api_key: str, totp: str) -> Any:
    from growwapi import GrowwAPI
    access_token = GrowwAPI.get_access_token(api_key=api_key, totp=totp)
    return GrowwAPI(access_token)


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


class GrowwClient:
    def __init__(self, mode: str, sdk_factory: Callable[[str, str], Any] = _default_sdk_factory):
        if mode not in VALID_MODES:
            raise GrowwClientError(f"invalid mode {mode!r}, must be one of {VALID_MODES}")
        self.mode = mode
        self._sdk_factory = sdk_factory
        self._sdk: Any = None
        self._paper_orders: list[dict] = []
        self._paper_order_seq = 0

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

    def ensure_ready(self) -> None:
        """Prepare the client to run a cycle. Live mode must authenticate against Groww; paper
        mode simulates every order locally and reads no broker data (the orchestrator prices from
        the indicator feed and tracks positions in the DB), so it needs no credentials — it just
        marks itself ready so `_require_auth` passes for the local order simulator."""
        if self.mode == "live":
            self.authenticate()
        elif self._sdk is None:
            self._sdk = _PAPER_READY

    def _require_auth(self) -> None:
        if self._sdk is None:
            raise GrowwClientError("not authenticated - call authenticate() first")

    def get_ltp(self, symbols: list[str]) -> dict[str, float]:
        self._require_auth()
        # Real SDK: get_ltp(exchange_trading_symbols: Tuple[str], segment: str), and keys
        # in the response are "EXCHANGE_SYMBOL" (e.g. "NSE_RELIANCE"), not bare symbols.
        exchange_symbols = tuple(f"NSE_{symbol}" for symbol in symbols)
        raw = _retry(lambda: self._sdk.get_ltp(exchange_symbols, segment=_SEGMENT_CASH))
        return {symbol: float(raw[f"NSE_{symbol}"]) for symbol in symbols}

    def get_quote(self, symbol: str) -> dict[str, Any]:
        self._require_auth()
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

    def get_holdings(self) -> list[dict]:
        self._require_auth()
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
        # Real SDK: get_positions_for_user(segment=...) -> dict payload; positions under the
        # "positions" key. Verified fields: trading_symbol / quantity / product / net_price
        # (net_price is the position's net entry price). No ltp in this response.
        raw = _retry(lambda: self._sdk.get_positions_for_user(segment=_SEGMENT_CASH))
        positions = raw["positions"] if isinstance(raw, dict) and "positions" in raw else raw
        return [
            {"symbol": p["trading_symbol"], "quantity": int(p["quantity"]),
             "product": p["product"], "avg_price": float(p["net_price"])}
            for p in positions
        ]

    def get_open_orders(self) -> list[dict]:
        """Today's broker order book (ALL statuses — the caller filters terminal ones out).
        Lets the orchestrator see manually placed orders. Paper mode has no broker book
        (paper orders live in the DB), so it returns []."""
        if self.mode == "paper":
            return []
        self._require_auth()
        raw = _retry(lambda: self._sdk.get_order_list(segment=_SEGMENT_CASH))
        orders = raw.get("order_list", raw) if isinstance(raw, dict) else raw
        return [
            {"symbol": o.get("trading_symbol") or o.get("symbol"),
             "order_id": o.get("groww_order_id") or o.get("order_id"),
             "status": o.get("order_status") or o.get("status"),
             "transaction_type": o.get("transaction_type")}
            for o in (orders or [])
        ]

    def get_margin(self) -> dict:
        self._require_auth()
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
        try:
            # Real SDK: place_order(validity, exchange, order_type, product, quantity,
            # segment, trading_symbol, transaction_type, price=..., trigger_price=..., ...)
            # -- param is `trading_symbol`, not `symbol`; `segment` and `validity` are
            # required and were entirely missing from the original assumption; the
            # response is a raw `groww_order_id` key, not `order_id`.
            # A live MARKET order carries no limit price (paper passes one only to seed the
            # simulated fill); forward a price to Groww only for non-MARKET orders.
            live_price = price if (order_type != "MARKET" and price is not None) else 0.0
            raw = self._sdk.place_order(
                trading_symbol=symbol, exchange=exchange, transaction_type=transaction_type,
                quantity=quantity, order_type=order_type, price=live_price,
                product=product, segment=_SEGMENT_CASH, validity="DAY",
                trigger_price=trigger_price,
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
        try:
            # Real SDK: cancel_order(groww_order_id, segment) - same naming/segment gap as above.
            raw = self._sdk.cancel_order(groww_order_id=order_id, segment=_SEGMENT_CASH)
        except Exception as e:
            raise GrowwClientError(f"order cancellation failed: {e}") from e
        return {"order_id": order_id, "status": raw["order_status"]}

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
