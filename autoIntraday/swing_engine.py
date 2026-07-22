"""Swing holdings-analysis engine — ONE agentic `claude -p` call embeds the FULL swing-analyst
AND shortswing-analyst skills, analyzes every Groww holding on BOTH horizons, and returns a
HOLD / ADD / REDUCE / EXIT verdict per holding per horizon. Same pattern as skill_screen_engine.
Totally separate from the intraday trading loop. See
docs/superpowers/specs/2026-07-21-swing-page-design.md."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable, Sequence

from claude_cli_engine import _result_text
from decision_engine import MODEL, DecisionEngineError

_STOCKANALYZE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze"
SWING_SKILL_PATH = os.path.expanduser("~/.claude/skills/swing-analyst/SKILL.md")
SHORTSWING_SKILL_PATH = os.path.expanduser("~/.claude/skills/shortswing-analyst/SKILL.md")
# Agentic: a data-tool run + WebSearch per holding across ~40 names -> minutes. Generous cap.
TIMEOUT_S = 1800
VALID_ACTIONS = ("HOLD", "ADD", "REDUCE", "EXIT")

_LEG = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": list(VALID_ACTIONS)},
        "conviction": {"type": "integer"},
        "target": {"type": ["number", "null"]},
        "stop": {"type": ["number", "null"]},
        "rationale": {"type": "string"},
    },
    "required": ["action", "conviction", "target", "stop", "rationale"],
}
SWING_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {"type": "object", "additionalProperties": False,
                      "properties": {"symbol": {"type": "string"},
                                     "swing": _LEG, "shortswing": _LEG},
                      "required": ["symbol", "swing", "shortswing"]},
        },
    },
    "required": ["verdicts"],
}


ONE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"swing": _LEG, "shortswing": _LEG},
    "required": ["swing", "shortswing"],
}


class SwingEngineError(DecisionEngineError):
    """Swing holdings analysis failed: missing skill file, CLI error/timeout, or bad JSON."""


def _default_runner(argv: list[str], input_text: str) -> tuple[int, str, str]:
    proc = subprocess.run(argv, input=input_text, capture_output=True, text=True,
                          timeout=TIMEOUT_S)
    return proc.returncode, proc.stdout, proc.stderr


def _swing_cmd() -> str:
    py = os.environ.get("SWING_PYTHON", f"{_STOCKANALYZE}/.venv/bin/python")
    script = os.environ.get("SWING_SCRIPT", f"{_STOCKANALYZE}/stock_analyze.py")
    return f"{py} {script}"


def _shortswing_cmd() -> str:
    py = os.environ.get("SWING_PYTHON", f"{_STOCKANALYZE}/.venv/bin/python")
    script = os.environ.get("SHORTSWING_SCRIPT", f"{_STOCKANALYZE}/stock_analyze_shortswing.py")
    return f"{py} {script}"


def _addendum() -> str:
    return f"""

# HOLDINGS ANALYSIS MODE (automated, headless)

You are analyzing an existing PORTFOLIO, not screening for new trades. For EACH holding in the
user message, apply BOTH skills above and return a verdict per horizon:

- `swing`  = the swing-analyst horizon (~10-20% over days-to-a-month).
- `shortswing` = the shortswing-analyst horizon (3-5 trading days).

For each, run the matching data tool and reason per that skill:
- swing: `{_swing_cmd()} -s <SYMBOL> --json 2>/dev/null`
- shortswing: `{_shortswing_cmd()} -s <SYMBOL> 2>/dev/null`

Each verdict `action` MUST be one of: HOLD (keep as-is), ADD (accumulate more), REDUCE (trim
part), EXIT (sell out). Give `conviction` 0-100, a `target` and `stop` price (or null if not
applicable), and a one-line `rationale`. The holding's average buy price is provided — factor
the position's unrealized P&L into the hold/trim/exit judgement.

Rules:
- One verdict object per holding, both legs filled. If a symbol's data can't be fetched, omit
  it rather than guessing.
