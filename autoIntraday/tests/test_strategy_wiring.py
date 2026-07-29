"""Phase 1: _build_orchestrator wires the config-selected strategy + a ScopedStore ledger."""
import run_cycle_job
from settings import load_settings
from skill_decision_engine import SkillDecisionEngine
from store import ScopedStore, Store


_ROSTER = """
strategies:
  available:
    - {id: intraday-v1, name: V1, skill: ~/.claude/skills/intraday-analyst/SKILL.md}
    - {id: intraday-v2, name: V2, skill: ~/.claude/skills/intraday-analyst-2/SKILL.md}
"""


def _settings(tmp_path, backend="skill", screen="classic"):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(f"decision:\n  backend: {backend}\n  screen_mode: {screen}\n{_ROSTER}")
    return load_settings(path=str(cfg), env={})


def _store(paper="intraday-v1", mode="paper", compare=False, compare_strats=("intraday-v1", "intraday-v2")):
    s = Store(":memory:")
    s.update_config(mode=mode, paper_strategy=paper, live_strategy=paper,
                    compare_enabled=compare, compare_strategies=list(compare_strats))
    return s


def test_build_orchestrator_selects_strategy_and_scopes_store(tmp_path):
    # runtime selection comes from the DB config (the UI's control surface), roster from yaml
    orch = run_cycle_job._build_orchestrator(_store(paper="intraday-v2"), _settings(tmp_path))
    assert isinstance(orch.store, ScopedStore) and orch.store.strategy_id == "intraday-v2"
    assert isinstance(orch.engine, SkillDecisionEngine)
    assert "intraday-analyst-2" in orch.engine.skill_path
    assert orch.screen_engine is None                     # classic screen_mode


def test_build_orchestrator_v1_uses_v1_skill(tmp_path):
    orch = run_cycle_job._build_orchestrator(_store(paper="intraday-v1"),
                                             _settings(tmp_path, screen="skill"))
    assert orch.store.strategy_id == "intraday-v1"
    assert "intraday-analyst/SKILL.md" in orch.engine.skill_path
    assert orch.screen_engine is not None and "intraday-analyst/SKILL.md" in orch.screen_engine.skill_path


def test_compare_enabled_builds_compare_orchestrator(tmp_path):
    orch = run_cycle_job._build_orchestrator(_store(compare=True), _settings(tmp_path))
    from compare_orchestrator import CompareOrchestrator
    assert isinstance(orch, CompareOrchestrator)
    assert [s.id for s in orch.strategies] == ["intraday-v1", "intraday-v2"]


def test_non_skill_backend_falls_back_to_factory(tmp_path, monkeypatch):
    monkeypatch.setenv("DECISION_BACKEND", "claude_cli")
    orch = run_cycle_job._build_orchestrator(_store(paper="intraday-v1"),
                                             _settings(tmp_path, backend="claude_cli"))
    from claude_cli_engine import ClaudeCliEngine
    assert isinstance(orch.engine, ClaudeCliEngine)
    assert isinstance(orch.store, ScopedStore)
