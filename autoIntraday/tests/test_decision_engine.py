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
                 stop_loss=None, target1=None, risk_reward=None, raw_response="{}")
    assert d.action == "WAIT"
    assert d.confidence == 40
    assert d.entry is None


def test_indicator_error_is_decision_engine_error():
    assert issubclass(IndicatorError, DecisionEngineError)


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


from decision_engine import _parse_decision

_GOOD = _json.dumps({
    "action": "BUY_NOW", "confidence": 78, "trade_quality": 82, "entry": 2456.7,
    "stop_loss": 2440.0, "target1": 2480.0, "risk_reward": 2.1})


def test_parse_decision_wellformed():
    d = _parse_decision(_GOOD)
    assert d.action == "BUY_NOW"
    assert d.confidence == 78
    assert d.entry == 2456.7
    assert d.target1 == 2480.0
    assert d.risk_reward == 2.1
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
    del obj["risk_reward"]
    with pytest.raises(DecisionEngineError, match="missing|risk_reward"):
        _parse_decision(_json.dumps(obj))


def test_parse_decision_no_json_raises():
    with pytest.raises(DecisionEngineError, match="JSON|parse"):
        _parse_decision("I cannot decide today.")


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
