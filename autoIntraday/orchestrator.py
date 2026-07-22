"""The hourly-cycle orchestrator: manage exits, screen entries, place paper/live orders,
persist state. Wires Phase 1 (client), Phase 2 (store), Phase 3 (engine). Every collaborator
is injected. See docs/superpowers/specs/2026-07-09-orchestrator-design.md."""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from itertools import zip_longest
from typing import Any, Callable

log = logging.getLogger("autointraday.orchestrator")

# Calibrated to the HONEST scoring scale the anti-optimism prompt produces (observed 2026-07-14:
# no-edge decisions score 21-42, genuine setups 58-63 — the old 70/62 floors sat above the
# engine's entire achievable range and blocked every trade). These floors sit in the gap.
# Lowered again 2026-07-16 (60->55 / 1.8->1.6): on extended trend days the engine's *actionable*
# BUY_ON_PULLBACK setups land in the 55-59 "aggressive" band (skill: 60+ tradable, 55+ aggressive),
# so a 60 floor left them just out of reach. NOTE: this only helps entry-actions that scored 55-59
# — it does NOT convert WAIT decisions (which are gated by `action in ENTRY_ACTIONS` first and carry
# no risk_reward). The dominant reason for idle cycles is the *candidate pool* (faded losers the
# engine rightly WAITs on) + genuinely extended tapes — not this floor. Don't chase activity by
# dropping below ~52; sub-50 setups backtested as no-edge and forcing them loses money.
# Lowered again 2026-07-17 (user request): 55->52 quality (the documented floor — no lower),
# 55->50 confidence, 1.6->1.5 R:R.
MIN_TRADE_QUALITY = 52
MIN_RISK_REWARD = 1.5
MIN_CONFIDENCE = 50
# Reject a trade whose stop sits closer than this % of price to the entry. A near-zero stop is
# both a guaranteed noise stop-out AND an oversizing trap (risk_qty = risk/stop_distance explodes
# as the distance -> 0, so the tightest stop produces the LARGEST position). 0.4% is inside normal
# intraday noise for a liquid name; anything tighter is not a real structural stop. Added
# 2026-07-22 after the paper post-mortem found a live stop 0.04% below entry.
MIN_STOP_DISTANCE_PCT = 0.4
SQUAREOFF_BARS = 1
SQUAREOFF_MINUTES = 15
# SIGNAL-exit gate (2026-07-22 post-mortem): the exit engine used to close a position on a
# SINGLE reverse read at any conviction — 4 of the 8 losing paper trades were exited early this
# way (CYIENTDLM entered at conviction 84 and was flipped out on a 61; MOL 74->44; WESTLIFE
# 74->42). Now a reverse signal must clear a conviction floor AND repeat for EXIT_CONFIRM_CYCLES
# consecutive cycles before it overrides the structural stop. The stop still protects throughout,
# so the only thing we give up is panic-exiting on one noisy read.
MIN_EXIT_QUALITY = 55
MIN_EXIT_CONFIDENCE = 55
EXIT_CONFIRM_CYCLES = 2
# Trend veto (2026-07-22 post-mortem): 8 of 9 paper entries were LONG and lost as a group, with
# no code-level check that a long was even with the tape. Veto a long in a bearish aggregate tape
# and a short in a bullish one, using the indicator tool's `higher_timeframe.overall_bias` (the
# daily/1h/15m/5m aggregate). Neutral/mixed tapes are allowed. Fails OPEN if the field is absent
# (the engine's own gates still apply). One switch to relax if it proves too strict.
TREND_VETO_ENABLED = True
# Risk-based sizing: every trade risks the same fraction of the pool (entry-to-stop distance
# determines quantity), still capped by capital_per_position as a margin/concentration limit.
RISK_PER_TRADE_PCT = 1.0
# Execution-probability margins ("breathing space", widened 2026-07-20 on user request after
# a pullback call — JGCHEM — never filled because price rallied without dipping to the level).
# Each margin trades a sliver of profit for a higher chance the trade actually happens. Entry
# moves TOWARD current price (pullback limit up, breakout trigger early); stop widens AWAY from
# entry (fewer noise stop-outs — rupee risk is UNCHANGED because _size_quantity sizes off the
# widened stop distance). Market entries (BUY_NOW/SHORT_NOW) fill at LTP, entry untouched.
ENTRY_TOLERANCE_PCT = 0.25
STOP_TOLERANCE_PCT = 0.35
# Near-miss fill band: a synthetic pullback LIMIT also fills when price OVERSHOOTS the level by
# up to this %, instead of only when it comes all the way back to the level. This is the direct
# JGCHEM fix — the pullback that rallies a hair past the entry still gets taken (at the current
# price, so a touch less profit), not missed. Bounded so we never chase a runaway; the STOP
# breakout path keeps its own >1% overextension guard on the other side.
ENTRY_FILL_TOLERANCE_PCT = 0.40
# Each cycle a still-resting order is re-evaluated against a fresh engine read: cancelled if the
# setup is gone, or its levels refreshed if they moved by MORE than this (a small drift isn't
# worth churning the order / a live broker cancel+replace and losing queue position).
PENDING_REFRESH_MIN_MOVE_PCT = 0.5
# Disciplined scale-in (add to a LOSING position when the engine still re-affirms the trade —
# user request 2026-07-20). This is the SAFE form: the add is sized so the COMBINED position
# still risks <= RISK_PER_TRADE_PCT to the UNCHANGED stop (never widen a stop on an add), and
# is hard-capped by the free pool + per-position capital so it can never over-commit. Only on a
# genuine dip (below entry by >= the min drawdown) that is still above the stop. The 1%-risk math
# self-limits it to effectively ONE meaningful add (after it, the risk budget is spent).
SCALE_IN_ENABLED = True
SCALE_IN_MIN_DRAWDOWN_PCT = 0.5
# Target shave is proportional to the EXPECTED MOVE, not the price (user, 2026-07-20): keep
# (100 - shave)% of the projected entry->target move — "if it says 5% we're happy with 4%".
# Reduced 25.0 -> 10.0 (2026-07-22 post-mortem): a 25% haircut was the single biggest destroyer
# of the engine's planned risk:reward (median planned R:R 1.82 collapsed to ~1.1 at fill). Early
# profit-taking is now handled properly by the partial profit-book (PROFIT_BOOK_*), so the target
# no longer needs to be pulled in so hard, and the post-margin R:R re-gate rejects whatever slips
# below MIN_RISK_REWARD anyway.
TARGET_MOVE_SHAVE_PCT = 10.0
# --- Intraday leverage & profit-taking (added 2026-07-22, user request) --------------------
# The broker gives ~5x MIS intraday leverage: capital_per_position and total_pool are treated as
# MARGIN, and a position deploys up to LEVERAGE x that as NOTIONAL. Per-trade RUPEE RISK is
# unchanged (risk-based sizing still caps each trade at RISK_PER_TRADE_PCT of the pool to its
# stop); leverage only lets a position reach that risk cap with a bigger notional, so a normal
# ~2% move produces a meaningful rupee P&L. WARNING: leverage amplifies losses too — only sound
# because the R:R re-gate + stop floor above make each trade's reward >= risk.
LEVERAGE = 5.0
# Book PART of a winner once it has earned this % return ON DEPLOYED MARGIN, rather than waiting
# for a target that may be too far and revert. 10% return on margin at LEVERAGE=5 == a ~2%
# favorable price move (PROFIT_BOOK_MOVE_PCT below). Quality-scaled: a higher-quality trade is let
# to run a little further before booking. On trigger we sell PROFIT_BOOK_FRACTION of the position
# and trail the remaining runner's stop to breakeven (a free trade to the target).
PROFIT_BOOK_RETURN_PCT = 10.0
PROFIT_BOOK_MOVE_PCT = PROFIT_BOOK_RETURN_PCT / LEVERAGE   # favorable price-move trigger, %
PROFIT_BOOK_FRACTION = 0.5
# Quality tilt: book move is scaled by clamp(0.8, 1.2, 1 + (quality-70)/100), so a quality-52
# trade books ~0.8x sooner and a quality-90 trade rides ~1.2x further before the partial book.
PROFIT_BOOK_QUALITY_PIVOT = 70.0
# Broker-side OCO bracket. False after the 2026-07-20 1-share verification: Groww accepts
# create_smart_order but modify/cancel return "Order already terminated" for orders whose
# status still reads ACTIVE, the list endpoint returns them as absent, and a live fire was
# never observed. Until cancel provably works, an un-cancellable maybe-armed bracket is more
# dangerous than none: cycle-level exits + square-off + reconcile are the protection.
USE_BROKER_OCO = False
# Circuit breaker: once today's realized loss reaches this fraction of the pool, no NEW entries
# for the rest of the day (open positions keep being managed to flat).
MAX_DAILY_LOSS_PCT = 5.0
# Immediate actions fill at market this cycle; resting actions place a PENDING order at the
# decision's entry level that fills on a later cycle when price trades to it.
IMMEDIATE_ENTRY_ACTIONS = ("BUY_NOW", "SHORT_NOW")
RESTING_ENTRY_ACTIONS = ("BUY_ON_PULLBACK", "BUY_ON_BREAKOUT")
ENTRY_ACTIONS = IMMEDIATE_ENTRY_ACTIONS + RESTING_ENTRY_ACTIONS
SHORT_ACTIONS = ("SHORT_NOW",)
# Candidate pool per direction = free_slots + SLOT_HEADROOM. At 5 (top-7 each way, ~14 names
# after interleave/dedup) the pool catches setups the old top-5 cut missed (NUVOCO was #7 on
# 2026-07-15). Ceiling: each name costs an Opus+web-search call (~1.5-3 min); a full CLASSIC
# screen ran 13-18 min on 2026-07-17 — at the 20-min spacing (schedule v5) classic mode WILL
# overrun and skip fires (acceptable: degrades to ~40-min cadence). Skill mode's one-shot
# screen ran ~2 min live, so it fits comfortably. Don't raise SLOT_HEADROOM above 5.
SLOT_HEADROOM = 5


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


