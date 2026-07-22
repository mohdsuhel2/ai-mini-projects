"""Skill-driven one-shot screening backend — ONE headless `claude -p` per cycle runs the FULL
intraday-analyst skill (movers screener + indicator tool via restricted Bash, optional web
search) and returns the top-5 candidates as typed Decisions. Selected by SCREEN_MODE=skill.
See docs/superpowers/specs/2026-07-20-skill-screen-design.md."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable, Sequence

from claude_cli_engine import _result_text
from decision_engine import MODEL, VALID_ACTIONS, Decision, DecisionEngineError

_STOCKANALYZE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze"
SKILL_PATH = os.path.expanduser("~/.claude/skills/intraday-analyst/SKILL.md")
# Agentic session: screener + indicator runs + reasoning (observed ~2 min live). An overrun
# past the 20-min cycle spacing only makes the overlap lock skip the next fire.
TIMEOUT_S = 1200
MAX_CANDIDATES = 5

_CANDIDATE_PROPS = {
    "symbol": {"type": "string"},
    "action": {"type": "string", "enum": list(VALID_ACTIONS)},
    "confidence": {"type": "integer"},
    "trade_quality": {"type": "integer"},
    "entry": {"type": ["number", "null"]},
    "stop_loss": {"type": ["number", "null"]},
    "target1": {"type": ["number", "null"]},
    "risk_reward": {"type": ["number", "null"]},
}
SCREEN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "candidates": {
            "type": "array",
            "maxItems": MAX_CANDIDATES,
            "items": {"type": "object", "additionalProperties": False,
                      "properties": _CANDIDATE_PROPS,
                      "required": list(_CANDIDATE_PROPS)},
        },
    },
    "required": ["candidates"],
}


class SkillScreenError(DecisionEngineError):
    """One-shot skill screen failed: missing SKILL.md, CLI error/timeout, or bad JSON."""


def _default_runner(argv: list[str], input_text: str) -> tuple[int, str, str]:
    proc = subprocess.run(argv, input=input_text, capture_output=True, text=True,
                          timeout=TIMEOUT_S)
    return proc.returncode, proc.stdout, proc.stderr


def _screener_cmd() -> str:
    py = os.environ.get("SCREENER_PYTHON", f"{_STOCKANALYZE}/.venv/bin/python")
    script = os.environ.get("SCREENER_SCRIPT", f"{_STOCKANALYZE}/groww_intraday_screener.py")
    return f"{py} {script}"


def _indicator_cmd() -> str:
    py = os.environ.get("INTRADAY_PYTHON", f"{_STOCKANALYZE}/.venv/bin/python")
    script = os.environ.get("INTRADAY_SCRIPT", f"{_STOCKANALYZE}/stock_analyze_intraday.py")
    return f"{py} {script}"


def _addendum() -> str:
    return f"""

# SCREENING MODE (automated, headless)

You are running inside an automated trading system. Apply the full skill above, but instead of
one requested symbol, produce the TOP INTRADAY CANDIDATES RIGHT NOW:

1. Run the movers screener BOTH directions:
   `{_screener_cmd()} --direction up --top 7 --min-price 50 --min-mcap-cr 1000`
   and the same with `--direction down`.
2. Run the indicator tool on each shortlisted name:
   `{_indicator_cmd()} -s <SYMBOL> 2>/dev/null`
   and apply the skill's full methodology to each.
3. Rank by trade-quality score and return AT MOST {MAX_CANDIDATES} candidates worth acting on
   NOW. Only include candidates whose action is BUY_NOW, BUY_ON_PULLBACK, BUY_ON_BREAKOUT or
   SHORT_NOW. Returning fewer — or an empty list — is the CORRECT answer when there is no edge.

Rules:
- Skip any symbol listed as excluded in the user message (already held).
- Derive every score honestly per the skill's bands; do not inflate to fill 5 slots.
- Final output: JSON only, matching the enforced schema. No prose.
"""


def _parse_screen(raw_text: str) -> list[tuple[str, Decision]]:
    try:
        obj = json.loads(raw_text)
    except json.JSONDecodeError:
        start, end = raw_text.find("{"), raw_text.rfind("}")
        if start == -1 or end <= start:
            raise SkillScreenError(f"no JSON object in screen reply: {raw_text[:200]!r}")
        try:
            obj = json.loads(raw_text[start:end + 1])
        except json.JSONDecodeError as e:
            raise SkillScreenError(f"could not parse screen JSON: {e}") from e
    if not isinstance(obj, dict) or not isinstance(obj.get("candidates"), list):
        raise SkillScreenError(f"screen reply missing 'candidates' list: {raw_text[:200]!r}")

    def num(v):
        return None if v is None else float(v)

    out: list[tuple[str, Decision]] = []
    for cand in obj["candidates"]:
        if not isinstance(cand, dict) or not isinstance(cand.get("symbol"), str) \
                or not cand["symbol"].strip():
            raise SkillScreenError(f"bad candidate entry: {cand!r}")
        if cand.get("action") not in VALID_ACTIONS:
            raise SkillScreenError(f"invalid action {cand.get('action')!r} for "
                                   f"{cand['symbol']}")
        out.append((cand["symbol"].strip().upper(), Decision(
            action=cand["action"], confidence=int(cand["confidence"]),
            trade_quality=int(cand["trade_quality"]), entry=num(cand["entry"]),
            stop_loss=num(cand["stop_loss"]), target1=num(cand["target1"]),
            risk_reward=num(cand["risk_reward"]), raw_response=json.dumps(cand))))
    return out


class SkillScreenEngine:
    """One `screen()` call per cycle; returns ranked (symbol, Decision) pairs."""

    def __init__(self, runner: Callable[[list[str], str], tuple[int, str, str]] = _default_runner,
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
                raise SkillScreenError(
                    f"intraday-analyst skill not found at {self.skill_path}")
            with open(self.skill_path, encoding="utf-8") as f:
                self._system_prompt = f.read() + _addendum()
        return self._system_prompt

    def _allowed_tools(self) -> str:
        tools = []
        if self.use_web_search:
            tools.append("WebSearch")
        tools.append(f"Bash({_screener_cmd()}:*)")
        tools.append(f"Bash({_indicator_cmd()}:*)")
        return ",".join(tools)

    def screen(self, exclude_symbols: Sequence[str] = ()) -> list[tuple[str, Decision]]:
        argv = [self.claude_bin, "-p", "--output-format", "json", "--model", self.model,
                "--append-system-prompt", self._system(),
                "--json-schema", json.dumps(SCREEN_SCHEMA),
                "--allowedTools", self._allowed_tools()]
        excluded = ", ".join(sorted(exclude_symbols)) or "none"
        user_message = (f"Find the top intraday candidates right now.\n"
                        f"Excluded symbols (already held — do not analyze or return): "
                        f"{excluded}")
        try:
            rc, out, err = self.runner(argv, user_message)
        except Exception as e:
            raise SkillScreenError(f"skill screen call failed: {e}") from e
        if rc != 0:
            raise SkillScreenError(f"claude CLI exit {rc}: {err.strip()[:300]}")
        if not out or not out.strip():
            raise SkillScreenError("claude CLI returned empty output")
        return _parse_screen(_result_text(out))
