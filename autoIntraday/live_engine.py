"""Live Intraday decision rules — a PURE function of the candles.

No LLM, no network, no clock, no database. `decide()` takes candles + the open position + config
and returns a Signal; the same inputs always produce the same output. That is the whole point of
this path: unlike the skill engines it can be unit-tested and backtested honestly.

"Now" is always the close of the last candle, never the wall clock, so a replay over historical
bars behaves exactly as the live loop did.

See docs/superpowers/specs/2026-07-31-live-intraday-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

SESSION_OPEN = "09:15"
OPENING_RANGE_END = "09:30"


def to_min(hhmm: str) -> int:
    """'09:35' -> 575 (minutes since midnight). The engine's only notion of time."""
    h, m = hhmm.split(":")[:2]
    return int(h) * 60 + int(m)


@dataclass(frozen=True)
class Candle:
    t: str            # IST bucket START, "HH:MM"
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0


@dataclass(frozen=True)
class LivePosition:
    entry_price: float
    stop: float
    target: float
    quantity: int
    opened_at: str    # "HH:MM"


@dataclass(frozen=True)
class LiveConfig:
    min_rr: float = 1.5
    atr_mult: float = 1.5
    rr_target: float = 2.0
    min_stop_pct: float = 0.35        # a stop may never sit closer than this % to price
    rvol_floor: float = 1.5
    vwap_exit_candles: int = 2
    max_hold_minutes: int = 45
    no_new_entry_after: str = "14:30"
    squareoff_at: str = "15:15"
    atr_period: int = 14
    rvol_lookback: int = 20


@dataclass(frozen=True)
class Signal:
    action: str                        # ENTER | EXIT | HOLD | NONE
    reason: str
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    rr: Optional[float] = None
    indicators: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------
# Indicators — all computed from our own candles, none fetched
# ---------------------------------------------------------------------------------------------
def vwap(candles: Sequence[Candle]) -> Optional[float]:
    """Session-anchored VWAP over typical price. None when no candle carries volume."""
    pv = vol = 0.0
    for c in candles:
        if c.v:
            pv += ((c.h + c.l + c.c) / 3.0) * c.v
            vol += c.v
    return pv / vol if vol else None


def ema(values: Sequence[float], span: int) -> Optional[float]:
    if len(values) < span:
        return None
    k = 2.0 / (span + 1)
    out = sum(values[:span]) / span
    for v in values[span:]:
        out = v * k + out * (1 - k)
    return out


def atr(candles: Sequence[Candle], period: int = 14) -> Optional[float]:
    """Wilder-style average true range. None until `period` + 1 candles exist."""
    if len(candles) < period + 1:
        return None
    trs = []
    for prev, cur in zip(candles, candles[1:]):
        trs.append(max(cur.h - cur.l, abs(cur.h - prev.c), abs(cur.l - prev.c)))
    out = sum(trs[:period]) / period
    for tr in trs[period:]:
        out = (out * (period - 1) + tr) / period
    return out


def opening_range(candles: Sequence[Candle]) -> Optional[tuple[float, float]]:
    """(high, low) of 09:15-09:30. None until the window has closed."""
    seg = [c for c in candles if to_min(SESSION_OPEN) <= to_min(c.t) < to_min(OPENING_RANGE_END)]
    if not seg or not _or_formed(candles):
        return None
    return max(c.h for c in seg), min(c.l for c in seg)


def _or_formed(candles: Sequence[Candle]) -> bool:
    return bool(candles) and to_min(candles[-1].t) >= to_min(OPENING_RANGE_END)


def rvol(candles: Sequence[Candle], lookback: int = 20) -> Optional[float]:
    """Last candle's volume against the mean of the preceding `lookback`. None without volume."""
    if len(candles) < 2:
        return None
    prior = [c.v for c in candles[-(lookback + 1):-1] if c.v]
    if not prior:
        return None
    mean = sum(prior) / len(prior)
    return candles[-1].v / mean if mean else None


# ---------------------------------------------------------------------------------------------
# Stop helper — carries the 2026-07-30 lesson
# ---------------------------------------------------------------------------------------------
def clamp_stop(entry: float, stop: float, price: float, min_stop_pct: float) -> float:
    """A LONG's stop must sit strictly below price by at least `min_stop_pct`.

    On 2026-07-30 trailed stops reached 0.07-0.17% of entry and were taken out by noise that was
    not a real invalidation. This is a floor the engine cannot cross, whatever the ATR says.
    """
    ceiling = price * (1 - min_stop_pct / 100.0)
    return min(stop, ceiling)


