"""Skill-driven single-symbol decision backend — runs the FULL intraday-analyst SKILL.md
(the same file the skill-screen uses) through one headless `claude -p`, for ONE stock with the
caller's position context, and returns a typed Decision. Selected by DECISION_BACKEND=skill.

Why this exists: the api/claude_cli backends use engine_prompt.ENGINE_PROMPT — a hand-maintained
condensation of the skill that can drift from it. This backend runs the real skill instead, so
entries AND open-position management share ONE methodology (the same one the screen already
uses). The indicator JSON is passed in (already computed); the model does NOT run the tools.
Same decide() interface as DecisionEngine/ClaudeCliEngine.

Note: like the claude_cli backend, do NOT set ANTHROPIC_API_KEY when using this — its presence
makes `claude` bill the API instead of the subscription."""
from __future__ import annotations

import json
import os
from typing import Callable

from claude_cli_engine import _result_text
from decision_engine import (DECISION_SCHEMA, MODEL, DecisionEngineError, _parse_decision,
                             build_user_message)
from skill_screen_engine import SKILL_PATH

# A single-symbol structured call (no tool-running); web search for news adds a little. The
# claude_cli backend uses 180s for the same shape — give the fuller skill prompt some headroom.
TIMEOUT_S = 300


def _default_runner(argv: list[str], input_text: str) -> tuple[int, str, str]:
    import subprocess
    proc = subprocess.run(argv, input=input_text, capture_output=True, text=True,
                          timeout=TIMEOUT_S)
    return proc.returncode, proc.stdout, proc.stderr


def _decision_addendum() -> str:
    """Turns the interactive skill into a single structured decision over PRE-COMPUTED data."""
    return """

# DECISION MODE (automated, headless)

You are running inside an automated trading system. Apply the full skill above to the ONE stock
in the user message, but:

1. The indicator JSON is ALREADY COMPUTED and included in the user message. Do NOT run the
   indicator tool or the movers screener — reason only over the JSON provided (you may still use
   web search for same-day news, which overrides the chart).
2. If the user message says a position is HELD, decide HOLD vs exit exactly per the skill's exit
   gate (SELL_NOW to close a long / BUY_NOW to cover a short) and re-quote stop_loss/target1 —
   stops ratchet toward profit, never loosen. If flat, decide the fresh entry action.
3. Derive trade_quality and confidence honestly on the skill's calibrated bands; no edge is
   WAIT / NO_TRADE, not a forced trade.
4. Levels follow the skill's TARGET LADDER: target1 = the PRACTICAL first objective
   (institutional_desk.risk_model.targets[0]); risk_reward = risk_model.rr_to_final_est — the
   geometry to the FINAL capped target, never a narrative number and never the R:R to target1.
5. Output ONLY the structured decision object matching the enforced JSON schema — the seven
   fields action/confidence/trade_quality/entry/stop_loss/target1/risk_reward. No prose, no
   trade summary, no explanation.
"""


class SkillDecisionEngine:
    """One `decide()` per symbol using the real intraday-analyst skill as the system prompt."""

    def __init__(self,
                 runner: Callable[[list[str], str], tuple[int, str, str]] = _default_runner,
                 use_web_search: bool = True, model: str = MODEL,
                 claude_bin: str | None = None, skill_path: str = SKILL_PATH):
        self.runner = runner
        self.use_web_search = use_web_search
        self.model = model
        self.claude_bin = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
        self.skill_path = skill_path
        self._system_prompt: str | None = None

    def _system(self) -> str:
        if self._system_prompt is None:
            if not os.path.exists(self.skill_path):
                raise DecisionEngineError(
                    f"intraday-analyst skill not found at {self.skill_path}")
            with open(self.skill_path, encoding="utf-8") as f:
                self._system_prompt = f.read() + _decision_addendum()
        return self._system_prompt

    def decide(self, symbol: str, indicators: dict, position: dict | None = None,
               book: dict | None = None):
        argv = [self.claude_bin, "-p", "--output-format", "json", "--model", self.model,
                "--append-system-prompt", self._system(),
                "--json-schema", json.dumps(DECISION_SCHEMA)]
        if self.use_web_search:
            argv += ["--allowedTools", "WebSearch"]
        user_message = build_user_message(symbol, indicators, position, book)
        try:
            rc, out, err = self.runner(argv, user_message)
        except Exception as e:
            raise DecisionEngineError(f"skill decision call failed for {symbol}: {e}") from e
        if rc != 0:
            raise DecisionEngineError(f"claude CLI exit {rc} for {symbol}: {err.strip()}")
        if not out or not out.strip():
            raise DecisionEngineError(f"claude CLI returned empty output for {symbol}")
        return _parse_decision(_result_text(out))
