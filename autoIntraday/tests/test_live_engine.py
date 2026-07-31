"""Live Intraday engine — every rule pinned to a fixture series.

The engine is pure, so each gate can be isolated: build a series that satisfies everything, then
break one condition at a time and assert the specific refusal.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from live_engine import (Candle, LiveConfig, LivePosition, atr, clamp_stop, decide, ema,
                         opening_range, rvol, to_min, vwap)

CFG = LiveConfig()


def _series(n=40, start="09:15", price=100.0, vol=1000.0, step=0.0):
    """Flat-ish 1m series from `start`, `n` candles, drifting by `step` per candle."""
    out, m = [], to_min(start)
    for i in range(n):
        p = price + step * i
        out.append(Candle(t=f"{m // 60:02d}:{m % 60:02d}", o=p, h=p + 0.2, l=p - 0.2, c=p, v=vol))
        m += 1
    return out


# ---- indicators -----------------------------------------------------------------------------
def test_to_min_and_vwap_and_ema_and_atr():
    assert to_min("09:35") == 575 and to_min("15:15") == 915
    flat = _series(20, price=100.0)
    assert abs(vwap(flat) - 100.0) < 0.01           # typical price of a flat series
    assert vwap([Candle("09:15", 1, 1, 1, 1, 0)]) is None   # no volume -> undefined
    assert ema([1, 2, 3], 9) is None                        # not enough points
    assert ema([5.0] * 20, 9) == 5.0                        # flat series -> the level itself
    assert atr(_series(5), 14) is None                      # not enough candles
    a = atr(_series(30), 14)
    assert a is not None and a > 0


def test_opening_range_only_after_the_window_closes():
    assert opening_range(_series(5, start="09:15")) is None      # still inside 09:15-09:30
    full = _series(20, start="09:15")                            # runs past 09:30
    orng = opening_range(full)
    assert orng is not None and orng[0] > orng[1]


def test_rvol_compares_last_candle_to_the_mean():
    s = _series(10, vol=100.0)
    s[-1] = Candle(s[-1].t, s[-1].o, s[-1].h, s[-1].l, s[-1].c, 300.0)
    assert abs(rvol(s, 20) - 3.0) < 0.01


# ---- the stop floor: the 2026-07-30 lesson --------------------------------------------------
def test_clamp_stop_enforces_the_minimum_distance():
    # a stop 0.1% away is pulled out to the 0.35% floor
    assert clamp_stop(100.0, 99.9, 100.0, 0.35) == 99.65
    # a stop already further away is left alone
    assert clamp_stop(100.0, 98.0, 100.0, 0.35) == 98.0
    # a stop at or above price is pushed below it
    assert clamp_stop(100.0, 101.0, 100.0, 0.35) == 99.65


# ---- entry gates ----------------------------------------------------------------------------
def _breakout_series():
    """30 flat candles then a high-volume candle breaking the opening-range high above EMA9."""
    s = _series(30, start="09:15", price=100.0, vol=1000.0)
    s.append(Candle("09:45", 100.0, 101.5, 100.0, 101.2, 5000.0))
    return s


def test_entry_fires_on_an_or_breakout_with_volume():
    sig = decide(_breakout_series(), None, CFG)
    assert sig.action == "ENTER"
    assert "OR breakout" in sig.reason
    assert sig.entry == 101.2 and sig.stop < sig.entry < sig.target
    assert sig.rr >= CFG.min_rr


def test_entry_blocked_when_rvol_is_below_the_floor():
    s = _breakout_series()
    s[-1] = Candle("09:45", 100.0, 101.5, 100.0, 101.2, 900.0)     # weak volume
    sig = decide(s, None, CFG)
    assert sig.action == "NONE" and "RVOL" in sig.reason


def test_entry_blocked_before_the_opening_range_forms():
    s = _series(8, start="09:15")                                   # still pre-09:30
    assert decide(s, None, CFG).action == "NONE"
    assert "opening range" in decide(s, None, CFG).reason


def test_entry_blocked_without_a_trigger():
    sig = decide(_series(35, start="09:15"), None, CFG)             # flat, no cross
    assert sig.action == "NONE" and "no trigger" in sig.reason


def test_entry_blocked_when_rr_is_below_the_floor():
    # rr_target below min_rr can never clear the gate, whatever the tape does
    sig = decide(_breakout_series(), None, LiveConfig(rr_target=1.0, min_rr=1.5))
    assert sig.action == "NONE" and "R:R" in sig.reason


def test_entry_blocked_after_the_no_new_entry_time():
    s = _breakout_series()
    s[-1] = Candle("14:35", 100.0, 101.5, 100.0, 101.2, 5000.0)
    sig = decide(s, None, CFG)
    assert sig.action == "NONE" and "no-new-entry" in sig.reason


def test_entry_stop_never_sits_inside_the_minimum_distance():
    """Even with a tiny ATR the stop is pushed out to the floor — no 0.1% stops."""
    s = _series(30, start="09:15", price=100.0, vol=1000.0)          # near-zero range -> tiny ATR
    s.append(Candle("09:45", 100.0, 100.3, 100.0, 100.25, 5000.0))
    sig = decide(s, None, CFG)
    if sig.action == "ENTER":
        assert sig.stop <= sig.entry * (1 - CFG.min_stop_pct / 100.0) + 1e-9


# ---- exit paths -----------------------------------------------------------------------------
def _held(entry=100.0, stop=99.0, target=102.0, opened="10:00"):
    return LivePosition(entry_price=entry, stop=stop, target=target, quantity=10, opened_at=opened)


def test_exit_on_stop():
    s = _series(30, start="10:00", price=98.5)
    sig = decide(s, _held(), CFG)
    assert sig.action == "EXIT" and "stop" in sig.reason


def test_exit_on_target():
    s = _series(30, start="10:00", price=102.5)
    sig = decide(s, _held(), CFG)
    assert sig.action == "EXIT" and "target" in sig.reason


def test_exit_after_consecutive_closes_below_vwap():
    s = _series(30, start="10:00", price=101.0)                      # VWAP ~101
    s.append(Candle("10:30", 100.0, 100.1, 99.8, 99.9, 1000.0))
    s.append(Candle("10:31", 99.9, 100.0, 99.6, 99.7, 1000.0))
    sig = decide(s, _held(stop=95.0, target=110.0), CFG)
    assert sig.action == "EXIT" and "below VWAP" in sig.reason


def test_exit_on_time_stop_when_still_under_1r():
    s = _series(60, start="10:00", price=100.2)                      # entry 100, 1R = 101
    sig = decide(s, _held(stop=99.0, target=110.0, opened="10:00"), CFG)
    assert sig.action == "EXIT" and "time stop" in sig.reason


def test_no_time_stop_once_past_1r():
    s = _series(60, start="10:00", price=101.5)                      # beyond 1R
    sig = decide(s, _held(stop=99.0, target=110.0, opened="10:00"), CFG)
    assert sig.action == "HOLD"


def test_squareoff_forces_an_exit_regardless():
    s = _series(3, start="15:16", price=100.5)                       # inside the plan, but late
    sig = decide(s, _held(stop=95.0, target=110.0), CFG)
    assert sig.action == "EXIT" and "square-off" in sig.reason


def test_never_enters_while_a_position_is_open():
    """Exits are evaluated first; a held position can only ever yield EXIT or HOLD."""
    sig = decide(_breakout_series(), _held(stop=95.0, target=110.0, opened="09:40"), CFG)
    assert sig.action in ("HOLD", "EXIT")


def test_empty_candles_is_handled():
    assert decide([], None, CFG).action == "NONE"


def test_decide_is_deterministic():
    s = _breakout_series()
    a, b = decide(s, None, CFG), decide(s, None, CFG)
    assert (a.action, a.entry, a.stop, a.target, a.rr) == (b.action, b.entry, b.stop, b.target, b.rr)