- Final output: JSON only, matching the enforced schema. No prose.
"""


def _num(v):
    return None if v is None else float(v)


def _leg(raw: dict) -> dict:
    if raw.get("action") not in VALID_ACTIONS:
        raise SwingEngineError(f"invalid action {raw.get('action')!r}")
    return {"action": raw["action"], "conviction": int(raw["conviction"]),
            "target": _num(raw.get("target")), "stop": _num(raw.get("stop")),
            "rationale": str(raw.get("rationale") or "")}


def _parse(raw_text: str) -> list[dict]:
    try:
        obj = json.loads(raw_text)
    except json.JSONDecodeError:
        start, end = raw_text.find("{"), raw_text.rfind("}")
        if start == -1 or end <= start:
            raise SwingEngineError(f"no JSON object in reply: {raw_text[:200]!r}")
        try:
            obj = json.loads(raw_text[start:end + 1])
        except json.JSONDecodeError as e:
            raise SwingEngineError(f"could not parse JSON: {e}") from e
    if not isinstance(obj, dict) or not isinstance(obj.get("verdicts"), list):
        raise SwingEngineError(f"reply missing 'verdicts' list: {raw_text[:200]!r}")
    out = []
    for v in obj["verdicts"]:
        if not isinstance(v, dict) or not isinstance(v.get("symbol"), str) \
                or not v["symbol"].strip():
            raise SwingEngineError(f"bad verdict entry: {v!r}")
        out.append({"symbol": v["symbol"].strip().upper(),
                    "swing": _leg(v.get("swing") or {}),
                    "shortswing": _leg(v.get("shortswing") or {})})
    return out


class SwingEngine:
    """One `analyze(holdings)` call -> per-holding verdicts on both swing horizons."""

    def __init__(self, runner: Callable[[list[str], str], tuple[int, str, str]] = _default_runner,
                 use_web_search: bool = True, model: str = MODEL, claude_bin: str | None = None,
                 swing_skill_path: str = SWING_SKILL_PATH,
                 shortswing_skill_path: str = SHORTSWING_SKILL_PATH):
        self.runner = runner
        self.use_web_search = use_web_search
        self.model = model
        self.claude_bin = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
        self.swing_skill_path = swing_skill_path
        self.shortswing_skill_path = shortswing_skill_path
        self._system_prompt: str | None = None

    def _system(self) -> str:
        if self._system_prompt is None:
            parts = []
            for path in (self.swing_skill_path, self.shortswing_skill_path):
                if not os.path.exists(path):
                    raise SwingEngineError(f"swing skill not found at {path}")
                with open(path, encoding="utf-8") as f:
                    parts.append(f.read())
            self._system_prompt = "\n\n".join(parts) + _addendum()
        return self._system_prompt

    def _allowed_tools(self) -> str:
        tools = ["WebSearch"] if self.use_web_search else []
        tools.append(f"Bash({_swing_cmd()}:*)")
        tools.append(f"Bash({_shortswing_cmd()}:*)")
        return ",".join(tools)

    def analyze_one(self, symbol: str, quantity=None, avg_price=None) -> dict:
        """Analyze ONE holding on both horizons — the per-stock primitive the job loops over so
        the UI can show progress. Returns {swing: leg, shortswing: leg}."""
        argv = [self.claude_bin, "-p", "--output-format", "json", "--model", self.model,
                "--append-system-prompt", self._system(),
                "--json-schema", json.dumps(ONE_SCHEMA),
                "--allowedTools", self._allowed_tools()]
        user_message = (f"Analyze this single holding on both swing horizons:\n"
                        f"- {symbol}: qty {quantity} @ avg ₹{avg_price}")
        try:
            rc, out, err = self.runner(argv, user_message)
        except Exception as e:
            raise SwingEngineError(f"swing analysis call failed for {symbol}: {e}") from e
        if rc != 0:
            raise SwingEngineError(f"claude CLI exit {rc} for {symbol}: {err.strip()[:300]}")
        if not out or not out.strip():
            raise SwingEngineError(f"claude CLI returned empty output for {symbol}")
        obj = _result_text(out)
        try:
            data = json.loads(obj)
        except json.JSONDecodeError:
            start, end = obj.find("{"), obj.rfind("}")
            if start == -1 or end <= start:
                raise SwingEngineError(f"no JSON in reply for {symbol}: {obj[:200]!r}")
            data = json.loads(obj[start:end + 1])
        if not isinstance(data, dict) or "swing" not in data or "shortswing" not in data:
            raise SwingEngineError(f"reply missing swing/shortswing for {symbol}: {obj[:200]!r}")
        return {"swing": _leg(data["swing"]), "shortswing": _leg(data["shortswing"])}

    def analyze(self, holdings: Sequence[dict]) -> list[dict]:
        argv = [self.claude_bin, "-p", "--output-format", "json", "--model", self.model,
                "--append-system-prompt", self._system(),
                "--json-schema", json.dumps(SWING_SCHEMA),
                "--allowedTools", self._allowed_tools()]
        lines = "\n".join(f"- {h['symbol']}: qty {h['quantity']} @ avg ₹{h['avg_price']}"
                          for h in holdings)
        user_message = ("Analyze these holdings on both swing horizons:\n" + (lines or "none"))
        try:
            rc, out, err = self.runner(argv, user_message)
        except Exception as e:
            raise SwingEngineError(f"swing analysis call failed: {e}") from e
        if rc != 0:
            raise SwingEngineError(f"claude CLI exit {rc}: {err.strip()[:300]}")
        if not out or not out.strip():
            raise SwingEngineError("claude CLI returned empty output")
        return _parse(_result_text(out))
