"""What a swing verdict is worth on the stock actually held — in % and in rupees."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from swing_engine import book_totals, level_outcome, verdict_economics


def test_a_target_above_cost_is_a_gain_in_both_units():
    o = level_outcome(1780, 116.5, 130.0)          # real GOLDBEES holding
    assert o["pct"] == pytest.approx(11.588, abs=0.01)
    assert o["amount"] == pytest.approx(24030.0, abs=1)
    assert o["invested"] == pytest.approx(207370.0, abs=1)


def test_a_target_BELOW_cost_is_reported_as_the_loss_it_is():
    """The live book on 2026-08-07 held GREENPOWER at 23.62 against an analyst target of 10.60,
    and HDFCSILVER at 277.83 against 230.00. Labelling those 'expected profit' would dress a 55%
    loss up as an objective."""
    o = level_outcome(3919, 23.62, 10.6)
    assert o["pct"] < 0 and o["amount"] < 0
    assert o["amount"] == pytest.approx(-51024.4, abs=1)
    assert level_outcome(550, 277.83, 230.0)["pct"] == pytest.approx(-17.21, abs=0.01)


def test_missing_or_degenerate_inputs_return_None_rather_than_zero():
    """A missing target is UNKNOWN, not break-even — zero would quietly flatter the roll-up."""
    assert level_outcome(10, 100.0, None) is None
    assert level_outcome(10, None, 110.0) is None
    assert level_outcome(None, 100.0, 110.0) is None
    assert level_outcome(0, 100.0, 110.0) is None
    assert level_outcome(10, 0.0, 110.0) is None
    assert level_outcome(10, -5.0, 110.0) is None
    assert level_outcome(10, "n/a", 110.0) is None


def test_verdict_economics_covers_both_legs_target_and_stop():
    v = {"quantity": 100, "avg_price": 100.0, "swing_target": 120.0, "swing_stop": 90.0,
         "ss_target": 110.0, "ss_stop": 95.0}
    e = verdict_economics(v)
    assert e["swing_target"]["amount"] == pytest.approx(2000.0)
    assert e["swing_stop"]["amount"] == pytest.approx(-1000.0)
    assert e["ss_target"]["amount"] == pytest.approx(1000.0)
    assert e["ss_stop"]["amount"] == pytest.approx(-500.0)
    assert e["invested"] == pytest.approx(10000.0)


def test_a_leg_with_no_levels_is_absent_not_zero():
    e = verdict_economics({"quantity": 100, "avg_price": 100.0, "swing_target": 120.0})
    assert e["swing_target"] is not None
    assert e["ss_target"] is None and e["ss_stop"] is None


def test_book_totals_roll_up_the_whole_analysed_book():
    rows = [{"quantity": 100, "avg_price": 100.0, "swing_target": 120.0, "swing_stop": 90.0},
            {"quantity": 10, "avg_price": 200.0, "swing_target": 190.0, "swing_stop": 180.0}]
    t = book_totals(rows, "swing")
    assert t["invested"] == pytest.approx(12000.0)
    assert t["at_target"] == pytest.approx(2000.0 - 100.0)
    assert t["at_stop"] == pytest.approx(-1000.0 - 200.0)
    assert t["at_target_pct"] == pytest.approx(1900.0 / 12000.0 * 100)
    assert t["targets_known"] == 2 and t["stops_known"] == 2


def test_rows_without_a_level_are_skipped_and_counted_so_coverage_is_visible():
    rows = [{"quantity": 100, "avg_price": 100.0, "swing_target": 120.0, "swing_stop": 90.0},
            {"quantity": 50, "avg_price": 50.0}]                 # analysed, no levels
    t = book_totals(rows, "swing")
    assert t["invested"] == pytest.approx(12500.0)   # still counts toward invested
    assert t["at_target"] == pytest.approx(2000.0)   # but contributes nothing to the outcome
    assert t["targets_known"] == 1 and t["stops_known"] == 1


def test_book_totals_survives_an_empty_or_junk_book():
    for rows in ([], None, [{"symbol": "X"}]):
        t = book_totals(rows, "swing")
        assert t["invested"] == 0 and t["at_target"] == 0
        assert t["at_target_pct"] is None


def test_the_short_swing_leg_can_be_rolled_up_independently():
    rows = [{"quantity": 100, "avg_price": 100.0, "swing_target": 130.0, "ss_target": 110.0}]
    assert book_totals(rows, "swing")["at_target"] == pytest.approx(3000.0)
    assert book_totals(rows, "ss")["at_target"] == pytest.approx(1000.0)
