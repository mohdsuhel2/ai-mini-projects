"""Live Intraday persistence — config round-trip, the single-position invariant, P&L."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from live_store import DEFAULTS, LiveStore


def _s():
    return LiveStore(":memory:")


def test_defaults_are_seeded_with_their_types_intact():
    cfg = _s().get_config()
    assert cfg["armed"] == 0
    assert cfg["mode"] == "paper"                     # paper until explicitly switched
    assert isinstance(cfg["min_rr"], float) and cfg["min_rr"] == 1.5
    assert isinstance(cfg["poll_seconds"], int) and cfg["poll_seconds"] == 2
    assert cfg["min_stop_pct"] == 0.35                # the 2026-07-30 floor
    assert set(cfg) == set(DEFAULTS)


def test_set_config_round_trips_and_keeps_types():
    s = _s()
    s.set_config(min_rr=2.5, capital_per_trade=50000, armed=1, mode="live")
    cfg = s.get_config()
    assert cfg["min_rr"] == 2.5 and isinstance(cfg["min_rr"], float)
    assert cfg["capital_per_trade"] == 50000.0
    assert s.is_armed() is True and cfg["mode"] == "live"


def test_set_config_rejects_an_unknown_key():
    with pytest.raises(KeyError):
        _s().set_config(not_a_real_key=1)


def test_disarm_clears_armed_and_records_the_reason():
    s = _s()
    s.set_config(armed=1)
    s.disarm("daily loss cap breached")
    assert s.is_armed() is False
    assert s.get_state()["disarmed_reason"] == "daily loss cap breached"


def test_state_update_rejects_unknown_fields():
    with pytest.raises(KeyError):
        _s().update_state(nonsense="x")


def test_state_round_trips():
    s = _s()
    s.update_state(symbol="MOIL", signal_action="ENTER", signal_reason="OR breakout")
    st = s.get_state()
    assert st["symbol"] == "MOIL" and st["signal_action"] == "ENTER"


def test_single_position_invariant_is_enforced_by_the_store():
    """The one-position rule lives here, not in the caller — so it holds even if a caller forgets."""
    s = _s()
    s.open_trade("2026-07-31", "MOIL", 100, 298.0, 293.0, 308.0, "paper")
    with pytest.raises(ValueError, match="already open"):
        s.open_trade("2026-07-31", "SYRMA", 10, 1400.0, 1380.0, 1440.0, "paper")


def test_close_trade_computes_pnl_and_frees_the_slot():
    s = _s()
    tid = s.open_trade("2026-07-31", "MOIL", 100, 298.0, 293.0, 308.0, "paper")
    pnl = s.close_trade(tid, 303.0, "TARGET")
    assert pnl == pytest.approx(500.0)
    assert s.get_open_trade() is None
    s.open_trade("2026-07-31", "SYRMA", 10, 1400.0, 1380.0, 1440.0, "paper")   # slot free again


def test_realized_pnl_sums_only_closed_trades():
    s = _s()
    t1 = s.open_trade("2026-07-31", "AAA", 10, 100.0, 98.0, 104.0, "paper")
    s.close_trade(t1, 104.0, "TARGET")                     # +40
    t2 = s.open_trade("2026-07-31", "BBB", 10, 100.0, 98.0, 104.0, "paper")
    s.close_trade(t2, 98.0, "STOP")                        # -20
    s.open_trade("2026-07-31", "CCC", 10, 100.0, 98.0, 104.0, "paper")   # still open
    assert s.realized_pnl("2026-07-31") == pytest.approx(20.0)
    assert s.realized_pnl("2026-07-30") == 0.0
    assert len(s.trades_for("2026-07-31")) == 3


def test_close_trade_rejects_an_unknown_id():
    with pytest.raises(ValueError):
        _s().close_trade(999, 100.0, "STOP")
