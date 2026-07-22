# Decision Engine (Phase 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the LLM-based decision engine — `indicators.py` (fetches indicator JSON by running the sibling `StockAnalayze` intraday tool) and `decision_engine.py` (asks `claude-opus-4-8`, running the intraday-analyst engine with web search, for a typed `Decision`).

**Architecture:** `get_indicators(symbol)` shells out to the sibling project's `stock_analyze_intraday.py` and returns its JSON. `DecisionEngine(client_factory, use_web_search, model)` builds a frozen engine system prompt + a per-symbol user message, calls the Anthropic SDK with adaptive thinking, an optional web-search tool, and a JSON-schema structured-output constraint, handles `pause_turn`, and parses the reply into a `Decision`. All SDK and subprocess access goes through injectable seams so unit tests never hit the network or run the real script.

**Tech Stack:** Python 3.10+, `anthropic` (official SDK), standard-library `subprocess`/`json`, `pytest`. Reuses (does not vendor) the sibling `StockAnalayze` project's intraday script via its own venv.

## Global Constraints

- Model is `claude-opus-4-8` with `thinking={"type": "adaptive"}`. Do NOT set `temperature`/`top_p`/`top_k` (they 400 on Opus 4.8). Do NOT use `budget_tokens`.
- Credentials come only from the Anthropic SDK's standard resolution (`ANTHROPIC_API_KEY` env or an `ant` profile) — never hardcoded.
- `client_factory` (Anthropic client) and the indicator subprocess runner are injectable seams; unit tests use fakes and never hit the network or run the real script.
- Every error the modules raise is a `DecisionEngineError`; `IndicatorError` subclasses it.
- The engine never returns a default decision on failure — it raises. A fabricated decision is worse than a missed one.
- The engine system prompt is a single frozen text block carrying `cache_control: {"type": "ephemeral"}`.
- Web search uses tool type `web_search_20260209` (the current variant for Opus 4.8).

---

### Task 1: Scaffolding — `DecisionEngineError`, `Decision`, `IndicatorError`

**Files:**
- Create: `requirements.txt` (append if it exists)
- Create: `decision_engine.py`
- Create: `indicators.py`
- Test: `tests/test_decision_engine.py`

**Interfaces:**
- Produces: `DecisionEngineError(Exception)` (in `decision_engine.py`); `IndicatorError(DecisionEngineError)` (in `indicators.py`); `Decision` dataclass with fields `action: str, confidence: int, trade_quality: int, entry, stop_loss, target1, target2, target3, risk_reward, expected_move_pct` (all `float | None`), `invalidation: str, rationale: str, news_catalyst: str | None, raw_response: str`; `VALID_ACTIONS` tuple.

- [ ] **Step 1: Ensure `anthropic` is a dependency**

The repo already has a `requirements.txt` from Phase 1. Append `anthropic` on its own line if not present, then install:

```bash
.venv/bin/pip install anthropic
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_decision_engine.py`:

```python
import pytest

from decision_engine import Decision, DecisionEngineError, VALID_ACTIONS
from indicators import IndicatorError


def test_valid_actions_present():
    assert "BUY_NOW" in VALID_ACTIONS
    assert "WAIT" in VALID_ACTIONS
    assert "NO_TRADE" in VALID_ACTIONS
    assert "HOLD" in VALID_ACTIONS


def test_decision_dataclass_fields():
    d = Decision(action="WAIT", confidence=40, trade_quality=50, entry=None,
                 stop_loss=None, target1=None, target2=None, target3=None,
                 risk_reward=None, expected_move_pct=None, invalidation="n/a",
                 rationale="no edge", news_catalyst=None, raw_response="{}")
    assert d.action == "WAIT"
    assert d.confidence == 40
    assert d.entry is None


def test_indicator_error_is_decision_engine_error():
    assert issubclass(IndicatorError, DecisionEngineError)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_decision_engine.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'decision_engine'`

- [ ] **Step 4: Write minimal implementation**

Create `decision_engine.py`:

