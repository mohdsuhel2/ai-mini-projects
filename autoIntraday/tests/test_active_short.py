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


# ---- the jobs: scan -> arm -> protect -> expire / square off ---------------------------------
from active_short_job import arm, expire_unfilled, protect, scan, square_off


class _Client:
    def __init__(self, fill_status="EXECUTED", fail_on=()):
        self.orders, self.cancelled = [], []
        self.fill_status, self.fail_on = fill_status, fail_on

    def place_order(self, **kw):
        if kw.get("order_type") in self.fail_on:
            raise RuntimeError(f"broker refused {kw.get('order_type')}")
        self.orders.append(kw)
        return {"order_id": f"OID{len(self.orders)}"}

    def get_order_status(self, oid):
        return {"order_id": oid, "status": self.fill_status, "price": 99.0}

    def cancel_order(self, oid):
        self.cancelled.append(oid)
        return {"order_id": oid, "status": "CANCELLED"}


def _quote(ltp=100.0, open_px=None):
    return lambda sym: {"ltp": ltp, "open": open_px if open_px is not None else ltp}


def _enabled_store():
    s = ActiveShortStore(":memory:")
    s.set_config(active_short_enabled=1)
    return s


_PAYLOAD = {"regime_note": "breadth negative", "candidates": [
    {"symbol": "AAA", "confidence": 88, "confirmation_level": 99.0, "stop": 101.5,
     "target": 96.0, "rvol": 2.2, "reason": "bearish engulfing at resistance"},
    {"symbol": "BBB", "confidence": 60, "confirmation_level": 50.0, "stop": 51.0,
     "target": 48.0, "rvol": 2.0, "reason": "below the confidence floor"}]}


def test_scan_records_only_picks_that_clear_the_bar():
    s = _enabled_store()
    assert scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01") == 1
    picks = s.picks_for("2026-08-01")
    assert [p.symbol for p in picks] == ["AAA"] and picks[0].rank == 1
    assert s.sessions()[0]["picks"] == 1


def test_scan_handles_an_empty_night_as_a_valid_result():
    s = _enabled_store()
    assert scan(s, lambda: {"regime_note": "strongly bullish", "candidates": []},
                "2026-07-31", "2026-08-01") == 0
    assert s.picks_for("2026-08-01") == []
    assert s.sessions()[0]["picks"] == 0        # the session is still recorded


def test_scan_survives_a_scanner_failure():
    s = _enabled_store()
    def _boom(): raise RuntimeError("skill died")
    assert scan(s, _boom, "2026-07-31", "2026-08-01") == 0


def test_disabled_does_nothing_anywhere():
    s = ActiveShortStore(":memory:")             # enabled defaults to 0
    assert scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01") == 0
    assert arm(s, _Client(), "2026-08-01", _quote()) == 0
    assert protect(s, _Client(), "2026-08-01") == 0


def test_arm_places_a_sell_stop_entry_below_the_market():
    s = _enabled_store()
    scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01")
    c = _Client()
    assert arm(s, c, "2026-08-01", _quote(ltp=100.0)) == 1
    o = c.orders[0]
    assert o["transaction_type"] == "SELL" and o["order_type"] == "SL_M"
    assert o["trigger_price"] == 99.0 and o["trigger_price"] < 100.0
    assert s.picks_for("2026-08-01")[0].status == "ARMED"


def test_arm_skips_a_name_that_gapped_through_its_level():
    s = _enabled_store()
    scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01")
    c = _Client()
    assert arm(s, c, "2026-08-01", _quote(ltp=94.0, open_px=94.0)) == 0   # >3% below 99
    p = s.picks_for("2026-08-01")[0]
    assert p.status == "SKIPPED" and "gapped" in p.status_note
    assert c.orders == []


def test_arm_skips_when_the_trigger_is_no_longer_below_the_market():
    """Price already under the level: a SELL stop-entry there fires instantly, defeating the
    whole confirmation design."""
    s = _enabled_store()
    scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01")
    c = _Client()
    assert arm(s, c, "2026-08-01", _quote(ltp=98.5, open_px=98.5)) == 0
    assert "at or above the market" in s.picks_for("2026-08-01")[0].status_note
    assert c.orders == []


def test_protect_attaches_a_stop_and_target_to_a_filled_entry():
    s = _enabled_store()
    scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01")
    c = _Client(fill_status="EXECUTED")
    arm(s, c, "2026-08-01", _quote(100.0))
    assert protect(s, c, "2026-08-01") == 1
    p = s.picks_for("2026-08-01")[0]
    assert p.status == "PROTECTED" and p.fill_price == 99.0
    stop = [o for o in c.orders if o["transaction_type"] == "BUY" and o["order_type"] == "SL_M"][0]
    tgt = [o for o in c.orders if o["order_type"] == "LIMIT"][0]
    assert stop["trigger_price"] > 99.0 > tgt["price"]     # short: stop above, target below


def test_protect_leaves_an_unfilled_entry_alone():
    s = _enabled_store()
    scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01")
    c = _Client(fill_status="NEW")
    arm(s, c, "2026-08-01", _quote(100.0))
    before = len(c.orders)
    assert protect(s, c, "2026-08-01") == 0
    assert len(c.orders) == before                          # nothing extra placed
    assert s.picks_for("2026-08-01")[0].status == "ARMED"


def test_protect_is_idempotent():
    s = _enabled_store()
    scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01")
    c = _Client()
    arm(s, c, "2026-08-01", _quote(100.0))
    assert protect(s, c, "2026-08-01") == 1
    n = len(c.orders)
    assert protect(s, c, "2026-08-01") == 0                 # already PROTECTED
    assert len(c.orders) == n


