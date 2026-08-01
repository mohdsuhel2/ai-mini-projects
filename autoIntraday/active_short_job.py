"""activeShort jobs — scan tonight, arm tomorrow, protect what fills, flatten at the close.

Four separate entry points rather than one loop, because each runs at a different time and has one
responsibility. Every collaborator is injected so the whole flow is testable without a broker.

Order of safety checks is deliberate and identical in each job: enabled -> mode/paper gate ->
per-pick validation. No job may place an order without passing all three.

See docs/superpowers/specs/2026-07-31-active-short-design.md.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from active_short import (ActiveShortError, gap_too_far, open_anchored_trigger, parse_candidates,
                          position_size, protective_levels, select, short_pnl,
                          validate_short_entry, validate_short_stop)

log = logging.getLogger("autointraday.active_short_job")

_FILLED = ("EXECUTED", "COMPLETE", "COMPLETED", "FILLED")
_DEAD = ("REJECTED", "CANCELLED", "CANCELED", "FAILED", "EXPIRED")


def _setup_type(pick) -> Optional[str]:
    """The scan's setup_type, carried in the pick's reason blob when present."""
    r = (getattr(pick, "reason", "") or "").lower()
    return "reversal_short" if "reversal" in r else None


def _mode(store) -> str:
    """The mode orders will actually be placed in — 'paper' unless the gate says otherwise.

    Never trust the configured mode alone: live_allowed() is the single authority, so a mis-set
    config can never commit real money before the paper period is done.
    """
    ok, why = store.live_allowed()
    if not ok:
        log.info("activeShort running in PAPER (%s)", why)
    return "live" if ok else "paper"


def _enabled(store) -> bool:
    if not store.get_config()["active_short_enabled"]:
        log.info("activeShort disabled — nothing to do")
        return False
    return True


# ---------------------------------------------------------------------------------------------
# 1. Scan (evening)
# ---------------------------------------------------------------------------------------------
def scan(store, run_scanner: Callable[[], Any], scan_date: str, trade_date: str) -> int:
    """Run the scanner skill and record the night's picks. Returns how many were kept."""
    if not _enabled(store):
        return 0
    cfg = store.get_config()
    try:
        payload = run_scanner()
    except Exception:
        log.exception("activeShort scan failed — no picks for %s", trade_date)
        return 0

    candidates = parse_candidates(payload)
    regime = (payload or {}).get("regime_note") if isinstance(payload, dict) else None
    chosen = select(candidates, cfg)
    if not chosen:
        # An empty night is a correct answer, not a failure — the scanner is told to return
        # nothing when the regime is bullish or nothing clears its bar.
        log.info("activeShort scan for %s: no candidates cleared the bar (%s)",
                 trade_date, regime or "no regime note")
        store.record_session(trade_date, _mode(store), 0)
        return 0

    for rank, c in enumerate(chosen, start=1):
        store.add_pick(scan_date=scan_date, trade_date=trade_date, symbol=c.symbol,
                       confidence=c.confidence, confirmation_level=c.confirmation_level,
                       stop=c.stop, target=c.target, rvol=c.rvol, reason=c.reason, rank=rank,
                       mode=_mode(store))
    store.record_session(trade_date, _mode(store), len(chosen))
    log.info("activeShort scan for %s: %d picks — %s", trade_date, len(chosen),
             ", ".join(f"{c.symbol}({c.confidence:.0f})" for c in chosen))
    return len(chosen)