```python
"""LLM-based intraday decision engine — asks claude-opus-4-8 (running the intraday-analyst
20-step institutional engine, with web search) for a typed trading Decision on one symbol.

Mirrors the `intraday-analyst` skill: indicators are computed by a Python tool (see
indicators.py) and Claude reasons over them. See
docs/superpowers/specs/2026-07-09-decision-engine-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass

MODEL = "claude-opus-4-8"

VALID_ACTIONS = (
    "BUY_NOW", "BUY_ON_PULLBACK", "BUY_ON_BREAKOUT",
    "SELL_NOW", "SHORT_NOW", "HOLD", "WAIT", "NO_TRADE",
)


class DecisionEngineError(Exception):
    """Wraps every error the decision engine raises: API, parsing, indicator fetch."""


@dataclass
class Decision:
    action: str
    confidence: int
    trade_quality: int
    entry: float | None
    stop_loss: float | None
    target1: float | None
    target2: float | None
    target3: float | None
    risk_reward: float | None
    expected_move_pct: float | None
    invalidation: str
    rationale: str
    news_catalyst: str | None
    raw_response: str
```

Create `indicators.py`:

```python
"""Indicator provider — runs the sibling StockAnalayze intraday tool and returns its JSON.

Does not recompute indicators; shells out to stock_analyze_intraday.py exactly as the
intraday-analyst skill does. See docs/superpowers/specs/2026-07-09-decision-engine-design.md.
"""
from __future__ import annotations

from decision_engine import DecisionEngineError


class IndicatorError(DecisionEngineError):
    """Indicator fetch failed: non-zero exit, empty output, or unparseable JSON."""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_decision_engine.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add requirements.txt decision_engine.py indicators.py tests/test_decision_engine.py
git commit -m "Scaffold decision engine: Decision, DecisionEngineError, IndicatorError"
```

---

### Task 2: Indicator provider — `get_indicators` subprocess adapter

**Files:**
- Modify: `indicators.py`
- Test: `tests/test_indicators.py`

**Interfaces:**
- Consumes: `IndicatorError`.
- Produces: `DEFAULT_PYTHON`, `DEFAULT_SCRIPT` module constants (from env `INTRADAY_PYTHON`/`INTRADAY_SCRIPT` with the StockAnalayze defaults); `get_indicators(symbol: str, runner: Callable[[list[str], str], tuple[int, str, str]] = _default_runner) -> dict`. `runner(argv, cwd)` returns `(returncode, stdout, stderr)`; `_default_runner` uses `subprocess.run`. `get_indicators` raises `IndicatorError` on non-zero return, empty stdout, or `json.JSONDecodeError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_indicators.py`:

```python
import json

import pytest

from indicators import get_indicators, IndicatorError


def _fake_runner_factory(returncode, stdout, stderr=""):
    calls = {}

    def runner(argv, cwd):
        calls["argv"] = argv
        calls["cwd"] = cwd
        return (returncode, stdout, stderr)

    runner.calls = calls
    return runner


def test_get_indicators_parses_json():
    payload = {"symbol": "RELIANCE", "price": {"last": 2456.7}}
    runner = _fake_runner_factory(0, json.dumps(payload))
    result = get_indicators("RELIANCE", runner=runner)
    assert result == payload
    # symbol is passed with -s and the yahoo source is forced
    assert "-s" in runner.calls["argv"]
    assert "RELIANCE" in runner.calls["argv"]
    assert "yahoo" in runner.calls["argv"]


def test_get_indicators_nonzero_exit_raises():
    runner = _fake_runner_factory(1, "", "no intraday data")
    with pytest.raises(IndicatorError, match="exit"):
        get_indicators("BADSYM", runner=runner)


def test_get_indicators_empty_stdout_raises():
    runner = _fake_runner_factory(0, "   ")
    with pytest.raises(IndicatorError, match="empty"):
        get_indicators("RELIANCE", runner=runner)


def test_get_indicators_bad_json_raises():
    runner = _fake_runner_factory(0, "not json {")
    with pytest.raises(IndicatorError, match="parse|JSON"):
        get_indicators("RELIANCE", runner=runner)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_indicators.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_indicators'`

