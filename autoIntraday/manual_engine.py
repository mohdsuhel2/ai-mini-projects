"""Manual analysis runner — one headless `claude -p` per request that embeds ANY installed
SKILL.md and returns that skill's FULL narrative plus the actionable levels, for a human
reading the answer directly. Backs the dashboard's Analyze page.

Why generic rather than per-skill: skills are discovered from ~/.claude/skills at runtime
(see observe.available_skills), so a skill installed tomorrow must work here with no code
change. One schema every skill can fill — a free-text `verdict` in the skill's own
vocabulary, a markdown `report`, and levels that are nullable throughout because a scanner
legitimately produces none.

Like observe.py, this module never imports a broker client. The safety property is
structural, not a flag: there is no code path here that can place, modify or cancel an
order. See docs/superpowers/specs/2026-08-12-manual-analysis-page-design.md."""
from __future__ import annotations

import json
import os
import subprocess
from typing import Callable

from claude_cli_engine import _result_text
from decision_engine import MODEL, DecisionEngineError

_STOCKANALYZE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze"
SKILLS_DIR = os.path.expanduser("~/.claude/skills")
# A full agentic run: the skill's data tool, sometimes a screener, plus web search and
# reasoning. Observed live at ~2 min; this is generous headroom, not an expectation. One
# value covers both modes — the top-5 screen is a single call of the same shape.
TIMEOUT_S = 1200
MAX_PICKS = 5

_NUM = {"type": ["number", "null"]}
ITEM_PROPS = {
    "symbol": {"type": "string"},
    "report": {"type": "string"},
    "verdict": {"type": "string"},
    "conviction": {"type": ["integer", "null"]},
    "entry": _NUM, "stop": _NUM,
    "target1": _NUM, "target2": _NUM, "target3": _NUM,
    "risk_reward": _NUM,
    "summary": {"type": "string"},
}
_ITEM = {"type": "object", "additionalProperties": False,
         "properties": ITEM_PROPS, "required": list(ITEM_PROPS)}
ONE_SCHEMA = dict(_ITEM)
TOP5_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"picks": {"type": "array", "maxItems": MAX_PICKS, "items": _ITEM}},
    "required": ["picks"],
}


class ManualEngineError(DecisionEngineError):
    """A manual skill run failed: missing SKILL.md, CLI error/timeout, or an unusable reply."""


def _default_runner(argv: list[str], input_text: str) -> tuple[int, str, str]:
    proc = subprocess.run(argv, input=input_text, capture_output=True, text=True,
                          timeout=TIMEOUT_S)
    return proc.returncode, proc.stdout, proc.stderr


def _python_prefix() -> str:
    """The interpreter every skill's data tool runs under. One allowlist entry keyed on this
    prefix covers all of them, so a skill using a script we have never heard of still works."""
    return os.environ.get("SWING_PYTHON", f"{_STOCKANALYZE}/.venv/bin/python")


def _addendum(mode: str) -> str:
    target = ("the ONE stock named in the user message"
              if mode == "one" else
              "the market — pick your own names, exactly as the skill's screening mode does")
    extra = "" if mode == "one" else f"""
Return AT MOST {MAX_PICKS} picks in `picks`, best first. Fewer — or none at all — is a valid
answer when your own bar is not met; do NOT pad the list to reach five.
"""
    return f"""

# MANUAL ANALYSIS MODE (headless, operator-initiated)

A human operator has pointed this skill at {target} and will read your answer directly.
Apply the FULL skill above: run its data tools, follow its decision hierarchy, and use web
search for same-day news.

Return JSON matching the enforced schema:

- `report` — your COMPLETE analysis in markdown, at the depth you would give a person in
  conversation: the steps you ran, what the data showed, what you concluded and why. A human
  reads this, not a parser. Do not compress it to bullet fragments.
- `verdict` — your call in THIS skill's own vocabulary (BUY NOW, WAIT, HOLD, EXIT,
  IGNITION_BUY, whatever this skill natively emits).
- `conviction` — 0-100, or null if this skill does not express one.
- `entry`, `stop`, `target1`, `target2`, `target3`, `risk_reward` — the actionable levels.
  Use null for any level this skill does not produce. NEVER invent one to fill a field.
- `summary` — one line a trader can act on.
- `symbol` — the NSE symbol this item is about.
{extra}
No prose outside the JSON.
"""


def _num(v):
    return None if v is None else float(v)


