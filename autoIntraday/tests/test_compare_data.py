"""Phase 3: strategy-comparison analytics + config toggle persistence."""
import math

import compare_data as cd
from store import Store


def _closed(store, sid, pnl, symbol="X", opened="2026-07-24T04:00:00+00:00",
            closed="2026-07-24T05:00:00+00:00"):
    pid = store.open_position(symbol=symbol, exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, mode="paper", strategy_id=sid)
    # backdate opened/closed so time-bucketing in the comparison views is deterministic
    store._conn.execute("UPDATE positions SET opened_at=? WHERE id=?", (opened, pid))
    store._conn.commit()
    store.close_position(pid, exit_price=100.0 + pnl / 10, exit_reason="TARGET", realized_pnl=pnl)
    store._conn.execute("UPDATE positions SET closed_at=? WHERE id=?", (closed, pid))
    store._conn.commit()
    return pid


def test_streaks_and_drawdown():
    assert cd._streaks([1, 1, -1, 1, 1, 1, -1]) == (3, 1)
    assert cd._streaks([]) == (0, 0)
    # equity 100,90,140,120 -> peak 140, trough 120 -> max drawdown 20
    assert cd._max_drawdown([100, 90, 140, 120]) == 20.0
    assert cd.equity_curve([10, -5, 20]) == [10, 5, 25]


def test_strategy_performance_metrics():
    s = Store(":memory:")
    for pnl in (100.0, -40.0, 60.0, -20.0):      # 2 wins (160), 2 losses (-60)
        _closed(s, "intraday-v1", pnl)
    p = cd.strategy_performance(s, "intraday-v1", total_pool=100000.0, today_iso="2000-01-01")
    assert p["total_trades"] == 4 and p["wins"] == 2 and p["losses"] == 2
    assert p["win_pct"] == 50.0
    assert p["net_profit"] == 100.0
    assert p["avg_profit"] == 80.0 and p["avg_loss"] == -30.0
    assert p["profit_factor"] == 160.0 / 60.0
    assert p["risk_reward"] == 80.0 / 30.0
    assert p["roi_pct"] == 0.1
    # a strategy with no losses -> infinite profit factor, not a crash
    _closed(s, "intraday-v2", 50.0)
    assert cd.strategy_performance(s, "intraday-v2", 100000.0, "2000-01-01")["profit_factor"] == math.inf


def test_performance_is_strategy_isolated():
    s = Store(":memory:")
    _closed(s, "intraday-v1", 100.0)
    _closed(s, "intraday-v2", -100.0)
    assert cd.strategy_performance(s, "intraday-v1", 100000.0, "2000-01-01")["net_profit"] == 100.0
    assert cd.strategy_performance(s, "intraday-v2", 100000.0, "2000-01-01")["net_profit"] == -100.0


def test_leaderboard_picks_best_per_metric():
    perfs = [
        {"strategy_id": "intraday-v1", "net_profit": 500, "win_pct": 70, "max_drawdown": 300,
         "profit_factor": 1.8, "risk_reward": 2.5},
        {"strategy_id": "intraday-v2", "net_profit": 900, "win_pct": 55, "max_drawdown": 120,
         "profit_factor": 2.4, "risk_reward": 1.9},
    ]
    lb = cd.leaderboard(perfs)
    assert lb["Highest net profit"] == "intraday-v2"
    assert lb["Highest win rate"] == "intraday-v1"
    assert lb["Lowest drawdown"] == "intraday-v2"
    assert lb["Best risk:reward"] == "intraday-v1"


def test_decision_comparison_aligns_by_time_and_symbol():
    s = Store(":memory:")
    r1 = s.start_run("paper", strategy_id="intraday-v1")
    r2 = s.start_run("paper", strategy_id="intraday-v2")
    s.record_decision(run_id=r1, symbol="RELIANCE", action="BUY_NOW", strategy_id="intraday-v1")
    s.record_decision(run_id=r2, symbol="RELIANCE", action="WAIT", strategy_id="intraday-v2")
    rows = cd.decision_comparison(s, ["intraday-v1", "intraday-v2"])
    rel = [r for r in rows if r["symbol"] == "RELIANCE"][0]
    assert rel["intraday-v1"] == "BUY_NOW" and rel["intraday-v2"] == "WAIT"


