"""LLM-based intraday decision engine — asks claude-opus-4-8 (running the intraday-analyst
20-step institutional engine, with web search) for a typed trading Decision on one symbol.

Mirrors the `intraday-analyst` skill: indicators are computed by a Python tool (see
indicators.py) and Claude reasons over them. See
docs/superpowers/specs/2026-07-09-decision-engine-design.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

from engine_prompt import ENGINE_PROMPT

MODEL = "claude-opus-4-8"

VALID_ACTIONS = (
    "BUY_NOW", "BUY_ON_PULLBACK", "BUY_ON_BREAKOUT",
    "SELL_NOW", "SHORT_NOW", "HOLD", "WAIT", "NO_TRADE",
)


class DecisionEngineError(Exception):
    """Wraps every error the decision engine raises: API, parsing, indicator fetch."""


@dataclass
class Decision:
    """Compact intraday decision — only the fields the orchestrator acts on (gate, sizing, OCO).
    No prose (rationale/invalidation/news) and no unused extra targets, to keep the model output
    small and fast."""
    action: str
    confidence: int
    trade_quality: int
    entry: float | None
    stop_loss: float | None
    target1: float | None
    risk_reward: float | None
    raw_response: str


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
        "risk_reward": {"type": ["number", "null"]},
    },
    "required": ["action", "confidence", "trade_quality", "entry", "stop_loss", "target1",
                 "risk_reward"],
}


def build_system_blocks() -> list[dict]:
    return [{"type": "text", "text": ENGINE_PROMPT, "cache_control": {"type": "ephemeral"}}]


def _position_line(position: dict | None) -> str:
    if not position:
        return "Current position: no position held (flat)."
    return (f"Current position: {position.get('side', '?')} "
            f"{position.get('quantity', '?')} @ {position.get('entry_price', '?')}, "
            f"unrealized P&L {position.get('unrealized_pnl_pct', '?')}%.")


def build_user_message(symbol: str, indicators: dict, position: dict | None) -> str:
    return (f"Decide the intraday trade for {symbol}.\n\n"
            f"{_position_line(position)}\n\n"
            f"Indicator JSON:\n{json.dumps(indicators, ensure_ascii=False, default=str)}")


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
        risk_reward=_as_float(obj["risk_reward"]), raw_response=raw_text)


_WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3}


def _default_client_factory() -> Any:
    import anthropic
    return anthropic.Anthropic()


class DecisionEngine:
    """Calls Claude to produce a typed intraday Decision for one symbol."""

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
