"""Claude-CLI decision backend — runs the intraday decision through headless `claude -p`
(on the user's Claude subscription) instead of the raw Anthropic API. Same decide() interface
as DecisionEngine; reuses the shared engine prompt + parser. See
docs/superpowers/specs/2026-07-10-claude-cli-backend-design.md.

Note: when using this backend, do NOT set ANTHROPIC_API_KEY in the environment — its presence
makes `claude` bill the API instead of the subscription."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable

from decision_engine import (DECISION_SCHEMA, MODEL, DecisionEngineError, _parse_decision,
                             build_user_message)
from engine_prompt import ENGINE_PROMPT


def _default_runner(argv: list[str], input_text: str) -> tuple[int, str, str]:
    proc = subprocess.run(argv, input=input_text, capture_output=True, text=True, timeout=180)
    return proc.returncode, proc.stdout, proc.stderr


def _result_text(stdout: str) -> str:
    """Extract the model's answer from `claude -p --output-format json` stdout. Defensive:
    unwrap the JSON envelope's result field if present, else return stdout unchanged (the
    downstream _parse_decision tolerates a JSON object embedded in text either way)."""
    s = stdout.strip()
    try:
        env = json.loads(s)
    except json.JSONDecodeError:
        return s
    if isinstance(env, dict):
        for key in ("result", "text", "content"):
            v = env.get(key)
            if isinstance(v, str) and v.strip():
                return v
    return s


class ClaudeCliEngine:
    def __init__(self, runner: Callable[[list[str], str], tuple[int, str, str]] = _default_runner,
                 use_web_search: bool = True, model: str = MODEL, claude_bin: str | None = None):
        self.runner = runner
        self.use_web_search = use_web_search
        self.model = model
        self.claude_bin = claude_bin or os.environ.get("CLAUDE_BIN", "claude")

    def decide(self, symbol: str, indicators: dict, position: dict | None = None):
        argv = [self.claude_bin, "-p", "--output-format", "json", "--model", self.model,
                "--append-system-prompt", ENGINE_PROMPT,
                "--json-schema", json.dumps(DECISION_SCHEMA)]
        if self.use_web_search:
            argv += ["--allowedTools", "WebSearch"]
        user_message = build_user_message(symbol, indicators, position)
        try:
            rc, out, err = self.runner(argv, user_message)
        except Exception as e:
            raise DecisionEngineError(f"claude CLI call failed for {symbol}: {e}") from e
        if rc != 0:
            raise DecisionEngineError(f"claude CLI exit {rc} for {symbol}: {err.strip()}")
        if not out or not out.strip():
            raise DecisionEngineError(f"claude CLI returned empty output for {symbol}")
        return _parse_decision(_result_text(out))