- [ ] **Step 3: Implement**

Add to `indicators.py`:

```python
import json
import os
import subprocess
from typing import Any, Callable

_STOCKANALYZE = "/Users/mohdsuhel/ai-mini-projects/StockAnalayze"
DEFAULT_PYTHON = os.environ.get("INTRADAY_PYTHON", f"{_STOCKANALYZE}/.venv/bin/python")
DEFAULT_SCRIPT = os.environ.get("INTRADAY_SCRIPT", f"{_STOCKANALYZE}/stock_analyze_intraday.py")


def _default_runner(argv: list[str], cwd: str) -> tuple[int, str, str]:
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=120)
    return proc.returncode, proc.stdout, proc.stderr


def get_indicators(symbol: str,
                   runner: Callable[[list[str], str], tuple[int, str, str]] = _default_runner
                   ) -> dict[str, Any]:
    argv = [DEFAULT_PYTHON, DEFAULT_SCRIPT, "-s", symbol, "--source", "yahoo"]
    cwd = os.path.dirname(DEFAULT_SCRIPT)
    returncode, stdout, stderr = runner(argv, cwd)
    if returncode != 0:
        raise IndicatorError(f"indicator tool exit {returncode} for {symbol}: {stderr.strip()}")
    if not stdout or not stdout.strip():
        raise IndicatorError(f"indicator tool returned empty output for {symbol}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        raise IndicatorError(f"could not parse indicator JSON for {symbol}: {e}") from e
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_indicators.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add indicators.py tests/test_indicators.py
git commit -m "Add get_indicators subprocess adapter"
```

---

### Task 3: Engine prompt + message/schema builders

**Files:**
- Create: `engine_prompt.py`
- Modify: `decision_engine.py`
- Test: `tests/test_decision_engine.py`