def _parse_item(raw: dict, fallback_symbol: str = "") -> dict:
    if not isinstance(raw, dict):
        raise ManualEngineError(f"expected an object, got {type(raw).__name__}")
    report = str(raw.get("report") or "").strip()
    verdict = str(raw.get("verdict") or "").strip()
    if not report:
        raise ManualEngineError("reply has no `report` — nothing for the operator to read")
    if not verdict:
        raise ManualEngineError("reply has no `verdict`")
    conv = raw.get("conviction")
    return {"symbol": str(raw.get("symbol") or fallback_symbol).strip().upper(),
            "report": report, "verdict": verdict,
            "conviction": int(conv) if conv is not None else None,
            "entry": _num(raw.get("entry")), "stop": _num(raw.get("stop")),
            "target1": _num(raw.get("target1")), "target2": _num(raw.get("target2")),
            "target3": _num(raw.get("target3")),
            "risk_reward": _num(raw.get("risk_reward")),
            "summary": str(raw.get("summary") or "")}


def _loads(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ManualEngineError(f"no JSON object in reply: {text[:200]!r}")
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as e:
            raise ManualEngineError(f"could not parse JSON: {e}") from e


class ManualSkillEngine:
    """Runs ONE installed skill, either on a named symbol or as its own top-5 screen."""

    def __init__(self, skill_id: str,
                 runner: Callable[[list[str], str], tuple[int, str, str]] = _default_runner,
                 model: str = MODEL, claude_bin: str | None = None,
                 skills_dir: str = SKILLS_DIR, use_web_search: bool = True):
        self.skill_id = skill_id
        self.runner = runner
        self.model = model
        self.claude_bin = claude_bin or os.environ.get("CLAUDE_BIN", "claude")
        self.skills_dir = skills_dir
        self.use_web_search = use_web_search

    def _system(self, mode: str) -> str:
        path = os.path.join(self.skills_dir, self.skill_id, "SKILL.md")
        if not os.path.exists(path):
            raise ManualEngineError(f"skill not found at {path}")
        with open(path, encoding="utf-8") as f:
            return f.read() + _addendum(mode)

    def _allowed_tools(self) -> str:
        tools = [f"Bash({_python_prefix()}:*)"]
        if self.use_web_search:
            tools.append("WebSearch")
        return ",".join(tools)

    def _call(self, mode: str, schema: dict, user_message: str) -> tuple[str, dict]:
        argv = [self.claude_bin, "-p", "--output-format", "json", "--model", self.model,
                "--append-system-prompt", self._system(mode),
                "--json-schema", json.dumps(schema),
                "--allowedTools", self._allowed_tools()]
        try:
            rc, out, err = self.runner(argv, user_message)
        except Exception as e:                                       # noqa: BLE001
            raise ManualEngineError(f"{self.skill_id} call failed: {e}") from e
        if rc != 0:
            raise ManualEngineError(
                f"claude CLI exit {rc} for {self.skill_id}: {err.strip()[:300]}")
        if not out or not out.strip():
            raise ManualEngineError(f"claude CLI returned empty output for {self.skill_id}")
        return out, _loads(_result_text(out))

    def run_symbol(self, symbol: str, position: dict | None = None) -> dict:
        """Full analysis of ONE symbol. `position` (quantity/avg_price/product) is passed to
        the skill as held-position context so its exit logic can engage."""
        if position:
            ctx = (f"You currently HOLD this: {position.get('quantity')} @ avg "
                   f"₹{position.get('avg_price')} ({position.get('product') or 'MIS'}).")
        else:
            ctx = "You have no open position in this stock — judge it as a fresh entry."
        msg = f"Analyze {symbol.upper()} now.\n{ctx}"
        raw, obj = self._call("one", ONE_SCHEMA, msg)
        return dict(_parse_item(obj, symbol), raw=raw)

    def run_top5(self) -> list[dict]:
        """The skill's own screen — it picks the names. One call, up to MAX_PICKS items."""
        msg = (f"Run your screening mode and give me your top {MAX_PICKS} candidates right "
               "now, best first.")
        raw, obj = self._call("top5", TOP5_SCHEMA, msg)
        picks = obj.get("picks")
        if not isinstance(picks, list):
            raise ManualEngineError(f"reply missing 'picks' list: {str(obj)[:200]!r}")
        return [dict(_parse_item(p), raw=raw) for p in picks]
