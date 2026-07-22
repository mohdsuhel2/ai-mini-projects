import sqlite3

import pytest

from store import Store, StoreError, SCHEMA_VERSION


def test_init_creates_all_tables():
    store = Store(":memory:")
    names = {row["name"] for row in store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"config", "job_runs", "decisions", "positions", "orders"} <= names


def test_init_sets_user_version():
    store = Store(":memory:")
    assert store._conn.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_foreign_keys_enabled():
    store = Store(":memory:")
    assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_init_is_idempotent(tmp_path):
    db = str(tmp_path / "t.db")
    Store(db).close()
    Store(db).close()  # must not raise


def test_fresh_db_has_seeded_default_config():
    store = Store(":memory:")
    cfg = store.get_config()
    assert cfg.mode == "paper"
    assert cfg.total_pool == 0
    assert cfg.max_open_positions == 0
    assert cfg.capital_per_position == 0
    assert cfg.is_paused is False


def test_update_config_roundtrips():
    store = Store(":memory:")
    cfg = store.update_config(mode="live", total_pool=100000.0,
                              max_open_positions=5, capital_per_position=20000.0,
                              is_paused=True)
    assert cfg.mode == "live"
    assert cfg.total_pool == 100000.0
    assert cfg.max_open_positions == 5
    assert cfg.capital_per_position == 20000.0
    assert cfg.is_paused is True
    # persisted, not just returned
    assert store.get_config().max_open_positions == 5


def test_update_config_partial():
    store = Store(":memory:")
    store.update_config(total_pool=50000.0)
    cfg = store.get_config()
    assert cfg.total_pool == 50000.0
    assert cfg.mode == "paper"  # untouched


def test_update_config_unknown_field_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="unknown config field"):
        store.update_config(bogus=1)


def test_config_second_row_rejected():
    store = Store(":memory:")
    with pytest.raises(sqlite3.IntegrityError):
        store._conn.execute(
            "INSERT INTO config (id, mode, total_pool, max_open_positions, "
            "capital_per_position, is_paused, updated_at) VALUES "
            "(2, 'paper', 0, 0, 0, 0, 'now')")


def test_start_run_creates_running_row():
    store = Store(":memory:")
    run_id = store.start_run(mode="paper")
    run = store.get_run(run_id)
    assert run.id == run_id
    assert run.status == "RUNNING"
    assert run.mode == "paper"
    assert run.started_at is not None
    assert run.finished_at is None


def test_finish_run_sets_fields():
    store = Store(":memory:")
    run_id = store.start_run(mode="paper")
    store.finish_run(run_id, status="SUCCESS", num_candidates=12, num_actions=2,
                     summary="ok")
    run = store.get_run(run_id)
    assert run.status == "SUCCESS"
    assert run.num_candidates == 12
    assert run.num_actions == 2
    assert run.summary == "ok"
    assert run.finished_at is not None


def test_finish_unknown_run_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="unknown run"):
        store.finish_run(999, status="SUCCESS")


def test_get_unknown_run_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="unknown run"):
        store.get_run(999)


def test_open_position_roundtrips():
    store = Store(":memory:")
    pid = store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG",
                              quantity=10, entry_price=2400.0, target_price=2500.0,
                              stop_loss=2350.0, entry_order_id="PAPER-1", mode="paper")
    p = store.get_position(pid)
    assert p.symbol == "RELIANCE"
    assert p.status == "OPEN"
    assert p.quantity == 10
    assert p.entry_price == 2400.0
    assert p.target_price == 2500.0
    assert p.entry_order_id == "PAPER-1"
    assert p.closed_at is None


def test_close_position_sets_exit_fields():
    store = Store(":memory:")
    pid = store.open_position(symbol="TCS", exchange="NSE", side="LONG",
                              quantity=5, entry_price=3800.0)
    store.close_position(pid, exit_price=3850.0, exit_reason="TARGET",
                         realized_pnl=250.0)
    p = store.get_position(pid)
    assert p.status == "CLOSED"
    assert p.exit_price == 3850.0
    assert p.exit_reason == "TARGET"
    assert p.realized_pnl == 250.0
    assert p.closed_at is not None


def test_close_unknown_position_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="unknown position"):
        store.close_position(999, exit_price=1.0, exit_reason="MANUAL", realized_pnl=0.0)


