"""Phase 1: per-strategy isolation in the store + ScopedStore injection + backward compat."""
from store import DEFAULT_STRATEGY_ID, ScopedStore, Store


def _open(store, sid, qty=10, entry=100.0, status="OPEN"):
    return store.open_position(symbol="X", exchange="NSE", side="LONG", quantity=qty,
                               entry_price=entry, mode="paper", status=status, strategy_id=sid)


def test_positions_are_isolated_by_strategy_id():
    s = Store(":memory:")
    _open(s, "intraday-v1", qty=10, entry=100.0)              # v1: 1000 notional
    _open(s, "intraday-v2", qty=5, entry=200.0)               # v2: 1000 notional
    _open(s, "intraday-v2", qty=3, entry=100.0)               # v2: +300
    assert s.count_open_positions("intraday-v1") == 1
    assert s.count_open_positions("intraday-v2") == 2
    assert s.deployed_capital("intraday-v1") == 1000.0
    assert s.deployed_capital("intraday-v2") == 1300.0
    assert [p.symbol for p in s.get_open_positions("intraday-v1")] == ["X"]
    assert len(s.get_open_positions("intraday-v2")) == 2


def test_committed_capital_and_counts_scoped():
    s = Store(":memory:")
    _open(s, "intraday-v1", qty=10, entry=100.0, status="OPEN")
    _open(s, "intraday-v1", qty=10, entry=100.0, status="PENDING")
    _open(s, "intraday-v2", qty=10, entry=100.0, status="PENDING")
    assert s.committed_capital("intraday-v1") == 2000.0       # open + pending, v1 only
    assert s.committed_capital("intraday-v2") == 1000.0
    assert s.count_committed_positions("intraday-v1") == 2
    assert s.count_committed_positions("intraday-v2") == 1


def test_default_strategy_id_is_backward_compatible():
    # No strategy_id passed -> everything lands under (and reads from) the default V1 strategy,
    # so existing single-strategy code behaves exactly as before.
    s = Store(":memory:")
    s.open_position(symbol="Y", exchange="NSE", side="LONG", quantity=7, entry_price=50.0,
                    mode="paper")                              # no strategy_id
    assert s.count_open_positions() == 1                      # default read
    assert s.count_open_positions(DEFAULT_STRATEGY_ID) == 1
    assert s.count_open_positions("intraday-v2") == 0


def test_realized_pnl_since_scoped():
    s = Store(":memory:")
    p1 = _open(s, "intraday-v1")
    p2 = _open(s, "intraday-v2")
    s.close_position(p1, exit_price=110.0, exit_reason="TARGET", realized_pnl=100.0)
    s.close_position(p2, exit_price=90.0, exit_reason="STOP", realized_pnl=-50.0)
    assert s.realized_pnl_since("2000-01-01", "intraday-v1") == 100.0
    assert s.realized_pnl_since("2000-01-01", "intraday-v2") == -50.0


def test_scoped_store_injects_strategy_id_and_delegates():
    s = Store(":memory:")
    v1 = ScopedStore(s, "intraday-v1")
    v2 = ScopedStore(s, "intraday-v2")
    # inserts via the scoped view are tagged automatically (no strategy_id passed by the caller)
    v1.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10, entry_price=100.0,
                     mode="paper")
    v2.open_position(symbol="B", exchange="NSE", side="LONG", quantity=20, entry_price=100.0,
                     mode="paper")
    # reads via the scoped view are filtered automatically
    assert v1.count_open_positions() == 1 and v2.count_open_positions() == 1
    assert v1.deployed_capital() == 1000.0 and v2.deployed_capital() == 2000.0
    assert [p.symbol for p in v1.get_open_positions()] == ["A"]
    # non-scoped methods pass straight through to the underlying store unchanged
    assert v1.get_config().mode == s.get_config().mode
    assert v1.strategy_id == "intraday-v1"


def test_scoped_store_scopes_dashboard_readers():
    # A ScopedStore view of the dashboard/history readers shows only its own strategy's rows;
    # the raw store (strategy_id=None default) still shows all — backward compatible.
    s = Store(":memory:")
    p1 = _open(s, "intraday-v1"); p2 = _open(s, "intraday-v2")
    s.close_position(p1, exit_price=110.0, exit_reason="TARGET", realized_pnl=100.0)
    s.close_position(p2, exit_price=90.0, exit_reason="STOP", realized_pnl=-50.0)
    v1, v2 = ScopedStore(s, "intraday-v1"), ScopedStore(s, "intraday-v2")
    assert s.realized_pnl_total() == 50.0                     # raw = all strategies
    assert v1.realized_pnl_total() == 100.0 and v2.realized_pnl_total() == -50.0
    assert len(v1.get_recent_positions()) == 1 and len(v2.get_recent_positions()) == 1
    assert v1.performance_summary()["trades"] == 1 and v1.performance_summary()["total_pnl"] == 100.0
    assert [r["exit_reason"] for r in v1.exit_reason_breakdown()] == ["TARGET"]
    assert [r["exit_reason"] for r in v2.exit_reason_breakdown()] == ["STOP"]


def test_scoped_store_run_and_decision_tagged():
    s = Store(":memory:")
    v2 = ScopedStore(s, "intraday-v2")
    run_id = v2.start_run("paper")
    v2.record_decision(run_id=run_id, symbol="Z", action="BUY_NOW")
    row = s._conn.execute("SELECT strategy_id FROM job_runs WHERE id=?", (run_id,)).fetchone()
    assert row["strategy_id"] == "intraday-v2"
    drow = s._conn.execute("SELECT strategy_id FROM decisions WHERE run_id=?", (run_id,)).fetchone()
    assert drow["strategy_id"] == "intraday-v2"
