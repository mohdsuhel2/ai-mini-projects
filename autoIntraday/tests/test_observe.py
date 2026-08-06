"""Skill Lab — shadow skill runs that must never touch the broker."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import observe
from observe import resolve_symbols, run_once
from observe_store import DEFAULTS, ObserveStore, geometric_rr


class _Dec:
    def __init__(self, action="BUY_NOW", q=80, conf=75, entry=100.0, stop=95.0, t1=110.0,
                 rr=2.0):
        self.action, self.trade_quality, self.confidence = action, q, conf
        self.entry, self.stop_loss, self.target1, self.risk_reward = entry, stop, t1, rr
        self.raw_response = '{"action":"' + action + '"}'


class _Engine:
    """Records every call so a test can prove what was asked, and of whom."""
    calls: list = []

    def __init__(self, skill_id, decision=None, fail=False):
        self.skill_id, self.decision, self.fail = skill_id, decision or _Dec(), fail

    def decide(self, symbol, indicators, position=None, book=None):
        _Engine.calls.append((self.skill_id, symbol, id(indicators)))
        if self.fail:
            raise RuntimeError("engine exploded")
        return self.decision


def _factory(decision=None, fail_for=()):
    def make(skill_id):
        return _Engine(skill_id, decision, fail=skill_id in fail_for)
    return make


def _store(**overrides):
    s = ObserveStore(":memory:")
    base = dict(observe_enabled=1, skills="skill-a,skill-b", universe_mode="watchlist",
                watchlist="AAA,BBB", max_symbols=5)
    base.update(overrides)
    s.set_config(**base)
    return s


def _indic(sym):
    return {"symbol": sym, "price": {"last": 100.0}}


# ---- the safety property ----------------------------------------------------------------------
def test_the_runner_has_no_way_to_place_an_order():
    """Structural, not a flag: observe.py must not reach a broker at all."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "observe.py")).read()
    for forbidden in ("groww_client", "place_order", "GrowwClient", "cancel_order",
                      "modify_order", "square_off"):
        assert forbidden not in src, f"observe.py references {forbidden}"


def test_the_store_exposes_no_order_methods():
    s = ObserveStore(":memory:")
    for forbidden in ("open_position", "close_position", "record_order", "place"):
        assert not hasattr(s, forbidden), f"ObserveStore exposes {forbidden}"


# ---- fairness ---------------------------------------------------------------------------------
def test_every_skill_sees_the_IDENTICAL_indicator_payload():
    """A difference in the answers must be a difference in the SKILLS, not in the data or the
    minute they ran. One fetch per symbol, shared by all."""
    _Engine.calls = []
    fetches = []

    def get_ind(sym):
        fetches.append(sym)
        return _indic(sym)

    run_once(_store(), get_indicators=get_ind, engine_factory=_factory())
    assert fetches == ["AAA", "BBB"], "indicators must be fetched once per symbol"
    by_symbol = {}
    for skill, sym, payload_id in _Engine.calls:
        by_symbol.setdefault(sym, set()).add(payload_id)
    for sym, ids in by_symbol.items():
        assert len(ids) == 1, f"{sym} was given different payload objects per skill"


def test_it_asks_every_selected_skill_about_every_symbol():
    _Engine.calls = []
    out = run_once(_store(), get_indicators=_indic, engine_factory=_factory())
    assert out["status"] == "SUCCESS" and out["calls"] == 4
    assert {(s, y) for s, y, _ in _Engine.calls} == {("skill-a", "AAA"), ("skill-a", "BBB"),
                                                     ("skill-b", "AAA"), ("skill-b", "BBB")}


# ---- geometric R:R ----------------------------------------------------------------------------
def test_geometric_rr_is_stored_beside_the_skills_own_claim():
    """On 2026-08-04 the two diverged 3-15x. A comparison that trusts the reported number would
    rank skills by how boldly they round up."""
    s = _store()
    run_once(s, get_indicators=_indic,
             engine_factory=_factory(_Dec(entry=100.0, stop=95.0, t1=110.0, rr=9.9)))
    d = s.decisions()[0]
    assert d["risk_reward"] == 9.9              # what the skill claimed
    assert d["rr_geometric"] == pytest.approx(2.0)   # what its own levels imply


def test_geometric_rr_handles_shorts_and_refuses_degenerate_geometry():
    assert geometric_rr(100, 105, 90, "SHORT_NOW") == pytest.approx(2.0)
    assert geometric_rr(100, 95, 110, "BUY_NOW") == pytest.approx(2.0)
    assert geometric_rr(100, 95, 99, "BUY_NOW") is None      # target below entry
    assert geometric_rr(100, 105, 110, "BUY_NOW") is None    # stop above entry
    assert geometric_rr(None, 95, 110, "BUY_NOW") is None


# ---- budget -----------------------------------------------------------------------------------
def test_a_run_is_skipped_whole_rather_than_half_done_when_the_budget_is_short():
    """A partial pass compares skills on different symbol sets, which is worse than no data."""
    s = _store(daily_call_budget=3)             # a full pass needs 2 skills x 2 symbols = 4
    out = run_once(s, get_indicators=_indic, engine_factory=_factory())
    assert out["status"] == "SKIPPED" and "budget" in out["reason"]
    assert s.decisions() == []
    assert s.runs()[0]["status"] == "SKIPPED"