# ---------------------------------------------------------------------------------------------
# 2. Arm (09:15)
# ---------------------------------------------------------------------------------------------
def arm(store, client, trade_date: str, get_quote: Callable[[str], dict]) -> int:
    """Arm a conditional SELL stop-entry per pick. Returns how many were armed.

    The trigger sits BELOW the market, so nothing fills at the open unless the stock genuinely
    breaks down — the whole point of the design. A pick that gapped through its level, or whose
    trigger is no longer below the tape, is SKIPPED rather than forced.
    """
    if not _enabled(store):
        return 0
    cfg = store.get_config()
    mode = _mode(store)
    armed = 0
    for pick in store.picks_for(trade_date, status="PLANNED"):
        try:
            quote = get_quote(pick.symbol)
            last = float(quote["ltp"])
            open_px = float(quote.get("open") or last)
        except Exception as e:
            store.update_pick(pick.id, status="SKIPPED", status_note=f"no quote: {e}")
            log.warning("%s: no quote — skipped", pick.symbol)
            continue

        if gap_too_far(pick.confirmation_level, open_px, cfg["max_gap_pct"]):
            note = (f"gapped {(pick.confirmation_level - open_px) / pick.confirmation_level * 100:.1f}% "
                    f"below the level — the move already happened")
            store.update_pick(pick.id, status="SKIPPED", status_note=note)
            log.info("%s: %s", pick.symbol, note)
            continue
        # Reversal setups re-anchor to the actual open, which the overnight scan could not know.
        trigger = open_anchored_trigger(pick.confirmation_level, open_px, last,
                                        _setup_type(pick))
        try:
            validate_short_entry(trigger, last)
        except ActiveShortError as e:
            store.update_pick(pick.id, status="SKIPPED", status_note=str(e))
            log.info("%s: %s", pick.symbol, e)
            continue

        qty = position_size(cfg["capital_per_short"], last)
        if qty < 1:
            store.update_pick(pick.id, status="SKIPPED", status_note="capital buys < 1 share")
            continue
        try:
            resp = client.place_order(
                symbol=pick.symbol, exchange="NSE", transaction_type="SELL", quantity=qty,
                order_type="SL_M", trigger_price=trigger, product="MIS")
        except Exception as e:
            store.update_pick(pick.id, status="SKIPPED", status_note=f"entry rejected: {e}")
            log.exception("%s: arm failed", pick.symbol)
            continue
        note = f"armed {mode} below {trigger:g}"
        if abs(trigger - (pick.confirmation_level or trigger)) > 0.01:
            note += f" (re-anchored to the open from {pick.confirmation_level:g})"
        store.update_pick(pick.id, status="ARMED", quantity=qty,
                          entry_order_id=(resp or {}).get("order_id"), status_note=note)
        armed += 1
        log.info("ARMED short %s x%d below %g (%s)", pick.symbol, qty, trigger, mode)
    return armed


