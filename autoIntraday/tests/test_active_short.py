"""activeShort — selection rules, short-side geometry, the paper gate."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from active_short import (ActiveShortError, Candidate, gap_too_far, parse_candidates,
                          position_size, protective_levels, select, short_pnl,
                          validate_short_entry)
from active_short_store import DEFAULTS, ActiveShortStore


def _cfg(**over):
    c = {"min_confidence": 70.0, "min_rvol": 1.5, "max_shorts": 4,
         "stop_pct": 1.5, "target_pct": 2.5, "max_gap_pct": 3.0}
    c.update(over)
    return c


def _cand(sym="AAA", conf=80.0, level=100.0, stop=101.5, target=97.5, rvol=2.0):
    return Candidate(symbol=sym, confidence=conf, confirmation_level=level, stop=stop,
                     target=target, rvol=rvol)


# ---- parsing --------------------------------------------------------------------------------
def test_parse_candidates_skips_malformed_rows():
    payload = {"candidates": [
        {"symbol": "AAA", "confidence": 80, "confirmation_level": 100, "stop": 101.5,
         "target": 97.5, "rvol": 2.0, "reason": "distribution day"},
        {"symbol": "BAD"},                                   # missing levels
        {"symbol": "CCC", "confidence": "x", "confirmation_level": 1, "stop": 2, "target": 0},
    ]}
    got = parse_candidates(payload)
    assert [c.symbol for c in got] == ["AAA"]
    assert got[0].reason == "distribution day"
    assert parse_candidates(None) == [] and parse_candidates({}) == []


# ---- selection ------------------------------------------------------------------------------
def test_select_ranks_by_confidence_and_caps_at_max_shorts():
    cands = [_cand("A", 75), _cand("B", 95), _cand("C", 85), _cand("D", 90), _cand("E", 72)]
    got = select(cands, _cfg(max_shorts=3))
    assert [c.symbol for c in got] == ["B", "D", "C"]


def test_select_applies_the_confidence_floor():
    assert select([_cand(conf=69.9)], _cfg()) == []
    assert len(select([_cand(conf=70.0)], _cfg())) == 1


def test_select_requires_rvol_evidence_of_distribution():
    """Below the RVOL floor there is no institutional distribution behind the pattern."""
    assert select([_cand(rvol=1.4)], _cfg()) == []
    assert select([_cand(rvol=None)], _cfg()) == []
    assert len(select([_cand(rvol=1.5)], _cfg())) == 1


def test_select_refuses_incoherent_short_geometry():
    """A short needs target < confirmation_level < stop. Anything else is not a short setup,
    whatever the skill called it — the same class of error that wrote a short's stop onto a long
    on 2026-07-30."""
    assert select([_cand(level=100, stop=99, target=97)], _cfg()) == []    # stop below level
    assert select([_cand(level=100, stop=102, target=101)], _cfg()) == []  # target above level
    assert len(select([_cand(level=100, stop=101.5, target=97.5)], _cfg())) == 1


# ---- entry safety ---------------------------------------------------------------------------
def test_short_stop_entry_must_sit_below_the_market():
    validate_short_entry(99.0, 100.0)                    # fine
    for bad in (100.0, 101.0):
        with pytest.raises(ActiveShortError, match="at or above the market"):
            validate_short_entry(bad, 100.0)
    with pytest.raises(ActiveShortError):
        validate_short_entry(None, 100.0)


def test_gap_guard_skips_names_that_opened_far_below_the_trigger():
    """SL_M is a MARKET order once triggered: a stock opening 4% below the level fills there, and
    the move the scan predicted has already happened without us."""
    assert gap_too_far(100.0, 96.0, 3.0) is True         # opened 4% below
    assert gap_too_far(100.0, 98.0, 3.0) is False        # 2% below, acceptable
    assert gap_too_far(100.0, 102.0, 3.0) is False       # opened above — fine, just won't trigger
    assert gap_too_far(100.0, 90.0, 0) is False          # guard disabled


# ---- short-side arithmetic ------------------------------------------------------------------
def test_protective_levels_put_the_stop_above_and_target_below_the_fill():
    stop, target = protective_levels(100.0, _cfg())
    assert stop == 101.5 and target == 97.5
    assert stop > 100.0 > target                          # a short's stop is UP


def test_protective_levels_derive_from_the_actual_fill_not_the_plan():
    """An SL_M can fill well below its trigger on a gap; a stop computed from the plan would then
    sit far too wide."""
    stop, _ = protective_levels(95.0, _cfg())
    assert stop == pytest.approx(95.0 * 1.015, abs=0.01)  # 1.5% above the FILL
    assert stop < protective_levels(100.0, _cfg())[0]     # ...not above the planned 100


def test_short_pnl_profits_when_price_falls():
    assert short_pnl(100.0, 97.0, 10) == 30.0
    assert short_pnl(100.0, 103.0, 10) == -30.0


def test_position_size_handles_a_bad_price():
    assert position_size(25000, 100.0) == 250
    assert position_size(25000, 0) == 0


# ---- the paper gate -------------------------------------------------------------------------
def test_defaults_ship_disabled_and_in_paper():
    cfg = ActiveShortStore(":memory:").get_config()
    assert cfg["active_short_enabled"] == 0
    assert cfg["active_short_mode"] == "paper"
    assert cfg["paper_sessions_required"] == 10
    assert set(cfg) == set(DEFAULTS)


def test_live_is_refused_until_the_paper_period_is_complete():
    s = ActiveShortStore(":memory:")
    s.set_config(active_short_mode="live", paper_sessions_required=3)
    ok, why = s.live_allowed()
    assert ok is False and "0/3 paper sessions" in why
    for d in ("2026-08-01", "2026-08-02"):
        s.record_session(d, "paper", 2)
        s.complete_session(d)
    ok, why = s.live_allowed()
    assert ok is False and "2/3" in why                   # still short of the gate
    s.record_session("2026-08-03", "paper", 2)
    s.complete_session("2026-08-03")
    ok, why = s.live_allowed()
    assert ok is True


def test_paper_mode_is_never_live_even_once_the_gate_is_met():
    s = ActiveShortStore(":memory:")
    s.set_config(paper_sessions_required=0)
    ok, why = s.live_allowed()
    assert ok is False and "mode is paper" in why


def test_incomplete_sessions_do_not_count_toward_the_gate():
    s = ActiveShortStore(":memory:")
    s.set_config(active_short_mode="live", paper_sessions_required=1)
    s.record_session("2026-08-01", "paper", 2)            # recorded but never completed
    assert s.live_allowed()[0] is False


# ---- picks ----------------------------------------------------------------------------------
def test_pick_lifecycle_and_session_rollup():
    s = ActiveShortStore(":memory:")
    a = s.add_pick("2026-07-31", "2026-08-01", "AAA", 85, 100.0, 101.5, 97.5, rvol=2.0, rank=1)
    b = s.add_pick("2026-07-31", "2026-08-01", "BBB", 75, 200.0, 203.0, 195.0, rvol=1.8, rank=2)
    s.record_session("2026-08-01", "paper", 2)
    assert [p.symbol for p in s.picks_for("2026-08-01")] == ["AAA", "BBB"]

    s.update_pick(a, status="FILLED", fill_price=99.5, quantity=250)
    s.update_pick(a, status="CLOSED", exit_price=97.5, pnl=short_pnl(99.5, 97.5, 250))
    s.update_pick(b, status="EXPIRED", status_note="never triggered")
    s.complete_session("2026-08-01")

    sess = s.sessions()[0]
    assert sess["picks"] == 2 and sess["triggered"] == 1
    assert sess["realized_pnl"] == pytest.approx(500.0)
    assert sess["completed_at"] is not None


def test_update_pick_rejects_bad_fields_and_statuses():
    s = ActiveShortStore(":memory:")
    pid = s.add_pick("2026-07-31", "2026-08-01", "AAA", 85, 100.0, 101.5, 97.5)
    with pytest.raises(KeyError):
        s.update_pick(pid, nonsense=1)
    with pytest.raises(ValueError):
        s.update_pick(pid, status="WOBBLE")


def test_set_config_rejects_unknown_keys():
    with pytest.raises(KeyError):
        ActiveShortStore(":memory:").set_config(not_a_key=1)
