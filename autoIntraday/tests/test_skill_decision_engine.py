import json

import pytest

from decision_engine import Decision, DecisionEngineError
from skill_decision_engine import SkillDecisionEngine, _decision_addendum

_DECISION = {"action": "BUY_NOW", "confidence": 78, "trade_quality": 82, "entry": 2456.7,
             "stop_loss": 2440.0, "target1": 2480.0, "risk_reward": 2.1}
_DECISION_JSON = json.dumps(_DECISION)


def _envelope(payload: str) -> str:
    return json.dumps({"type": "result", "is_error": False, "result": payload})


def _runner_factory(rc, out, err=""):
    def runner(argv, input_text):
        runner.argv = argv
        runner.input_text = input_text
        return (rc, out, err)
    return runner


@pytest.fixture
def skill_file(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("# ROLE — test intraday skill body with 90+/80+/70+ bands\n")
    return f


def _eng(runner, skill_file, **kw):
    return SkillDecisionEngine(runner=runner, skill_path=str(skill_file), **kw)


def test_decide_uses_skill_as_system_prompt_and_parses(skill_file):
    runner = _runner_factory(0, _envelope(_DECISION_JSON))
    d = _eng(runner, skill_file, model="claude-opus-4-8").decide(
        "RELIANCE", {"symbol": "RELIANCE", "price": {"last": 2456.7}})
    assert isinstance(d, Decision) and d.action == "BUY_NOW" and d.entry == 2456.7
    argv = runner.argv
    assert "-p" in argv and "--json-schema" in argv
    assert argv[argv.index("--model") + 1] == "claude-opus-4-8"
    # the system prompt is the SKILL body + the decision-mode addendum
    sysp = argv[argv.index("--append-system-prompt") + 1]
    assert "test intraday skill body" in sysp and "DECISION MODE" in sysp
    # indicators + symbol are passed via stdin (the user message)
    assert "RELIANCE" in runner.input_text and "2456.7" in runner.input_text


def test_decide_does_not_grant_bash_tools(skill_file):
    # indicators are pre-computed and passed in — the model must NOT run the screener/indicator
    # tools; only web search (for news) may be allowed.
    runner = _runner_factory(0, _envelope(_DECISION_JSON))
    _eng(runner, skill_file, use_web_search=True).decide("X", {"symbol": "X"})
    tools = runner.argv[runner.argv.index("--allowedTools") + 1] if "--allowedTools" \
        in runner.argv else ""
    assert "Bash" not in tools and tools == "WebSearch"


def test_decide_without_web_search_omits_tools(skill_file):
    runner = _runner_factory(0, _envelope(_DECISION_JSON))
    _eng(runner, skill_file, use_web_search=False).decide("X", {"symbol": "X"})
    assert "--allowedTools" not in runner.argv


def test_decide_passes_position_context(skill_file):
    runner = _runner_factory(0, _envelope(_DECISION_JSON))
    _eng(runner, skill_file).decide("TCS", {"symbol": "TCS"},
                                    position={"side": "LONG", "quantity": 10,
                                              "entry_price": 100.0, "unrealized_pnl_pct": 1.5})
    assert "LONG" in runner.input_text and "100.0" in runner.input_text


def test_missing_skill_file_raises(tmp_path):
    eng = SkillDecisionEngine(runner=_runner_factory(0, _envelope(_DECISION_JSON)),
                              skill_path=str(tmp_path / "nope.md"))
    with pytest.raises(DecisionEngineError, match="skill not found"):
        eng.decide("X", {"symbol": "X"})


def test_nonzero_exit_and_empty_output_raise(skill_file):
    with pytest.raises(DecisionEngineError, match="claude CLI exit"):
        _eng(_runner_factory(1, "", "usage limit"), skill_file).decide("X", {"symbol": "X"})
    with pytest.raises(DecisionEngineError, match="empty"):
        _eng(_runner_factory(0, "   "), skill_file).decide("X", {"symbol": "X"})


def test_addendum_forbids_tool_running_and_prose():
    a = _decision_addendum()
    assert "ALREADY COMPUTED" in a and "movers screener" in a   # don't re-run the tools
    assert "HOLD vs exit" in a and "No prose" in a