def test_get_open_positions_excludes_closed():
    store = Store(":memory:")
    a = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=1,
                            entry_price=100.0)
    b = store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=1,
                            entry_price=200.0)
    store.close_position(b, exit_price=210.0, exit_reason="TARGET", realized_pnl=10.0)
    open_syms = {p.symbol for p in store.get_open_positions()}
    assert open_syms == {"A"}
    assert store.count_open_positions() == 1


def test_deployed_capital_sums_open_only():
    store = Store(":memory:")
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                        entry_price=100.0)   # 1000
    store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=2,
                        entry_price=500.0)   # 1000
    closed = store.open_position(symbol="C", exchange="NSE", side="LONG", quantity=5,
                                 entry_price=400.0)
    store.close_position(closed, exit_price=410.0, exit_reason="TARGET", realized_pnl=50.0)
    assert store.deployed_capital() == 2000.0


def test_deployed_capital_zero_when_no_open():
    store = Store(":memory:")
    assert store.deployed_capital() == 0.0


def test_record_order_roundtrips():
    store = Store(":memory:")
    oid = store.record_order(broker_order_id="PAPER-1", symbol="RELIANCE",
                             transaction_type="BUY", quantity=10, order_type="MARKET",
                             price=2400.0, status="COMPLETE", mode="paper")
    o = store.get_order(oid)
    assert o.broker_order_id == "PAPER-1"
    assert o.transaction_type == "BUY"
    assert o.quantity == 10
    assert o.price == 2400.0
    assert o.status == "COMPLETE"
    assert o.position_id is None


def test_record_order_links_to_position():
    store = Store(":memory:")
    pid = store.open_position(symbol="RELIANCE", exchange="NSE", side="LONG",
                              quantity=10, entry_price=2400.0)
    oid = store.record_order(broker_order_id="PAPER-1", symbol="RELIANCE",
                             transaction_type="BUY", quantity=10, order_type="MARKET",
                             position_id=pid, mode="paper")
    assert store.get_order(oid).position_id == pid
    linked = store.get_orders_for_position(pid)
    assert [o.broker_order_id for o in linked] == ["PAPER-1"]


def test_record_order_bad_position_fk_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="foreign key|unknown position"):
        store.record_order(broker_order_id="PAPER-1", symbol="X", transaction_type="BUY",
                           quantity=1, order_type="MARKET", position_id=999, mode="paper")


def test_update_order_status_roundtrips():
    store = Store(":memory:")
    store.record_order(broker_order_id="PAPER-OCO-1", symbol="RELIANCE",
                       transaction_type="SELL", quantity=10, order_type="OCO",
                       status="ACTIVE", mode="paper")
    store.update_order_status("PAPER-OCO-1", "TRIGGERED")
    # fetch via the position-less path: read by broker id through get_orders_for_position
    # is not applicable, so assert through a fresh query helper
    o = store._conn.execute(
        "SELECT status FROM orders WHERE broker_order_id = 'PAPER-OCO-1'").fetchone()
    assert o["status"] == "TRIGGERED"


def test_update_unknown_order_status_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="unknown order"):
        store.update_order_status("NOPE-1", "COMPLETE")


def test_record_decision_roundtrips():
    store = Store(":memory:")
    run_id = store.start_run(mode="paper")
    did = store.record_decision(run_id=run_id, symbol="RELIANCE", action="BUY",
                                score=0.82, reason="breakout", entry_price=2400.0,
                                target_price=2500.0, stop_loss=2350.0)
    decs = store.get_decisions_for_run(run_id)
    assert len(decs) == 1
    d = decs[0]
    assert d.id == did
    assert d.symbol == "RELIANCE"
    assert d.action == "BUY"
    assert d.score == 0.82
    assert d.target_price == 2500.0
    assert d.position_id is None


def test_record_decision_links_position():
    store = Store(":memory:")
    run_id = store.start_run(mode="paper")
    pid = store.open_position(symbol="TCS", exchange="NSE", side="LONG", quantity=5,
                              entry_price=3800.0)
    store.record_decision(run_id=run_id, symbol="TCS", action="BUY", position_id=pid)
    assert store.get_decisions_for_run(run_id)[0].position_id == pid


def test_record_decision_bad_run_fk_raises():
    store = Store(":memory:")
    with pytest.raises(StoreError, match="foreign key|integrity"):
        store.record_decision(run_id=999, symbol="X", action="SKIP")