def test_a_failed_stop_marks_the_position_unprotected_rather_than_claiming_success():
    """The one place this design can hurt: a filled short with no stop is unbounded risk. It must
    be recorded loudly, never silently treated as protected."""
    s = _enabled_store()
    scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01")
    c = _Client(fail_on=("SL_M",))
    # arm needs SL_M too, so place the entry with a working client first
    ok = _Client()
    arm(s, ok, "2026-08-01", _quote(100.0))
    assert protect(s, c, "2026-08-01") == 0
    p = s.picks_for("2026-08-01")[0]
    assert p.status == "FILLED" and "UNPROTECTED" in p.status_note


def test_expire_cancels_entries_that_never_triggered():
    s = _enabled_store()
    scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01")
    c = _Client(fill_status="NEW")
    arm(s, c, "2026-08-01", _quote(100.0))
    assert expire_unfilled(s, c, "2026-08-01") == 1
    assert s.picks_for("2026-08-01")[0].status == "EXPIRED"
    assert c.cancelled


def test_square_off_closes_and_completes_the_session():
    s = _enabled_store()
    scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01")
    c = _Client()
    arm(s, c, "2026-08-01", _quote(100.0))
    protect(s, c, "2026-08-01")
    assert square_off(s, c, "2026-08-01", _quote(96.0)) == 1
    p = s.picks_for("2026-08-01")[0]
    assert p.status == "CLOSED" and p.exit_price == 96.0
    assert p.pnl > 0                                        # short filled 99, covered 96
    assert s.sessions()[0]["completed_at"] is not None


def test_live_config_still_runs_paper_until_the_gate_opens():
    """A mis-set config must never commit real money before the paper period is done."""
    s = _enabled_store()
    s.set_config(active_short_mode="live", paper_sessions_required=5)
    scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01")
    assert s.picks_for("2026-08-01")[0].mode == "paper"


def test_short_protective_stop_must_sit_above_the_market():
    """A BUY stop below the tape covers the short the instant it is placed."""
    from active_short import validate_short_stop
    validate_short_stop(101.5, 100.0)                    # fine
    for bad in (100.0, 99.0):
        with pytest.raises(ActiveShortError, match="at or below the market"):
            validate_short_stop(bad, 100.0)


def test_protect_refuses_a_stop_on_the_wrong_side_of_the_tape():
    """Caught by the end-to-end smoke test: a bad broker fill price produced a stop of 99.98 for a
    stock trading at 243, and it was placed without complaint. The position must be recorded
    UNPROTECTED instead."""
    s = _enabled_store()
    scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01")
    c = _Client(fill_status="EXECUTED")                  # reports price 99.0 for everything
    arm(s, c, "2026-08-01", _quote(100.0))
    before = len(c.orders)
    # the tape says 243, so a stop derived from a 99.0 "fill" is nonsense
    assert protect(s, c, "2026-08-01", get_quote=_quote(243.0)) == 0
    assert len(c.orders) == before                       # nothing placed
    p = s.picks_for("2026-08-01")[0]
    assert p.status == "FILLED" and "UNPROTECTED" in p.status_note


def test_protect_still_works_when_the_stop_is_sane():
    s = _enabled_store()
    scan(s, lambda: _PAYLOAD, "2026-07-31", "2026-08-01")
    c = _Client(fill_status="EXECUTED")
    arm(s, c, "2026-08-01", _quote(100.0))
    assert protect(s, c, "2026-08-01", get_quote=_quote(99.0)) == 1
    assert s.picks_for("2026-08-01")[0].status == "PROTECTED"


def test_the_skills_documented_payload_parses_and_selects():
    """Contract pin between SKILL.md and the consumer. The skill now emits a rich institutional
    payload (setup_type, scores, gap_probability, targets 2/3, entry_zone...). parse_candidates
    must keep reading only what it needs and ignore the rest — otherwise a skill edit silently
    yields zero picks every night, with nothing in the logs to show why."""
    payload = {
        "scan_date": "2026-07-31", "trade_date": "2026-08-01", "regime": "neutral",
        "regime_note": "NIFTY +0.1%, India VIX 11.8", "data_gaps": ["options_oi", "futures_oi"],
        "candidates": [{
            "symbol": "EXAMPLE", "company": "Example Industries", "confidence": 84,
            "setup_type": "reversal_short", "cmp": 1252.0, "confirmation_level": 1240.5,
            "entry_zone": [1240.5, 1236.0], "stop": 1268.0, "target": 1198.0,
            "target2": 1180.0, "target3": None, "risk_reward": 2.4, "rvol": 2.1,
            "expected_move_pct": 3.4,
            "gap_probability": {"gap_down": 45, "flat": 40, "gap_up": 15},
            "best_entry_time": "09:20-10:30", "invalidation": "above 1268",
            "scores": {"price_action": 88, "options": None},
            "primary_reasons": ["Bearish engulfing"], "secondary_reasons": ["Below SMA20"],
            "risks": ["Sector strength"],
            "reason": "Bearish engulfing at the 1265 supply zone on 2.1x RVOL"}]}
    got = parse_candidates(payload)
    assert len(got) == 1
    c = got[0]
    assert (c.symbol, c.confidence, c.confirmation_level, c.stop, c.target, c.rvol) == \
        ("EXAMPLE", 84.0, 1240.5, 1268.0, 1198.0, 2.1)
    assert select(got, _cfg())[0].symbol == "EXAMPLE"