def test_the_budget_counts_down_across_runs_in_a_day():
    s = _store(daily_call_budget=8)
    assert s.budget_left() == 8
    run_once(s, get_indicators=_indic, engine_factory=_factory())
    assert s.budget_left() == 4
    run_once(s, get_indicators=_indic, engine_factory=_factory())
    assert s.budget_left() == 0
    out = run_once(s, get_indicators=_indic, engine_factory=_factory())
    assert out["status"] == "SKIPPED"


# ---- resilience -------------------------------------------------------------------------------
def test_one_failing_skill_does_not_cost_the_others_their_comparison():
    s = _store()
    out = run_once(s, get_indicators=_indic, engine_factory=_factory(fail_for={"skill-a"}))
    assert out["status"] == "SUCCESS" and out["errors"] == 2
    rows = s.decisions()
    assert {r["skill_id"] for r in rows} == {"skill-a", "skill-b"}
    assert all(r["error"] for r in rows if r["skill_id"] == "skill-a")
    assert all(r["action"] == "BUY_NOW" for r in rows if r["skill_id"] == "skill-b")


def test_an_indicator_failure_is_recorded_against_every_skill_for_that_symbol():
    s = _store()

    def boom(sym):
        if sym == "AAA":
            raise RuntimeError("yahoo down")
        return _indic(sym)

    out = run_once(s, get_indicators=boom, engine_factory=_factory())
    aaa = [r for r in s.decisions() if r["symbol"] == "AAA"]
    assert len(aaa) == 2 and all("indicators" in (r["error"] or "") for r in aaa)
    assert out["errors"] == 2


# ---- skipping ---------------------------------------------------------------------------------
@pytest.mark.parametrize("cfg,expect", [
    ({"observe_enabled": 0}, "disabled"),
    ({"skills": ""}, "no skills"),
    ({"watchlist": ""}, "no symbols"),
])
def test_nothing_to_do_is_RECORDED_not_silent(cfg, expect):
    """A quiet day must be distinguishable from a broken scheduler."""
    s = _store(**cfg)
    out = run_once(s, get_indicators=_indic, engine_factory=_factory())
    assert out["status"] == "SKIPPED" and expect in out["reason"]
    assert s.runs()[0]["status"] == "SKIPPED"


# ---- universe ---------------------------------------------------------------------------------
def test_universe_modes():
    s = _store()
    cfg = dict(s.get_config(), universe_mode="watchlist", max_symbols=1)
    assert resolve_symbols(cfg, s) == ["AAA"]

    cfg = dict(s.get_config(), universe_mode="screener", max_symbols=2)
    assert resolve_symbols(cfg, s, get_candidates=lambda: [{"symbol": "X"}, {"symbol": "Y"},
                                                           {"symbol": "Z"}]) == ["X", "Y"]

    cfg = dict(s.get_config(), universe_mode="book", max_symbols=5)
    assert resolve_symbols(cfg, s, get_open_symbols=lambda: ["P", "Q"]) == ["P", "Q"]


def test_watchlist_is_forgiving_about_how_you_type_it():
    s = _store(watchlist=" reliance , KEI\ntcs ,, ")
    assert s.watchlist() == ["RELIANCE", "KEI", "TCS"]


# ---- discovery --------------------------------------------------------------------------------
def test_skills_are_discovered_from_disk_not_hard_coded(tmp_path):
    """A skill added tomorrow must appear in the UI without a code change."""
    (tmp_path / "brand-new-skill").mkdir()
    (tmp_path / "brand-new-skill" / "SKILL.md").write_text("# hi")
    (tmp_path / "not-a-skill").mkdir()          # no SKILL.md
    assert observe.available_skills(str(tmp_path)) == ["brand-new-skill"]
    assert observe.available_skills("/nonexistent/path") == []


# ---- storage ----------------------------------------------------------------------------------
def test_decisions_are_queryable_by_date_and_skill():
    s = _store()
    run_once(s, get_indicators=_indic, engine_factory=_factory(), trade_date="2026-08-06")
    assert len(s.decisions(trade_date="2026-08-06")) == 4
    assert len(s.decisions(trade_date="2026-08-06", skill_id="skill-a")) == 2
    assert s.decisions(trade_date="1999-01-01") == []
    assert s.dates() == ["2026-08-06"]


def test_config_round_trips_and_rejects_unknown_keys():
    s = ObserveStore(":memory:")
    assert s.get_config()["observe_enabled"] == DEFAULTS["observe_enabled"]
    s.set_config(interval_min=45, skills="a,b")
    assert s.get_config()["interval_min"] == 45 and s.skills() == ["a", "b"]
    with pytest.raises(ValueError, match="unknown"):
        s.set_config(nonsense=1)


def test_the_raw_skill_output_is_kept_for_inspection():
    s = _store()
    run_once(s, get_indicators=_indic, engine_factory=_factory())
    assert all(r["raw_json"] for r in s.decisions())
    assert all(r["latency_ms"] is not None for r in s.decisions())
