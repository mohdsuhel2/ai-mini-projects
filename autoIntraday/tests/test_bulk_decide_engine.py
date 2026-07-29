import json

import pytest

from bulk_decide_engine import BulkDecideEngine, BulkDecideError, _parse_bulk
from decision_engine import Decision


def _payload(*cands):
    return {"decisions": list(cands)}


def _cand(symbol, action="BUY_NOW", tq=75, conf=70, entry=100.0, stop=98.0, target=110.0, rr=2.0):
    return {"symbol": symbol, "action": action, "confidence": conf, "trade_quality": tq,
            "entry": entry, "stop_loss": stop, "target1": target, "risk_reward": rr}


def _envelope(payload):
    return json.dumps({"type": "result", "result": json.dumps(payload)})


@pytest.fixture
def skill_file(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("# skill body, bands 90+/80+/70+/60+/<60\n")
    return f


def _runner_factory(payload):
    def runner(argv, text):
        runner.argv, runner.input_text = argv, text
        return (0, _envelope(payload), "")
    return runner


def test_decide_many_one_call_returns_decision_per_symbol(skill_file):
    runner = _runner_factory(_payload(_cand("CNL"), _cand("SBIN", action="WAIT", tq=40,
                                                           entry=None, stop=None, target=None, rr=None)))
    eng = BulkDecideEngine(runner=runner, skill_path=str(skill_file), use_web_search=False)
    items = [{"symbol": "CNL", "indicators": {"symbol": "CNL", "price": {"last": 100}}},
             {"symbol": "SBIN", "indicators": {"symbol": "SBIN"}}]
    out = eng.decide_many(items)
    assert set(out) == {"CNL", "SBIN"}
    assert isinstance(out["CNL"], Decision) and out["CNL"].action == "BUY_NOW"
    assert out["SBIN"].action == "WAIT"
    # ONE call, both symbols + indicators in the single prompt, schema enforced, no Bash tools
    assert "--json-schema" in runner.argv and "Bash" not in "".join(runner.argv)
    assert "CNL" in runner.input_text and "SBIN" in runner.input_text


def test_decide_many_empty_is_noop(skill_file):
    called = []
    eng = BulkDecideEngine(runner=lambda a, t: called.append(1) or (0, "", ""),
                           skill_path=str(skill_file))
    assert eng.decide_many([]) == {} and not called      # no CLI call for an empty list


def test_parse_bulk_skips_bad_entries():
    out = _parse_bulk(json.dumps(_payload(
        _cand("AAA"), {"symbol": "BAD", "action": "NONSENSE"}, {"no_symbol": 1})))
    assert set(out) == {"AAA"}                            # invalid action / missing symbol dropped


def test_nonzero_exit_raises(skill_file):
    eng = BulkDecideEngine(runner=lambda a, t: (1, "", "limit"), skill_path=str(skill_file))
    with pytest.raises(BulkDecideError, match="claude CLI exit"):
        eng.decide_many([{"symbol": "X", "indicators": {"symbol": "X"}}])
