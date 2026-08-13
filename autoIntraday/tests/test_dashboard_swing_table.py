"""The swing Analysis table's column picker: hidden columns disappear from header and
rows alike, while Symbol and the ↻ re-analyze control can never be hidden."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Importing dashboard runs load_settings(), which loads .env (Groww creds) into os.environ —
# and that leaks into tests that assert credentials are ABSENT. Import, then restore.
_env_before = dict(os.environ)
import dashboard  # noqa: E402

os.environ.clear()
os.environ.update(_env_before)

V = dict(symbol="ENRIN", status="DONE", quantity=10, avg_price=100.0,
         swing_action="HOLD", swing_conviction=7, swing_target=120.0, swing_stop=95.0,
         ss_action="EXIT", ss_conviction=5, ss_target=110.0, ss_stop=98.0,
         swing_rationale="trend intact", ss_rationale="overbought",
         analyzed_at="2026-08-07T10:00:00+05:30")


def test_default_renders_every_column():
    h = dashboard._swing_table_html([V], running=False)
    for label in ("Symbol", "Status", "Qty", "Avg", "Swing",
                  "Short-swing", "At target", "Analyzed"):
        assert label in h


def test_hidden_columns_vanish_from_header_and_rows():
    h = dashboard._swing_table_html([V], running=False, visible={"Swing", "Short-swing"})
    for cls in ("c-status", "c-qty", "c-avg", "c-pnl", "c-when"):
        assert cls not in h
    assert "c-swing" in h and "c-ss" in h


def test_symbol_and_reanalyze_survive_any_selection():
    h = dashboard._swing_table_html([V], running=False, visible=set())
    assert "ENRIN" in h and "c-sym" in h and "swre=" in h


def test_from_here_and_total_pnl_are_separate_columns():
    underwater = dict(V, symbol="GREENPOWER", quantity=3919, avg_price=23.62,
                      price_at_analysis=9.0, swing_target=10.6, swing_stop=8.0)
    h = dashboard._swing_table_html([underwater], running=False)
    assert "c-tpnl" in h                       # the Total PnL column exists in its own right
    assert "+6,270" in h                       # At target: (10.6 - 9.0) * 3919 from here
    assert '<span class="c-tpnl"><span class="ai-neg">-51,025' in h   # total vs the 23.62 cost
    legacy = dict(underwater)
    del legacy["price_at_analysis"]
    h2 = dashboard._swing_table_html([legacy], running=False)
    assert "-51,025" in h2                     # total is still known...
    assert "+6,270" not in h2                  # ...but from-here is unknown, not borrowed


def test_a_refreshed_ltp_reprices_from_here_without_touching_the_analysis():
    v = dict(V, symbol="GREENPOWER", quantity=3919, avg_price=23.62,
             price_at_analysis=9.0, swing_target=10.6, analyzed_at="2026-08-07T05:00:00+00:00")
    fresh = [{"symbol": "GREENPOWER", "ltp": 9.5, "fetched_at": "2026-08-07T07:00:00+00:00"}]
    dashboard._attach_live_price([v], fresh)
    assert v["current_price"] == 9.5           # refresh is newer -> live price wins
    stale = dict(v, analyzed_at="2026-08-07T08:00:00+00:00")
    stale.pop("current_price")
    dashboard._attach_live_price([stale], fresh)
    assert "current_price" not in stale        # re-analysis is newer -> keep its price
    noltp = dict(v); noltp.pop("current_price")
    dashboard._attach_live_price([noltp], [{"symbol": "GREENPOWER", "ltp": None,
                                            "fetched_at": "2026-08-07T09:00:00+00:00"}])
    assert "current_price" not in noltp


def test_sorting_orders_rows_and_sends_unknowns_last():
    a = dict(V, symbol="AAA", price_at_analysis=100.0, swing_target=110.0)   # +qty*10 from here
    b = dict(V, symbol="BBB", price_at_analysis=100.0, swing_target=150.0)   # biggest
    c = dict(V, symbol="CCC", price_at_analysis=None, swing_target=None)     # unknown
    top = dashboard._sort_swing_rows([a, c, b], "At target ↓")
    assert [v["symbol"] for v in top] == ["BBB", "AAA", "CCC"]
    bottom = dashboard._sort_swing_rows([a, c, b], "At target ↑")
    assert [v["symbol"] for v in bottom] == ["AAA", "BBB", "CCC"]
    assert [v["symbol"] for v in dashboard._sort_swing_rows([b, a, c], "Symbol A→Z")] \
        == ["AAA", "BBB", "CCC"]
    same = dashboard._sort_swing_rows([b, a], "Sort: analysis order")
    assert [v["symbol"] for v in same] == ["BBB", "AAA"]


# ---- ETA column: analyst's trading-days estimate rendered as a calendar date -----------

from datetime import date

VE = dict(V, swing_eta_days=7, ss_eta_days=3)


def test_eta_cell_future_shows_days_and_date():
    # analyzed Fri 2026-08-07 IST, 3 trading days -> Wed 12 Aug
    out = dashboard._eta_cell(3, "2026-08-07T10:00:00+05:30", set(),
                              today=date(2026, 8, 10))
    assert out == "~3 td · 12 Aug"


def test_eta_cell_past_is_dimmed_was_due():
    out = dashboard._eta_cell(1, "2026-08-07T10:00:00+05:30", set(),
                              today=date(2026, 8, 20))
    assert out == '<span class="ai-eta-past">was due 10 Aug</span>'


def test_eta_cell_unknown_is_dash():
    assert dashboard._eta_cell(None, "2026-08-07T10:00:00+05:30", set()) == "—"


def test_eta_cell_without_timestamp_still_shows_days():
    assert dashboard._eta_cell(5, None, set()) == "~5 td"


def test_eta_column_renders_and_hides():
    h = dashboard._swing_table_html([VE], running=False)
    assert "ETA" in h and "c-eta" in h
    h2 = dashboard._swing_table_html([VE], running=False, visible={"Swing"})
    assert "c-eta" not in h2


def test_expanded_row_shows_both_leg_etas():
    """Both legs carry an ETA annotation. Asserted on "· ETA " rather than "ETA ~" because a
    due date that has passed renders as "was due 12 Aug" — the earlier assertion silently
    became date-dependent and broke once the seeded dates fell into the past."""
    h = dashboard._swing_table_html([VE], running=False)
    assert h.count("· ETA ") >= 2   # swing + short-swing lines both carry an ETA