# A broker order in any of these states did NOT establish a position — never record one for it.
_REJECTED_STATES = ("REJECTED", "CANCELLED", "CANCELED", "FAILED", "REJECT", "EXPIRED")
# Broker order-status strings that mean a resting LIMIT order has executed (Groww may vary these;
# verify with the live one-share smoke test before trusting live resting fills).
_FILLED_STATES = ("EXECUTED", "COMPLETE", "COMPLETED", "FILLED")


def _is_rejected(order: dict) -> bool:
    return str(order.get("status", "")).upper() in _REJECTED_STATES


def _txn(side: str) -> str:
    return "BUY" if side == "LONG" else "SELL"


def _tick(px: float) -> float:
    """Round to the NSE tick size (₹0.05) — broker rejects off-tick limit prices."""
    return round(round(px / 0.05) * 0.05, 2)


def _passes_entry_gate(decision) -> bool:
    """A trade is only worth taking with a genuine, data-backed edge. Every hard floor here must
    clear before capital is risked: a strong setup (trade_quality), a real payoff-to-risk skew
    (risk_reward), the engine's own conviction (confidence), and a valid entry+stop to size and
    protect it. Anything short of all four is a WAIT, not a marginal trade."""
    return (decision.action in ENTRY_ACTIONS
            and decision.trade_quality is not None and decision.trade_quality >= MIN_TRADE_QUALITY
            and decision.risk_reward is not None and decision.risk_reward >= MIN_RISK_REWARD
            and decision.confidence is not None and decision.confidence >= MIN_CONFIDENCE
            and decision.entry is not None and decision.stop_loss is not None
            and decision.target1 is not None)


def _size_quantity(entry: float, stop_loss: float | None, capital_per_position: float,
                   risk_amount: float, leverage: float = 1.0) -> int:
    """Risk-based sizing: qty = risk_amount / |entry - stop| so every trade risks the same
    rupee amount regardless of how wide the stop is, capped by (capital_per_position * leverage)
    / entry — the NOTIONAL cap, i.e. the margin allotment times intraday leverage. Capital-only
    sizing let the stop distance silently decide the risk: a 1% stop risked 4x less than a 4%
    stop on the same capital. Per-trade rupee risk is set by risk_amount and is UNCHANGED by
    leverage; leverage only raises the notional ceiling so a trade can reach that risk cap."""
    if entry <= 0:
        return 0
    cap_qty = int(math.floor(capital_per_position * leverage / entry))
    if stop_loss is None:
        return cap_qty
    stop_distance = abs(entry - stop_loss)
    if stop_distance <= 0:
        return 0
    risk_qty = int(math.floor(risk_amount / stop_distance))
    return min(cap_qty, risk_qty)


def _position_side(action: str) -> str:
    return "SHORT" if action in SHORT_ACTIONS else "LONG"


def _geometric_rr(entry, stop, target, side) -> float | None:
    """The ACTUAL reward:risk implied by the entry/stop/target geometry — as opposed to the
    number the engine self-reports. Returns None if any leg is missing or the geometry is
    degenerate (risk or reward <= 0, e.g. stop on the wrong side of entry). Used to re-gate a
    trade AFTER execution margins have moved the levels, so a shaved target / widened stop can
    never sneak a sub-threshold trade past the entry gate."""
    if entry is None or stop is None or target is None:
        return None
    if side == "LONG":
        risk, reward = entry - stop, target - entry
    else:
        risk, reward = stop - entry, entry - target
    if risk <= 0 or reward <= 0:
        return None
    return reward / risk


def _trend_blocks(side: str, indicators: dict) -> str | None:
    """Return a veto reason if this entry fights the aggregate tape, else None. Uses the indicator
    tool's `higher_timeframe.overall_bias` (e.g. 'strong bearish' / 'neutral' / 'bullish'): a LONG
    is vetoed in a bearish tape, a SHORT in a bullish one. Fails OPEN (no veto) when the field is
    missing, so a thin/degraded indicator payload never silently blocks everything."""
    bias = str(((indicators.get("higher_timeframe") or {}).get("overall_bias") or "")).lower()
    if not bias:
        return None
    if side == "LONG" and "bearish" in bias:
        return f"long vetoed: tape bias '{bias}'"
    if side == "SHORT" and "bullish" in bias:
        return f"short vetoed: tape bias '{bias}'"
    return None


def _market_summary(indicators: dict) -> str:
    """Compact, human-readable snapshot of the tape at decision time — persisted on entry
    decisions so a later post-mortem can see the regime a trade was taken in (the earlier gap:
    market context was never recorded, so we couldn't tell if the all-long book fought a downtape)."""
    htf = indicators.get("higher_timeframe") or {}
    mc = indicators.get("market_context") or {}
    nifty = mc.get("nifty") or {}
    vix = mc.get("india_vix") or {}
    parts = []
    if htf.get("overall_bias"):
        parts.append(f"tape {htf['overall_bias']}")
    if nifty.get("day_change_pct") is not None:
        parts.append(f"NIFTY {nifty['day_change_pct']:+g}% ({nifty.get('trend_15m', '?')})")
    if vix.get("regime"):
        parts.append(f"VIX {vix['regime']}")
    return " · ".join(parts)


def _stop_distance_ok(entry, stop) -> bool:
    """True if the stop is at least MIN_STOP_DISTANCE_PCT away from entry — i.e. a real structural
    stop, not one buried inside intraday noise (which both guarantees a stop-out and, via
    risk-based sizing, blows the position size up)."""
    if entry is None or stop is None or entry <= 0:
        return False
    return abs(entry - stop) / entry >= MIN_STOP_DISTANCE_PCT / 100.0


