"""Bulk decision backend — decides a WHOLE provided candidate list in ONE headless `claude -p`
call, instead of one call per name. This is what makes Compare mode fast: a compare cycle drops
from ~(candidates x strategies) skill calls to ~1 call per strategy, while still deciding every
name on identical, pre-computed indicators. Same skill, same 7-field decision contract, same
scoring bands as the per-name SkillDecisionEngine. See
docs/superpowers/specs/2026-07-24-multi-strategy-compare-design.md."""
from __future__ import annotations

import json
import os
from typing import Callable, Sequence

from claude_cli_engine import _result_text
from decision_engine import MODEL, VALID_ACTIONS, Decision, DecisionEngineError, _as_float
from skill_screen_engine import SKILL_PATH

TIMEOUT_S = 600

_DECISION_PROPS = {
    "symbol": {"type": "string"},
    "action": {"type": "string", "enum": list(VALID_ACTIONS)},
    "confidence": {"type": "integer"},
    "trade_quality": {"type": "integer"},
    "entry": {"type": ["number", "null"]},
    "stop_loss": {"type": ["number", "null"]},
    "target1": {"type": ["number", "null"]},
    "risk_reward": {"type": ["number", "null"]},
}
BULK_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "decisions": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False,
                      "properties": _DECISION_PROPS, "required": list(_DECISION_PROPS)},
        },
    },
    "required": ["decisions"],
}


class BulkDecideError(DecisionEngineError):
    """Bulk decision call failed: missing skill, CLI error/timeout, or unparseable JSON."""


def _default_runner(argv: list[str], input_text: str) -> tuple[int, str, str]:
    import subprocess
    proc = subprocess.run(argv, input=input_text, capture_output=True, text=True, timeout=TIMEOUT_S)
    return proc.returncode, proc.stdout, proc.stderr


def _bulk_addendum() -> str:
    return """

# BULK DECISION MODE (automated, headless)

Apply the full skill above to EACH stock in the user message INDEPENDENTLY. Each stock's indicator
JSON is already provided — do NOT run the indicator tool or the screener. Return EXACTLY ONE
decision object per input symbol (use WAIT / NO_TRADE when there is no edge — NEVER omit a symbol,
NEVER invent a symbol that wasn't provided). Derive trade_quality/confidence honestly on the
skill's calibrated bands. Output ONLY the JSON matching the enforced schema:
{"decisions":[{symbol, action, confidence, trade_quality, entry, stop_loss, target1, risk_reward}]}.
No prose.
"""


class BulkDecideEngine:
    """One `decide_many()` call decides every provided (symbol, indicators) pair using the skill."""

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
                raise BulkDecideError(f"skill not found at {self.skill_path}")
            with open(self.skill_path, encoding="utf-8") as f:
                self._system_prompt = f.read() + _bulk_addendum()
        return self._system_prompt

    def decide_many(self, items: Sequence[dict]) -> dict[str, Decision]:
        """items: [{"symbol": str, "indicators": dict}]. Returns {symbol: Decision} for every
        symbol the model returned. Missing symbols simply aren't in the dict (caller falls back)."""
        items = [it for it in items if it.get("symbol") and it.get("indicators")]
        if not items:
            return {}
        argv = [self.claude_bin, "-p", "--output-format", "json", "--model", self.model,
                "--append-system-prompt", self._system(),
                "--json-schema", json.dumps(BULK_SCHEMA)]
        if self.use_web_search:
            argv += ["--allowedTools", "WebSearch"]
        payload = [{"symbol": it["symbol"], "indicators": it["indicators"]} for it in items]
        user_message = ("Decide the intraday trade for EACH of these stocks; return one decision "
                        "per symbol.\n\n" + json.dumps(payload, ensure_ascii=False, default=str))
        try:
            rc, out, err = self.runner(argv, user_message)
        except Exception as e:
            raise BulkDecideError(f"bulk decide call failed: {e}") from e
        if rc != 0:
            raise BulkDecideError(f"claude CLI exit {rc}: {err.strip()[:300]}")
        if not out or not out.strip():
            raise BulkDecideError("claude CLI returned empty output")
        return _parse_bulk(_result_text(out))


def _parse_bulk(raw_text: str) -> dict[str, Decision]:
    try:
        obj = json.loads(raw_text)
    except json.JSONDecodeError:
        start, end = raw_text.find("{"), raw_text.rfind("}")
        if start == -1 or end <= start:
            raise BulkDecideError(f"no JSON object in bulk reply: {raw_text[:200]!r}")
        try:
            obj = json.loads(raw_text[start:end + 1])
        except json.JSONDecodeError as e:
            raise BulkDecideError(f"could not parse bulk JSON: {e}") from e
    if not isinstance(obj, dict) or not isinstance(obj.get("decisions"), list):
        raise BulkDecideError(f"bulk reply missing 'decisions' list: {raw_text[:200]!r}")
    out: dict[str, Decision] = {}
    for c in obj["decisions"]:
        if not isinstance(c, dict) or not isinstance(c.get("symbol"), str):
            continue
        if c.get("action") not in VALID_ACTIONS:
            continue
        out[c["symbol"].strip().upper()] = Decision(
            action=c["action"], confidence=int(c["confidence"]), trade_quality=int(c["trade_quality"]),
            entry=_as_float(c["entry"]), stop_loss=_as_float(c["stop_loss"]),
            target1=_as_float(c["target1"]), risk_reward=_as_float(c["risk_reward"]),
            raw_response=json.dumps(c))
    return out
