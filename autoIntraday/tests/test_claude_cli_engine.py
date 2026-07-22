import json

import pytest

from claude_cli_engine import ClaudeCliEngine, _result_text
from decision_engine import DecisionEngineError

_DECISION = {
    "action": "BUY_NOW", "confidence": 78, "trade_quality": 82, "entry": 2456.7,
    "stop_loss": 2440.0, "target1": 2480.0, "target2": None, "target3": None,
    "risk_reward": 2.1, "expected_move_pct": 1.3, "invalidation": "15m close below VWAP",
    "rationale": "fresh breakout", "news_catalyst": None}
_DECISION_JSON = json.dumps(_DECISION)


def _runner_factory(rc, out, err=""):
    def runner(argv, input_text):
        runner.argv = argv
        runner.input_text = input_text
        return (rc, out, err)
    return runner


def test_result_text_unwraps_envelope():
    env = json.dumps({"type": "result", "result": _DECISION_JSON, "session_id": "x"})
    assert _result_text(env) == _DECISION_JSON


def test_result_text_bare_json_passthrough():
    # stdout that is already the decision object (no envelope) is returned as-is
    assert _result_text(_DECISION_JSON) == _DECISION_JSON


def test_result_text_non_json_passthrough():
    assert _result_text("plain text answer") == "plain text answer"


def test_decide_builds_argv_and_parses():
    runner = _runner_factory(0, json.dumps({"result": _DECISION_JSON}))
    eng = ClaudeCliEngine(runner=runner, use_web_search=True, model="claude-opus-4-8")
    d = eng.decide("RELIANCE", {"symbol": "RELIANCE", "price": {"last": 2456.7}})
    assert d.action == "BUY_NOW" and d.entry == 2456.7
    argv = runner.argv
    assert "-p" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "claude-opus-4-8"
    assert "--append-system-prompt" in argv and "--json-schema" in argv
    assert "--allowedTools" in argv and "WebSearch" in argv
    # the indicator JSON + symbol go to the CLI via stdin (the user message)
    assert "RELIANCE" in runner.input_text and "2456.7" in runner.input_text


def test_decide_without_web_search_omits_tool():
    runner = _runner_factory(0, json.dumps({"result": _DECISION_JSON}))
    ClaudeCliEngine(runner=runner, use_web_search=False).decide("TCS", {"symbol": "TCS"})
    assert "--allowedTools" not in runner.argv


def test_decide_nonzero_exit_raises():
    runner = _runner_factory(1, "", "usage limit reached")
    with pytest.raises(DecisionEngineError, match="claude"):
        ClaudeCliEngine(runner=runner).decide("X", {"symbol": "X"})


def test_decide_empty_output_raises():
    runner = _runner_factory(0, "   ")
    with pytest.raises(DecisionEngineError, match="empty"):
        ClaudeCliEngine(runner=runner).decide("X", {"symbol": "X"})


def test_decide_garbage_output_raises():
    runner = _runner_factory(0, json.dumps({"result": "I could not decide."}))
    with pytest.raises(DecisionEngineError):
        ClaudeCliEngine(runner=runner).decide("X", {"symbol": "X"})
