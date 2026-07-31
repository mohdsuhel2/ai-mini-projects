"""The hourly-cycle orchestrator: manage exits, screen entries, place paper/live orders,
persist state. Wires Phase 1 (client), Phase 2 (store), Phase 3 (engine). Every collaborator
is injected. See docs/superpowers/specs/2026-07-09-orchestrator-design.md."""
from __future__ import annotations

import json
import logging
import math
from dataclasses import replace
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
# Capital-based sizing (user, 2026-07-24): a position deploys the FULL capital_per_position as
# intraday margin — quantity = capital_per_position * LEVERAGE / entry (e.g. 30k margin at 5x on a
# 1000 stock = 150 shares, costing the 30k margin). MAX_RISK_PER_TRADE_PCT is only a SAFETY
# CEILING: if that full-margin size would lose more than this fraction of the pool to its stop
# (a pathological wide stop), the quantity is trimmed so one trade can't blow that ceiling. For
# normal ~1.5-2.5% stops the ceiling doesn't bind and the full margin is deployed.
MAX_RISK_PER_TRADE_PCT = 2.5
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
# stays within MAX_RISK_PER_TRADE_PCT to the UNCHANGED stop (never widen a stop on an add), and
# is hard-capped by the free pool + per-position capital so it can never over-commit. Only on a
# genuine dip (below entry by >= the min drawdown) that is still above the stop. NOTE: with
# full-margin sizing the initial entry usually spends the per-position capital, leaving no room to
# add — scale-in now fires mainly when the first entry was trimmed by the risk ceiling (wide stop).
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
# MARGIN, and a position deploys LEVERAGE x that as NOTIONAL — full-margin sizing (see
# _size_quantity), trimmed only by the MAX_RISK_PER_TRADE_PCT safety ceiling on a pathological
# wide stop. So the full margin is put to work on every normal trade and a ~2% move produces a
# meaningful rupee P&L. WARNING: leverage amplifies losses too — only sound because the R:R
# re-gate + stop floor above keep each trade's reward >= risk.
LEVERAGE = 5.0
# Early profit-taking — stop before the far (~25%) target that often reverts. Config-driven &
# toggleable (config.profit_book_enabled / _partial_pct / _full_pct, edited from the dashboard):
# book PROFIT_BOOK_FRACTION of the position once it has earned `profit_book_partial_pct` RETURN ON
# MARGIN (trailing the runner to breakeven), then EXIT the whole remaining position at
# `profit_book_full_pct`. Percentages are return-on-margin, so at LEVERAGE=5 a 7% level == a ~1.4%
# price move and 15% == a ~3% move. Only PROFIT_BOOK_FRACTION (how much the partial books) is a
# code constant; the trigger levels + on/off live in config so they can be tuned without a deploy.
PROFIT_BOOK_FRACTION = 0.5
# Armed broker exit (LIVE-only, config.arm_exit_enabled): when price comes within
# config.arm_exit_band_pct of a profit level, rest a real SELL LIMIT at the broker so a between-
# poll spike fills instantly. If the target then trails and the armed order's price drifts more
# than this fraction from the new level, cancel + re-arm at the new level (never lock a stale
# target). See docs/superpowers/specs/2026-07-28-armed-exit-design.md.
ARM_REARM_DRIFT_PCT = 0.1
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
# The broker transaction that CLOSES a position of each side — used to tell a user's own resting
# exit order apart from a pending manual add.
_EXIT_TXN = {"LONG": "SELL", "SHORT": "BUY"}


def _is_rejected(order: dict) -> bool:
    return str(order.get("status", "")).upper() in _REJECTED_STATES


def _txn(side: str) -> str:
    return "BUY" if side == "LONG" else "SELL"


def _tick(px: float, symbol: str | None = None) -> float:
    """Round to the SYMBOL'S OWN exchange tick — the broker rejects off-tick prices.

    Was hard-coded to ₹0.05, but Groww's instrument master gives most large NSE names a 0.10 tick
    (BAJFINANCE, MANKIND, NETWEB, TDPOWERSYS, RELIANCE) and only some 0.05. A 0.05-aligned price
    such as 4399.95 is INVALID on a 0.10 grid, which is what "choose price in multiples of the
    tick size" meant. Unknown symbols fall back to the coarser 0.10, which is always safe.
    """
    from instrument_master import round_to_tick
    return round_to_tick(px, symbol)


def _passes_entry_gate(decision, rr_gate: bool = True) -> bool:
    """A trade is only worth taking with a genuine, data-backed edge. Every hard floor here must
    clear before capital is risked: a strong setup (trade_quality), the engine's own conviction
    (confidence), and a valid entry+stop to size and protect it. When rr_gate is on (default) the
    self-reported payoff-to-risk skew (risk_reward) must also clear MIN_RISK_REWARD; when off
    (config.rr_gate_enabled=False) the R:R floor is skipped and the trade rides on quality +
    confidence. Anything short is a WAIT, not a marginal trade."""
    return (decision.action in ENTRY_ACTIONS
            and decision.trade_quality is not None and decision.trade_quality >= MIN_TRADE_QUALITY
            and (not rr_gate
                 or (decision.risk_reward is not None and decision.risk_reward >= MIN_RISK_REWARD))
            and decision.confidence is not None and decision.confidence >= MIN_CONFIDENCE
            and decision.entry is not None and decision.stop_loss is not None
            and decision.target1 is not None)


def _size_quantity(entry: float, stop_loss: float | None, capital_per_position: float,
                   max_risk_amount: float, leverage: float = 1.0) -> int:
    """Capital-based sizing with a risk SAFETY CEILING. The target is to deploy the full margin
    allotment at intraday leverage: qty = floor(capital_per_position * leverage / entry) — e.g.
    30k margin at 5x on a 1000 stock = 150 shares (costing the 30k margin). That size is then
    trimmed only if it would lose more than `max_risk_amount` to its stop (a pathological wide
    stop): qty = min(full-margin qty, floor(max_risk_amount / stop_distance)). For a normal stop
    the ceiling doesn't bind and the full margin is deployed."""
    if entry <= 0:
        return 0
    cap_qty = int(math.floor(capital_per_position * leverage / entry))   # full-margin target
    if stop_loss is None:
        return cap_qty
    stop_distance = abs(entry - stop_loss)
    if stop_distance <= 0:
        return 0
    risk_ceiling_qty = int(math.floor(max_risk_amount / stop_distance))   # catastrophe guard only
    return min(cap_qty, risk_ceiling_qty)


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


def _full_exit_target(decision, indicators) -> float | None:
    """The bracket/full-exit target: the desk risk_model's FINAL capped target (the Gate F
    ceiling), NOT the practical T1 the skill quotes as target1.

    Exit A/B 2026-07-31 (n=2,643 on the practical ladder): a full exit at practical T1 averages
    +0.009%/trade vs +0.103% for trail-to-ceiling — a fixed leg at T1 cuts the edge ~10x. The
    ceiling leg stays out of the way (~11% hit) so winners exit via the Gate K trail, while a
    gift spike still books. Fails open to target1 when the desk block is absent, and never
    brings the leg CLOSER to entry than target1 (wrong-side desk data is ignored).
    """
    t1 = decision.target1
    fin = (((indicators or {}).get("institutional_desk") or {}).get("risk_model") or {}) \
        .get("targets") or []
    if not fin or t1 is None:
        return t1
    t3 = fin[-1]
    if not isinstance(t3, (int, float)):
        return t1
    return max(t1, t3) if _position_side(decision.action) == "LONG" else min(t1, t3)


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


def _with_level_margins(decision, entry_tol_pct: float = ENTRY_TOLERANCE_PCT,
                        stop_tol_pct: float = STOP_TOLERANCE_PCT,
                        target_shave_pct: float = TARGET_MOVE_SHAVE_PCT):
    """Copy of the decision with execution "breathing space" applied (config-driven — see the
    Config.*_tolerance_pct / target_shave_pct fields; the constants are just the defaults).
    Direction logic: entry moves toward current price (pullback limit up, breakout trigger down —
    shorts are market-only so entry is untouched for them); stop widens AWAY from entry (a long's
    SL goes a little lower, a short's a little higher); target pulls in toward entry."""
    from dataclasses import replace
    short = _position_side(decision.action) == "SHORT"
    entry = decision.entry
    if entry is not None and decision.action in RESTING_ENTRY_ACTIONS:
        e_tol = entry_tol_pct / 100.0
        entry = entry * (1 - e_tol) if decision.action == "BUY_ON_BREAKOUT" \
            else entry * (1 + e_tol)
    stop = decision.stop_loss
    if stop is not None:
        s_tol = stop_tol_pct / 100.0
        stop = stop * (1 + s_tol) if short else stop * (1 - s_tol)
    target = decision.target1
    if target is not None and decision.entry is not None:
        # Keep (100 - shave)% of the projected move, measured from the ORIGINAL entry —
        # works symmetrically for shorts because the move is signed.
        keep = 1.0 - target_shave_pct / 100.0
        target = decision.entry + (target - decision.entry) * keep
    return replace(decision, entry=entry, stop_loss=stop, target1=target)