def test_get_decisions_for_run_ordered_and_scoped():
    store = Store(":memory:")
    r1 = store.start_run(mode="paper")
    r2 = store.start_run(mode="paper")
    store.record_decision(run_id=r1, symbol="A", action="BUY")
    store.record_decision(run_id=r1, symbol="B", action="SKIP")
    store.record_decision(run_id=r2, symbol="C", action="BUY")
    assert [d.symbol for d in store.get_decisions_for_run(r1)] == ["A", "B"]
    assert [d.symbol for d in store.get_decisions_for_run(r2)] == ["C"]


def test_get_recent_runs_newest_first_and_limited():
    store = Store(":memory:")
    ids = [store.start_run("paper") for _ in range(5)]
    recent = store.get_recent_runs(limit=3)
    assert [r.id for r in recent] == list(reversed(ids))[:3]


def test_get_recent_decisions_newest_first():
    store = Store(":memory:")
    run_id = store.start_run("paper")
    store.record_decision(run_id=run_id, symbol="A", action="BUY_NOW")
    store.record_decision(run_id=run_id, symbol="B", action="WAIT")
    recent = store.get_recent_decisions(limit=10)
    assert [d.symbol for d in recent] == ["B", "A"]


def test_get_recent_positions_newest_first():
    store = Store(":memory:")
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=1, entry_price=100.0)
    store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=1, entry_price=200.0)
    recent = store.get_recent_positions(limit=10)
    assert [p.symbol for p in recent] == ["B", "A"]


def test_realized_pnl_total_sums_closed_only():
    store = Store(":memory:")
    a = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10, entry_price=100.0)
    store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=1, entry_price=100.0)  # open
    store.close_position(a, exit_price=110.0, exit_reason="TARGET", realized_pnl=100.0)
    assert store.realized_pnl_total() == 100.0   # B still open, not counted


def test_realized_pnl_total_zero_when_none_closed():
    store = Store(":memory:")
    assert store.realized_pnl_total() == 0.0


def test_realized_pnl_since_date_boundary():
    store = Store(":memory:")
    a = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=1, entry_price=100.0)
    store.close_position(a, exit_price=150.0, exit_reason="TARGET", realized_pnl=50.0)
    # closed_at is an ISO UTC timestamp today; a far-past cutoff includes it, a far-future one excludes it
    assert store.realized_pnl_since("2000-01-01") == 50.0
    assert store.realized_pnl_since("2999-01-01") == 0.0


def test_migration_adds_trigger_kind_to_old_db(tmp_path):
    """A DB created before the trigger_kind column existed must be migrated in place."""
    import sqlite3 as _sq
    db = str(tmp_path / "old.db")
    conn = _sq.connect(db)
    conn.execute("""CREATE TABLE positions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL, exchange TEXT NOT NULL,
        side TEXT NOT NULL, quantity INTEGER NOT NULL, entry_price REAL NOT NULL,
        target_price REAL, stop_loss REAL, status TEXT NOT NULL, entry_order_id TEXT,
        oco_order_id TEXT, exit_price REAL, exit_reason TEXT, realized_pnl REAL,
        mode TEXT NOT NULL, opened_at TEXT NOT NULL, closed_at TEXT)""")
    conn.execute("INSERT INTO positions (symbol, exchange, side, quantity, entry_price, status,"
                 " mode, opened_at) VALUES ('OLD','NSE','LONG',1,10.0,'PENDING','paper','t')")
    conn.commit()
    conn.close()
    store = Store(db)                                   # triggers the migration
    p = store.get_pending_positions()[0]
    assert p.symbol == "OLD" and p.trigger_kind is None  # legacy rows read back as None
    pid = store.open_position(symbol="NEW", exchange="NSE", side="LONG", quantity=1,
                              entry_price=10.0, status="PENDING", trigger_kind="STOP")
    assert store.get_position(pid).trigger_kind == "STOP"


