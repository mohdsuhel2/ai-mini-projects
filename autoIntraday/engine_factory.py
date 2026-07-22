"""Selects the decision backend from the DECISION_BACKEND env var: 'api' (default, raw
Anthropic API) or 'claude_cli' (headless `claude -p`, on the Claude subscription). See
docs/superpowers/specs/2026-07-10-claude-cli-backend-design.md."""
from __future__ import annotations

import os

from decision_engine import MODEL, DecisionEngine, DecisionEngineError


def make_decision_engine(use_web_search: bool = True, model: str = MODEL):
    backend = os.environ.get("DECISION_BACKEND", "api")
    if backend == "api":
        return DecisionEngine(use_web_search=use_web_search, model=model)
    if backend == "claude_cli":
        from claude_cli_engine import ClaudeCliEngine
        return ClaudeCliEngine(use_web_search=use_web_search, model=model)
    raise DecisionEngineError(
        f"unknown DECISION_BACKEND {backend!r}; use 'api' or 'claude_cli'")


def make_screen_engine(use_web_search: bool = True, model: str = MODEL):
    """Entry-screening backend from SCREEN_MODE: 'skill' (default — one-shot top-5 via the
    full intraday-analyst skill) returns a SkillScreenEngine; 'classic' returns None (movers
    screener + per-name decisions). See
    docs/superpowers/specs/2026-07-20-skill-screen-design.md."""
    mode = os.environ.get("SCREEN_MODE", "skill")
    if mode == "classic":
        return None
    if mode == "skill":
        from skill_screen_engine import SkillScreenEngine
        return SkillScreenEngine(use_web_search=use_web_search, model=model)
    raise DecisionEngineError(
        f"unknown SCREEN_MODE {mode!r}; use 'skill' or 'classic'")
