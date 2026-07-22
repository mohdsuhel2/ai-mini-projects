import json

import pytest

from decision_engine import Decision
from skill_screen_engine import (SCREEN_SCHEMA, SkillScreenEngine, SkillScreenError)


def _cand(symbol="RELIANCE", action="BUY_NOW", tq=70, conf=65, entry=100.0, stop=95.0,
          target1=110.0, rr=2.0):
    return {"symbol": symbol, "action": action, "confidence": conf, "trade_quality": tq,
            "entry": entry, "stop_loss": stop, "target1": target1, "risk_reward": rr}


def _envelope(payload: dict) -> str:
    # claude -p --output-format json wraps the answer in an envelope's "result" field
    return json.dumps({"type": "result", "is_error": False, "result": json.dumps(payload)})


def _engine(runner, skill_file, **kw):
    return SkillScreenEngine(runner=runner, skill_path=str(skill_file), **kw)


@pytest.fixture
def skill_file(tmp_path):
    f = tmp_path / "SKILL.md"
    f.write_text("# ROLE — test skill body\n")
    return f


def test_screen_parses_candidates_in_order(skill_file):
    payload = {"candidates": [_cand("AAA", tq=80), _cand("BBB", action="SHORT_NOW", tq=60)]}
    eng = _engine(lambda argv, text: (0, _envelope(payload), ""), skill_file)
    out = eng.screen(exclude_symbols=[])
    assert [s for s, _ in out] == ["AAA", "BBB"]
    sym, dec = out[0]
    assert isinstance(dec, Decision)
    assert dec.action == "BUY_NOW" and dec.trade_quality == 80 and dec.entry == 100.0


def test_screen_accepts_bare_json_without_envelope(skill_file):
    payload = {"candidates": [_cand("AAA")]}
    eng = _engine(lambda argv, text: (0, json.dumps(payload), ""), skill_file)
    assert [s for s, _ in eng.screen([])] == ["AAA"]


def test_screen_empty_list_is_valid(skill_file):
    eng = _engine(lambda argv, text: (0, _envelope({"candidates": []}), ""), skill_file)
    assert eng.screen([]) == []


def test_screen_missing_candidates_key_raises(skill_file):
    eng = _engine(lambda argv, text: (0, _envelope({"nope": 1}), ""), skill_file)
    with pytest.raises(SkillScreenError):
        eng.screen([])


def test_screen_invalid_action_raises(skill_file):
    eng = _engine(lambda argv, text: (0, _envelope(
        {"candidates": [_cand(action="YOLO")]}), ""), skill_file)
    with pytest.raises(SkillScreenError):
        eng.screen([])


def test_screen_nonzero_exit_raises(skill_file):
    eng = _engine(lambda argv, text: (1, "", "boom"), skill_file)
    with pytest.raises(SkillScreenError):
        eng.screen([])


def test_screen_empty_stdout_raises(skill_file):
    eng = _engine(lambda argv, text: (0, "", ""), skill_file)
    with pytest.raises(SkillScreenError):
        eng.screen([])


def test_screen_runner_exception_wrapped(skill_file):
    def boom(argv, text):
        raise TimeoutError("timed out")
    eng = _engine(boom, skill_file)
    with pytest.raises(SkillScreenError):
        eng.screen([])


def test_missing_skill_file_raises(tmp_path):
    eng = SkillScreenEngine(runner=lambda a, t: (0, "{}", ""),
                            skill_path=str(tmp_path / "nope.md"))
    with pytest.raises(SkillScreenError):
        eng.screen([])


def test_argv_and_prompt_wiring(skill_file):
    seen = {}

    def spy(argv, text):
        seen["argv"], seen["text"] = argv, text
        return 0, _envelope({"candidates": []}), ""

    eng = _engine(spy, skill_file, claude_bin="/bin/claude")
    eng.screen(exclude_symbols=["HELD1", "HELD2"])
    argv = seen["argv"]
    assert argv[0] == "/bin/claude" and "-p" in argv
    assert "--json-schema" in argv and json.dumps(SCREEN_SCHEMA) in argv
    sys_prompt = argv[argv.index("--append-system-prompt") + 1]
    assert "test skill body" in sys_prompt          # full SKILL.md embedded
    assert "SCREENING MODE" in sys_prompt           # addendum appended
    allowed = argv[argv.index("--allowedTools") + 1]
    assert "WebSearch" in allowed
    assert allowed.count("Bash(") == 2              # exactly the two scripts, nothing else
    assert "groww_intraday_screener.py" in allowed and "stock_analyze_intraday.py" in allowed
    assert "HELD1" in seen["text"] and "HELD2" in seen["text"]   # exclusions in user message


def test_no_web_search_flag(skill_file):
    seen = {}

    def spy(argv, text):
        seen["argv"] = argv
        return 0, _envelope({"candidates": []}), ""

    _engine(spy, skill_file, use_web_search=False).screen([])
    allowed = seen["argv"][seen["argv"].index("--allowedTools") + 1]
    assert "WebSearch" not in allowed and "Bash(" in allowed


from engine_factory import make_screen_engine
from decision_engine import DecisionEngineError


def test_factory_skill_mode(monkeypatch):
    monkeypatch.setenv("SCREEN_MODE", "skill")
    eng = make_screen_engine()
    assert isinstance(eng, SkillScreenEngine)


def test_factory_default_is_skill(monkeypatch):
    monkeypatch.delenv("SCREEN_MODE", raising=False)
    assert isinstance(make_screen_engine(), SkillScreenEngine)


def test_factory_classic_mode_returns_none(monkeypatch):
    monkeypatch.setenv("SCREEN_MODE", "classic")
    assert make_screen_engine() is None


def test_factory_unknown_mode_raises(monkeypatch):
    monkeypatch.setenv("SCREEN_MODE", "bogus")
    with pytest.raises(DecisionEngineError):
        make_screen_engine()