def test_performance_summary_and_exit_breakdown():
    store = Store(":memory:")
    w1 = store.open_position(symbol="W1", exchange="NSE", side="LONG", quantity=1, entry_price=10)
    store.close_position(w1, exit_price=20, exit_reason="TARGET", realized_pnl=300.0)
    w2 = store.open_position(symbol="W2", exchange="NSE", side="LONG", quantity=1, entry_price=10)
    store.close_position(w2, exit_price=20, exit_reason="TARGET", realized_pnl=100.0)
    l1 = store.open_position(symbol="L1", exchange="NSE", side="LONG", quantity=1, entry_price=10)
    store.close_position(l1, exit_price=5, exit_reason="STOP", realized_pnl=-100.0)
    store.open_position(symbol="OPEN", exchange="NSE", side="LONG", quantity=1, entry_price=10)
    perf = store.performance_summary()
    assert perf["trades"] == 3 and perf["wins"] == 2 and perf["losses"] == 1
    assert perf["win_rate_pct"] == 66.7
    assert perf["avg_win"] == 200.0 and perf["avg_loss"] == -100.0
    # expectancy = 2/3*200 + 1/3*(-100) = 100
    assert perf["expectancy_per_trade"] == 100.0
    assert perf["total_pnl"] == 300.0
    reasons = {r["exit_reason"]: r for r in store.exit_reason_breakdown()}
    assert reasons["TARGET"]["count"] == 2 and reasons["TARGET"]["total_pnl"] == 400.0
    assert reasons["STOP"]["count"] == 1 and reasons["STOP"]["total_pnl"] == -100.0


def test_performance_summary_empty():
    store = Store(":memory:")
    perf = store.performance_summary()
    assert perf["trades"] == 0 and perf["win_rate_pct"] == 0.0
    assert store.exit_reason_breakdown() == []


def test_update_position_quantity():
    store = Store(":memory:")
    pid = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live")
    store.update_position_quantity(pid, 4)
    assert store.get_position(pid).quantity == 4


def test_update_pending_order():
    store = Store(":memory:")
    pid = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="live", entry_order_id="OLD", status="PENDING")
    store.update_pending_order(pid, entry_price=101.5, stop_loss=96.0, target_price=112.0,
                               quantity=8, entry_order_id="NEW")
    p = store.get_position(pid)
    assert p.status == "PENDING"
    assert p.entry_price == 101.5 and p.stop_loss == 96.0 and p.target_price == 112.0
    assert p.quantity == 8 and p.entry_order_id == "NEW"


def test_update_pending_order_rejects_non_pending():
    store = Store(":memory:")
    pid = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="paper")   # OPEN
    with pytest.raises(Exception):
        store.update_pending_order(pid, entry_price=101.0, stop_loss=96.0,
                                   target_price=112.0, quantity=8, entry_order_id=None)


def test_add_to_position_weighted_average():
    store = Store(":memory:")
    pid = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="paper")
    new_avg = store.add_to_position(pid, 10, 90.0)
    assert new_avg == pytest.approx(95.0)                 # (10*100 + 10*90)/20
    p = store.get_position(pid)
    assert p.quantity == 20 and p.entry_price == pytest.approx(95.0)
    assert p.stop_loss == 95.0 and p.target_price == 110.0   # stop/target unchanged


def test_add_to_position_rejects_non_open():
    store = Store(":memory:")
    pid = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0,
                              mode="paper", status="PENDING")
    with pytest.raises(Exception):
        store.add_to_position(pid, 5, 90.0)


def test_primer_enabled_config_defaults_false_and_toggles():
    store = Store(":memory:")
    assert store.get_config().primer_enabled is False        # default off (opt-in)
    store.update_config(primer_enabled=True)
    assert store.get_config().primer_enabled is True
    store.update_config(primer_enabled=False)
    assert store.get_config().primer_enabled is False


