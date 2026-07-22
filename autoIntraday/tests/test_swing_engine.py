import json

import pytest

from swing_engine import SWING_SCHEMA, SwingEngine, SwingEngineError


def _verdict(symbol="RELIANCE", swing_action="HOLD", ss_action="HOLD"):
    leg = lambda a: {"action": a, "conviction": 70, "target": 2600.0, "stop": 2300.0,
                     "rationale": "trend intact"}
    return {"symbol": symbol, "swing": leg(swing_action), "shortswing": leg(ss_action)}


def _envelope(payload: dict) -> str:
    return json.dumps({"type": "result", "is_error": False, "result": json.dumps(payload)})


@pytest.fixture
def skills(tmp_path):
    sw = tmp_path / "swing.md"
    sw.write_text("# SWING SKILL body\n")
    ss = tmp_path / "shortswing.md"
    ss.write_text("# SHORTSWING SKILL body\n")
    return str(sw), str(ss)


def _engine(runner, skills, **kw):
    return SwingEngine(runner=runner, swing_skill_path=skills[0],
                       shortswing_skill_path=skills[1], **kw)


HOLDINGS = [{"symbol": "RELIANCE", "quantity": 10, "avg_price": 2400.0},
            {"symbol": "TCS", "quantity": 5, "avg_price": 3800.0}]


def test_analyze_parses_verdicts(skills):
    payload = {"verdicts": [_verdict("RELIANCE", "ADD"), _verdict("TCS", "EXIT", "REDUCE")]}
    eng = _engine(lambda argv, text: (0, _envelope(payload), ""), skills)
    out = eng.analyze(HOLDINGS)
    assert [v["symbol"] for v in out] == ["RELIANCE", "TCS"]
    assert out[0]["swing"]["action"] == "ADD"
    assert out[1]["swing"]["action"] == "EXIT" and out[1]["shortswing"]["action"] == "REDUCE"


def test_analyze_accepts_bare_json(skills):
    payload = {"verdicts": [_verdict("RELIANCE")]}
    eng = _engine(lambda argv, text: (0, json.dumps(payload), ""), skills)
    assert eng.analyze(HOLDINGS)[0]["symbol"] == "RELIANCE"


def test_analyze_empty_is_valid(skills):
    eng = _engine(lambda argv, text: (0, _envelope({"verdicts": []}), ""), skills)
    assert eng.analyze(HOLDINGS) == []


def test_analyze_missing_verdicts_key_raises(skills):
    eng = _engine(lambda argv, text: (0, _envelope({"nope": 1}), ""), skills)
    with pytest.raises(SwingEngineError):
        eng.analyze(HOLDINGS)


def test_analyze_bad_action_raises(skills):
    bad = {"verdicts": [{"symbol": "X", "swing": {"action": "YOLO", "conviction": 1,
                         "target": None, "stop": None, "rationale": ""},
                         "shortswing": {"action": "HOLD", "conviction": 1, "target": None,
                                        "stop": None, "rationale": ""}}]}
    eng = _engine(lambda argv, text: (0, _envelope(bad), ""), skills)
    with pytest.raises(SwingEngineError):
        eng.analyze(HOLDINGS)


def test_analyze_nonzero_exit_raises(skills):
    eng = _engine(lambda argv, text: (1, "", "boom"), skills)
    with pytest.raises(SwingEngineError):
        eng.analyze(HOLDINGS)


def test_analyze_runner_exception_wrapped(skills):
    def boom(argv, text):
        raise TimeoutError("timed out")
    with pytest.raises(SwingEngineError):
        _engine(boom, skills).analyze(HOLDINGS)


def test_missing_skill_file_raises(tmp_path):
    eng = SwingEngine(runner=lambda a, t: (0, "{}", ""),
                      swing_skill_path=str(tmp_path / "nope.md"),
                      shortswing_skill_path=str(tmp_path / "nope2.md"))
    with pytest.raises(SwingEngineError):
        eng.analyze(HOLDINGS)


def test_argv_and_prompt_wiring(skills):
    seen = {}

    def spy(argv, text):
        seen["argv"], seen["text"] = argv, text
        return 0, _envelope({"verdicts": []}), ""

    _engine(spy, skills, claude_bin="/bin/claude").analyze(HOLDINGS)
    argv = seen["argv"]
    assert argv[0] == "/bin/claude" and "-p" in argv
    assert "--json-schema" in argv and json.dumps(SWING_SCHEMA) in argv
    sys_prompt = argv[argv.index("--append-system-prompt") + 1]
    assert "SWING SKILL body" in sys_prompt and "SHORTSWING SKILL body" in sys_prompt
    assert "HOLDINGS ANALYSIS MODE" in sys_prompt
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "WebSearch" in allowed
    assert allowed.count("Bash(") == 2
    assert "stock_analyze.py" in allowed and "stock_analyze_shortswing.py" in allowed
    # holdings appear in the user message
    assert "RELIANCE" in seen["text"] and "2400" in seen["text"] and "TCS" in seen["text"]


def test_analyze_one_parses_single(skills):
    payload = {"swing": {"action": "ADD", "conviction": 72, "target": 2600.0, "stop": 2300.0,
                         "rationale": "strong"},
               "shortswing": {"action": "HOLD", "conviction": 55, "target": None, "stop": None,
                              "rationale": "flat"}}
    eng = _engine(lambda argv, text: (0, _envelope(payload), ""), skills)
    v = eng.analyze_one("RELIANCE", 10, 2400.0)
    assert v["swing"]["action"] == "ADD" and v["swing"]["conviction"] == 72
    assert v["shortswing"]["action"] == "HOLD"


def test_analyze_one_missing_leg_raises(skills):
    eng = _engine(lambda argv, text: (0, _envelope({"swing": {}}), ""), skills)
    with pytest.raises(SwingEngineError):
        eng.analyze_one("X")
