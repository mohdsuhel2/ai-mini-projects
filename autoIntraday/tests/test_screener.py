import json

import pytest

from screener import get_candidates, ScreenerError
from decision_engine import DecisionEngineError


def _runner_factory(rc, out, err=""):
    def runner(argv, cwd):
        runner.argv = argv
        runner.cwd = cwd
        return (rc, out, err)
    return runner


def test_screener_error_is_decision_engine_error():
    assert issubclass(ScreenerError, DecisionEngineError)


def test_get_candidates_returns_picks():
    payload = {"picks": [{"symbol": "RELIANCE", "ltp": 2456.7, "change_pct": 2.1},
                         {"symbol": "TCS", "ltp": 3820.0, "change_pct": 1.4}]}
    runner = _runner_factory(0, json.dumps(payload))
    picks = get_candidates(direction="up", top=5, runner=runner)
    assert [p["symbol"] for p in picks] == ["RELIANCE", "TCS"]
    assert "--direction" in runner.argv and "up" in runner.argv
    assert "--top" in runner.argv and "5" in runner.argv


def test_get_candidates_nonzero_exit_raises():
    with pytest.raises(ScreenerError, match="exit"):
        get_candidates(runner=_runner_factory(1, "", "boom"))


def test_get_candidates_empty_raises():
    with pytest.raises(ScreenerError, match="empty"):
        get_candidates(runner=_runner_factory(0, "  "))


def test_get_candidates_bad_json_raises():
    with pytest.raises(ScreenerError, match="parse|JSON"):
        get_candidates(runner=_runner_factory(0, "not json {"))


def test_get_candidates_missing_picks_raises():
    with pytest.raises(ScreenerError, match="picks"):
        get_candidates(runner=_runner_factory(0, json.dumps({"error": "x"})))