def test_activity_summary_counts_operations():
    store = Store(":memory:")
    run = store.start_run("paper")
    # an opened+closed long (entry + exit), a cancelled pending, a scale-in, a trail adjust
    pid = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, target_price=110.0, stop_loss=95.0, mode="paper")
    store.close_position(pid, exit_price=108.0, exit_reason="TARGET", realized_pnl=80.0)
    pend = store.open_position(symbol="B", exchange="NSE", side="SHORT", quantity=5,
                               entry_price=200.0, target_price=190.0, stop_loss=205.0,
                               mode="paper", status="PENDING")
    store.cancel_position(pend, "SQUAREOFF")
    store.record_order(broker_order_id="O1", symbol="A", transaction_type="BUY", quantity=10,
                       order_type="MARKET", price=100.0, status="COMPLETE", mode="paper",
                       position_id=pid)
    store.record_order(broker_order_id="O2", symbol="A", transaction_type="SELL", quantity=10,
                       order_type="MARKET", price=108.0, status="COMPLETE", mode="paper",
                       position_id=pid)
    store.record_order(broker_order_id="O3", symbol="A", transaction_type="BUY", quantity=10,
                       order_type="OCO", price=None, status="ACTIVE", mode="paper",
                       position_id=pid)   # protective bracket — must NOT count as a buy
    store.record_decision(run_id=run, symbol="A", action="ADD", reason="scale-in", position_id=pid)
    store.record_decision(run_id=run, symbol="A", action="ADJUSTED", reason="trailed",
                          position_id=pid)
    store.record_decision(run_id=run, symbol="C", action="ADOPTED", reason="manual")

    a = store.activity_summary("2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    assert a["buys"] == 1 and a["sells"] == 1
    assert a["entries"] == 1              # A reached OPEN
    assert a["exits"] == 1               # A closed
    assert a["cancels"] == 1             # B cancelled
    assert a["added"] == 1 and a["adjusted"] == 1 and a["adopted"] == 1


def test_activity_summary_empty_window_is_zeros():
    store = Store(":memory:")
    a = store.activity_summary("2000-01-01T00:00:00+00:00", "2000-01-02T00:00:00+00:00")
    assert a == {"buys": 0, "sells": 0, "entries": 0, "exits": 0, "cancels": 0,
                 "added": 0, "adjusted": 0, "adopted": 0}


def test_performance_summary_windowed():
    store = Store(":memory:")
    # two closed trades; we'll only be able to window by closed_at which is set to "now"
    a = store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=10,
                            entry_price=100.0, mode="paper")
    store.close_position(a, exit_price=110.0, exit_reason="TARGET", realized_pnl=100.0)
    b = store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=10,
                            entry_price=100.0, mode="paper")
    store.close_position(b, exit_price=95.0, exit_reason="STOP", realized_pnl=-50.0)

    allt = store.performance_summary()
    assert allt["trades"] == 2 and allt["total_pnl"] == 50.0

    wide = ("2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    win = store.performance_summary(*wide)
    assert win["trades"] == 2 and win["total_pnl"] == 50.0            # both fall in the window

    past = ("2000-01-01T00:00:00+00:00", "2000-01-02T00:00:00+00:00")
    empty = store.performance_summary(*past)
    assert empty["trades"] == 0 and empty["total_pnl"] == 0.0

    assert len(store.exit_reason_breakdown(*wide)) == 2               # TARGET + STOP
    assert store.exit_reason_breakdown(*past) == []


def test_purge_old_history_deletes_old_keeps_live():
    from datetime import datetime, timezone, timedelta
    store = Store(":memory:")
    run = store.start_run("paper")
    closed = store.open_position(symbol="OLD", exchange="NSE", side="LONG", quantity=10,
                                 entry_price=100.0, mode="paper")
    store.record_order(broker_order_id="O1", symbol="OLD", transaction_type="BUY", quantity=10,
                       order_type="MARKET", price=100.0, status="COMPLETE", mode="paper",
                       position_id=closed)
    store.record_decision(run_id=run, symbol="OLD", action="EXIT", reason="STOP",
                          position_id=closed)
    store.close_position(closed, exit_price=95.0, exit_reason="STOP", realized_pnl=-50.0)
    live = store.open_position(symbol="LIVE", exchange="NSE", side="LONG", quantity=5,
                               entry_price=200.0, mode="paper")   # OPEN — must survive

    # purge as if it's 60 days in the future -> everything above is >30 days old
    future = datetime.now(timezone.utc) + timedelta(days=60)
    counts = store.purge_old_history(now=future)
    assert counts["decisions"] >= 1 and counts["orders"] >= 1
    assert counts["positions"] >= 1 and counts["job_runs"] >= 1
    assert store.get_position(live).status == "OPEN"          # live position NOT deleted
    assert store.get_config().mode == "paper"                 # config untouched
    with pytest.raises(StoreError):
        store.get_position(closed)                            # old closed position gone


def test_purge_keeps_everything_within_30_days():
    store = Store(":memory:")
    run = store.start_run("paper")
    store.record_decision(run_id=run, symbol="X", action="WAIT", reason="r")
    counts = store.purge_old_history()                        # now: nothing older than 30d
    assert counts == {"decisions": 0, "orders": 0, "positions": 0, "job_runs": 0}


def test_purge_floor_cannot_go_below_30_days():
    from datetime import datetime, timezone, timedelta
    store = Store(":memory:")
    run = store.start_run("paper")
    store.record_decision(run_id=run, symbol="X", action="WAIT")   # created ~now
    # even asking for days=1, the 30-day floor protects data younger than 30 days
    counts = store.purge_old_history(days=1)
    assert counts["decisions"] == 0


def test_swing_run_and_verdicts_roundtrip():
    store = Store(":memory:")
    rid = store.start_swing_run()
    assert store.latest_swing_run()["status"] == "RUNNING"
    rows = [
        {"symbol": "RELIANCE", "quantity": 10, "avg_price": 2400.0,
         "swing": {"action": "HOLD", "conviction": 70, "target": 2600.0, "stop": 2300.0,
                   "rationale": "trend intact"},
         "shortswing": {"action": "REDUCE", "conviction": 55, "target": 2500.0, "stop": 2350.0,
                        "rationale": "near-term soft"}},
        {"symbol": "TCS", "quantity": 5, "avg_price": 3800.0,
         "swing": {"action": "EXIT", "conviction": 80, "target": None, "stop": None,
                   "rationale": "breakdown"},
         "shortswing": {"action": "EXIT", "conviction": 75, "target": None, "stop": None,
                        "rationale": "weak"}},
    ]
    store.seed_swing_verdicts(rid, [{"symbol": r["symbol"], "quantity": r["quantity"],
                                     "avg_price": r["avg_price"]} for r in rows])
    for r in rows:
        store.update_swing_verdict(rid, r["symbol"], "DONE", swing=r["swing"],
                                   shortswing=r["shortswing"])
    store.finish_swing_run(rid, "SUCCESS", num_holdings=2)

    run = store.latest_swing_run()
    assert run["status"] == "SUCCESS" and run["num_holdings"] == 2 and run["finished_at"]
    v = store.get_swing_verdicts(rid)
    assert [r["symbol"] for r in v] == ["RELIANCE", "TCS"]
    assert v[0]["swing_action"] == "HOLD" and v[0]["ss_action"] == "REDUCE"
    assert v[0]["swing_target"] == 2600.0 and v[1]["swing_action"] == "EXIT"


def test_finish_swing_run_failed_records_error():
    store = Store(":memory:")
    rid = store.start_swing_run()
    store.finish_swing_run(rid, "FAILED", num_holdings=0, error="no groww creds")
    run = store.latest_swing_run()
    assert run["status"] == "FAILED" and run["error"] == "no groww creds"


def test_swing_runs_empty_and_list():
    store = Store(":memory:")
    assert store.latest_swing_run() is None
    assert store.get_swing_runs() == []
    r1 = store.start_swing_run(); store.finish_swing_run(r1, "SUCCESS", num_holdings=1)
    r2 = store.start_swing_run(); store.finish_swing_run(r2, "SUCCESS", num_holdings=1)
    runs = store.get_swing_runs()
    assert [r["id"] for r in runs] == [r2, r1]        # newest first
    assert store.latest_swing_run()["id"] == r2


def test_swing_progress_counts():
    store = Store(":memory:")
    rid = store.start_swing_run()
    holds = [{"symbol": s, "quantity": 1, "avg_price": 1.0} for s in ("A", "B", "C")]
    store.seed_swing_verdicts(rid, holds)
    assert store.swing_progress(rid) == {"total": 3, "done": 0, "pending": 3,
                                         "analyzing": 0, "errors": 0}
    store.update_swing_verdict(rid, "A", "ANALYZING")
    store.update_swing_verdict(rid, "B", "DONE",
                               swing={"action": "HOLD", "conviction": 60, "target": None,
                                      "stop": None, "rationale": "x"},
                               shortswing={"action": "HOLD", "conviction": 60, "target": None,
                                           "stop": None, "rationale": "x"})
    store.update_swing_verdict(rid, "C", "ERROR")
    p = store.swing_progress(rid)
    assert p["done"] == 2 and p["analyzing"] == 1 and p["errors"] == 1 and p["pending"] == 0
    v = {r["symbol"]: r for r in store.get_swing_verdicts(rid)}
    assert v["B"]["status"] == "DONE" and v["B"]["swing_action"] == "HOLD"
    assert v["A"]["status"] == "ANALYZING"


def test_replace_and_get_holdings():
    store = Store(":memory:")
    assert store.get_holdings() == [] and store.holdings_fetched_at() is None
    store.replace_holdings([{"symbol": "B", "quantity": 5, "avg_price": 200.0},
                            {"symbol": "A", "quantity": 10, "avg_price": 100.0}])
    hs = store.get_holdings()
    assert [h["symbol"] for h in hs] == ["A", "B"]      # ordered
    assert store.holdings_fetched_at() is not None
    # replacing swaps the snapshot entirely
    store.replace_holdings([{"symbol": "C", "quantity": 1, "avg_price": 50.0}])
    assert [h["symbol"] for h in store.get_holdings()] == ["C"]