**Interfaces:**
- Consumes: `Decision`, `VALID_ACTIONS`.
- Produces: `ENGINE_PROMPT: str` (in `engine_prompt.py`); `DECISION_SCHEMA: dict` (JSON schema matching `Decision`'s decision fields — excludes `raw_response`); `build_system_blocks() -> list[dict]` (one text block carrying the engine prompt with `cache_control`); `build_user_message(symbol, indicators, position) -> str` (embeds the indicator JSON and a position-context line); `_position_line(position: dict | None) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_decision_engine.py`:

```python
import json as _json

from decision_engine import (build_system_blocks, build_user_message, DECISION_SCHEMA,
                             _position_line)


def test_system_block_has_cache_control_and_engine():
    blocks = build_system_blocks()
    assert len(blocks) == 1
    assert blocks[0]["type"] == "text"
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "intraday" in blocks[0]["text"].lower()


def test_user_message_embeds_indicator_json_and_symbol():
    indicators = {"symbol": "RELIANCE", "price": {"last": 2456.7}}
    msg = build_user_message("RELIANCE", indicators, position=None)
    assert "RELIANCE" in msg
    assert "2456.7" in msg
    assert "no position" in msg.lower()


def test_user_message_reflects_held_position():
    msg = build_user_message("TCS", {"symbol": "TCS"},
                             position={"quantity": 10, "entry_price": 3800.0,
                                       "side": "LONG", "unrealized_pnl_pct": 1.2})
    assert "10" in msg
    assert "3800" in msg
    assert "LONG" in msg


def test_decision_schema_matches_action_enum():
    props = DECISION_SCHEMA["properties"]
    assert set(props["action"]["enum"]) == set(VALID_ACTIONS)
    assert DECISION_SCHEMA["additionalProperties"] is False
    assert "raw_response" not in props  # audit field is not model-produced
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_decision_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_system_blocks'`

- [ ] **Step 3: Create `engine_prompt.py`**

```python
"""Frozen system prompt: the intraday-analyst 20-step institutional decision engine,
adapted for a structured-output API call. Kept in its own module so it stays byte-stable
(prompt-cache friendly)."""

ENGINE_PROMPT = """\
ROLE — Institutional Intraday Trading Decision Engine.
You are a professional proprietary/hedge-fund intraday trader. Make the highest-probability,
highest-expected-value decision on ONE Indian-market stock for a full-day intraday hold
(enter now, square off by ~3:20 PM IST). Prioritize making money over describing technicals.
Never force a trade — if there is no edge, return WAIT or NO_TRADE and say why. Maximize
expected value, not trade frequency. Never manufacture data; use only the indicator JSON
provided and any same-day news you find via web search (fresh news overrides the chart).

You are given a JSON block of computed indicators for the stock (VWAP, opening range, prior-day
pivots/levels, gap, RVOL, RSI/MACD/ATR, EMA 9/20/50/200 + alignment, ADX +DI/-DI, SuperTrend,
Bollinger, intraday structure + directional_bias, breakout state, reversal_watch, higher-
timeframe trends + overall bias, India-VIX/NIFTY context, session/bars_remaining, ATR
projection) plus the caller's current position in the stock (or none).

Work the standard engine: (1) market regime, (2) higher-timeframe bias, (3) market structure
[read intraday_structure FIRST — lower highs after the day high is a downtrend even above VWAP],
(4) trend strength [ADX], (5) volume [RVOL; a per-bar volume climax at a new high is a blow-off,
do not chase], (6) smart-money read, (7) intraday context vs VWAP, (8) momentum [never buy on
low RSI alone / sell on high RSI alone in a strong trend], (9) key levels [short the break of
support, not support itself], (10) news [web search same-day catalysts], (11) trade-quality
score /100, (12) probabilities [derive from the score, mark est.], (13) risk engine [ATR +
structural stop, targets capped by the ATR projection], (14) pick ONE action, (15) timing
[respect bars_remaining; no fresh entry after ~15:20], (16) invalidation, (17) exit plan,
(18) hard behaviour rules, (19) confidence.

Direction/breakout gates: use intraday_structure.directional_bias and the breakout state to
choose entry-now vs pullback vs breakout vs short-the-breakdown; do not tell the user to buy a
pullback to a level price just broke out of. If the caller holds a position, decide HOLD vs an
exit (SELL_NOW/target/stop) rather than a fresh entry.

Output ONLY the structured decision object. action is one of: BUY_NOW, BUY_ON_PULLBACK,
BUY_ON_BREAKOUT, SELL_NOW, SHORT_NOW, HOLD, WAIT, NO_TRADE. confidence and trade_quality are
0-100 integers. entry/stop_loss/target1..3/risk_reward/expected_move_pct are numbers (or null
when not applicable, e.g. WAIT/NO_TRADE). invalidation and rationale are short strings.
news_catalyst is a one-line same-day finding or null. All prices in INR. This is educational
output, not financial advice.
"""
```

- [ ] **Step 4: Implement builders in `decision_engine.py`**

Add `from engine_prompt import ENGINE_PROMPT` at the top, then:

```python
import json

DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": list(VALID_ACTIONS)},
        "confidence": {"type": "integer"},
        "trade_quality": {"type": "integer"},
        "entry": {"type": ["number", "null"]},
        "stop_loss": {"type": ["number", "null"]},
        "target1": {"type": ["number", "null"]},
        "target2": {"type": ["number", "null"]},
        "target3": {"type": ["number", "null"]},
        "risk_reward": {"type": ["number", "null"]},
        "expected_move_pct": {"type": ["number", "null"]},
        "invalidation": {"type": "string"},
        "rationale": {"type": "string"},
        "news_catalyst": {"type": ["string", "null"]},
    },
    "required": ["action", "confidence", "trade_quality", "entry", "stop_loss", "target1",
                 "target2", "target3", "risk_reward", "expected_move_pct", "invalidation",
                 "rationale", "news_catalyst"],
}


def build_system_blocks() -> list[dict]:
    return [{"type": "text", "text": ENGINE_PROMPT, "cache_control": {"type": "ephemeral"}}]


def _position_line(position: dict | None) -> str:
    if not position:
        return "Current position: none (flat)."
    return (f"Current position: {position.get('side', '?')} "
            f"{position.get('quantity', '?')} @ {position.get('entry_price', '?')}, "
            f"unrealized P&L {position.get('unrealized_pnl_pct', '?')}%.")


def build_user_message(symbol: str, indicators: dict, position: dict | None) -> str:
    return (f"Decide the intraday trade for {symbol}.\n\n"
            f"{_position_line(position)}\n\n"
            f"Indicator JSON:\n{json.dumps(indicators, ensure_ascii=False, default=str)}")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_decision_engine.py -v`
Expected: PASS (7 tests total in this file)

- [ ] **Step 6: Commit**

```bash
git add engine_prompt.py decision_engine.py tests/test_decision_engine.py
git commit -m "Add engine prompt, decision schema, and message builders"
```

---

### Task 4: `_parse_decision` — turn a model reply into a `Decision`

**Files:**
- Modify: `decision_engine.py`
- Test: `tests/test_decision_engine.py`

**Interfaces:**
- Consumes: `Decision`, `VALID_ACTIONS`, `DecisionEngineError`.
- Produces: `_extract_json(text: str) -> dict` (parse a JSON object, tolerating leading/trailing prose by taking the first `{`…last `}` span — supports the structured-output path AND the free-text fallback); `_parse_decision(raw_text: str) -> Decision` (validates `action` against `VALID_ACTIONS`, coerces `confidence`/`trade_quality` to int and price fields to `float | None`, sets `raw_response=raw_text`, raises `DecisionEngineError` on bad action or missing required keys).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_decision_engine.py`:

```python
from decision_engine import _parse_decision

_GOOD = _json.dumps({
    "action": "BUY_NOW", "confidence": 78, "trade_quality": 82, "entry": 2456.7,
    "stop_loss": 2440.0, "target1": 2480.0, "target2": 2500.0, "target3": None,
    "risk_reward": 2.1, "expected_move_pct": 1.3, "invalidation": "15m close below VWAP",
    "rationale": "fresh OR breakout on 2x RVOL, HTF up", "news_catalyst": None})


def test_parse_decision_wellformed():
    d = _parse_decision(_GOOD)
    assert d.action == "BUY_NOW"
    assert d.confidence == 78
    assert d.entry == 2456.7
    assert d.target3 is None
    assert d.raw_response == _GOOD


def test_parse_decision_tolerates_surrounding_prose():
    d = _parse_decision("Here is my call:\n" + _GOOD + "\nSquare off by 3:20.")
    assert d.action == "BUY_NOW"


def test_parse_decision_bad_action_raises():
    bad = _json.dumps({**_json.loads(_GOOD), "action": "YOLO"})
    with pytest.raises(DecisionEngineError, match="action"):
        _parse_decision(bad)


def test_parse_decision_missing_key_raises():
    obj = _json.loads(_GOOD)
    del obj["invalidation"]
    with pytest.raises(DecisionEngineError, match="missing|invalidation"):
        _parse_decision(_json.dumps(obj))


def test_parse_decision_no_json_raises():
    with pytest.raises(DecisionEngineError, match="JSON|parse"):
        _parse_decision("I cannot decide today.")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_decision_engine.py -v`
Expected: FAIL — `ImportError: cannot import name '_parse_decision'`

- [ ] **Step 3: Implement**

Add to `decision_engine.py`:

```python
_REQUIRED_KEYS = DECISION_SCHEMA["required"]


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise DecisionEngineError(f"no JSON object found in model reply: {text[:200]!r}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise DecisionEngineError(f"could not parse JSON from model reply: {e}") from e


def _as_float(value) -> float | None:
    return None if value is None else float(value)


def _parse_decision(raw_text: str) -> Decision:
    obj = _extract_json(raw_text)
    missing = [k for k in _REQUIRED_KEYS if k not in obj]
    if missing:
        raise DecisionEngineError(f"model reply missing keys: {missing}")
    if obj["action"] not in VALID_ACTIONS:
        raise DecisionEngineError(f"invalid action {obj['action']!r}")
    return Decision(
        action=obj["action"], confidence=int(obj["confidence"]),
        trade_quality=int(obj["trade_quality"]), entry=_as_float(obj["entry"]),
        stop_loss=_as_float(obj["stop_loss"]), target1=_as_float(obj["target1"]),
        target2=_as_float(obj["target2"]), target3=_as_float(obj["target3"]),
        risk_reward=_as_float(obj["risk_reward"]),
        expected_move_pct=_as_float(obj["expected_move_pct"]),
        invalidation=str(obj["invalidation"]), rationale=str(obj["rationale"]),
        news_catalyst=obj["news_catalyst"], raw_response=raw_text)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_decision_engine.py -v`
Expected: PASS (12 tests total in this file)

- [ ] **Step 5: Commit**

```bash
git add decision_engine.py tests/test_decision_engine.py
git commit -m "Add _parse_decision (structured + free-text-fallback JSON parsing)"
```

---

### Task 5: `DecisionEngine.decide` — the Claude call

**Files:**
- Modify: `decision_engine.py`
- Test: `tests/test_decision_engine.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `_default_client_factory() -> Any` (builds `anthropic.Anthropic()`); `DecisionEngine.__init__(self, client_factory=_default_client_factory, use_web_search=True, model=MODEL, max_continuations=4)`; `DecisionEngine.decide(self, symbol, indicators, position=None) -> Decision`. `decide` builds the request (system blocks, user message, `thinking={"type": "adaptive"}`, `output_config` with `DECISION_SCHEMA`, web-search tool when enabled), calls `self._client.messages.create(...)`, loops on `stop_reason == "pause_turn"` up to `max_continuations`, extracts the final text, and returns `_parse_decision(text)`. Any SDK exception is wrapped as `DecisionEngineError`. `_final_text(response) -> str` concatenates the response's `text`-type content blocks.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_decision_engine.py`:

```python
from decision_engine import DecisionEngine, MODEL


class _Block:
    def __init__(self, type_, text=None):
        self.type = type_
        self.text = text


class _Resp:
    def __init__(self, content, stop_reason="end_turn"):
        self.content = content
        self.stop_reason = stop_reason


class _FakeMessages:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeClient:
    def __init__(self, responses):
        self.messages = _FakeMessages(responses)


def _engine(responses, **kw):
    client = _FakeClient(responses)
    eng = DecisionEngine(client_factory=lambda: client, **kw)
    return eng, client


def test_decide_returns_parsed_decision():
    eng, client = _engine([_Resp([_Block("text", _GOOD)])])
    d = eng.decide("RELIANCE", {"symbol": "RELIANCE"})
    assert d.action == "BUY_NOW"
    req = client.messages.calls[0]
    assert req["model"] == MODEL
    assert req["thinking"] == {"type": "adaptive"}
    assert any(t["type"] == "web_search_20260209" for t in req["tools"])


def test_decide_without_web_search_omits_tool():
    eng, client = _engine([_Resp([_Block("text", _GOOD)])], use_web_search=False)
    eng.decide("RELIANCE", {"symbol": "RELIANCE"})
    assert "tools" not in client.messages.calls[0] or client.messages.calls[0]["tools"] == []


def test_decide_continues_on_pause_turn():
    eng, client = _engine([
        _Resp([_Block("server_tool_use")], stop_reason="pause_turn"),
        _Resp([_Block("text", _GOOD)], stop_reason="end_turn"),
    ])
    d = eng.decide("RELIANCE", {"symbol": "RELIANCE"})
    assert d.action == "BUY_NOW"
    assert len(client.messages.calls) == 2


def test_decide_pause_turn_exhaustion_raises():
    responses = [_Resp([_Block("server_tool_use")], stop_reason="pause_turn")
                 for _ in range(10)]
    eng, client = _engine(responses, max_continuations=3)
    with pytest.raises(DecisionEngineError, match="pause"):
        eng.decide("RELIANCE", {"symbol": "RELIANCE"})


def test_decide_wraps_sdk_error():
    class Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("api down")
    eng = DecisionEngine(client_factory=lambda: Boom())
    with pytest.raises(DecisionEngineError, match="decision call failed"):
        eng.decide("RELIANCE", {"symbol": "RELIANCE"})
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_decision_engine.py -v`
Expected: FAIL — `ImportError: cannot import name 'DecisionEngine'`

- [ ] **Step 3: Implement**

Add to `decision_engine.py`:

```python
from typing import Any, Callable

_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}


def _default_client_factory() -> Any:
    import anthropic
    return anthropic.Anthropic()


class DecisionEngine:
    def __init__(self, client_factory: Callable[[], Any] = _default_client_factory,
                 use_web_search: bool = True, model: str = MODEL,
                 max_continuations: int = 4):
        self._client = client_factory()
        self.use_web_search = use_web_search
        self.model = model
        self.max_continuations = max_continuations

    @staticmethod
    def _final_text(response: Any) -> str:
        return "".join(b.text for b in response.content
                       if getattr(b, "type", None) == "text" and getattr(b, "text", None))

    def decide(self, symbol: str, indicators: dict, position: dict | None = None) -> Decision:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 8000,
            "thinking": {"type": "adaptive"},
            "system": build_system_blocks(),
            "output_config": {"format": {"type": "json_schema", "schema": DECISION_SCHEMA}},
            "messages": [{"role": "user",
                          "content": build_user_message(symbol, indicators, position)}],
        }
        if self.use_web_search:
            kwargs["tools"] = [_WEB_SEARCH_TOOL]
        try:
            response = self._client.messages.create(**kwargs)
            continuations = 0
            while getattr(response, "stop_reason", None) == "pause_turn":
                if continuations >= self.max_continuations:
                    raise DecisionEngineError(
                        f"web-search pause_turn not resolved after {self.max_continuations} "
                        f"continuations for {symbol}")
                kwargs["messages"] = [
                    kwargs["messages"][0],
                    {"role": "assistant", "content": response.content},
                ]
                response = self._client.messages.create(**kwargs)
                continuations += 1
        except DecisionEngineError:
            raise
        except Exception as e:
            raise DecisionEngineError(f"decision call failed for {symbol}: {e}") from e
        return _parse_decision(self._final_text(response))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_decision_engine.py -v`
Expected: PASS (17 tests total in this file)

- [ ] **Step 5: Commit**

```bash
git add decision_engine.py tests/test_decision_engine.py
git commit -m "Add DecisionEngine.decide with web search and pause_turn handling"
```

---

### Task 6: Manual smoke script (real Opus 4.8 call) + verify combo + README

**Files:**
- Create: `scripts/smoke_test_decision.py`
- Modify: `decision_engine.py` (only if the smoke test shows web-search + `output_config.format` do NOT compose — see Step 2)
- Modify: `README.md`

**Interfaces:**
- Consumes: `DecisionEngine`, `get_indicators`.
- Produces: nothing new for later phases; validates the real-API assumptions and documents usage.

- [ ] **Step 1: Write the smoke script**

Create `scripts/smoke_test_decision.py`:

```python
#!/usr/bin/env python3
"""Manual, not-CI smoke test: fetch real indicators for one symbol and make a real
claude-opus-4-8 decision call (with web search). Verifies the whole Phase 3 path end to end.

Usage: .venv/bin/python scripts/smoke_test_decision.py RELIANCE
Requires ANTHROPIC_API_KEY (or an `ant` login) and the sibling StockAnalayze venv.
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from decision_engine import DecisionEngine, DecisionEngineError
from indicators import get_indicators


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    try:
        print(f"fetching indicators for {symbol} ...")
        indicators = get_indicators(symbol)
        print("indicators: OK")
        engine = DecisionEngine(use_web_search=True)
        decision = engine.decide(symbol, indicators)
        print(f"decide: OK -> {decision.action} "
              f"(confidence {decision.confidence}, quality {decision.trade_quality})")
        print(f"  entry={decision.entry} stop={decision.stop_loss} "
              f"t1={decision.target1} R:R={decision.risk_reward}")
        print(f"  rationale: {decision.rationale}")
        print(f"  news: {decision.news_catalyst}")
    except DecisionEngineError as e:
        print(f"FAILED: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the smoke test against the real API and verify the web-search + structured-output combination**

```bash
.venv/bin/python scripts/smoke_test_decision.py RELIANCE
```

Expected: `indicators: OK` then `decide: OK -> <ACTION> ...`. This confirms the assumption in the spec that the `web_search_20260209` tool and `output_config.format` structured output compose in one call.

**If instead the call errors specifically because `output_config.format` and the web-search tool cannot be combined** (a 400 about `output_config`/tools incompatibility): apply the documented fallback in `decision_engine.py` — remove the `output_config` key from `decide`'s `kwargs`, and add one line to the end of `ENGINE_PROMPT` in `engine_prompt.py`: `"Return ONLY a single JSON object with exactly these keys and no other text."` `_parse_decision`/`_extract_json` already tolerate a JSON object embedded in text, so no parsing change is needed. Re-run the full test suite (`.venv/bin/python -m pytest -q`) to confirm the unit tests still pass (they assert on request kwargs; if you removed `output_config`, update `test_decide_returns_parsed_decision` to not assert its presence), then re-run the smoke test. Document in your report exactly what you changed and why. Do NOT guess — only apply the fallback if the real call proves the combination fails.

- [ ] **Step 3: Add a Phase 3 section to `README.md`**

Append to `README.md`:

```markdown
## Phase 3: Decision engine

`decision_engine.py` asks `claude-opus-4-8` (running the intraday-analyst 20-step engine,
with web search for same-day catalysts) for a typed `Decision` on one stock. `indicators.py`
supplies the technical indicators by running the sibling `StockAnalayze` intraday tool. See
`docs/superpowers/specs/2026-07-09-decision-engine-design.md`.

### Setup

Requires `ANTHROPIC_API_KEY` (or an `ant auth login` profile) and the sibling `StockAnalayze`
project's venv. Override the indicator tool location with `INTRADAY_PYTHON` / `INTRADAY_SCRIPT`
if it lives elsewhere.

### Test

\`\`\`bash
.venv/bin/python -m pytest tests/test_decision_engine.py tests/test_indicators.py -v
\`\`\`

### Verify end to end (real API call on one symbol)

\`\`\`bash
.venv/bin/python scripts/smoke_test_decision.py RELIANCE
\`\`\`
```

- [ ] **Step 4: Commit**

```bash
git add scripts/smoke_test_decision.py README.md decision_engine.py engine_prompt.py tests/
git commit -m "Add decision-engine smoke test, verify web-search+structured-output, README"
```

---

## Self-Review Notes

- **Spec coverage:** indicator provider via sibling-venv subprocess with env-overridable paths (Task 2) · engine system prompt frozen + cache_control (Task 3) · structured-output schema matching `Decision` (Task 3) · position-aware user message (Task 3) · decision parsing with free-text fallback (Task 4) · Opus 4.8 + adaptive thinking + web search + pause_turn handling (Task 5) · single `DecisionEngineError`/`IndicatorError`, raises-not-defaults, `client_factory` seam, no sampling params (throughout) · unit tests mocking both the SDK and subprocess boundaries + a real-API smoke test that verifies the web-search/structured-output combination with a documented fallback (Task 6). All spec sections map to a task.
- **Type consistency:** `Decision` fields defined once (Task 1) and constructed once in `_parse_decision` (Task 4); `DECISION_SCHEMA.required` drives `_parse_decision`'s missing-key check; `build_system_blocks`/`build_user_message`/`_parse_decision`/`decide` signatures match across tasks; the web-search tool type string `web_search_20260209` is identical in `decide` and its test.
- **No placeholders:** every step has complete, runnable code and exact expected test counts (3 → +4 indicators → 7 → 12 → 17 decision-engine). The one real unknown (web-search + structured-output composition) is an explicit real-API verification in Task 6 with a precise, documented fallback — not a silent assumption.