# ---------------------------------------------------------------------------------------------
# The decision
# ---------------------------------------------------------------------------------------------
def decide(candles: Sequence[Candle], position: Optional[LivePosition],
           cfg: LiveConfig = LiveConfig()) -> Signal:
    """Long-only. Exits are evaluated before entries; a position is never opened while one is
    already held. 'Now' is the last candle's timestamp."""
    if not candles:
        return Signal("NONE", "no candles yet")

    last = candles[-1]
    now = to_min(last.t)
    price = last.c
    ind = {
        "vwap": vwap(candles),
        "ema9": ema([c.c for c in candles], 9),
        "ema20": ema([c.c for c in candles], 20),
        "atr": atr(candles, cfg.atr_period),
        "rvol": rvol(candles, cfg.rvol_lookback),
        "opening_range": opening_range(candles),
        "price": price,
        "at": last.t,
    }

    if position is not None:
        return _exit_decision(candles, position, cfg, ind, now, price)

    # --- square-off window: never open, only close -------------------------------------------
    if now >= to_min(cfg.squareoff_at):
        return Signal("NONE", f"past square-off {cfg.squareoff_at}", indicators=ind)
    if now >= to_min(cfg.no_new_entry_after):
        return Signal("NONE", f"past no-new-entry {cfg.no_new_entry_after}", indicators=ind)
    return _entry_decision(candles, cfg, ind, price)


def _exit_decision(candles, position, cfg, ind, now, price) -> Signal:
    if now >= to_min(cfg.squareoff_at):
        return Signal("EXIT", f"square-off {cfg.squareoff_at}", indicators=ind)
    if price <= position.stop:
        return Signal("EXIT", f"stop {position.stop:g} hit at {price:g}", indicators=ind)
    if price >= position.target:
        return Signal("EXIT", f"target {position.target:g} hit at {price:g}", indicators=ind)

    vw = ind["vwap"]
    if vw is not None and cfg.vwap_exit_candles > 0:
        tail = candles[-cfg.vwap_exit_candles:]
        if len(tail) == cfg.vwap_exit_candles and all(c.c < vw for c in tail):
            return Signal("EXIT", f"{cfg.vwap_exit_candles} closes below VWAP {vw:.2f}",
                          indicators=ind)

    held = now - to_min(position.opened_at)
    risk = position.entry_price - position.stop
    if held >= cfg.max_hold_minutes and risk > 0 and price < position.entry_price + risk:
        return Signal("EXIT", f"time stop: {held}m held, still under 1R", indicators=ind)

    return Signal("HOLD", f"holding, price {price:g}", indicators=ind)


def _entry_decision(candles, cfg, ind, price) -> Signal:
    vw, e9, a, rv, orng = ind["vwap"], ind["ema9"], ind["atr"], ind["rvol"], ind["opening_range"]

    if orng is None:
        return Signal("NONE", "opening range not formed", indicators=ind)
    if a is None or a <= 0:
        return Signal("NONE", "ATR unavailable", indicators=ind)
    if vw is None:
        return Signal("NONE", "VWAP unavailable (no volume)", indicators=ind)

    or_high, or_low = orng
    prev = candles[-2] if len(candles) >= 2 else None

    # Trigger: an OR-high breakout, or a VWAP reclaim from below. Both need the PREVIOUS candle
    # to have been on the other side, so we act on the crossing rather than on a standing state.
    breakout = prev is not None and prev.c <= or_high < price
    reclaim = prev is not None and prev.c < vw <= price
    if not (breakout or reclaim):
        return Signal("NONE", "no trigger: no OR breakout and no VWAP reclaim", indicators=ind)
    trigger = "OR breakout" if breakout else "VWAP reclaim"

    if rv is None or rv < cfg.rvol_floor:
        return Signal("NONE", f"{trigger} but RVOL {rv if rv is None else round(rv, 2)} "
                              f"< {cfg.rvol_floor}", indicators=ind)
    if e9 is None or price <= e9:
        return Signal("NONE", f"{trigger} but price not above EMA9", indicators=ind)

    stop = clamp_stop(price, min(price - cfg.atr_mult * a, or_low), price, cfg.min_stop_pct)
    risk = price - stop
    if risk <= 0:
        return Signal("NONE", "non-positive risk after stop clamp", indicators=ind)
    target = price + cfg.rr_target * risk
    rr = (target - price) / risk
    if rr < cfg.min_rr:
        return Signal("NONE", f"R:R {rr:.2f} < {cfg.min_rr}", indicators=ind)

    return Signal("ENTER", f"{trigger} with RVOL {rv:.2f}", entry=price, stop=round(stop, 2),
                  target=round(target, 2), rr=round(rr, 2), indicators=ind)
