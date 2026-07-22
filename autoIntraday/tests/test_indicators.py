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