def _margins_from_cfg(cfg):
    """The three breathing-space percentages from config, as a kwargs dict for _with_level_margins."""
    return {"entry_tol_pct": cfg.entry_tolerance_pct, "stop_tol_pct": cfg.stop_tolerance_pct,
            "target_shave_pct": cfg.target_shave_pct}


def _should_square_off(indicators: dict) -> bool:
    session = indicators.get("session") or {}
    bars = session.get("bars_remaining")
    mins = session.get("minutes_to_squareoff")
    if bars is not None and bars <= SQUAREOFF_BARS:
        return True
    if mins is not None and mins <= SQUAREOFF_MINUTES:
        return True
    return False


LIVE_MAX_DRIFT_PCT = 20.0     # a live tick further than this from the closed bar is a bad tick


def _held_minutes(position) -> int | None:
    """How long the position has been open, in minutes. None when unparseable — the prompt line
    simply omits it rather than printing a bogus number."""
    from datetime import datetime, timezone as _tz
    try:
        opened = datetime.fromisoformat(position.opened_at)
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=_tz.utc)
        return max(0, int((datetime.now(_tz.utc) - opened).total_seconds() // 60))
    except Exception:
        return None


def _opposes(action: str, side: str) -> bool:
    """True when a decision's action is a directional call AGAINST an open position.

    HOLD / WAIT / NO_TRADE are neutral, not opposing — they carry valid same-side levels and must
    keep trailing normally. Kept as one definition shared by the resting-order refresh and the
    trail gate so the two can never drift apart.
    """
    opposite = (("SELL_NOW", "SHORT_NOW") if side == "LONG"
                else ("BUY_NOW", "BUY_ON_PULLBACK", "BUY_ON_BREAKOUT"))
    return action in opposite


def _reverse_flip_vetoed(side: str, indicators: dict) -> bool:
    """True when the engine's OWN analytics veto the direction a reverse read is flipping to —
    the flip is then not a credible exit signal and must not feed the confirmation counter.

    BALKRISIND 2026-07-30: seven bearish flips in four hours, every one FINOPB-vetoed AND
    filtered "short vs bullish HTF" by the v2 desk — yet they still counted as exit signals.
    A vetoed flip is the mirror of Ledger Gate C: a veto is not permission, in either direction.
    Fails OPEN: v1 indicators carry no `institutional_desk`, so nothing changes there.
    """
    desk = (indicators or {}).get("institutional_desk") or {}
    gates = desk.get("validated_gates") or {}
    filters = desk.get("no_trade_filters_failed") or []
    bias = ((indicators or {}).get("intraday_structure") or {}).get("directional_bias")
    if bias == "neutral":
        return True                                  # Gate E: neutral is a non-label, not a flip
    if side == "LONG":                               # reverse read flips to the SHORT side
        if gates.get("finopb_veto"):
            return True
        return any("short vs bullish HTF" in f for f in filters)
    # SHORT position: reverse read flips to the LONG side
    return any("long vs bearish HTF" in f for f in filters)


def _stop_is_sane(side: str, stop: float, ltp: float) -> bool:
    """A LONG's stop must sit strictly BELOW the live price, a SHORT's strictly ABOVE.

    A stop on the wrong side of the tape is not a stop — it is an instruction to exit at once. A
    short's stop written onto a long looks like a normal ratchet toward profit (it is simply
    'higher'), which is how BALKRISIND was closed on 2026-07-30 at a stop 21 points above the
    market. This is the last line of defence, independent of where the level came from.
    """
    if stop is None or ltp is None or ltp <= 0:
        return True                              # nothing to check against; other guards apply
    return stop < ltp if side == "LONG" else stop > ltp


def _ltp(indicators: dict) -> float:
    """The price to MANAGE a position against: the live tick when the feed gives one, else the
    close of the last completed bar.

    The engine LABELS on completed 15m bars — it drops the forming candle on purpose, so the label
    is stable — but `price.last` can be a full bar old. Managing against it made every soft exit
    (stop, target, take-profit, trailing, scale-in, pyramid, square-off fill) react up to 15 minutes
    late, while the broker's resting stop was already working off the live tape. `price.live` is
    Yahoo's regularMarketPrice, published alongside the closed-bar report for exactly this purpose.

    Entry/label logic is unaffected — that still reasons on completed bars.

    Falls back to the closed bar when the live tick is missing, non-positive, or implausibly far
    from it: inside a single bar a >LIVE_MAX_DRIFT_PCT gap is a feed glitch, and acting on one
    would fire a bogus stop on a real position.
    """
    price = indicators["price"]
    closed = float(price["last"])
    live = price.get("live")
    if live is None:
        return closed
    live = float(live)
    if live <= 0:
        return closed
    if closed > 0 and abs(live / closed - 1.0) * 100.0 > LIVE_MAX_DRIFT_PCT:
        log.warning("%s: ignoring implausible live tick %.2f vs closed bar %.2f — managing on the "
                    "closed bar", indicators.get("symbol", "?"), live, closed)
        return closed
    return live


def _profit_price(side: str, entry: float, return_pct: float, leverage: float = LEVERAGE) -> float:
    """Price at which a position reaches `return_pct` RETURN ON MARGIN. At LEVERAGE x, that return
    is a (return_pct / leverage)% price move — up for a LONG, down for a SHORT."""
    move = (return_pct / leverage) / 100.0
    return entry * (1 + move) if side == "LONG" else entry * (1 - move)


def _full_exit_price(side: str, entry: float, full_pct: float, target: float | None,
                     leverage: float = LEVERAGE) -> float:
    """The price the poll would take the FULL profit at — the nearer (to entry) of the full-margin
    take-profit level and the structural target, so the armed order never sits past where the poll
    would already have exited. Falls back to the take-profit level when there is no target."""
    tp = _profit_price(side, entry, full_pct, leverage)
    if target is None:
        return tp
    return min(tp, target) if side == "LONG" else max(tp, target)


def _near(ltp: float, level: float, band_pct: float) -> bool:
    """True once price is within band_pct% of `level` (either side). Arming trigger."""
    if level <= 0:
        return False
    return abs(ltp - level) / level <= band_pct / 100.0


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

    def _broker_state(self):
        """Read the broker ONCE per cycle and normalize it. Returns (mis, orders):
        - mis: symbol -> {"net": signed_qty, "avg": avg_price|None}, built from MIS rows ONLY.
          CNC/delivery is excluded on purpose — summing it in let a delivery holding mask a
          fully-closed MIS position, keeping a dead trade alive in the book.
        - orders: the non-terminal broker order book, or None if that read FAILED. None and []
          must stay distinguishable: an empty list means the user has no resting orders, while
          None means we don't know — and we must never cancel or exclude on a guess.
        A positions failure propagates; the caller skips the whole reconcile."""
        mis: dict[str, dict] = {}
        for p in self.client.get_positions():
            if p.get("product", "MIS") != "MIS":
                continue
            row = mis.setdefault(p["symbol"], {"net": 0, "avg": None})
            row["net"] += int(p["quantity"])
            if p.get("avg_price"):
                row["avg"] = float(p["avg_price"])
        try:
            terminal = set(_REJECTED_STATES) | set(_FILLED_STATES)
            orders = [o for o in self.client.get_open_orders()
                      if o.get("symbol") and str(o.get("status", "")).upper() not in terminal]
        except Exception:
            log.exception("broker reconcile: get_open_orders failed — no takeover, no exclusions")
            return mis, None
        return mis, orders

    def _adopt(self, run_id: int, symbol: str, net: int, avg: float | None) -> int:
        """Take an unknown broker MIS position into the book and return its id. Levels are left
        None ON PURPOSE: _manage_positions runs later in THIS same cycle, analyses the symbol
        with the full strategy skill and writes the real structural stop/target, with
        _ensure_protective_stop as the floor if that read returns none."""
        side = "LONG" if net > 0 else "SHORT"
        entry = avg or _ltp(self.get_indicators(symbol))
        pid = self.store.open_position(
            symbol=symbol, exchange="NSE", side=side, quantity=abs(net),
            entry_price=entry, target_price=None, stop_loss=None, mode="live")
        self.store.record_decision(
            run_id=run_id, symbol=symbol, action="ADOPTED",
            reason=f"manual {side} x{abs(net)} @ ~{entry} found at broker — "
                   f"adopted; bot manages it from this cycle", position_id=pid)
        log.warning("adopted manual %s position: %s x%d @ ~%.2f",
                    side, symbol, abs(net), entry)
        return pid

    def _sync_known(self, run_id: int, position, mis: dict) -> int:
        """Repair ONE DB-open position against broker MIS reality. Exactly one outcome:
          net 0            -> the user flattened it: cancel our orders, book it
          opposite sign    -> the user reversed it: book the old trade, adopt the new side fresh
          same size        -> nothing to do
          smaller          -> manual partial exit: shrink (that slice's P&L is not booked)
          larger           -> manual add: absorb at the broker's blended average
        Any size change tears the bracket down — its legs rest at the OLD quantity and would
        over- or under-sell. _manage_one rebuilds them later this cycle at the corrected size.
        Returns 1 if anything changed, else 0."""
        row = mis.get(position.symbol) or {}
        net = int(row.get("net", 0))
        avg = row.get("avg")
        if net == 0:
            self._close_broker_synced(
                run_id, position,
                "broker sync: no net qty at broker (manual exit / OCO fired?)")
            return 1
        if (net > 0) != (position.side == "LONG"):
            flipped = "LONG" if net > 0 else "SHORT"
            self._close_broker_synced(
                run_id, position,
                f"broker sync: side flipped to {flipped} x{abs(net)} (manual reversal)")
            self._adopt(run_id, position.symbol, net, avg)
            return 1
        if abs(net) == position.quantity:
            return 0
        self._cancel_stale_orders(position)
        if abs(net) < position.quantity:
            self.store.update_position_quantity(position.id, abs(net))
            reason = (f"broker sync: qty {position.quantity} -> {abs(net)} "
                      f"(manual partial exit)")
        else:
            entry = avg or position.entry_price
            self.store.update_position_size(position.id, abs(net), entry)
            reason = (f"broker sync: qty {position.quantity} -> {abs(net)} @ blended {entry} "
                      f"(manual add)")
        self.store.record_decision(run_id=run_id, symbol=position.symbol, action="ADJUSTED",
                                   reason=reason, position_id=position.id)
        log.warning("reconciled %s: %s", position.symbol, reason)
        return 1

    def _takeover_foreign_orders(self, run_id: int, orders: list) -> int:
        """Cancel the user's OWN resting exit orders on symbols the bot manages, so the bot's
        analysed stop/target is the only protection resting against those shares (their explicit
        choice: update the SL per the analysis rather than defer to the manual one).

        The position is pinned to eager bracket management so its protection is REPLACED, never
        merely removed — cancelling a stop while exit_mode is 'db_only' would otherwise leave it
        barer than it was before we touched it.

        Never touches: CNC orders (the delivery portfolio), orders whose product is unknown (fail
        safe), entry-side orders (a pending manual add — absorbed next cycle if it fills), or the
        bot's own bracket / entry / OCO ids."""
        taken = 0
        for position in self.store.get_open_positions():
            own = {position.broker_stop_order_id, position.broker_target_order_id,
                   position.entry_order_id, position.oco_order_id}
            exit_txn = _EXIT_TXN[position.side]
            for o in orders:
                if o.get("symbol") != position.symbol or o.get("order_id") in own:
                    continue
                if str(o.get("product") or "").upper() != "MIS":
                    continue
                if str(o.get("transaction_type") or "").upper() != exit_txn:
                    continue
                try:
                    self.client.cancel_order(o["order_id"])
                except Exception:
                    self._cycle_errors += 1
                    log.exception("foreign exit-order cancel FAILED for %s (%s) — a live "
                                  "resting order may remain; verify at broker!",
                                  position.symbol, o["order_id"])
                    continue
                self.store.set_force_bracket(position.id)
                self.store.record_decision(
                    run_id=run_id, symbol=position.symbol, action="ADJUSTED",
                    reason=f"took over manual {exit_txn} order {o['order_id']} — bot re-places "
                           f"the exit at its own analysed level", position_id=position.id)
                log.warning("took over manual %s order %s on %s",
                            exit_txn, o["order_id"], position.symbol)
                taken += 1
        return taken

    def _reconcile_broker(self, run_id: int) -> int:
        """LIVE only, first thing every cycle: the BROKER is the source of truth, not the DB —
        the user trades manually between cycles (their explicit request 2026-07-20). It does not
        merely RECORD the drift, it REPAIRS both sides — every DB-open position is put through
        _sync_known (flat / shrink / absorb / flip), every unknown broker MIS position is
        _adopt-ed, and the user's own resting exit orders are taken over so the bot's analysed
        levels are the only protection left standing.

        CNC/delivery rows are NEVER adopted and CNC orders are never cancelled: flattening the
        long-term portfolio would be catastrophic.

        Broker state (positions + the order book) is read once by _broker_state; non-terminal
        order symbols land in _external_order_symbols so the entry screen can't double-commit a
        symbol that already has a live order.
        Fully defensive: any error here must never block the cycle."""
        self._external_order_symbols = set()
        if self.client.mode != "live":
            return 0
        try:
            mis, orders = self._broker_state()
        except Exception:
            log.exception("broker reconcile: get_positions failed — skipping reconcile")
            return 0
        self._external_order_symbols = {o["symbol"] for o in orders} if orders else set()
        synced = 0
        for position in self.store.get_open_positions():
            try:
                synced += self._sync_known(run_id, position, mis)
            except Exception:
                log.exception("broker reconcile failed for %s", position.symbol)
        # Rebuilt AFTER _sync_known so a symbol adopted by the side-flip branch isn't adopted twice.
        known = ({p.symbol for p in self.store.get_open_positions()}
                 | {p.symbol for p in self.store.get_pending_positions()})
        for symbol, row in mis.items():
            net = int(row["net"])
            if net == 0 or symbol in known:
                continue
            try:
                self._adopt(run_id, symbol, net, row["avg"])
                synced += 1
            except Exception:
                log.exception("broker reconcile: adopting %s failed", symbol)
        if orders is not None:      # None = the order book read FAILED; never cancel on a guess
            synced += self._takeover_foreign_orders(run_id, orders)
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

    def _cancel_leg(self, position, which: str) -> None:
        """Cancel one broker bracket leg ('stop'|'target') and clear its id. cancel_order is benign
        on an already-filled/terminal order, so the fill-vs-cancel race is safe; a cancel that fails
        on a STILL-RESTING order is surfaced loudly (never swallowed) — a live leg could remain."""
        order_id = position.broker_stop_order_id if which == "stop" else position.broker_target_order_id
        if order_id:
            try:
                self.client.cancel_order(order_id)
            except Exception:
                self._cycle_errors += 1
                log.exception("bracket cancel FAILED for %s (%s %s) — a live resting order may "
                              "remain; verify at broker!", position.symbol, which, order_id)
        self.store.set_bracket_leg(position.id, which, None, None)

    def _cancel_bracket(self, position) -> None:
        """Cancel BOTH bracket legs before any market exit, so a resting order can't fire after
        we're flat and open a naked reverse position."""
        if position.broker_stop_order_id:
            self._cancel_leg(position, "stop")
        if position.broker_target_order_id:
            self._cancel_leg(position, "target")

    def _cancel_stale_orders(self, position) -> None:
        """Cancel EVERY broker order this position owns — both bracket legs and the OCO —
        because its size or its existence just changed at the broker. A leg left resting after
        a manual exit fires against shares we no longer hold and opens a naked reverse
        position. Never raises; a failed cancel is counted and logged loudly."""
        self._cancel_bracket(position)
        if position.oco_order_id:
            try:
                self.client.cancel_oco_order(position.oco_order_id)
            except Exception:
                self._cycle_errors += 1
                log.exception("OCO cancel failed for %s (%s) — verify at broker!",
                              position.symbol, position.oco_order_id)

    def _close_broker_synced(self, run_id: int, position, reason: str) -> None:
        """Book a position the user closed (or reversed) by hand. Stale broker orders are
        cancelled FIRST, then the position is booked at LTP — the manual fill price is unknown
        and never invented; if indicators fail we book at entry so no fictional move lands in
        the P&L."""
        self._cancel_stale_orders(position)
        try:
            exit_price = _ltp(self.get_indicators(position.symbol))
        except Exception:
            exit_price = position.entry_price
        pnl = self._realized_pnl(position.side, position.entry_price, exit_price,
                                 position.quantity)
        self.store.close_position(position.id, exit_price=exit_price,
                                  exit_reason="BROKER_SYNC", realized_pnl=pnl)
        self.store.record_decision(run_id=run_id, symbol=position.symbol, action="EXIT",
                                   reason=reason, position_id=position.id)
        log.warning("reconciled %s: closed in DB (%s), exit~%.2f",
                    position.symbol, reason, exit_price)

    def _close_position(self, position, exit_price: float, reason: str) -> None:
        # Disarm every protective order FIRST: exiting at market while a bracket leg or the OCO
        # stays armed at the broker means a leg can fire after we're flat and leave a naked
        # reverse position.
        self._cancel_stale_orders(position)
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

    def _book_context(self) -> dict:
        """Day-level state attached to EVERY decide() call, held or flat.

        The engine is stateless and single-stock: without this it cannot know the book is already
        deep in the red, or that four other names are open. On 2026-07-30 it kept sizing fresh
        entries as if nothing had happened while the day ran to -17.5k. Best-effort — a store
        hiccup must never block a decision."""
        try:
            from datetime import timedelta as _td

            from trading_calendar import IST as _IST
            # DB stamps are UTC but the trading day is IST — an IST day starts at 18:30 UTC the
            # previous evening.
            ist_midnight = datetime.now(_IST).replace(hour=0, minute=0, second=0, microsecond=0)
            start = ist_midnight.astimezone(timezone.utc).isoformat()
            return {"realized_pnl_today": round(self.store.realized_pnl_since(start), 2),
                    "open_positions": len(self.store.get_open_positions())}
        except Exception:
            log.debug("book context unavailable", exc_info=True)
            return {}

    def _manage_one(self, run_id: int, position) -> int:
        """Exit-or-trail a single open position. Returns 1 if it exited, else 0."""
        indicators = self.get_indicators(position.symbol)
        cfg = self.store.get_config()
        # force_bracket pins a position to eager management regardless of the global mode: it is
        # set when reconcile cancelled the user's own exit order, and the replacement bracket is
        # the only thing standing in for the protection we removed.
        eager = ((cfg.exit_mode in ("armed", "on_fill") or position.force_bracket)
                 and self.client.mode == "live")
        # Broker bracket (eager modes): reflect any FILLED leg first — one fills, the OTHER is
        # cancelled (software OCO). A fill closes the position; a dead leg refreshes our snapshot.
        if eager:
            res = self._reconcile_bracket_fills(run_id, position, indicators)
            if res == "closed":
                return 1
            if res == "changed":
                position = self.store.get_position(position.id)
        # Soft poll exit — but a live broker leg OWNS its side (skip the poll market-exit, no
        # double-sell); square-off always flattens (and _close_position cancels the bracket first).
        level = self._exit_level(position, indicators)
        if level is not None:
            exit_price, reason = level
            owned = ((reason == "STOP" and position.broker_stop_order_id)
                     or (reason == "TARGET" and position.broker_target_order_id))
            if reason == "SQUARE_OFF" or not owned:
                self.store.record_decision(run_id=run_id, symbol=position.symbol,
                                           action="EXIT", reason=reason, position_id=position.id)
                self._close_position(position, exit_price, reason)
                return 1
        # Early profit-taking: full exit at the upper level (suppressed if a broker target leg owns
        # it), or book half at the lower level (soft in all modes) and trail the rest to breakeven.
        tp = self._maybe_take_profit(run_id, position, indicators)
        if tp == "full":
            return 1
        if tp == "partial":
            position = self.store.get_position(position.id)
            if eager and self._bracket_live(position):
                self._cancel_bracket(position)            # stale quantity — rebuild below
                position = self.store.get_position(position.id)
        # Place/refresh the broker bracket (eager), or tear it down if the mode is now db_only.
        if eager:
            self._ensure_bracket(run_id, position, indicators, cfg)
            position = self.store.get_position(position.id)
        elif self._bracket_live(position):
            self._cancel_bracket(position)
            position = self.store.get_position(position.id)
        price_pct = round(
            self._realized_pnl(position.side, position.entry_price, _ltp(indicators), 1)
            / position.entry_price * 100, 2)
        ctx = {"side": position.side, "quantity": position.quantity,
               "entry_price": position.entry_price,
               "unrealized_pnl_pct": price_pct,
               # At LEVERAGE x MIS the price move understates the damage to the capital actually
               # at risk; the engine used to see only the smaller number.
               "unrealized_pnl_margin_pct": round(price_pct * LEVERAGE, 2),
               "held_minutes": _held_minutes(position),
               # Its own previous levels, so a re-quote has something to anchor to. The engine is
               # stateless — without these it re-derives a stop from scratch every cycle.
               "stop_loss": position.stop_loss,
               "target_price": position.target_price}
        decision = self.engine.decide(position.symbol, indicators, position=ctx,
                                      book=self._book_context())
        exit_actions = ("SELL_NOW",) if position.side == "LONG" else ("BUY_NOW",)
        # A SIGNAL exit must be CONVICTED and CONFIRMED: the reverse read clears the exit floors
        # (quality + confidence) and repeats for EXIT_CONFIRM_CYCLES consecutive cycles. A weak or
        # one-off flip just resets the counter and the trade rides its structural stop.
        convicted_exit = (decision.action in exit_actions
                          and decision.trade_quality is not None
                          and decision.trade_quality >= MIN_EXIT_QUALITY
                          and decision.confidence is not None
                          and decision.confidence >= MIN_EXIT_CONFIDENCE)
        if convicted_exit and _reverse_flip_vetoed(position.side, indicators):
            # The engine's own gates veto the side this flip points to (FINOPB / HTF conflict /
            # neutral label) — not a credible reversal, however confident the wording. Ride the
            # structural stop instead of feeding the confirmation counter.
            convicted_exit = False
            self.store.record_decision(
                run_id=run_id, symbol=position.symbol, action="HOLD",
                reason=f"{decision.action} vetoed by engine gates (FINOPB/HTF/neutral) — "
                       f"not counted as an exit signal",
                position_id=position.id)
        self.store.record_decision(run_id=run_id, symbol=position.symbol,
                                   action=decision.action, score=decision.trade_quality,
                                   position_id=position.id, raw_json=decision.raw_response)
        # Rotation's ranking key. Recorded every cycle so the weakest holding is identified from
        # the engine's own current read rather than from P&L.
        if decision.trade_quality is not None:
            self.store.set_position_quality(position.id, float(decision.trade_quality))
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
        # Scale INTO strength: a STRONG, persisting same-side re-affirm adds capital to a winner and
        # raises its full-book. Checked BEFORE the underwater scale-in so only one adds per cycle.
        if self._maybe_pyramid(run_id, position, decision, indicators):
            return 0                       # added this cycle; don't also scale-in / trail
        # Engine re-affirmed the trade while it's underwater -> consider a disciplined scale-in.
        if self._maybe_scale_in(run_id, position, decision, indicators):
            return 0                       # added this cycle; don't also trail off the same read
        # Position stays open — trail its stop/target to the engine's latest read, guarantee it
        # has SOME stop, then re-sync the broker bracket so a level that just moved is resting at
        # the broker now rather than a cycle from now (_ensure_leg is a no-op when it already is).
        self._maybe_trail(run_id, position, decision, indicators)
        position = self.store.get_position(position.id)
        self._ensure_protective_stop(run_id, position, indicators)
        if eager:
            self._ensure_bracket(run_id, self.store.get_position(position.id), indicators, cfg)
        return 0

    def _maybe_take_profit(self, run_id: int, position, indicators):
        """Config-driven early profit-taking (stops before the far target). Exit the WHOLE position
        at profit_book_full_pct return-on-margin; else book PROFIT_BOOK_FRACTION once at
        profit_book_partial_pct (trailing the runner to breakeven). Returns 'full' (position
        closed), 'partial' (booked, still open), or None. Disabled / near square-off -> None."""
        cfg = self.store.get_config()
        if not cfg.profit_book_enabled or _should_square_off(indicators):
            return None
        ltp = _ltp(indicators)
        if position.entry_price <= 0 or ltp <= 0:
            return None
        if position.side == "LONG":
            favorable = (ltp - position.entry_price) / position.entry_price
        else:
            favorable = (position.entry_price - ltp) / position.entry_price
        # return-on-margin % -> favorable price-move fraction (return / leverage). A position that
        # has been pyramided into rides a HIGHER full-book (pyramid_full_pct) so the added capital
        # can chase a bigger move; the partial book is unchanged.
        full_pct = cfg.pyramid_full_pct if position.pyramid_count > 0 else cfg.profit_book_full_pct
        full_move = (full_pct / LEVERAGE) / 100.0
        partial_move = (cfg.profit_book_partial_pct / LEVERAGE) / 100.0
        # Full take-profit: close the whole remaining position. Suppressed while a broker TARGET
        # leg is resting at Groww — that order is the single source of this exit (no double-sell).
        if (full_pct > 0 and favorable >= full_move
                and not position.broker_target_order_id):
            self.store.record_decision(
                run_id=run_id, symbol=position.symbol, action="EXIT",
                reason=f"take-profit {full_pct:g}% on margin @ {ltp}",
                position_id=position.id)
            self._close_position(position, ltp, "TAKE_PROFIT")
            log.info("take-profit FULL %s @ %.2f (+%.1f%% on margin)", position.symbol, ltp,
                     favorable * LEVERAGE * 100)
            return "full"
        # Partial book once at the lower level, trailing the runner to breakeven. Stays SOFT in all
        # modes; when it books in an eager mode the caller resizes the bracket to the new quantity.
        if (not position.partial_booked and cfg.profit_book_partial_pct > 0
                and favorable >= partial_move):
            if self._book_partial_slice(run_id, position, ltp):
                return "partial"
        return None

    def _book_partial_slice(self, run_id: int, position, ltp: float) -> bool:
        """Sell PROFIT_BOOK_FRACTION of the position at ltp and trail the runner's stop to
        breakeven. Returns True if booked (never if it can't leave >=1 share running)."""
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

    # --- Broker exit bracket (LIVE eager modes: exit_mode 'armed' / 'on_fill') ----------------
    def _broker_order_status(self, order_id: str) -> str:
        """Best-effort broker status, UPPER-cased; '' on any error (treated as still-resting so a
        transient status glitch never books/closes a position by mistake)."""
        try:
            return str(self.client.get_order_status(order_id).get("status", "")).upper()
        except Exception:
            log.exception("bracket status check failed for %s", order_id)
            return ""

    def _bracket_live(self, position) -> bool:
        return bool(position.broker_stop_order_id or position.broker_target_order_id)

    def _bracket_levels(self, position, cfg):
        """The (stop_price, target_price) the bracket should rest at. Target = the nearer of the
        full-profit level and the structural target (never past where we'd exit), or the raw target
        when profit-taking is off. Either may be None (that leg is not placed)."""
        stop_px = position.stop_loss
        if cfg.profit_book_enabled and cfg.profit_book_full_pct > 0 and position.entry_price > 0:
            target_px = _full_exit_price(position.side, position.entry_price,
                                         cfg.profit_book_full_pct, position.target_price)
        else:
            target_px = position.target_price
        return stop_px, target_px

    def _reconcile_bracket_fills(self, run_id: int, position, indicators):
        """Reflect a FILLED bracket leg into the DB before soft levels are evaluated, cancelling the
        OTHER leg (software OCO). Returns 'closed' (position exited), 'changed' (a dead leg cleared),
        else None. LIVE-only."""
        if self.client.mode != "live":
            return None
        for which, reason, order_id, price in (
                ("target", "TAKE_PROFIT", position.broker_target_order_id,
                 position.broker_target_price),
                ("stop", "STOP", position.broker_stop_order_id, position.broker_stop_price)):
            if not order_id:
                continue
            status = self._broker_order_status(order_id)
            if status in _FILLED_STATES:
                fill_px = price or _ltp(indicators)
                self.store.set_bracket_leg(position.id, which, None, None)   # filled; don't cancel
                self._cancel_leg(position, "stop" if which == "target" else "target")   # OCO
                pnl = self._realized_pnl(position.side, position.entry_price, fill_px,
                                         position.quantity)
                self.store.close_position(position.id, exit_price=fill_px, exit_reason=reason,
                                          realized_pnl=pnl)
                self.store.record_decision(run_id=run_id, symbol=position.symbol, action="EXIT",
                                           reason=f"broker {which} filled @ {fill_px}",
                                           position_id=position.id)
                log.info("broker %s leg filled %s @ %.2f (%s)", which, position.symbol, fill_px,
                         reason)
                return "closed"
            if status in _REJECTED_STATES:
                self.store.set_bracket_leg(position.id, which, None, None)
                return "changed"
        return None

    def _place_bracket_leg(self, run_id: int, position, which: str, px: float):
        """Place one resting exit leg — target = LIMIT at px, stop = SL_M triggered at px; SELL for a
        long, BUY for a short. Returns the broker order id, or None if rejected."""
        txn = "SELL" if position.side == "LONG" else "BUY"
        qty = position.quantity
        if which == "target":
            order = self.client.place_order(
                symbol=position.symbol, exchange=position.exchange, transaction_type=txn,
                quantity=qty, order_type="LIMIT", price=px, product="MIS")
            otype = "LIMIT"
        else:                                              # protective stop = market on trigger
            order = self.client.place_order(
                symbol=position.symbol, exchange=position.exchange, transaction_type=txn,
                quantity=qty, order_type="SL_M", trigger_price=px, product="MIS")
            otype = "SL_M"
        if _is_rejected(order):
            self._cycle_errors += 1
            self.store.record_decision(run_id=run_id, symbol=position.symbol, action="SKIP",
                                       reason=f"bracket {which} rejected: {order.get('status')}",
                                       position_id=position.id)
            return None
        self.store.record_order(
            broker_order_id=order["order_id"], symbol=position.symbol, transaction_type=txn,
            quantity=qty, order_type=otype, price=px, status=order.get("status", "PENDING"),
            mode=self.client.mode, position_id=position.id, raw_json=json.dumps(order, default=str))
        return order["order_id"]

    def _ensure_leg(self, run_id: int, position, which: str, desired_px) -> None:
        """Make the `which` leg rest at desired_px: place it if missing, cancel+replace it if its
        price drifted past ARM_REARM_DRIFT_PCT (a trailed level). No-op if already correct."""
        if desired_px is None:
            return
        tpx = _tick(desired_px, position.symbol)
        cur_id = (position.broker_stop_order_id if which == "stop"
                  else position.broker_target_order_id)
        cur_px = (position.broker_stop_price if which == "stop"
                  else position.broker_target_price)
        if cur_id and cur_px is not None and _near(cur_px, tpx, ARM_REARM_DRIFT_PCT):
            return                                         # already resting at the right price
        if cur_id:                                         # drifted -> cancel + replace
            self._cancel_leg(position, which)
        new_id = self._place_bracket_leg(run_id, position, which, tpx)
        if new_id:
            self.store.set_bracket_leg(position.id, which, new_id, tpx)
            log.info("bracket %s leg %s @ %.2f (%d)", which,
                     "re-placed" if cur_id else "placed", tpx, position.quantity)

    def _ensure_bracket(self, run_id: int, position, indicators, cfg) -> None:
        """Rest the protective STOP at the broker (eager modes). ONLY the stop rests: a full-qty
        target LIMIT and a full-qty stop SL_M cannot co-exist on one MIS holding — Groww keeps the
        LIMIT and auto-cancels the SL_M, so a two-leg bracket left the downside naked and re-placed
        a fresh (uncancelled) stop every cycle, flooding the order book (verified 2026-07-29). The
        target is enforced by the soft cycle-level take-profit / exit instead; native OCO stays off
        (USE_BROKER_OCO=False — Groww's smart-order modify/cancel proved unreliable 2026-07-20).
        'on_fill' always keeps the stop; 'armed' places it once price is within arm_exit_band_pct of
        the stop. Skipped in the square-off window (that flattens at market)."""
        if self.client.mode != "live" or _should_square_off(indicators):
            return
        stop_px, _ = self._bracket_levels(position, cfg)
        if position.broker_target_order_id:      # tear down any stale/legacy target leg — stop only
            self._cancel_leg(position, "target")
        if (cfg.exit_mode == "armed" and not position.force_bracket
                and not position.broker_stop_order_id):
            ltp = _ltp(indicators)
            if stop_px is None or not _near(ltp, stop_px, cfg.arm_exit_band_pct):
                return                                     # not near the stop yet -> stay soft
        self._ensure_leg(run_id, position, "stop", stop_px)

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
        max_risk_amount = cfg.total_pool * MAX_RISK_PER_TRADE_PCT / 100.0
        remaining_risk = max_risk_amount - existing_risk
        if remaining_risk <= 0:
            return False                   # combined position already at the risk ceiling — no add
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

    def _maybe_pyramid(self, run_id: int, position, decision, indicators) -> bool:
        """Scale INTO strength: when the engine re-affirms a STRONG same-side entry for
        pyramid_confirm_cycles consecutive cycles, add pyramid_add_pct% of the per-position capital
        at market — up to pyramid_max_adds adds and a hard 1+add_pct*max_adds/100 x base-capital
        ceiling, never over-committing the free pool. The structural stop is NOT widened; once
        added, the position's full-book rises to pyramid_full_pct (see _maybe_take_profit). Returns
        True if it added this cycle. Opt-in (pyramid_enabled); mirror of the underwater _maybe_scale_in
        (only one adds per cycle — pyramid is checked first)."""
        cfg = self.store.get_config()
        if not cfg.pyramid_enabled or _should_square_off(indicators):
            return False
        # Persistence: a STRONG same-side re-affirm advances the counter; anything else resets it.
        strong = (decision.action in ENTRY_ACTIONS
                  and _position_side(decision.action) == position.side
                  and decision.trade_quality is not None
                  and decision.trade_quality >= cfg.pyramid_min_quality
                  and decision.confidence is not None
                  and decision.confidence >= cfg.pyramid_min_confidence)
        if not strong:
            if position.pyramid_signal_count:
                self.store.set_pyramid_signal_count(position.id, 0)
            return False
        confirmed = position.pyramid_signal_count + 1
        # Not yet confirmed, or already at the add-count ceiling -> just track persistence.
        if confirmed < cfg.pyramid_confirm_cycles or position.pyramid_count >= cfg.pyramid_max_adds:
            self.store.set_pyramid_signal_count(position.id, confirmed)
            return False
        ltp = _ltp(indicators)
        if ltp <= 0:
            self.store.set_pyramid_signal_count(position.id, confirmed)
            return False
        # Size the add in MARGIN terms: add_pct% of the per-position capital, capped by (a) the room
        # left under the position's hard ceiling and (b) the free pool. Pool/cap are margin; notional
        # = margin * LEVERAGE.
        add_margin = cfg.capital_per_position * cfg.pyramid_add_pct / 100.0
        ceiling_margin = cfg.capital_per_position * (1 + cfg.pyramid_add_pct * cfg.pyramid_max_adds / 100.0)
        cur_margin = position.quantity * position.entry_price / LEVERAGE
        free_margin = cfg.total_pool - self.store.committed_capital() / LEVERAGE
        add_margin = min(add_margin, ceiling_margin - cur_margin, free_margin)
        add_qty = int(math.floor(add_margin * LEVERAGE / ltp)) if add_margin > 0 else 0
        if add_qty < 1:
            self.store.set_pyramid_signal_count(position.id, confirmed)   # keep persistence; no room
            return False
        txn = _txn(position.side)
        order = self.client.place_order(
            symbol=position.symbol, exchange=position.exchange, transaction_type=txn,
            quantity=add_qty, order_type="MARKET", price=ltp, product="MIS")
        if _is_rejected(order):
            self.store.record_decision(run_id=run_id, symbol=position.symbol, action="SKIP",
                                       reason=f"pyramid add rejected: {order.get('status')}",
                                       position_id=position.id)
            self.store.set_pyramid_signal_count(position.id, confirmed)
            return False
        self.store.record_order(
            broker_order_id=order["order_id"], symbol=position.symbol, transaction_type=txn,
            quantity=add_qty, order_type="MARKET", price=ltp,
            status=order.get("status", "COMPLETE"), mode=self.client.mode,
            position_id=position.id, raw_json=json.dumps(order, default=str))
        new_avg = self.store.add_to_position(position.id, add_qty, ltp)
        self.store.record_pyramid_add(position.id)          # +count, reset the persistence counter
        self.store.record_decision(
            run_id=run_id, symbol=position.symbol, action="ADD",
            reason=f"pyramid +{add_qty} @ {ltp} (strong x{cfg.pyramid_confirm_cycles}; avg "
                   f"{position.entry_price:.2f}->{new_avg:.2f}; add {position.pyramid_count + 1}/"
                   f"{cfg.pyramid_max_adds}; full-book->{cfg.pyramid_full_pct:g}%; "
                   f"stop {position.stop_loss} kept)",
            entry_price=new_avg, stop_loss=position.stop_loss,
            target_price=position.target_price, position_id=position.id)
        log.warning("pyramid %s: +%d @ %.2f, qty %d->%d, avg %.2f->%.2f, add %d/%d (stop %s kept)",
                    position.symbol, add_qty, ltp, position.quantity, position.quantity + add_qty,
                    position.entry_price, new_avg, position.pyramid_count + 1, cfg.pyramid_max_adds,
                    position.stop_loss)
        return True

    def _maybe_trail(self, run_id: int, position, decision, indicators=None) -> None:
        """Re-check an open position's protective levels each cycle and update them where the
        engine's latest read moved. The stop only RATCHETS toward profit (never loosens): up for
        a long, down for a short. The target follows the engine's latest target1. Changed levels
        are pushed to the BROKER's OCO too — a trailed stop that only lives in our DB protects
        nothing between cycles. A no-op when neither level moves; a real change is logged as an
        ADJUSTED operation so the dashboard's activity tally shows stop/target updates.

        An OPPOSING read moves nothing. When the engine flips against the position it emits the
        OTHER side's plan — a short's stop sits above the market — and 'is the new stop higher?'
        reads that as a ratchet toward profit. Writing it forces an exit the conviction gate at
        _manage_one has already refused (BALKRISIND, 2026-07-30: a quality-34 SELL_NOW put the
        stop 21 points above the tape on a long that went on to close +3.20%). An unconvicted flip
        only feeds the reversal counter; the trade rides the stop its last same-side read set.
        """
        if _opposes(decision.action, position.side):
            self.store.record_decision(
                run_id=run_id, symbol=position.symbol, action="HOLD",
                reason=f"ignored {decision.action} levels — opposing read, "
                       f"stop {position.stop_loss} kept",
                position_id=position.id)
            log.info("%s: ignored %s levels (opposing read) — stop %s / target %s kept",
                     position.symbol, decision.action, position.stop_loss, position.target_price)
            return
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
        # Gate K (the skill's exit doctrine, wired 2026-07-30): BEFORE +1R the entry's ORIGINAL
        # structural stop stands — per-cycle re-quotes may not tighten it (that is trailing
        # compression in slow motion). AT/AFTER +1R: lock at least breakeven, then ratchet
        # normally. 1R = |entry - initial_stop|; positions without an initial_stop (legacy rows)
        # fall through to the old behaviour, still bounded by the noise floor below.
        if indicators is not None and position.initial_stop is not None and position.entry_price:
            ltp = _ltp(indicators)
            risk = abs(position.entry_price - position.initial_stop)
            if ltp and risk > 0:
                profit = (ltp - position.entry_price if position.side == "LONG"
                          else position.entry_price - ltp)
                if profit < risk:
                    if new_stop != position.stop_loss:
                        log.info("%s: pre-+1R (%.2f of %.2f risk) — structural stop %s stands, "
                                 "re-quote %s refused (Gate K)", position.symbol, profit, risk,
                                 position.stop_loss, new_stop)
                        new_stop = position.stop_loss
                else:
                    be = position.entry_price
                    if position.side == "LONG":
                        cand = max(new_stop if new_stop is not None else be, be)
                        if position.stop_loss is None or cand > position.stop_loss:
                            new_stop = cand
                    else:
                        cand = min(new_stop if new_stop is not None else be, be)
                        if position.stop_loss is None or cand < position.stop_loss:
                            new_stop = cand
        # Noise floor (post-mortem 2026-07-30, "trailing-stop compression"): same-side/HOLD
        # re-quotes walked stops to 0.07-0.17% of the tape (BALKRISIND 3.84%->0.17% in 36 min ->
        # noise-stopped 30 min before a +3.2% close). A stop inside MIN_STOP_DISTANCE_PCT of the
        # LIVE price is a guaranteed noise stop-out, whichever side quoted it — ratchet AT MOST
        # to the floor, never inside it (and, as always, never loosen).
        if new_stop != position.stop_loss and indicators is not None:
            ltp = _ltp(indicators)
            # a WRONG-side stop is a defect, not a trailing intent — leave it to the clamp below
            if ltp and _stop_is_sane(position.side, new_stop, ltp):
                if position.side == "LONG":
                    floor = ltp * (1 - MIN_STOP_DISTANCE_PCT / 100.0)
                    capped = min(new_stop, floor)
                else:  # SHORT
                    floor = ltp * (1 + MIN_STOP_DISTANCE_PCT / 100.0)
                    capped = max(new_stop, floor)
                if capped != new_stop:
                    tighter = (position.stop_loss is None
                               or (capped > position.stop_loss if position.side == "LONG"
                                   else capped < position.stop_loss))
                    log.info("%s: stop %s is inside the %.1f%% noise floor of price %.2f — "
                             "capped to %.2f", position.symbol, new_stop,
                             MIN_STOP_DISTANCE_PCT, ltp, capped)
                    new_stop = capped if tighter else position.stop_loss
        # Last-line clamp: a stop on the wrong side of the tape is an instruction to exit at once.
        # Refuse it, keep the level we had, and log it as the defect it is.
        if new_stop != position.stop_loss and indicators is not None:
            if not _stop_is_sane(position.side, new_stop, _ltp(indicators)):
                log.error("%s: REFUSED stop %s for a %s at %.2f — a stop on the wrong side of the "
                          "tape would exit immediately; keeping %s", position.symbol, new_stop,
                          position.side, _ltp(indicators), position.stop_loss)
                self.store.record_decision(
                    run_id=run_id, symbol=position.symbol, action="HOLD",
                    reason=f"refused stop {new_stop} (wrong side of price "
                           f"{_ltp(indicators)}) — stop {position.stop_loss} kept",
                    position_id=position.id)
                new_stop = position.stop_loss
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

    def _ensure_protective_stop(self, run_id: int, position, indicators) -> None:
        """Last-resort floor: no OPEN position may sit without a stop. An adopted position starts
        with none by design, and an engine read that returns WAIT supplies none — so a fallback
        is placed adopt_fallback_stop_pct away from entry. _maybe_trail only ratchets toward
        profit, so the engine's real structural stop replaces this the moment it arrives and can
        never widen it. 0 pct disables the floor."""
        if position.stop_loss is not None:
            return
        pct = self.store.get_config().adopt_fallback_stop_pct
        if pct <= 0 or position.entry_price <= 0:
            return
        raw = (position.entry_price * (1 - pct / 100) if position.side == "LONG"
               else position.entry_price * (1 + pct / 100))
        stop = _tick(raw)
        self.store.update_position_levels(position.id, stop_loss=stop,
                                          target_price=position.target_price)
        self.store.record_decision(
            run_id=run_id, symbol=position.symbol, action="ADJUSTED",
            reason=f"protective fallback stop {stop} ({pct}% from entry) — no engine stop yet",
            stop_loss=stop, target_price=position.target_price, position_id=position.id)
        log.warning("fallback stop %.2f set on %s (engine returned no stop)",
                    stop, position.symbol)

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
        # Bracket leg at the CEILING (2026-07-31): target1 is the skill's practical first
        # objective; the FULL-EXIT leg rides at the desk's final capped target so winners exit
        # via the Gate K trail, not a fixed 0.6R leg. Upgraded pre-margin so the R:R gate and
        # the standard target shave both see the real reward.
        decision = replace(decision, target1=_full_exit_target(decision, indicators))
        raw = decision                                                      # pre-margin levels
        decision = _with_level_margins(decision, **_margins_from_cfg(cfg))   # config breathing space
        # P0 guard #2 — re-gate on the ACTUAL geometry (recomputed from entry/stop/target, not the
        # engine's self-reported number). By default (cfg.rr_gate_pre_margin) the gate judges the
        # RAW levels: the execution margins exist to shape the orders and shouldn't veto a good
        # setup by eroding its R:R — a shaved target still books (100 - shave)% of the move, the
        # trade just isn't rejected for it. Flag OFF re-gates on the post-margin geometry, so a
        # shaved target / widened stop must still clear MIN_RISK_REWARD after margins.
        rr_levels = raw if cfg.rr_gate_pre_margin else decision
        actual_rr = _geometric_rr(rr_levels.entry, rr_levels.stop_loss, rr_levels.target1, side)
        # A None R:R means degenerate geometry (no reward — target at/below entry, or stop on the
        # wrong side): that's an INVALID trade, rejected even when the R:R floor is off. The
        # MIN_RISK_REWARD floor itself is skipped when cfg.rr_gate_enabled is False (trade on
        # quality + confidence).
        if actual_rr is None:
            self.store.record_decision(run_id=run_id, symbol=symbol, action=decision.action,
                                       score=decision.trade_quality,
                                       reason="rejected: invalid geometry (no reward)",
                                       raw_json=decision.raw_response)
            return False
        if cfg.rr_gate_enabled and actual_rr < MIN_RISK_REWARD:
            # Practical-T1 ladder (2026-07-31): the engine's target1 is now the FIRST objective
            # (~0.6%, ~62% hit-before-stop), not the trade's full reward — so geometry-to-target1
            # alone under-states the trade. Before rejecting, judge the best achievable reward:
            # the desk risk_model's FINAL capped target (Gate F ceiling). Fails open to the old
            # behaviour when the desk block is absent (v1 indicators).
            fin = (((indicators.get("institutional_desk") or {}).get("risk_model") or {})
                   .get("targets") or [])
            rr_final = (_geometric_rr(rr_levels.entry, rr_levels.stop_loss, fin[-1], side)
                        if fin else None)
            if rr_final is None or rr_final < MIN_RISK_REWARD:
                self.store.record_decision(
                    run_id=run_id, symbol=symbol, action=decision.action,
                    score=decision.trade_quality,
                    reason=f"rejected: "
                           f"{'pre' if cfg.rr_gate_pre_margin else 'post'}-margin "
                           f"R:R {round(actual_rr, 2)} < {MIN_RISK_REWARD}"
                           + (f" (final-target R:R {round(rr_final, 2)} also thin)"
                              if rr_final is not None else ""),
                    raw_json=decision.raw_response)
                return False
        max_risk_amount = cfg.total_pool * MAX_RISK_PER_TRADE_PCT / 100.0
        qty = _size_quantity(decision.entry, decision.stop_loss, cfg.capital_per_position,
                             max_risk_amount, LEVERAGE)
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
                # A live broker-tracked resting order is checked for a FILL before any refresh.
                # _refresh_pending does a cancel+replace, and cancel_order silently succeeds on an
                # order the broker has already FILLED (it is terminal), so a fill that landed
                # between cycles was churned into a fresh resting order and NEVER armed — the
                # position sat "pending" until square-off cancelled it, with no exit ever placed
                # (TIL, 2026-07-29). Resolve the fill first; only a still-resting order is then
                # re-evaluated and its levels refreshed.
                if self.client.mode == "live" and position.entry_order_id:
                    if self._resolve_pending_broker(run_id, position):
                        filled_ids.add(position.id)
                        fills += 1
                        continue
                    position = self.store.get_position(position.id)
                    if position is None or position.status != "PENDING":
                        continue                               # broker rejected it this cycle
                    self._refresh_pending(run_id, position)    # still resting -> refresh levels
                    continue                                   # fills only on a later cycle
                # Synthetic (paper / live breakout stop) — no broker fill to miss: re-evaluate
                # against a fresh read (cancel if the setup is gone), then fill by price.
                if self._refresh_pending(run_id, position):
                    continue                                   # cancelled early — slot freed
                position = self.store.get_position(position.id)   # reload refreshed levels
                filled = self._resolve_pending_synthetic(run_id, position)
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
        cfg = self.store.get_config()
        try:
            fresh = self.engine.decide(position.symbol, indicators, position=None,
                                       book=self._book_context())
            # same ceiling upgrade as the entry path — the resting order's target leg must not
            # be refreshed down to the practical T1 (see _full_exit_target)
            fresh = replace(fresh, target1=_full_exit_target(fresh, indicators))
            decision = _with_level_margins(fresh, **_margins_from_cfg(cfg))
        except Exception:
            log.exception("refresh pending: engine failed for %s — leaving as-is",
                          position.symbol)
            return False
        # Loosened cancellation (2026-07-22 post-mortem): a resting order is only cancelled when
        # the engine actively flags the OPPOSITE side — a genuine invalidation of the thesis. A
        # plain WAIT (the pullback simply hasn't printed yet) or a few-point quality wobble no
        # longer kills the order; that over-cancelling drove the low fill rate on pullback/breakout
        # entries. Post-fill stop protection is the exit path's job, not the resting-order refresh.
        if _opposes(decision.action, position.side):
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
        max_risk_amount = cfg.total_pool * MAX_RISK_PER_TRADE_PCT / 100.0   # cfg fetched above
        qty = _size_quantity(decision.entry, decision.stop_loss, cfg.capital_per_position,
                             max_risk_amount, LEVERAGE)
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

    def _rotation_due(self, run_id: int, cfg) -> bool:
        """Screen-when-full cadence. Counted off the run id so it needs no extra state; rotation
        is not time-critical the way a stop is, so ~every 15 min on the 5-minute grid is plenty."""
        every = max(1, int(getattr(cfg, "rotation_screen_every", 3)))
        return bool(getattr(cfg, "rotation_enabled", False)) and (run_id % every == 0)

    def _rank_holdings(self, cfg) -> list:
        """Open positions ordered weakest-quality first, and maintain each one's weakest_streak.

        Ranking is on the engine's trade_quality ONLY. Never on P&L: BALKRISIND was the most
        underwater holding on 2026-07-30 and closed +3.20%, so 'losing' is not 'bad'. A position
        with no quality read yet is not rankable and is never evicted.
        """
        held = [p for p in self.store.get_open_positions() if p.last_quality is not None]
        if not held:
            return []
        ranked = sorted(held, key=lambda p: p.last_quality)
        weakest_id = ranked[0].id
        for p in held:
            streak = (p.weakest_streak + 1) if p.id == weakest_id else 0
            if streak != p.weakest_streak:
                self.store.set_weakest_streak(p.id, streak)
        # Re-read so callers see the streak we just wrote, not the pre-increment snapshot the
        # rows were loaded with — otherwise the eligibility check is always one cycle behind.
        return [self.store.get_position(p.id) for p in ranked]

    def _maybe_rotate(self, run_id: int, cfg) -> tuple[int, int]:
        """Replace the weakest holding with a clearly better candidate. Returns (screened, entries).

        Four independent brakes must ALL release — persisted weakness, a quality margin, a minimum
        hold, and the candidate independently clearing the normal entry gate. Every rejection is
        logged with the brake that stopped it, so 'why didn't it rotate?' is always answerable.
        """
        if self._daily_loss_breached(cfg):
            return 0, 0
        ranked = self._rank_holdings(cfg)
        if not ranked:
            log.info("rotation: no holding has a quality read yet — nothing rankable")
            return 0, 0
        weakest = ranked[0]
        if weakest.weakest_streak < cfg.rotation_confirm_cycles:
            log.info("rotation: %s weakest for %d/%d cycles — not yet persistent",
                     weakest.symbol, weakest.weakest_streak, cfg.rotation_confirm_cycles)
            return 0, 0
        held_min = _held_minutes(weakest)
        if held_min is not None and held_min < cfg.rotation_min_hold_minutes:
            log.info("rotation: %s held %dm < %dm minimum — too young to displace",
                     weakest.symbol, held_min, cfg.rotation_min_hold_minutes)
            return 0, 0
        if weakest.broker_stop_order_id:
            # _cancel_leg surfaces a failed cancel loudly rather than returning a flag; if the leg
            # will not die we must not rotate, or the old stop outlives the position it guarded.
            try:
                self._cancel_leg(weakest, "stop")
            except Exception:
                log.exception("rotation: %s broker stop would not cancel — leaving it alone",
                              weakest.symbol)
                return 0, 0

        held = ({p.symbol for p in self.store.get_open_positions()}
                | {p.symbol for p in self.store.get_pending_positions()}
                | self._external_order_symbols)
        best, best_dec = None, None
        for cand in self._gather_candidates(top=SLOT_HEADROOM):
            symbol = cand["symbol"] if isinstance(cand, dict) else cand
            if symbol in held:
                continue
            try:
                dec = self.engine.decide(symbol, self.get_indicators(symbol), position=None,
                                         book=self._book_context())
            except DecisionEngineError:
                log.exception("rotation: candidate %s failed to decide", symbol)
                continue
            if not _passes_entry_gate(dec) or dec.trade_quality is None:
                continue
            if best_dec is None or dec.trade_quality > best_dec.trade_quality:
                best, best_dec = symbol, dec
        if best_dec is None:
            log.info("rotation: no candidate cleared the entry gate")
            return 0, 0
        needed = (weakest.last_quality or 0) + cfg.rotation_margin
        if best_dec.trade_quality < needed:
            log.info("rotation: best candidate %s q%.0f < %.0f needed (%s q%.0f + %.0f margin)",
                     best, best_dec.trade_quality, needed, weakest.symbol,
                     weakest.last_quality or 0, cfg.rotation_margin)
            return 0, 0

        # Close FIRST — the slot and the margin must actually be free before we buy. If the exit
        # fails, abandon the rotation and leave the book exactly as it was.
        reason = f"rotated out: quality {weakest.last_quality:.0f} vs {best} {best_dec.trade_quality:.0f}"
        try:
            self._close_position(weakest, _ltp(self.get_indicators(weakest.symbol)), "ROTATED")
        except Exception:
            log.exception("rotation: exit of %s FAILED — not entering %s", weakest.symbol, best)
            return 0, 0
        self.store.record_decision(run_id=run_id, symbol=weakest.symbol, action="EXIT",
                                   reason=reason, position_id=weakest.id)
        log.warning("ROTATION %s -> %s (%s)", weakest.symbol, best, reason)
        entered = self._place_entry(run_id, best, best_dec, self.get_indicators(best),
                                    self.client.mode)
        return 1, (1 if entered else 0)

    def _screen_and_enter(self, run_id: int) -> tuple[int, int]:
        cfg = self.store.get_config()
        committed = self.store.count_committed_positions()   # OPEN + resting PENDING
        free_slots = cfg.max_open_positions - committed
        # Free MARGIN: pool minus committed NOTIONAL / LEVERAGE (see _place_entry accounting).
        free_capital = cfg.total_pool - self.store.committed_capital() / LEVERAGE
        if free_slots <= 0 or free_capital < cfg.capital_per_position:
            # Book full (open + resting orders fill every slot / the pool): exits and pending fills
            # were already handled this cycle. Normally we do NOT screen — it would waste an
            # expensive market scan + LLM calls on trades we can't take. With rotation on we screen
            # every Nth cycle anyway, to see whether something is worth displacing a holding for.
            if self._rotation_due(run_id, cfg):
                return self._maybe_rotate(run_id, cfg)
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
                decision = self.engine.decide(symbol, indicators, position=None,
                                              book=self._book_context())
            except Exception as e:
                self.store.record_decision(run_id=run_id, symbol=symbol, action="SKIP",
                                           reason=f"decision error: {e}")
                continue
            if not _passes_entry_gate(decision, cfg.rr_gate_enabled):
                self.store.record_decision(run_id=run_id, symbol=symbol, action=decision.action,
                                           score=decision.trade_quality, reason="below gate",
                                           raw_json=decision.raw_response)
                continue
            try:
                placed = self._place_entry(run_id, symbol, decision, indicators, cfg.mode)
            except Exception as e:
                # A broker error placing ONE entry must not abort the rest of the screen. Keep the
                # engine's raw output so the run's Claude output stays visible even when the order
                # failed (e.g. a gateway 502) — the decision was real, only the placement failed.
                log.exception("entry placement failed for %s", symbol)
                self.store.record_decision(run_id=run_id, symbol=symbol, action="SKIP",
                                           reason=f"entry error: {e}", raw_json=decision.raw_response)
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
            if not _passes_entry_gate(decision, cfg.rr_gate_enabled):
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
                                           reason=f"entry error: {e}", raw_json=decision.raw_response)
                continue
            if placed:
                entries += 1
                held.add(symbol)
        return len(results), entries
