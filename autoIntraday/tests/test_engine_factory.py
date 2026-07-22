import pytest

from engine_factory import make_decision_engine
from decision_engine import DecisionEngine, DecisionEngineError
from claude_cli_engine import ClaudeCliEngine


def test_default_backend_is_api(monkeypatch):
    monkeypatch.delenv("DECISION_BACKEND", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")   # so anthropic.Anthropic() constructs
    eng = make_decision_engine()
    assert isinstance(eng, DecisionEngine)


def test_api_backend_explicit(monkeypatch):
    monkeypatch.setenv("DECISION_BACKEND", "api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    assert isinstance(make_decision_engine(), DecisionEngine)


def test_claude_cli_backend(monkeypatch):
    monkeypatch.setenv("DECISION_BACKEND", "claude_cli")
    eng = make_decision_engine(use_web_search=False)
    assert isinstance(eng, ClaudeCliEngine)
    assert eng.use_web_search is False


def test_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("DECISION_BACKEND", "bogus")
    with pytest.raises(DecisionEngineError, match="DECISION_BACKEND"):
        make_decision_engine()
