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


def net_positions(rows) -> list[dict]:
    """Collapse Groww's raw position rows into ONE row per symbol, intraday only.

    `get_positions` returns a row per LEG, not per symbol, and includes CNC (delivery) rows
    alongside MIS ones. Persisting them verbatim into a symbol-keyed table raised
    "UNIQUE constraint failed: broker_positions.symbol", and would also have shown the
    delivery portfolio — the Swing page's job — as intraday positions.

    Mirrors orchestrator._broker_state, the proven reference for this payload: filter to MIS,
    sum the quantities, and take the average from the last leg that carries one. That average
    is approximate when legs genuinely differ (a partial exit leaves the remaining shares at
    the original cost, not a blend); the netted quantity is the number that must be right, and
    it is.

    A symbol whose legs net to zero is dropped — it is flat, not held."""
    out: dict[str, dict] = {}
    for p in (rows or []):
        if str(p.get("product") or "MIS").upper() != "MIS":
            continue
        sym = p.get("symbol")
        if not sym:
            continue
        row = out.setdefault(sym, {"symbol": sym, "quantity": 0, "avg_price": None,
                                   "product": "MIS", "ltp": None})
        try:
            row["quantity"] += int(p.get("quantity") or 0)
        except (TypeError, ValueError):
            continue
        if p.get("avg_price"):
            row["avg_price"] = float(p["avg_price"])
        if p.get("ltp") is not None:
            row["ltp"] = p["ltp"]
    return [r for r in out.values() if r["quantity"] != 0]


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
                f"entry {res.get('status')} for {symbol}: "
                f"{res.get('reason') or 'no reason given'}")
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