# ---------------------------------------------------------------------------------------------
# 3. Protect (09:20 onward)
# ---------------------------------------------------------------------------------------------
def protect(store, client, trade_date: str, get_quote: Optional[Callable[[str], dict]] = None) -> int:
    """Attach a stop and target to every entry that has FILLED. Returns how many were protected.

    MANDATORY, and the one place this design can hurt you: a stop cannot be attached to a position
    that does not exist yet, so an entry that fills while nothing is watching is a NAKED SHORT with
    unbounded upside risk. Failures are logged at error level, never swallowed.

    Idempotent — a pick already carrying a stop order is left alone, so running twice never
    double-places.
    """
    if not _enabled(store):
        return 0
    cfg = store.get_config()
    protected = 0
    for pick in store.picks_for(trade_date, status="ARMED"):
        if not pick.entry_order_id:
            continue
        try:
            status = str(client.get_order_status(pick.entry_order_id).get("status", "")).upper()
        except Exception:
            log.exception("%s: entry status check failed — will retry next pass", pick.symbol)
            continue
        if status in _DEAD:
            store.update_pick(pick.id, status="EXPIRED", status_note=f"entry {status}")
            continue
        if status not in _FILLED:
            continue                                   # still resting; nothing to protect yet

        fill = pick.confirmation_level                 # best estimate if the broker gives no price
        try:
            fill = float(client.get_order_status(pick.entry_order_id).get("price") or fill)
        except Exception:
            pass
        stop_px, target_px = protective_levels(fill, cfg)
        store.update_pick(pick.id, status="FILLED", fill_price=fill)

        # A stop below the market covers the short the instant it is placed. Verify against the
        # live tape before sending, because the levels derive from a broker-reported fill price
        # that can be missing or wrong.
        if get_quote is not None:
            try:
                validate_short_stop(stop_px, float(get_quote(pick.symbol)["ltp"]))
            except ActiveShortError as e:
                log.error("%s: REFUSING nonsense stop (%s) — position left UNPROTECTED, "
                          "close it manually", pick.symbol, e)
                store.update_pick(pick.id, status_note=f"UNPROTECTED — {e}")
                continue
            except Exception:
                log.warning("%s: could not verify stop against the tape — placing anyway",
                            pick.symbol, exc_info=True)

        stop_id = target_id = None
        try:
            stop_id = (client.place_order(
                symbol=pick.symbol, exchange="NSE", transaction_type="BUY",
                quantity=pick.quantity, order_type="SL_M", trigger_price=stop_px,
                product="MIS") or {}).get("order_id")
        except Exception:
            log.error("%s: STOP PLACEMENT FAILED on a filled short — position is UNPROTECTED",
                      pick.symbol, exc_info=True)
        try:
            target_id = (client.place_order(
                symbol=pick.symbol, exchange="NSE", transaction_type="BUY",
                quantity=pick.quantity, order_type="LIMIT", price=target_px,
                product="MIS") or {}).get("order_id")
        except Exception:
            log.warning("%s: target placement failed (stop is what matters)", pick.symbol,
                        exc_info=True)
        if stop_id:
            store.update_pick(pick.id, status="PROTECTED", stop_order_id=stop_id,
                              target_order_id=target_id,
                              status_note=f"stop {stop_px:g} target {target_px:g}")
            protected += 1
            log.info("PROTECTED %s: stop %g target %g", pick.symbol, stop_px, target_px)
        else:
            store.update_pick(pick.id, status_note="FILLED but UNPROTECTED — stop failed")
    return protected


# ---------------------------------------------------------------------------------------------
# 4. Expire unfilled / square off open
# ---------------------------------------------------------------------------------------------
def expire_unfilled(store, client, trade_date: str) -> int:
    """Cancel entries that never triggered. A breakdown that has not happened by late morning is
    not the setup the scan predicted."""
    if not _enabled(store):
        return 0
    cancelled = 0
    for pick in store.picks_for(trade_date, status="ARMED"):
        if pick.entry_order_id:
            try:
                client.cancel_order(pick.entry_order_id)
            except Exception:
                log.warning("%s: cancel failed", pick.symbol, exc_info=True)
        store.update_pick(pick.id, status="EXPIRED", status_note="never triggered by expiry")
        cancelled += 1
    return cancelled


def square_off(store, client, trade_date: str, get_quote: Callable[[str], dict]) -> int:
    """Flatten anything still open. Retail shorts are intraday-only; closing here beats leaving it
    to the broker's ~15:20 auto-square at whatever price that gets."""
    if not _enabled(store):
        return 0
    closed = 0
    for status in ("FILLED", "PROTECTED"):
        for pick in store.picks_for(trade_date, status=status):
            for oid in (pick.stop_order_id, pick.target_order_id):
                if oid:
                    try:
                        client.cancel_order(oid)
                    except Exception:
                        log.warning("%s: leg cancel failed", pick.symbol, exc_info=True)
            try:
                px = float(get_quote(pick.symbol)["ltp"])
                client.place_order(symbol=pick.symbol, exchange="NSE", transaction_type="BUY",
                                   quantity=pick.quantity, order_type="MARKET", price=px,
                                   product="MIS")
            except Exception:
                log.error("%s: SQUARE-OFF FAILED — position may be left to the broker",
                          pick.symbol, exc_info=True)
                continue
            pnl = short_pnl(pick.fill_price or 0.0, px, pick.quantity or 0)
            store.update_pick(pick.id, status="CLOSED", exit_price=px, pnl=pnl,
                              status_note="squared off")
            closed += 1
            log.info("CLOSED short %s @ %g pnl %.0f", pick.symbol, px, pnl)
    store.complete_session(trade_date)
    return closed