def test_drawdown_series():
    # equity 10, 5, 25, 20 -> peaks 10,10,25,25 -> drawdown 0,-5,0,-5
    assert cd.drawdown_series([10, -5, 20, -5]) == [0, -5, 0, -5]


def test_daily_pnl_groups_by_ist_day_per_strategy():
    s = Store(":memory:")
    # two trades same IST day for v1, one for v2 on a later day
    _closed(s, "intraday-v1", 100.0, closed="2026-07-24T05:00:00+00:00")
    _closed(s, "intraday-v1", -30.0, closed="2026-07-24T06:00:00+00:00")
    _closed(s, "intraday-v2", 50.0, closed="2026-07-25T05:00:00+00:00")
    rows = cd.daily_pnl(s, ["intraday-v1", "intraday-v2"])
    d = {r["date"]: r for r in rows}
    assert d["2026-07-24"]["intraday-v1"] == 70.0 and d["2026-07-24"]["intraday-v2"] == 0.0
    assert d["2026-07-25"]["intraday-v2"] == 50.0


def test_config_toggles_persist_and_parse():
    s = Store(":memory:")
    c = s.update_config(compare_enabled=True, paper_strategy="intraday-v2",
                        compare_strategies=["intraday-v1", "intraday-v2"])
    assert c.compare_enabled is True
    assert c.paper_strategy == "intraday-v2"
    assert c.compare_strategies == ["intraday-v1", "intraday-v2"]      # parsed back to a list
    # round-trips through a reopened store row
    assert s.get_config().compare_strategies == ["intraday-v1", "intraday-v2"]


def test_profit_book_config_defaults_and_persist():
    s = Store(":memory:")
    c = s.get_config()
    assert c.profit_book_enabled is True                 # on by default
    assert c.profit_book_partial_pct == 7.0 and c.profit_book_full_pct == 15.0
    c = s.update_config(profit_book_enabled=False, profit_book_partial_pct=5.0,
                        profit_book_full_pct=12.0)
    assert c.profit_book_enabled is False
    assert c.profit_book_partial_pct == 5.0 and c.profit_book_full_pct == 12.0


def test_execution_margin_config_defaults_and_persist():
    s = Store(":memory:")
    c = s.get_config()
    assert c.entry_tolerance_pct == 0.25 and c.stop_tolerance_pct == 0.35
    assert c.target_shave_pct == 10.0
    c = s.update_config(entry_tolerance_pct=0.5, stop_tolerance_pct=1.0, target_shave_pct=20.0)
    assert c.entry_tolerance_pct == 0.5 and c.stop_tolerance_pct == 1.0 and c.target_shave_pct == 20.0


def test_arm_exit_config_defaults_and_persist():
    s = Store(":memory:")
    c = s.get_config()
    assert c.arm_exit_enabled is False            # OFF by default (live-only, opt-in)
    assert c.arm_exit_band_pct == 1.0
    c = s.update_config(arm_exit_enabled=True, arm_exit_band_pct=0.5)
    assert c.arm_exit_enabled is True and c.arm_exit_band_pct == 0.5
    assert s.get_config().arm_exit_enabled is True   # round-trips through a reopened row


def test_exit_mode_config_default_and_persist():
    s = Store(":memory:")
    assert s.get_config().exit_mode == "db_only"     # soft levels by default (= today's behavior)
    for mode in ("armed", "on_fill", "db_only"):
        assert s.update_config(exit_mode=mode).exit_mode == mode
        assert s.get_config().exit_mode == mode      # persists


def test_primer_time_config_default_and_persist():
    s = Store(":memory:")
    assert s.get_config().primer_time == "07:30"           # default
    assert s.update_config(primer_time="08:15").primer_time == "08:15"
    assert s.get_config().primer_time == "08:15"            # persists
