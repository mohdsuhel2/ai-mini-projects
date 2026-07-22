import importlib.util
import os
import sys

SPEC = importlib.util.spec_from_file_location(
    "stock_analyze",
    os.path.join(os.path.dirname(__file__), "..", "stock_analyze.py"),
)
sa = importlib.util.module_from_spec(SPEC)
sys.modules["stock_analyze"] = sa
SPEC.loader.exec_module(sa)


def test_gather_stock_data_exists():
    assert hasattr(sa, "gather_stock_data")
    assert callable(sa.gather_stock_data)


def _bar(date, o, h, l, c, v=1000.0):
    return sa.OHLCVBar(date=date, open=o, high=h, low=l, close=c, volume=v)


def _uptrend_bars(n=60):
    bars = []
    price = 100.0
    for i in range(n):
        price *= 1.01
        bars.append(_bar(f"2026-01-{(i % 28) + 1:02d}", price * 0.99, price * 1.01, price * 0.98, price))
    return bars


def test_swing_signals_uptrend():
    bars = _uptrend_bars(60)
    metrics = sa.build_metrics_package(bars, bars, bars, bars[-20:])
    sig = sa.compute_swing_signals(bars, bars, metrics)
    assert sig["trend"] == "up"
    assert sig["above_sma20"] is True
    assert sig["breakout"] is False
    assert sig["consolidating"] is False
    assert sig["above_sma50"] is True
    assert sig["adx_proxy"] == 100.0


def test_swing_signals_volume_surge():
    bars = _uptrend_bars(40)
    bars[-1] = _bar(bars[-1].date, bars[-1].open, bars[-1].high, bars[-1].low, bars[-1].close, v=5000.0)
    metrics = sa.build_metrics_package(bars, bars, bars, bars[-20:])
    sig = sa.compute_swing_signals(bars, bars, metrics)
    assert sig["volume_surge_ratio"] is not None
    assert sig["volume_surge_ratio"] > 1.5
    assert sig["volume_confirmed"] is True


def test_swing_signals_breakout():
    # 24 flat consolidation bars, then one bar that breaks above the range on heavy volume
    bars = []
    for i in range(24):
        bars.append(_bar(f"2026-02-{i + 1:02d}", 100, 102, 98, 101 if i % 2 else 99, 1000.0))
    bars.append(_bar("2026-03-01", 103, 116, 103, 115, 6000.0))
    metrics = sa.build_metrics_package(bars, bars, bars, bars[-20:])
    sig = sa.compute_swing_signals(bars, bars, metrics)
    assert sig["breakout"] is True
    assert sig["volume_confirmed"] is True
    assert sig["volume_surge_ratio"] == 6.0
    assert sig["trend"] == "up"


def test_build_json_report_shape():
    bars = _uptrend_bars(60)
    metrics = sa.build_metrics_package(bars, bars, bars, bars[-20:])
    metrics["derived_from_daily_bars"] = sa.derived_technical_factors(bars)
    metrics["extended_technicals"] = sa.extended_technical_indicators(bars)
    data = {
        "ticker": "RELIANCE.NS",
        "meta": {"short_name": "Reliance", "currency": "INR", "yahoo_symbol": "RELIANCE.NS", "exchange": "NSI"},
        "w5y": bars, "d2y": bars, "d3m": bars, "d1m": bars[-20:],
        "last_bar_date": bars[-1].date,
        "metrics": metrics,
        "bench_ctx": {"benchmark": "^NSEI", "excess_return_vs_benchmark_pct": 2.5},
        "fund_pack": {"sector_profile": "Energy"},
        "quote_ctx": {},
        "news_merged": [{"title": "X", "publisher": "Y", "link": "z"}],
    }
    rep = sa.build_json_report(data)
    for key in ("symbol", "resolved_ticker", "as_of", "price", "indicators",
                "volume", "swing_signals", "benchmark", "fundamentals", "news", "warnings", "meta"):
        assert key in rep
    assert rep["resolved_ticker"] == "RELIANCE.NS"
    assert rep["price"]["last"] is not None
    assert isinstance(rep["news"], list)
    assert rep["meta"]["currency"] == "INR"


def test_run_screen_handles_errors(monkeypatch):
    def fake_gather(ticker):
        if "BAD" in ticker:
            raise ValueError("no bars")
        bars = _uptrend_bars(60)
        metrics = sa.build_metrics_package(bars, bars, bars, bars[-20:])
        metrics["derived_from_daily_bars"] = sa.derived_technical_factors(bars)
        metrics["extended_technicals"] = sa.extended_technical_indicators(bars)
        return {
            "ticker": ticker,
            "meta": {"short_name": ticker, "currency": "INR", "yahoo_symbol": ticker, "exchange": "NSI"},
            "w5y": bars, "d2y": bars, "d3m": bars, "d1m": bars[-20:],
            "last_bar_date": bars[-1].date, "metrics": metrics,
            "bench_ctx": {}, "fund_pack": {}, "quote_ctx": {}, "news_merged": [],
        }
    monkeypatch.setattr(sa, "gather_stock_data", fake_gather)
    out = sa.run_screen(["TCS", "BADSYM", "INFY"])
    assert len(out) == 3
    assert out[0]["resolved_ticker"] == "TCS.NS"
    assert "error" in out[1]
    assert out[1]["symbol"] == "BADSYM"
    assert out[2]["resolved_ticker"] == "INFY.NS"
