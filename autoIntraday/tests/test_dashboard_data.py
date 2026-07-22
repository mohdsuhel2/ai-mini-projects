from store import Store
from dashboard_data import (header_view, pending_view, positions_view, pnl_summary,
                            decisions_view, runs_view)


def _seeded():
    store = Store(":memory:")
    store.update_config(mode="paper", total_pool=100000.0, max_open_positions=5,
                        capital_per_position=20000.0)
    store.open_position(symbol="A", exchange="NSE", side="LONG", quantity=100,
                        entry_price=100.0, target_price=110.0, stop_loss=95.0)  # 10000 deployed
    return store


def test_header_view_math():
    store = _seeded()
    h = header_view(store)
    assert h["mode"] == "paper"
    assert h["is_paused"] is False
    assert h["total_pool"] == 100000.0
    assert h["deployed_capital"] == 10000.0
    assert h["utilization_pct"] == 10.0     # 10000 / 100000
    assert h["open_count"] == 1
    assert h["max_open_positions"] == 5


def test_header_view_zero_pool_no_divzero():
    store = Store(":memory:")   # seeded default total_pool = 0
    h = header_view(store)
    assert h["utilization_pct"] == 0.0      # must not divide by zero


def test_positions_view_shape():
    store = _seeded()
    rows = positions_view(store)
    assert rows[0]["symbol"] == "A"
    assert rows[0]["status"] == "OPEN"
    assert rows[0]["target_price"] == 110.0
    assert rows[0]["realized_pnl"] is None


def test_pnl_summary_total_and_today():
    store = _seeded()
    a = store.open_position(symbol="B", exchange="NSE", side="LONG", quantity=10,
                            entry_price=100.0)
    store.close_position(a, exit_price=120.0, exit_reason="TARGET", realized_pnl=200.0)
    s = pnl_summary(store, today_iso="2000-01-01")   # far-past cutoff → today == total
    assert s["realized_total"] == 200.0
    assert s["realized_today"] == 200.0
    assert s["open_count"] == 1                       # A still open


def test_decisions_view_newest_first():
    store = _seeded()
    run_id = store.start_run("paper")
    store.record_decision(run_id=run_id, symbol="X", action="BUY_NOW", score=80,
                          reason="strong")
    store.record_decision(run_id=run_id, symbol="Y", action="WAIT", score=40, reason="no edge")
    rows = decisions_view(store)
    assert rows[0]["symbol"] == "Y" and rows[0]["action"] == "WAIT"
    assert rows[1]["symbol"] == "X" and rows[1]["score"] == 80
    # each row carries the decision time (raw UTC created_at; dashboard renders it HH:MM:SS IST)
    assert list(rows[0])[0] == "time" and rows[0]["time"]


def test_runs_view_shape():
    store = _seeded()
    run_id = store.start_run("paper")
    store.finish_run(run_id, "SUCCESS", num_candidates=3, num_actions=1, summary="1 entry")
    rows = runs_view(store)
    assert rows[0]["status"] == "SUCCESS"
    assert rows[0]["num_candidates"] == 3
    assert rows[0]["summary"] == "1 entry"


def test_pending_view_and_header_count():
    store = _seeded()   # one OPEN position 'A'
    store.open_position(symbol="P", exchange="NSE", side="LONG", quantity=50, entry_price=200.0,
                        target_price=214.0, stop_loss=194.0, status="PENDING")
    pend = pending_view(store)
    assert len(pend) == 1
    assert pend[0] == {"symbol": "P", "side": "LONG", "quantity": 50, "rest_at": 200.0,
                       "target": 214.0, "stop": 194.0, "placed_at": pend[0]["placed_at"]}
    h = header_view(store)
    assert h["pending_count"] == 1
    assert h["open_count"] == 1              # pending is NOT counted as open


def test_views_empty_db_do_not_raise():
    store = Store(":memory:")
    assert positions_view(store) == []
    assert pending_view(store) == []
    assert decisions_view(store) == []
    assert runs_view(store) == []
    assert header_view(store)["pending_count"] == 0
    assert pnl_summary(store, "2000-01-01") == {"realized_total": 0.0, "realized_today": 0.0,
                                                "open_count": 0}


def test_day_scoped_views_filter_by_range():
    from dashboard_data import (closed_positions_for_day, decisions_for_day,
                                realized_for_day, runs_for_day)
    store = Store(":memory:")
    store.update_config(mode="paper", total_pool=100000.0, max_open_positions=5,
                        capital_per_position=20000.0)
    run_id = store.start_run("paper")
    store.record_decision(run_id=run_id, symbol="X", action="WAIT", score=40, reason="r")
    pid = store.open_position(symbol="X", exchange="NSE", side="LONG", quantity=1,
                              entry_price=100.0)
    store.close_position(pid, exit_price=110.0, exit_reason="TARGET", realized_pnl=10.0)

    # everything above was stamped with "now" (UTC) — a window around now contains it all
    wide = ("2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    assert len(runs_for_day(store, *wide)) == 1
    assert len(decisions_for_day(store, *wide)) == 1
    closed = closed_positions_for_day(store, *wide)
    assert len(closed) == 1 and closed[0]["realized_pnl"] == 10.0
    assert realized_for_day(store, *wide) == 10.0

    # a window entirely in the past contains none of it
    past = ("2000-01-01T00:00:00+00:00", "2000-01-02T00:00:00+00:00")
    assert runs_for_day(store, *past) == []
    assert decisions_for_day(store, *past) == []
    assert closed_positions_for_day(store, *past) == []
    assert realized_for_day(store, *past) == 0.0


def test_activity_log_filters_to_operations_newest_first():
    from dashboard_data import activity_log
    store = _seeded()
    run = store.start_run("paper")
    store.record_decision(run_id=run, symbol="X", action="WAIT", reason="below gate")  # noise
    store.record_decision(run_id=run, symbol="X", action="BUY_ON_PULLBACK",
                          reason="resting @ 100")                                        # placed
    store.record_decision(run_id=run, symbol="X", action="FILL", reason="resting filled @ 100")
    store.record_decision(run_id=run, symbol="X", action="ADJUSTED", reason="trailed 95->97")
    # an exit whose position booked a profit -> P&L shown on the Exit row
    pid = store.open_position(symbol="X", exchange="NSE", side="LONG", quantity=10,
                              entry_price=100.0, mode="paper")
    store.close_position(pid, exit_price=108.0, exit_reason="TARGET", realized_pnl=80.0)
    store.record_decision(run_id=run, symbol="X", action="EXIT", reason="TARGET",
                          position_id=pid)
    wide = ("2000-01-01T00:00:00+00:00", "2100-01-01T00:00:00+00:00")
    rows = activity_log(store, *wide)
    events = [r["event"] for r in rows]
    assert "Exit" == events[0]                     # newest first
    assert events == ["Exit", "Adjusted SL/target", "Filled", "Order placed"]
    assert "WAIT" not in [r.get("event") for r in rows]   # screening noise excluded
    assert all(set(r) == {"time", "symbol", "event", "detail", "P&L"} for r in rows)
    assert rows[0]["P&L"] == "₹80.00"              # exit shows the booked profit
    assert rows[1]["P&L"] == ""                    # non-exit rows have no P&L
