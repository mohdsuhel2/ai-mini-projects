"""Strategy plug-in layer — makes the trading engine strategy-agnostic.

A `Strategy` bundles the identity + the skill that generates decisions, and knows how to build
the decision/screen engines for it (the existing SkillDecisionEngine / SkillScreenEngine,
parameterized by skill_path). The `StrategyRegistry` resolves a strategy id from config, so
adding V3 / an AI model / an ML model is one config entry — the engine never changes. See
docs/superpowers/specs/2026-07-24-multi-strategy-compare-design.md."""
from __future__ import annotations

import os
from dataclasses import dataclass

from decision_engine import MODEL, DecisionEngineError
from store import DEFAULT_STRATEGY_ID   # single source of truth (persistence default); re-exported

__all__ = ["DEFAULT_STRATEGY_ID", "Strategy", "StrategyRegistry", "StrategyError",
           "compare_ledger_id", "is_compare_ledger", "base_strategy_id"]

# Compare-mode ledgers are namespaced so they are FULLY isolated from the live/paper ledgers of the
# same strategy — otherwise a strategy's pre-existing live/paper P&L would contaminate the clean
# head-to-head comparison. A compare cycle tags its rows "cmp:<id>"; live/paper keep the bare id.
_COMPARE_PREFIX = "cmp:"


def compare_ledger_id(base_id: str) -> str:
    return f"{_COMPARE_PREFIX}{base_id}"


def is_compare_ledger(strategy_id: str) -> bool:
    return strategy_id.startswith(_COMPARE_PREFIX)


def base_strategy_id(strategy_id: str) -> str:
    return strategy_id[len(_COMPARE_PREFIX):] if is_compare_ledger(strategy_id) else strategy_id

# Built-in default roster used when config.yaml has no `strategies.available` block. V1 is the
# existing intraday-analyst skill; V2 is the parallel intraday-analyst-2 skill.
_DEFAULT_STRATEGIES = [
    {"id": "intraday-v1", "name": "Intraday Skill V1",
     "skill": "~/.claude/skills/intraday-analyst/SKILL.md"},
    {"id": "intraday-v2", "name": "Intraday Skill V2",
     "skill": "~/.claude/skills/intraday-analyst-2/SKILL.md"},
]


class StrategyError(DecisionEngineError):
    """Raised for an unknown / misconfigured strategy."""


@dataclass(frozen=True)
class Strategy:
    """One pluggable intraday strategy. `skill_path` is the SKILL.md whose methodology generates
    every decision (analysis, entry, exit, stop/target updates, trailing, position mgmt, risk).
    A non-skill strategy (AI/ML) would subclass/replace make_decision_engine to return any object
    exposing `.decide(symbol, indicators, position)` — the engine is agnostic to how it decides."""
    id: str
    name: str
    skill_path: str
    model: str = MODEL
    web_search: bool = True

    def make_decision_engine(self, runner=None):
        from skill_decision_engine import SkillDecisionEngine
        kw = {} if runner is None else {"runner": runner}
        return SkillDecisionEngine(use_web_search=self.web_search, model=self.model,
                                   skill_path=self.skill_path, **kw)

    def make_screen_engine(self, runner=None):
        from skill_screen_engine import SkillScreenEngine
        kw = {} if runner is None else {"runner": runner}
        return SkillScreenEngine(use_web_search=self.web_search, model=self.model,
                                 skill_path=self.skill_path, **kw)

    def make_bulk_engine(self, runner=None):
        """One-call decider over a provided candidate list — used by Compare mode to decide the
        whole shared universe in a single call per strategy (fast) instead of one call per name."""
        from bulk_decide_engine import BulkDecideEngine
        kw = {} if runner is None else {"runner": runner}
        return BulkDecideEngine(use_web_search=self.web_search, model=self.model,
                                skill_path=self.skill_path, **kw)


class StrategyRegistry:
    """Resolves strategy ids -> Strategy. Built from config so new strategies are config-only."""

    def __init__(self, strategies):
        self._by_id: dict[str, Strategy] = {}
        for s in strategies:
            if s.id in self._by_id:
                raise StrategyError(f"duplicate strategy id: {s.id!r}")
            self._by_id[s.id] = s
        if not self._by_id:
            raise StrategyError("no strategies configured")

    def get(self, strategy_id: str) -> Strategy:
        if strategy_id not in self._by_id:
            raise StrategyError(
                f"unknown strategy {strategy_id!r}; available: {sorted(self._by_id)}")
        return self._by_id[strategy_id]

    def ids(self) -> list[str]:
        return list(self._by_id)

    def all(self) -> list[Strategy]:
        return list(self._by_id.values())

    def __contains__(self, strategy_id: str) -> bool:
        return strategy_id in self._by_id

    @classmethod
    def from_config(cls, available=None, model: str = MODEL, web_search: bool = True):
        """Build from a list of {id, name, skill} dicts (config.yaml `strategies.available`),
        falling back to the built-in V1/V2 roster when none is given."""
        strategies = []
        for entry in (available or _DEFAULT_STRATEGIES):
            if not entry.get("id") or not entry.get("skill"):
                raise StrategyError(f"strategy entry needs 'id' and 'skill': {entry!r}")
            strategies.append(Strategy(
                id=entry["id"], name=entry.get("name", entry["id"]),
                skill_path=os.path.expanduser(entry["skill"]),
                model=entry.get("model") or model,
                web_search=entry.get("web_search", web_search)))
        return cls(strategies)
