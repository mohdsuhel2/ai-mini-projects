import pytest

from skill_decision_engine import SkillDecisionEngine
from skill_screen_engine import SkillScreenEngine
from strategies import (DEFAULT_STRATEGY_ID, Strategy, StrategyError, StrategyRegistry)


def _strat(sid="intraday-v1", skill="/tmp/x/SKILL.md", **kw):
    return Strategy(id=sid, name=kw.get("name", sid), skill_path=skill,
                    model=kw.get("model", "claude-opus-4-8"), web_search=kw.get("web_search", True))


def test_strategy_builds_skill_engines_at_its_own_skill_path():
    s = _strat("intraday-v2", skill="/skills/v2/SKILL.md", web_search=False)
    dec = s.make_decision_engine()
    scr = s.make_screen_engine()
    assert isinstance(dec, SkillDecisionEngine) and dec.skill_path == "/skills/v2/SKILL.md"
    assert dec.use_web_search is False
    assert isinstance(scr, SkillScreenEngine) and scr.skill_path == "/skills/v2/SKILL.md"


def test_registry_resolves_and_rejects_unknown():
    reg = StrategyRegistry([_strat("intraday-v1"), _strat("intraday-v2")])
    assert reg.get("intraday-v2").id == "intraday-v2"
    assert reg.ids() == ["intraday-v1", "intraday-v2"]
    assert "intraday-v1" in reg
    with pytest.raises(StrategyError, match="unknown strategy"):
        reg.get("intraday-v9")


def test_registry_rejects_duplicate_and_empty():
    with pytest.raises(StrategyError, match="duplicate"):
        StrategyRegistry([_strat("dup"), _strat("dup")])
    with pytest.raises(StrategyError, match="no strategies"):
        StrategyRegistry([])


def test_from_config_defaults_to_v1_v2():
    reg = StrategyRegistry.from_config(None)
    assert reg.ids() == ["intraday-v1", "intraday-v2"]
    v1 = reg.get(DEFAULT_STRATEGY_ID)
    assert v1.name == "Intraday Skill V1" and v1.skill_path.endswith("intraday-analyst/SKILL.md")
    assert "intraday-analyst-2" in reg.get("intraday-v2").skill_path


def test_from_config_expands_user_and_validates():
    reg = StrategyRegistry.from_config([
        {"id": "s1", "name": "One", "skill": "~/skills/one/SKILL.md"}])
    assert reg.get("s1").skill_path.startswith("/") and "~" not in reg.get("s1").skill_path
    with pytest.raises(StrategyError, match="needs 'id' and 'skill'"):
        StrategyRegistry.from_config([{"id": "no-skill"}])