def _with_level_margins(decision):
    """Copy of the decision with execution-probability margins applied (see the
    *_TOLERANCE_PCT constants). Direction logic: entry moves toward current price
    (pullback limit up, breakout trigger down — shorts are market-only so entry is
    untouched for them); stop widens away from entry; target pulls in toward entry."""
    from dataclasses import replace
    short = _position_side(decision.action) == "SHORT"
    entry = decision.entry
    if entry is not None and decision.action in RESTING_ENTRY_ACTIONS:
        e_tol = ENTRY_TOLERANCE_PCT / 100.0
        entry = entry * (1 - e_tol) if decision.action == "BUY_ON_BREAKOUT" \
            else entry * (1 + e_tol)
    stop = decision.stop_loss
    if stop is not None:
        s_tol = STOP_TOLERANCE_PCT / 100.0
        stop = stop * (1 + s_tol) if short else stop * (1 - s_tol)
    target = decision.target1
    if target is not None and decision.entry is not None:
        # Keep (100 - shave)% of the projected move, measured from the ORIGINAL entry —
        # works symmetrically for shorts because the move is signed.
        keep = 1.0 - TARGET_MOVE_SHAVE_PCT / 100.0
        target = decision.entry + (target - decision.entry) * keep
    return replace(decision, entry=entry, stop_loss=stop, target1=target)


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
                 get_candidates: Callable[..., list], now_provider: Callable[[], datetime] = _utc_now,
                 screen_engine=None):
        self.store = store
        self.client = client
        self.engine = engine
        self.get_indicators = get_indicators
        self.get_candidates = get_candidates
        self.now_provider = now_provider
        self.screen_engine = screen_engine   # non-None -> one-shot skill screening for entries
        self._cycle_errors = 0
        self._external_order_symbols: set[str] = set()   # broker order book, set by reconcile

    def run_cycle(self, squareoff_only: bool = False) -> dict:
        run_id = self.store.start_run(self.client.mode)
        self._cycle_errors = 0     # dangerous-but-survivable failures (e.g. OCO placement)
        cfg = self.store.get_config()
        if cfg.is_paused:
            self.store.finish_run(run_id, "SUCCESS", num_candidates=0, num_actions=0,
                                  summary="paused")
            return {"run_id": run_id, "status": "SUCCESS", "exits": 0, "entries": 0,
                    "fills": 0, "cancels": 0, "errors": 0, "candidates": 0}
        try:
            self.client.ensure_ready()
            # 0) reconcile with the broker (live only): the OCO can fire BETWEEN cycles — the DB
            #    must learn the position is gone before we try to manage/exit it a second time.
            self._reconcile_broker(run_id)
            if squareoff_only:
                # End-of-day pass: flatten everything, place no new trades.
                exits, cancels, errors = self._square_off_all(run_id)
                errors += self._cycle_errors
                self.store.finish_run(run_id, "SUCCESS", num_candidates=0,
                                      num_actions=exits + cancels,
                                      summary=f"squareoff: {exits} exits, {cancels} cancels"
                                              + (f", {errors} ERRORS" if errors else ""))
                return {"run_id": run_id, "status": "SUCCESS", "exits": exits, "entries": 0,
                        "fills": 0, "cancels": cancels, "errors": errors, "candidates": 0}
            # 1) resolve resting orders (fill if price reached, cancel if expired),
            # 2) manage positions open BEFORE this cycle (exit / trail) — a position filled just
            #    now is left until next cycle so a stale intraday range can't instantly exit it,
            # 3) screen for new entries.
            fills, just_filled = self._resolve_pending(run_id)
            exits = self._manage_positions(run_id, skip_ids=just_filled)
            candidates, entries = self._screen_and_enter(run_id)
            self.store.finish_run(run_id, "SUCCESS", num_candidates=candidates,
                                  num_actions=exits + entries + fills,
                                  summary=f"{entries} entries, {fills} fills, {exits} exits"
                                          + (f", {self._cycle_errors} ERRORS"
                                             if self._cycle_errors else ""))
            return {"run_id": run_id, "status": "SUCCESS", "exits": exits, "entries": entries,
                    "fills": fills, "cancels": 0, "errors": self._cycle_errors,
                    "candidates": candidates}
        except Exception as e:
            self.store.finish_run(run_id, "FAILED", error=str(e))
            raise

    def _reconcile_broker(self, run_id: int) -> int:
        """LIVE only, first thing every cycle: the BROKER is the source of truth, not the DB —
        the user trades manually between cycles (their explicit request 2026-07-20). Syncs:
        1. DB-OPEN position flat at the broker -> close (BROKER_SYNC, ~LTP exit).
        2. DB-OPEN position larger than the broker's net -> shrink to broker qty (manual
           partial exit; that slice's P&L is not booked — its fill price is unknown).
        3. Broker MIS position unknown to the DB -> ADOPT into the book and manage like our
           own from this cycle on (engine exits, square-off included — user chose
           adopt-and-manage). CNC/delivery rows are NEVER adopted: flattening the long-term
           portfolio would be catastrophic.
        Also snapshots non-terminal broker-order symbols into _external_order_symbols so the
        entry screen can't double-commit a symbol that already has a live order.
        Fully defensive: any error here must never block the cycle."""
        self._external_order_symbols = set()
        if self.client.mode != "live":
            return 0
        try:
            broker_positions = self.client.get_positions()
        except Exception:
            log.exception("broker reconcile: get_positions failed — skipping reconcile")
            return 0
        try:
            terminal = set(_REJECTED_STATES) | set(_FILLED_STATES)
            self._external_order_symbols = {
                o["symbol"] for o in self.client.get_open_orders()
                if o.get("symbol") and str(o.get("status", "")).upper() not in terminal}
        except Exception:
            log.exception("broker reconcile: get_open_orders failed — no order exclusions")
        qty_by_symbol: dict[str, int] = {}
        mis_net: dict[str, int] = {}
        mis_avg: dict[str, float] = {}
        for p in broker_positions:
            qty = int(p["quantity"])
            qty_by_symbol[p["symbol"]] = qty_by_symbol.get(p["symbol"], 0) + qty
            if p.get("product", "MIS") == "MIS":
                mis_net[p["symbol"]] = mis_net.get(p["symbol"], 0) + qty
                if p.get("avg_price"):
                    mis_avg[p["symbol"]] = float(p["avg_price"])
        synced = 0
        for position in self.store.get_open_positions():
            net = qty_by_symbol.get(position.symbol, 0)
            try:
                if net == 0:
                    try:
                        exit_price = _ltp(self.get_indicators(position.symbol))
                    except Exception:
                        exit_price = position.entry_price   # P&L unknown; don't invent a move
                    pnl = self._realized_pnl(position.side, position.entry_price, exit_price,
                                             position.quantity)
                    self.store.close_position(position.id, exit_price=exit_price,
                                              exit_reason="BROKER_SYNC", realized_pnl=pnl)
                    self.store.record_decision(
                        run_id=run_id, symbol=position.symbol, action="EXIT",
                        reason="broker sync: no net qty at broker (manual exit / OCO fired?)",
                        position_id=position.id)
                    log.warning("reconciled %s: closed in DB (absent at broker), exit~%.2f",
                                position.symbol, exit_price)
                    synced += 1
                elif abs(net) < position.quantity:
                    self.store.update_position_quantity(position.id, abs(net))
                    self.store.record_decision(
                        run_id=run_id, symbol=position.symbol, action="ADJUSTED",
                        reason=f"broker sync: qty {position.quantity} -> {abs(net)} "
                               f"(manual partial exit)", position_id=position.id)
                    log.warning("reconciled %s: qty %d -> %d (manual partial exit)",
                                position.symbol, position.quantity, abs(net))
                    synced += 1
            except Exception:
                log.exception("broker reconcile failed for %s", position.symbol)
        known = ({p.symbol for p in self.store.get_open_positions()}
                 | {p.symbol for p in self.store.get_pending_positions()})
        for symbol, net in mis_net.items():
            if net == 0 or symbol in known:
                continue
            try:
                side = "LONG" if net > 0 else "SHORT"
                entry = mis_avg.get(symbol)
                if not entry:
                    entry = _ltp(self.get_indicators(symbol))
                pid = self.store.open_position(
                    symbol=symbol, exchange="NSE", side=side, quantity=abs(net),
                    entry_price=entry, target_price=None, stop_loss=None, mode="live")
                self.store.record_decision(
                    run_id=run_id, symbol=symbol, action="ADOPTED",
                    reason=f"manual {side} x{abs(net)} @ ~{entry} found at broker — "
                           f"adopted; bot manages it from this cycle", position_id=pid)
                log.warning("adopted manual %s position: %s x%d @ ~%.2f",
                            side, symbol, abs(net), entry)
                synced += 1
            except Exception:
                log.exception("broker reconcile: adopting %s failed", symbol)
        return synced

    def _square_off_all(self, run_id: int) -> tuple[int, int, int]:
        """Final end-of-day pass: flatten every OPEN position at market and cancel every resting
        order (in live, cancel the broker order too). No screening, no new entries. Each item is
        isolated so one broker failure can't leave the rest unmanaged; failures are counted so
        the job can raise an alert — a position NOT squared off is the worst silent failure."""
        exits = 0
        errors = 0
        for position in self.store.get_open_positions():
            try:
                indicators = self.get_indicators(position.symbol)
                self._close_position(position, _ltp(indicators), "SQUARE_OFF")
                self.store.record_decision(run_id=run_id, symbol=position.symbol, action="EXIT",
                                           reason="end-of-day square-off", position_id=position.id)
                exits += 1
            except Exception as e:
                errors += 1
                log.exception("square-off failed for %s", position.symbol)
                self.store.record_decision(run_id=run_id, symbol=position.symbol, action="SKIP",
                                           reason=f"square-off error: {e}", position_id=position.id)
        cancels = 0
        for position in self.store.get_pending_positions():
            try:
                if self.client.mode == "live" and position.entry_order_id:
                    self.client.cancel_order(position.entry_order_id)
                self.store.cancel_position(position.id, "SQUAREOFF")
                self.store.record_decision(run_id=run_id, symbol=position.symbol, action="CANCEL",
                                           reason="end-of-day cancel", position_id=position.id)
                cancels += 1
            except Exception:
                errors += 1
                log.exception("resting cancel failed for %s", position.symbol)
        return exits, cancels, errors

    @staticmethod
    def _realized_pnl(side: str, entry: float, exit_price: float, qty: int) -> float:
        return (exit_price - entry) * qty if side == "LONG" else (entry - exit_price) * qty

    def _close_position(self, position, exit_price: float, reason: str) -> None:
        # Disarm the protective OCO FIRST: exiting at market while the OCO legs stay armed at the
        # broker means a leg can fire after we're flat and leave a naked reverse position.
        if position.oco_order_id:
            try:
                self.client.cancel_oco_order(position.oco_order_id)
            except Exception:
                log.exception("OCO cancel failed for %s (%s) — verify at broker!",
                              position.symbol, position.oco_order_id)
        txn = "SELL" if position.side == "LONG" else "BUY"
        order = self.client.place_order(
            symbol=position.symbol, exchange=position.exchange, transaction_type=txn,
            quantity=position.quantity, order_type="MARKET", price=exit_price, product="MIS")
        self.store.record_order(
            broker_order_id=order["order_id"], symbol=position.symbol, transaction_type=txn,
            quantity=position.quantity, order_type="MARKET", price=exit_price,
            status=order.get("status", "COMPLETE"), mode=self.client.mode,
            position_id=position.id, raw_json=json.dumps(order, default=str))
        pnl = self._realized_pnl(position.side, position.entry_price, exit_price, position.quantity)
        self.store.close_position(position.id, exit_price=exit_price, exit_reason=reason,
                                  realized_pnl=pnl)

    def _exit_level(self, position, indicators):
        """Return (exit_price, reason) if this position should exit now, else None.

        Uses the CURRENT price (LTP), not the day's high/low: the day range includes hours from
        before this position existed, which produced phantom stop/target exits (look-ahead bias —
        a 13:05 entry could be 'stopped out' by a 10:30 low). LTP at cycle time is exactly what a
        market exit gets. Intra-cycle touches are the broker OCO's job in live and are
        deliberately not simulated in paper (conservative, no fiction)."""
        ltp = _ltp(indicators)
        if _should_square_off(indicators):
            return ltp, "SQUARE_OFF"
        if position.side == "LONG":
            if position.stop_loss is not None and ltp <= position.stop_loss:
                return ltp, "STOP"
            if position.target_price is not None and ltp >= position.target_price:
                return ltp, "TARGET"
        else:  # SHORT
            if position.stop_loss is not None and ltp >= position.stop_loss:
                return ltp, "STOP"
            if position.target_price is not None and ltp <= position.target_price:
                return ltp, "TARGET"
        return None

    def _manage_positions(self, run_id: int, skip_ids=frozenset()) -> int:
        exits = 0
        for position in self.store.get_open_positions():
            if position.id in skip_ids:
                continue                       # just filled this cycle — manage it next cycle
            try:
                exits += self._manage_one(run_id, position)
            except Exception as e:
                # A broker/indicator/engine error on ONE position must not abort managing the rest.
                log.exception("manage failed for %s", position.symbol)
                self.store.record_decision(run_id=run_id, symbol=position.symbol,
                                           action="SKIP", reason=f"manage error: {e}",
                                           position_id=position.id)
        return exits

    def _manage_one(self, run_id: int, position) -> int:
        """Exit-or-trail a single open position. Returns 1 if it exited, else 0."""
        indicators = self.get_indicators(position.symbol)
        level = self._exit_level(position, indicators)
        if level is not None:
            exit_price, reason = level
            self.store.record_decision(run_id=run_id, symbol=position.symbol,
                                       action="EXIT", reason=reason, position_id=position.id)
            self._close_position(position, exit_price, reason)
            return 1
        # Lock in a reverting winner: once the trade has earned the profit-book return, sell part
        # and trail the rest to breakeven. Purely price-driven (no engine call); stays open after.
        if self._maybe_book_partial(run_id, position, indicators):
            return 0
        ctx = {"side": position.side, "quantity": position.quantity,
               "entry_price": position.entry_price,
               "unrealized_pnl_pct": round(
                   self._realized_pnl(position.side, position.entry_price, _ltp(indicators), 1)
                   / position.entry_price * 100, 2)}
        decision = self.engine.decide(position.symbol, indicators, position=ctx)
        exit_actions = ("SELL_NOW",) if position.side == "LONG" else ("BUY_NOW",)
        # A SIGNAL exit must be CONVICTED and CONFIRMED: the reverse read clears the exit floors
        # (quality + confidence) and repeats for EXIT_CONFIRM_CYCLES consecutive cycles. A weak or
        # one-off flip just resets the counter and the trade rides its structural stop.
        convicted_exit = (decision.action in exit_actions
                          and decision.trade_quality is not None
                          and decision.trade_quality >= MIN_EXIT_QUALITY
                          and decision.confidence is not None
                          and decision.confidence >= MIN_EXIT_CONFIDENCE)
        self.store.record_decision(run_id=run_id, symbol=position.symbol,
                                   action=decision.action, score=decision.trade_quality,
                                   position_id=position.id, raw_json=decision.raw_response)
        if convicted_exit:
            confirmed = position.reverse_signal_count + 1
            if confirmed >= EXIT_CONFIRM_CYCLES:
                self._close_position(position, _ltp(indicators), "SIGNAL")
                return 1
            self.store.set_reverse_signal_count(position.id, confirmed)
            self.store.record_decision(
                run_id=run_id, symbol=position.symbol, action="HOLD",
                reason=f"exit signal {confirmed}/{EXIT_CONFIRM_CYCLES} — awaiting confirmation",
                position_id=position.id)
            return 0
        if position.reverse_signal_count:
            self.store.set_reverse_signal_count(position.id, 0)   # flip not sustained — reset
        # Engine re-affirmed the trade while it's underwater -> consider a disciplined scale-in.
        if self._maybe_scale_in(run_id, position, decision, indicators):
            return 0                       # added this cycle; don't also trail off the same read
        # Position stays open — trail its stop/target to the engine's latest read.
        self._maybe_trail(run_id, position, decision)
        return 0

    def _profit_book_move(self, position) -> float:
        """The favorable price move (as a fraction) that triggers the partial book for this
        position — PROFIT_BOOK_MOVE_PCT tilted by the entry trade_quality: higher-quality trades
        ride a little further before booking, lower-quality book sooner."""
        q = position.entry_quality if position.entry_quality is not None \
            else PROFIT_BOOK_QUALITY_PIVOT
        q_factor = min(1.2, max(0.8, 1.0 + (q - PROFIT_BOOK_QUALITY_PIVOT) / 100.0))
        return PROFIT_BOOK_MOVE_PCT / 100.0 * q_factor

    def _maybe_book_partial(self, run_id: int, position, indicators) -> bool:
        """Book PROFIT_BOOK_FRACTION of a position once it has run the (quality-scaled)
        profit-book move in our favor, and trail the runner's stop to breakeven. Books at most
        once per position (partial_booked). Returns True if it booked. Never near square-off (the
        whole position is about to flatten anyway), never if it can't leave >=1 share running."""
        if position.partial_booked or _should_square_off(indicators):
            return False
        ltp = _ltp(indicators)
        if position.entry_price <= 0 or ltp <= 0:
            return False
        if position.side == "LONG":
            favorable = (ltp - position.entry_price) / position.entry_price
        else:
            favorable = (position.entry_price - ltp) / position.entry_price
        if favorable < self._profit_book_move(position):
            return False
        sell_qty = int(math.floor(position.quantity * PROFIT_BOOK_FRACTION))
        if sell_qty < 1 or sell_qty >= position.quantity:
            return False                       # can't split (would leave the runner empty)
        txn = "SELL" if position.side == "LONG" else "BUY"
        order = self.client.place_order(
            symbol=position.symbol, exchange=position.exchange, transaction_type=txn,
            quantity=sell_qty, order_type="MARKET", price=ltp, product="MIS")
        if _is_rejected(order):
            self.store.record_decision(run_id=run_id, symbol=position.symbol, action="SKIP",
                                       reason=f"partial-book rejected: {order.get('status')}",
                                       position_id=position.id)
            return False
        self.store.record_order(
            broker_order_id=order["order_id"], symbol=position.symbol, transaction_type=txn,
            quantity=sell_qty, order_type="MARKET", price=ltp,
            status=order.get("status", "COMPLETE"), mode=self.client.mode,
            position_id=position.id, raw_json=json.dumps(order, default=str))
        slice_pnl = self._realized_pnl(position.side, position.entry_price, ltp, sell_qty)
        # Trail the runner's stop to breakeven (entry): a ratchet in the protective direction for
        # both sides, so the rest of the position can no longer turn into a loss.
        self.store.book_partial(position.id, sell_qty, slice_pnl, new_stop=position.entry_price)
        self.store.record_decision(
            run_id=run_id, symbol=position.symbol, action="BOOK_PARTIAL",
            reason=f"booked {sell_qty}/{position.quantity} @ {ltp} (+{slice_pnl:.0f}); "
                   f"stop->breakeven {position.entry_price}",
            entry_price=position.entry_price, stop_loss=position.entry_price,
            target_price=position.target_price, position_id=position.id)
        log.info("partial book %s: sold %d/%d @ %.2f (+%.0f), runner stop->BE %.2f",
                 position.symbol, sell_qty, position.quantity, ltp, slice_pnl,
                 position.entry_price)
        return True

    def _maybe_scale_in(self, run_id: int, position, decision, indicators) -> bool:
        """Add to an underwater position when the engine still re-affirms it — sized so the
        COMBINED position risks <= 1% of the pool to the UNCHANGED stop, and hard-capped by the
        free pool + per-position capital. Returns True if it added. Never widens the stop, never
        adds below the stop, never adds in profit, never over-commits the pool."""
        if not SCALE_IN_ENABLED or position.stop_loss is None or _should_square_off(indicators):
            return False
        # The engine must re-affirm the SAME side with a real entry edge (not a weak "still hope").
        same_side_entry = (decision.action in ENTRY_ACTIONS
                           and _position_side(decision.action) == position.side)
        if not (same_side_entry and _passes_entry_gate(decision)):
            return False
        ltp = _ltp(indicators)
        dd = SCALE_IN_MIN_DRAWDOWN_PCT / 100.0
        if position.side == "LONG":
            on_dip = ltp <= position.entry_price * (1 - dd) and ltp > position.stop_loss
            per_share_risk = ltp - position.stop_loss
            existing_risk = position.quantity * (position.entry_price - position.stop_loss)
        else:  # SHORT
            on_dip = ltp >= position.entry_price * (1 + dd) and ltp < position.stop_loss
            per_share_risk = position.stop_loss - ltp
            existing_risk = position.quantity * (position.stop_loss - position.entry_price)
        if not on_dip or per_share_risk <= 0:
            return False
        cfg = self.store.get_config()
        risk_amount = cfg.total_pool * RISK_PER_TRADE_PCT / 100.0
        remaining_risk = risk_amount - existing_risk
        if remaining_risk <= 0:
            return False                   # combined position already at the 1% budget — no add
        add_by_risk = remaining_risk / per_share_risk
        # Pool guard (user requirement): the add's cost must fit the FREE pool and the per-position
        # capital cap — an add can never push committed capital past the pool. All in MARGIN terms
        # (pool/cap are margin; notional = margin * LEVERAGE), so notional room = margin room * L.
        free_margin = cfg.total_pool - self.store.committed_capital() / LEVERAGE
        cap_room_margin = cfg.capital_per_position - position.quantity * position.entry_price / LEVERAGE
        notional_room = min(free_margin, cap_room_margin) * LEVERAGE
        if notional_room <= 0 or ltp <= 0:
            return False
        add_qty = int(math.floor(min(add_by_risk, notional_room / ltp)))
        if add_qty < 1:
            return False
        txn = _txn(position.side)
        order = self.client.place_order(
            symbol=position.symbol, exchange=position.exchange, transaction_type=txn,
            quantity=add_qty, order_type="MARKET", price=ltp, product="MIS")
        if _is_rejected(order):
            self.store.record_decision(run_id=run_id, symbol=position.symbol, action="SKIP",
                                       reason=f"scale-in rejected: {order.get('status')}",
                                       position_id=position.id)
            return False
        self.store.record_order(
            broker_order_id=order["order_id"], symbol=position.symbol, transaction_type=txn,
            quantity=add_qty, order_type="MARKET", price=ltp,
            status=order.get("status", "COMPLETE"), mode=self.client.mode,
            position_id=position.id, raw_json=json.dumps(order, default=str))
        new_avg = self.store.add_to_position(position.id, add_qty, ltp)
        self.store.record_decision(
            run_id=run_id, symbol=position.symbol, action="ADD",
            reason=f"scale-in +{add_qty} @ {ltp} (avg {position.entry_price:.2f}->{new_avg:.2f}, "
                   f"stop {position.stop_loss} unchanged)",
            entry_price=new_avg, stop_loss=position.stop_loss,
            target_price=position.target_price, position_id=position.id)
        log.warning("scaled in %s: +%d @ %.2f, qty %d->%d, avg %.2f->%.2f (stop %.2f unchanged)",
                    position.symbol, add_qty, ltp, position.quantity,
                    position.quantity + add_qty, position.entry_price, new_avg,
                    position.stop_loss)
        return True

    def _maybe_trail(self, run_id: int, position, decision) -> None:
        """Re-check an open position's protective levels each cycle and update them where the
        engine's latest read moved. The stop only RATCHETS toward profit (never loosens): up for
        a long, down for a short. The target follows the engine's latest target1. Changed levels
        are pushed to the BROKER's OCO too — a trailed stop that only lives in our DB protects
        nothing between cycles. A no-op when neither level moves; a real change is logged as an
        ADJUSTED operation so the dashboard's activity tally shows stop/target updates."""
        new_stop = position.stop_loss
        if decision.stop_loss is not None:
            if position.side == "LONG":
                if position.stop_loss is None or decision.stop_loss > position.stop_loss:
                    new_stop = decision.stop_loss
            else:  # SHORT
                if position.stop_loss is None or decision.stop_loss < position.stop_loss:
                    new_stop = decision.stop_loss
        # Target ratchets ONLY away from entry (never pull a winner's target in): up for a long,
        # down for a short. A re-quote that moved the target toward entry used to shrink the
        # reward mid-trade and trigger an early TARGET exit — post-mortem 2026-07-22.
        new_target = position.target_price
        if decision.target1 is not None:
            if position.side == "LONG":
                if position.target_price is None or decision.target1 > position.target_price:
                    new_target = decision.target1
            else:  # SHORT
                if position.target_price is None or decision.target1 < position.target_price:
                    new_target = decision.target1
        if new_stop != position.stop_loss or new_target != position.target_price:
            self.store.update_position_levels(position.id, stop_loss=new_stop,
                                              target_price=new_target)
            if position.oco_order_id and new_stop is not None and new_target is not None:
                try:
                    self.client.modify_oco_order(position.oco_order_id,
                                                 target=_tick(new_target),
                                                 stop_loss=_tick(new_stop))
                except Exception:
                    # DB has the new levels (cycle-level exits still honor them); the broker
                    # keeps the old, still-protective legs. Log loudly, never break the cycle.
                    self._cycle_errors += 1
                    log.exception("broker OCO modify failed for %s (%s) — broker still holds "
                                  "the previous levels", position.symbol, position.oco_order_id)
            self.store.record_decision(
                run_id=run_id, symbol=position.symbol, action="ADJUSTED",
                reason=f"trailed stop {position.stop_loss}->{new_stop} "
                       f"target {position.target_price}->{new_target}",
                stop_loss=new_stop, target_price=new_target, position_id=position.id)
            log.info("trailed %s: stop %s->%s target %s->%s (broker OCO %s)", position.symbol,
                     position.stop_loss, new_stop, position.target_price, new_target,
                     "synced" if position.oco_order_id else "n/a")

    def _oco_legs(self, txn: str, qty: int, target: float, stop: float) -> dict:
        return dict(
            entry={"transaction_type": txn, "quantity": qty, "order_type": "MARKET"},
            target={"trigger_price": target, "order_type": "LIMIT", "price": target},
            stop_loss={"trigger_price": stop, "order_type": "LIMIT", "price": stop})

    def _place_oco_or_none(self, symbol: str, txn: str, qty: int, target: float, stop: float,
                           entry_order_id: str):
        """Place the protective OCO; on failure return None instead of raising. The entry order
        has ALREADY filled by the time this runs — letting the exception propagate would skip
        recording the position, leaving a real, invisible, unprotected holding at the broker.
        Recorded-but-OCO-less positions still get stop/target/square-off management every cycle."""
        if self.client.mode == "live" and not USE_BROKER_OCO:
            # Verified live 2026-07-20: Groww's smart-order API accepts OCO creation but
            # modify/cancel then fail with "Order already terminated" while status still reads
            # ACTIVE, the list endpoint can't see them, and firing could not be confirmed.
            # A bracket we cannot cancel before a manual exit can double-fire into a naked
            # position — so live OCOs are OFF until Groww's API proves trustworthy. Stops and
            # targets are enforced by cycle-level exits + the 15:18 square-off + reconcile.
            log.info("broker OCO disabled (USE_BROKER_OCO=False) — %s protected by "
                     "cycle-level exits only", symbol)
            return None
        try:
            return self.client.place_oco_order(
                symbol=symbol, **self._oco_legs(txn, qty, target, stop))
        except Exception:
            self._cycle_errors += 1
            log.exception("OCO placement FAILED for %s after entry %s — position is recorded "
                          "but UNPROTECTED at the broker; cycle-level exits still apply",
                          symbol, entry_order_id)
            return None

    def _place_entry(self, run_id: int, symbol: str, decision, indicators, mode: str) -> bool:
        cfg = self.store.get_config()
        side = _position_side(decision.action)
        # Trend veto — a long must not fight a bearish aggregate tape (nor a short a bullish one).
        if TREND_VETO_ENABLED:
            veto = _trend_blocks(side, indicators)
            if veto:
                self.store.record_decision(run_id=run_id, symbol=symbol, action=decision.action,
                                           score=decision.trade_quality,
                                           reason=f"rejected: {veto} · {_market_summary(indicators)}",
                                           raw_json=decision.raw_response)
                return False
        # P0 guard #1 — real stop distance, judged on the ENGINE'S structural stop (pre-margin):
        # the 0.35% execution widening is cosmetic breathing room, not structure, so a noise-level
        # stop must be rejected before it's masked by the widen (guaranteed stop-out + oversizing).
        if not _stop_distance_ok(decision.entry, decision.stop_loss):
            self.store.record_decision(run_id=run_id, symbol=symbol, action=decision.action,
                                       score=decision.trade_quality,
                                       reason=f"rejected: stop too tight "
                                              f"(< {MIN_STOP_DISTANCE_PCT}% from entry)",
                                       raw_json=decision.raw_response)
            return False
        decision = _with_level_margins(decision)   # sizing below uses the widened stop
        # P0 guard #2 — re-gate on the ACTUAL geometry after margins moved the levels. The entry
        # gate trusted the engine's self-reported risk_reward; here we recompute it from
        # entry/stop/target so a shaved target or widened stop can't open a sub-1.5 trade.
        actual_rr = _geometric_rr(decision.entry, decision.stop_loss, decision.target1, side)
        if actual_rr is None or actual_rr < MIN_RISK_REWARD:
            self.store.record_decision(run_id=run_id, symbol=symbol, action=decision.action,
                                       score=decision.trade_quality,
                                       reason=f"rejected: post-margin R:R "
                                              f"{actual_rr and round(actual_rr, 2)} "
                                              f"< {MIN_RISK_REWARD}",
                                       raw_json=decision.raw_response)
            return False
        risk_amount = cfg.total_pool * RISK_PER_TRADE_PCT / 100.0
        qty = _size_quantity(decision.entry, decision.stop_loss, cfg.capital_per_position,
                             risk_amount, LEVERAGE)
        # Pool is MARGIN; committed_capital() is NOTIONAL (sum of qty*entry across OPEN+PENDING),
        # so the free margin is pool minus committed-notional/LEVERAGE, and this trade's margin
        # cost is qty*entry/LEVERAGE. This keeps a resting order from over-committing the pool.
        free_margin = cfg.total_pool - self.store.committed_capital() / LEVERAGE
        if qty < 1 or qty * decision.entry / LEVERAGE > free_margin:
            self.store.record_decision(run_id=run_id, symbol=symbol, action=decision.action,
                                       score=decision.trade_quality,
                                       reason="rejected: sizing/capital", raw_json=decision.raw_response)
            return False
        market_note = _market_summary(indicators)      # tape snapshot recorded on the entry
        if decision.action in RESTING_ENTRY_ACTIONS:
            return self._place_resting_entry(run_id, symbol, decision, side, qty, mode, market_note)
        return self._place_market_entry(run_id, symbol, decision, side, qty, mode, market_note)

    def _place_market_entry(self, run_id: int, symbol: str, decision, side: str, qty: int,
                            mode: str, market_note: str = "") -> bool:
        txn = "BUY" if side == "LONG" else "SELL"
        entry_order = self.client.place_order(
            symbol=symbol, exchange="NSE", transaction_type=txn, quantity=qty,
            order_type="MARKET", price=decision.entry, product="MIS")
        if _is_rejected(entry_order):
            # Broker rejected the entry — do NOT open a phantom position or arm an OCO on nothing.
            self.store.record_order(
                broker_order_id=entry_order["order_id"], symbol=symbol, transaction_type=txn,
                quantity=qty, order_type="MARKET", price=decision.entry,
                status=entry_order.get("status", "REJECTED"), mode=mode,
                raw_json=json.dumps(entry_order, default=str))
            self.store.record_decision(run_id=run_id, symbol=symbol, action=decision.action,
                                       score=decision.trade_quality,
                                       reason=f"entry order rejected: {entry_order.get('status')}",
                                       raw_json=decision.raw_response)
            log.warning("entry order REJECTED for %s: %s", symbol, entry_order.get("status"))
            return False
        oco = self._place_oco_or_none(symbol, txn, qty, decision.target1, decision.stop_loss,
                                      entry_order["order_id"])
        pid = self.store.open_position(
            symbol=symbol, exchange="NSE", side=side, quantity=qty, entry_price=decision.entry,
            target_price=decision.target1, stop_loss=decision.stop_loss,
            entry_order_id=entry_order["order_id"],
            oco_order_id=oco["order_id"] if oco else None, mode=mode,
            entry_quality=decision.trade_quality)
        for o, otype in ((entry_order, "MARKET"), (oco, "OCO")):
            if o is None:
                continue
            self.store.record_order(
                broker_order_id=o["order_id"], symbol=symbol, transaction_type=txn,
                quantity=qty, order_type=otype, price=decision.entry,
                status=o.get("status", "COMPLETE"), mode=mode, position_id=pid,
                raw_json=json.dumps(o, default=str))
        self.store.record_decision(run_id=run_id, symbol=symbol, action=decision.action,
                                   score=decision.trade_quality, reason=market_note or None,
                                   entry_price=decision.entry, target_price=decision.target1,
                                   stop_loss=decision.stop_loss, position_id=pid,
                                   raw_json=decision.raw_response)
        return True

    def _broker_resting_order(self, symbol: str, side: str, qty: int, entry: float,
                              kind: str) -> dict:
        """Place ONE real resting broker order and return its dict. LIMIT = pullback at the
        level; SL = breakout stop-entry (trigger at the level, limit ~0.5% beyond to bound
        slippage). Raises on broker error — the caller decides the fallback."""
        txn = _txn(side)
        if kind == "LIMIT":
            return self.client.place_order(
                symbol=symbol, exchange="NSE", transaction_type=txn, quantity=qty,
                order_type="LIMIT", price=_tick(entry), product="MIS")
        buffer = 1.005 if side == "LONG" else 0.995
        return self.client.place_order(
            symbol=symbol, exchange="NSE", transaction_type=txn, quantity=qty,
            order_type="SL", price=_tick(entry * buffer), product="MIS",
            trigger_price=_tick(entry))

    def _place_resting_entry(self, run_id: int, symbol: str, decision, side: str, qty: int,
                             mode: str, market_note: str = "") -> bool:
        """Reserve a slot with a PENDING position at the decision's entry level.

        trigger_kind: PULLBACK entries are LIMIT-like (fill when price comes BACK to the level);
        BREAKOUT entries are STOP-like (fill when price breaks THROUGH the level).

        LIVE: BOTH kinds are REAL broker orders so they execute at the level in real time —
        pullback = resting LIMIT at the level; breakout = SL stop-entry (trigger at the level,
        limit ~0.5% beyond it to bound slippage). The broker fires them the moment price gets
        there; cycles only poll status. PAPER: synthetic (fills checked per cycle from LTP).
        Target/stop are stored now so the fill can arm the OCO."""
        kind = "STOP" if decision.action == "BUY_ON_BREAKOUT" else "LIMIT"
        txn = _txn(side)
        entry_order = None
        if mode == "live":
            entry_order = self._broker_resting_order(symbol, side, qty, decision.entry, kind)
            if _is_rejected(entry_order):
                self.store.record_decision(
                    run_id=run_id, symbol=symbol, action=decision.action,
                    score=decision.trade_quality,
                    reason=f"resting order rejected: {entry_order.get('status')}",
                    raw_json=decision.raw_response)
                log.warning("resting order REJECTED for %s: %s", symbol, entry_order.get("status"))
                return False
        entry_order_id = entry_order["order_id"] if entry_order else None
        pid = self.store.open_position(
            symbol=symbol, exchange="NSE", side=side, quantity=qty, entry_price=decision.entry,
            target_price=decision.target1, stop_loss=decision.stop_loss, mode=mode,
            entry_order_id=entry_order_id, status="PENDING", trigger_kind=kind,
            entry_quality=decision.trade_quality)
        if entry_order is not None:
            self.store.record_order(
                broker_order_id=entry_order_id, symbol=symbol, transaction_type=txn, quantity=qty,
                order_type="LIMIT", price=decision.entry,
                status=entry_order.get("status", "PENDING"), mode=mode, position_id=pid,
                raw_json=json.dumps(entry_order, default=str))
        self.store.record_decision(run_id=run_id, symbol=symbol, action=decision.action,
                                   score=decision.trade_quality,
                                   reason=f"resting @ {decision.entry}"
                                          + (f" · {market_note}" if market_note else ""),
                                   entry_price=decision.entry, target_price=decision.target1,
                                   stop_loss=decision.stop_loss, position_id=pid,
                                   raw_json=decision.raw_response)
        log.info("placed resting %s %s @ %s (%s)", side, symbol, decision.entry,
                 f"live {'SL stop-entry' if kind == 'STOP' else 'LIMIT'} order at broker"
                 if entry_order_id else "paper synthetic")
        return True

    def _resolve_pending(self, run_id: int):
        """Each cycle, walk every resting order and fill/cancel it. Returns (fill_count,
        set_of_filled_position_ids) so the caller can skip just-filled positions in this cycle's
        exit management. One order's broker error never aborts the others."""
        fills = 0
        filled_ids = set()
        for position in self.store.get_pending_positions():
            try:
                # Re-evaluate the resting order against a fresh read BEFORE trying to fill it:
                # cancel if the setup is gone, else refresh its levels. Then resolve as usual.
                if self._refresh_pending(run_id, position):
                    continue                                   # cancelled early — slot freed
                position = self.store.get_position(position.id)   # reload refreshed levels
                # A broker-tracked resting order (live pullback LIMIT) resolves by broker status;
                # everything else (paper, live breakout) resolves synthetically by price.
                filled = (self._resolve_pending_broker(run_id, position)
                          if position.entry_order_id
                          else self._resolve_pending_synthetic(run_id, position))
            except Exception as e:
                log.exception("pending resolve failed for %s", position.symbol)
                self.store.record_decision(run_id=run_id, symbol=position.symbol, action="SKIP",
                                           reason=f"pending resolve error: {e}",
                                           position_id=position.id)
                continue
            if filled:
                filled_ids.add(position.id)
                fills += 1
        return fills, filled_ids

    def _cancel_pending(self, position, reason: str) -> None:
        """Cancel a resting order — live: cancel the broker order first (best-effort), then mark
        the DB row CANCELLED so its slot + reserved capital free up."""
        if self.client.mode == "live" and position.entry_order_id:
            try:
                self.client.cancel_order(position.entry_order_id)
            except Exception:
                log.exception("resting cancel (broker) failed for %s — verify at broker",
                              position.symbol)
        self.store.cancel_position(position.id, reason)

    def _refresh_pending(self, run_id: int, position) -> bool:
        """Re-evaluate a still-resting order against a FRESH engine read each cycle (user request
        2026-07-20 — a resting order shouldn't keep stale levels between cycles).
        - Setup gone or flipped side -> cancel early and free the slot.
        - Still a valid same-side entry with moved levels -> refresh entry/stop/target/qty
          (live: cancel + replace the broker order). Quantity is re-sized so rupee risk stays
          ~1% off the NEW stop distance.
        Returns True if the order was cancelled (caller skips the fill attempt). Fully defensive:
        any error leaves the existing order untouched and never aborts the cycle."""
        try:
            indicators = self.get_indicators(position.symbol)
        except Exception:
            log.exception("refresh pending: indicators failed for %s — leaving as-is",
                          position.symbol)
            return False
        if _should_square_off(indicators):
            return False                       # let the normal resolve path expire it at close
        try:
            decision = _with_level_margins(
                self.engine.decide(position.symbol, indicators, position=None))
        except Exception:
            log.exception("refresh pending: engine failed for %s — leaving as-is",
                          position.symbol)
            return False
        # Loosened cancellation (2026-07-22 post-mortem): a resting order is only cancelled when
        # the engine actively flags the OPPOSITE side — a genuine invalidation of the thesis. A
        # plain WAIT (the pullback simply hasn't printed yet) or a few-point quality wobble no
        # longer kills the order; that over-cancelling drove the low fill rate on pullback/breakout
        # entries. Post-fill stop protection is the exit path's job, not the resting-order refresh.
        opposite_actions = (("SELL_NOW", "SHORT_NOW") if position.side == "LONG"
                            else ("BUY_NOW", "BUY_ON_PULLBACK", "BUY_ON_BREAKOUT"))
        if decision.action in opposite_actions:
            self._cancel_pending(position, "SETUP_GONE")
            self.store.record_decision(
                run_id=run_id, symbol=position.symbol, action="CANCEL",
                reason=f"resting update: flipped side (now {decision.action})",
                position_id=position.id)
            log.info("cancelled resting %s: flipped side (now %s)", position.symbol,
                     decision.action)
            return True
        # Not invalidated -> keep resting. Only refresh the levels if the fresh read is still a
        # valid SAME-SIDE entry with moved levels; a WAIT/HOLD leaves the existing order untouched.
        if not (_passes_entry_gate(decision)
                and _position_side(decision.action) == position.side):
            return False
        cfg = self.store.get_config()
        risk_amount = cfg.total_pool * RISK_PER_TRADE_PCT / 100.0
        qty = _size_quantity(decision.entry, decision.stop_loss, cfg.capital_per_position,
                             risk_amount, LEVERAGE)
        # Only churn the order (live: a broker cancel+replace) for a MEANINGFUL move — a few
        # paise of drift isn't worth losing queue position / a round of broker risk.
        thresh = PENDING_REFRESH_MIN_MOVE_PCT / 100.0

        def far(new, old):
            if new is None or old is None:
                return (new is None) != (old is None)
            return abs(new - old) > abs(old) * thresh
        moved = (far(decision.entry, position.entry_price)
                 or far(decision.stop_loss, position.stop_loss)
                 or far(decision.target1, position.target_price))
        if qty < 1 or not moved:
            return False                       # nothing worth churning the order for
        new_order_id = position.entry_order_id
        if self.client.mode == "live" and position.entry_order_id:
            try:
                self.client.cancel_order(position.entry_order_id)
                order = self._broker_resting_order(
                    position.symbol, position.side, qty, decision.entry,
                    position.trigger_kind or "LIMIT")
                if _is_rejected(order):
                    raise RuntimeError(f"replacement rejected: {order.get('status')}")
                new_order_id = order["order_id"]
            except Exception:
                self._cycle_errors += 1
                log.exception("resting update: cancel+replace failed for %s — keeping the old "
                              "order; verify at broker", position.symbol)
                return False
        self.store.update_pending_order(
            position.id, entry_price=decision.entry, stop_loss=decision.stop_loss,
            target_price=decision.target1, quantity=qty, entry_order_id=new_order_id)
        self.store.record_decision(
            run_id=run_id, symbol=position.symbol, action="ADJUSTED",
            reason=f"resting update -> entry {decision.entry}", entry_price=decision.entry,
            target_price=decision.target1, stop_loss=decision.stop_loss,
            position_id=position.id)
        log.info("refreshed resting %s: entry %.2f->%.2f stop %s->%s target %s->%s qty %d->%d",
                 position.symbol, position.entry_price, decision.entry, position.stop_loss,
                 decision.stop_loss, position.target_price, decision.target1,
                 position.quantity, qty)
        return False

    def _resolve_pending_synthetic(self, run_id: int, position) -> bool:
        """No broker order is resting — decide the fill from the CURRENT price only (LTP), never
        the day range: the day's high/low includes hours before this order existed, which used to
        'fill' orders on price levels the market never revisited (look-ahead bias).
        LIMIT (pullback) long fills when LTP has come back DOWN to the level; STOP (breakout)
        long fills when LTP has broken UP through it. Shorts mirror. Conservative: a touch-and-
        bounce between cycles is missed, not invented."""
        indicators = self.get_indicators(position.symbol)
        if _should_square_off(indicators):
            self.store.cancel_position(position.id, "EXPIRED")
            self.store.record_decision(run_id=run_id, symbol=position.symbol, action="CANCEL",
                                       reason="resting order expired at square-off",
                                       position_id=position.id)
            return False
        ltp = _ltp(indicators)
        kind = position.trigger_kind or "LIMIT"
        # LIMIT (pullback) fills when price comes back to the level OR overshoots it by up to
        # ENTRY_FILL_TOLERANCE_PCT (the near-miss breathing space — don't miss a shallow dip that
        # rallies past). STOP (breakout) fills when price breaks through the level.
        fill_band = ENTRY_FILL_TOLERANCE_PCT / 100.0
        if position.side == "LONG":
            touched = (ltp <= position.entry_price * (1 + fill_band) if kind == "LIMIT"
                       else ltp >= position.entry_price)
        else:  # SHORT
            touched = (ltp >= position.entry_price * (1 - fill_band) if kind == "LIMIT"
                       else ltp <= position.entry_price)
        if not touched:
            return False
        # Overextension guard (STOP entries): between cycles price can run FAR past the trigger
        # (BECTORFOOD 2026-07-16: trigger 188.6, next cycle saw 193.5 — filling there turns a
        # 1:2 plan into 1:0.4). If price is beyond the level by more than the tolerance, do NOT
        # chase — leave the order resting; it fills only on a retest near the level.
        if kind == "STOP":
            tolerance = position.entry_price * 0.01
            overextended = (ltp > position.entry_price + tolerance if position.side == "LONG"
                            else ltp < position.entry_price - tolerance)
            if overextended:
                log.info("resting STOP %s %s: price %.2f is >1%% past trigger %.2f — not "
                         "chasing; waiting for a retest", position.side, position.symbol,
                         ltp, position.entry_price)
                return False
        # Fill price. STOP becomes a market order -> current price. LIMIT books the level when
        # price actually reached it (long: ltp<=level, short: ltp>=level); when it filled only
        # via the near-miss band (a small overshoot) it pays the current price -> a touch less
        # profit, which is the deliberate trade for not missing the trade.
        if kind == "STOP":
            fill_price = ltp
        elif position.side == "LONG":
            fill_price = position.entry_price if ltp <= position.entry_price else ltp
        else:  # SHORT LIMIT
            fill_price = position.entry_price if ltp >= position.entry_price else ltp
        entry_order = self.client.place_order(
            symbol=position.symbol, exchange=position.exchange,
            transaction_type=_txn(position.side), quantity=position.quantity,
            order_type="LIMIT" if kind == "LIMIT" else "MARKET", price=fill_price,
            product="MIS")
        self._arm_filled(run_id, position, entry_order, fill_price, record_entry_order=True)
        return True

    def _resolve_pending_broker(self, run_id: int, position) -> bool:
        # Ask the broker whether the real resting LIMIT order has filled.
        st = self.client.get_order_status(position.entry_order_id)
        status = str(st.get("status", "")).upper()
        if status in _FILLED_STATES:
            self._arm_filled(run_id, position, st, position.entry_price,
                             record_entry_order=False)   # order was recorded at placement
            return True
        if status in _REJECTED_STATES:
            self.store.cancel_position(position.id, f"broker {status}")
            self.store.record_decision(run_id=run_id, symbol=position.symbol, action="CANCEL",
                                       reason=f"resting order {status} at broker",
                                       position_id=position.id)
        return False   # still resting — leave it for next cycle

    def _arm_filled(self, run_id: int, position, entry_order, fill_price: float,
                    record_entry_order: bool) -> None:
        """The entry filled (synthetically or broker-confirmed): arm the OCO and open the
        position at the actual fill price. record_entry_order=True when the entry order was
        placed just now (synthetic paths); False when it was already recorded at placement
        (live broker-tracked resting orders)."""
        txn = _txn(position.side)
        oco = self._place_oco_or_none(position.symbol, txn, position.quantity,
                                      position.target_price, position.stop_loss,
                                      str(position.entry_order_id))
        self.store.activate_position(position.id, entry_price=fill_price,
                                     oco_order_id=oco["order_id"] if oco else None)
        if record_entry_order:
            self.store.record_order(
                broker_order_id=entry_order["order_id"], symbol=position.symbol,
                transaction_type=txn, quantity=position.quantity, order_type="LIMIT",
                price=fill_price, status=entry_order.get("status", "COMPLETE"),
                mode=self.client.mode, position_id=position.id,
                raw_json=json.dumps(entry_order, default=str))
        if oco is not None:
            self.store.record_order(
                broker_order_id=oco["order_id"], symbol=position.symbol, transaction_type=txn,
                quantity=position.quantity, order_type="OCO", price=fill_price,
                status=oco.get("status", "ACTIVE"), mode=self.client.mode,
                position_id=position.id, raw_json=json.dumps(oco, default=str))
        self.store.record_decision(run_id=run_id, symbol=position.symbol, action="FILL",
                                   reason=f"resting order filled @ {fill_price}",
                                   entry_price=fill_price,
                                   target_price=position.target_price,
                                   stop_loss=position.stop_loss, position_id=position.id)
        log.info("filled resting %s %s @ %s", position.side, position.symbol, fill_price)

    def _daily_loss_breached(self, cfg) -> bool:
        """Circuit breaker: true once today's realized loss reaches MAX_DAILY_LOSS_PCT of the
        pool. Blocks NEW entries only — open positions keep being managed to flat (which is why
        this must not use is_paused: pausing would strand open positions unmanaged)."""
        today_iso = self.now_provider().date().isoformat()
        realized_today = self.store.realized_pnl_since(today_iso)
        max_loss = cfg.total_pool * MAX_DAILY_LOSS_PCT / 100.0
        if cfg.total_pool > 0 and realized_today <= -max_loss:
            log.warning("CIRCUIT BREAKER: realized today %.0f <= -%.0f (%.1f%% of pool) — "
                        "no new entries for the rest of the day", realized_today, max_loss,
                        MAX_DAILY_LOSS_PCT)
            return True
        return False

    def _gather_candidates(self, top: int) -> list:
        """Screen BOTH directions and interleave: top gainers alone gave the engine only
        already-extended longs (which the prompt then rightly refuses to chase) and made
        SHORT_NOW unreachable. Interleaving gives shorts a fair look within the same budget."""
        # Screener failures are transient (external endpoint) and must never fail the cycle —
        # exits were already managed; the only cost of a miss is no new entries this cycle.
        try:
            ups = self.get_candidates(direction="up", top=top)
        except Exception as e:
            log.warning("up-direction screen failed (%s) — continuing without gainers", e)
            ups = []
        try:
            downs = self.get_candidates(direction="down", top=top)
        except Exception as e:
            log.warning("down-direction screen failed (%s) — continuing without losers", e)
            downs = []
        if not ups and not downs:
            log.warning("both screens failed — no candidates this cycle")
        out, seen = [], set()
        for pair in zip_longest(ups, downs):
            for cand in pair:
                if cand and cand["symbol"] not in seen:
                    seen.add(cand["symbol"])
                    out.append(cand)
        return out

    def _screen_and_enter(self, run_id: int) -> tuple[int, int]:
        cfg = self.store.get_config()
        committed = self.store.count_committed_positions()   # OPEN + resting PENDING
        free_slots = cfg.max_open_positions - committed
        # Free MARGIN: pool minus committed NOTIONAL / LEVERAGE (see _place_entry accounting).
        free_capital = cfg.total_pool - self.store.committed_capital() / LEVERAGE
        if free_slots <= 0 or free_capital < cfg.capital_per_position:
            # Book full (open + resting orders fill every slot / the pool): do NOT screen the
            # market — exits and pending fills were already handled this cycle. Screening now
            # would waste an expensive market scan + LLM calls on trades we can't take.
            log.info("book full (%d/%d committed incl. pending, free_capital=%.0f) — skipping "
                     "market screen", committed, cfg.max_open_positions, free_capital)
            return 0, 0
        if self._daily_loss_breached(cfg):
            self.store.record_decision(run_id=run_id, symbol="*", action="SKIP",
                                       reason="circuit breaker: daily loss limit hit")
            return 0, 0
        held = ({p.symbol for p in self.store.get_open_positions()}
                | {p.symbol for p in self.store.get_pending_positions()}
                | self._external_order_symbols)   # live broker order book (incl. manual orders)
        if self.screen_engine is not None:
            return self._skill_screen_entries(run_id, cfg, free_slots, held)
        candidates = self._gather_candidates(top=free_slots + SLOT_HEADROOM)
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
                                           score=decision.trade_quality, reason="below gate",
                                           raw_json=decision.raw_response)
                continue
            try:
                placed = self._place_entry(run_id, symbol, decision, indicators, cfg.mode)
            except Exception as e:
                # A broker error placing ONE entry must not abort the rest of the screen.
                log.exception("entry placement failed for %s", symbol)
                self.store.record_decision(run_id=run_id, symbol=symbol, action="SKIP",
                                           reason=f"entry error: {e}")
                continue
            if placed:
                entries += 1
                held.add(symbol)
        return screened, entries

    def _skill_screen_entries(self, run_id: int, cfg, free_slots: int,
                              held: set[str]) -> tuple[int, int]:
        """One-shot skill screen: a single agentic claude call ranks the whole market and
        returns <=5 ready-made Decisions; gate + placement below are the SAME code the classic
        path uses. A screen failure degrades to 0 candidates — never fails the cycle."""
        try:
            results = self.screen_engine.screen(exclude_symbols=sorted(held))
        except Exception as e:
            log.warning("skill screen failed (%s) — no candidates this cycle", e)
            return 0, 0
        results = sorted(results, key=lambda sc: (sc[1].trade_quality is None,
                                                  -(sc[1].trade_quality or 0)))
        entries = 0
        for symbol, decision in results:
            if entries >= free_slots:
                break
            if symbol in held:      # belt and braces — the model was told to exclude these
                log.warning("skill screen returned held symbol %s — ignoring", symbol)
                continue
            if not _passes_entry_gate(decision):
                self.store.record_decision(run_id=run_id, symbol=symbol,
                                           action=decision.action,
                                           score=decision.trade_quality, reason="below gate",
                                           raw_json=decision.raw_response)
                continue
            try:
                indicators = self.get_indicators(symbol)
                placed = self._place_entry(run_id, symbol, decision, indicators, cfg.mode)
            except Exception as e:
                log.exception("entry placement failed for %s", symbol)
                self.store.record_decision(run_id=run_id, symbol=symbol, action="SKIP",
                                           reason=f"entry error: {e}")
                continue
            if placed:
                entries += 1
                held.add(symbol)
        return len(results), entries
