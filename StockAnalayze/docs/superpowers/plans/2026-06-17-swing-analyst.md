# Swing Stock Analyst Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade `stock_analyze.py` with a clean JSON data mode, swing-specific signals, and a batch-screen mode; then ship a Claude Code skill (`swing-analyst`) that uses Claude (not Ollama) as the brain to produce Indian-market swing trade plans, in two modes (specific stock / top-10 recommendations).

**Architecture:** Extract the data-gathering currently inlined in `main()` into a reusable `gather_stock_data(ticker)` function returning a dict. Reuse it from three call sites: the existing Ollama path, a new `--json` path (via `build_json_report`), and a new `--screen` batch path. A personal Claude skill orchestrates the script for data, adds live WebSearch, and writes the trade plan.

**Tech Stack:** Python 3.10+ (stdlib `urllib`/`json`/`argparse`), `yfinance`, `pytest` (new dev dep), Claude Code skill (Markdown `SKILL.md`).

## Global Constraints

- Indian market default: bare symbols normalize to `.NS` (NSE); `NSE:`/`BSE:` prefixes supported. Copied from existing `normalize_yahoo_ticker`.
- `--json` and `--screen` print **only** JSON to **stdout**; all logs go to **stderr** (existing logging already targets stderr — do not add `print()` for logs).
- All changes are **additive**: existing `-s`, `-m`, `--ollama-host`, `--dump-prompt`, `--log-level` flags and the Ollama decision path must keep working unchanged.
- No new data vendors, no DB, no caching. Reuse existing Yahoo/yfinance/news fetchers.
- Output is **educational, not financial advice** — the skill must include that disclaimer.
- Focus: **swing trading** (multi-day to ~1 month), target **10–20%** moves. Never intraday.
- Skill is a **personal** skill at `~/.claude/skills/swing-analyst/SKILL.md`, referencing the script by absolute path:
  - Python: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python`
  - Script: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/stock_analyze.py`

---

## File Structure

- **Modify** `stock_analyze.py`:
  - New `gather_stock_data(ticker) -> Dict[str, Any]` (refactor extract from `main`).
  - New `compute_swing_signals(d3m, d2y, metrics) -> Dict[str, Any]`.
  - New `build_json_report(data) -> Dict[str, Any]`.
  - New `run_screen(symbols) -> List[Dict[str, Any]]`.
  - `main()` gains `--json` and `--screen` args and routes to the above.
- **Create** `tests/test_swing.py` — pytest unit tests for the pure functions.
- **Create** `~/.claude/skills/swing-analyst/SKILL.md` — the skill.
- **Modify** `README.md` — usage note for the skill + new flags.
- **Modify** `requirements.txt` — add `pytest` as dev dependency comment + install.

---

### Task 1: Extract `gather_stock_data()` (pure refactor) + test harness

Refactor the data-gathering block in `main()` (lines ~1438–1518) into a reusable function so `--json`/`--screen` can call it. Behavior of the existing Ollama/`--dump-prompt` path must not change.

**Files:**
- Modify: `stock_analyze.py` (add `gather_stock_data`, rewire `main`)
- Create: `tests/test_swing.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `gather_stock_data(ticker: str) -> Dict[str, Any]` returning keys:
  `ticker`, `meta` (dict from `fetch_meta`), `w5y`, `d2y`, `d3m`, `d1m` (List[OHLCVBar]),
  `last_bar_date` (str), `metrics` (dict), `bench_ctx` (dict), `fund_pack` (dict),
  `quote_ctx` (dict), `news_merged` (List[dict]).

- [ ] **Step 1: Install pytest into the venv**

Run:
```bash
.venv/bin/pip install pytest
```
Expected: `Successfully installed pytest-...`

- [ ] **Step 2: Add pytest note to requirements.txt**

Add this line to `requirements.txt`:
```
# Dev/test: pip install pytest   (run: .venv/bin/python -m pytest -q)
pytest>=8.0
```

- [ ] **Step 3: Write a failing import test**

Create `tests/test_swing.py`:
```python
import importlib.util
import os

SPEC = importlib.util.spec_from_file_location(
    "stock_analyze",
    os.path.join(os.path.dirname(__file__), "..", "stock_analyze.py"),
)
sa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sa)


def test_gather_stock_data_exists():
    assert hasattr(sa, "gather_stock_data")
    assert callable(sa.gather_stock_data)
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_swing.py::test_gather_stock_data_exists -v`
Expected: FAIL — `AttributeError: module 'stock_analyze' has no attribute 'gather_stock_data'`

- [ ] **Step 5: Add `gather_stock_data` above `main()`**

Insert this function just before `def main()` in `stock_analyze.py`:
```python
def gather_stock_data(ticker: str) -> Dict[str, Any]:
    """Fetch + compute everything for one ticker. Network-bound. Logs to stderr."""
    LOG.info("→ Fetching market data… (%s)", ticker)
    meta = fetch_meta(ticker)
    w5y = fetch_yahoo_chart(ticker, "5y", "1wk")
    d2y = fetch_yahoo_chart(ticker, "2y", "1d")
    d3m = fetch_yahoo_chart(ticker, "3mo", "1d")
    d1m = fetch_yahoo_chart(ticker, "1mo", "1d")
    if not d3m:
        raise ValueError("No daily bars returned — check symbol or exchange suffix (.NS / .BO).")

    last_bar_date = d3m[-1].date
    metrics = build_metrics_package(w5y, d2y, d3m, d1m)
    metrics["derived_from_daily_bars"] = derived_technical_factors(d3m)
    metrics["extended_technicals"] = extended_technical_indicators(d3m)

    benchmark_sym = benchmark_index_for_ticker(ticker)
    bench_ctx: Dict[str, Any] = {}
    if benchmark_sym:
        time.sleep(0.55)
        try:
            bench_bars = fetch_yahoo_chart(benchmark_sym, "3mo", "1d")
            bench_ctx = relative_vs_benchmark(d3m, bench_bars, benchmark_sym)
        except Exception as e:
            LOG.warning("Benchmark fetch failed (continuing): %s", e)
            bench_ctx = {"benchmark": benchmark_sym, "note": str(e)}
    else:
        bench_ctx = {"note": "no benchmark mapped for this ticker pattern"}

    time.sleep(0.45)
    fund_pack: Dict[str, Any] = {}
    try:
        fund_pack = fetch_deep_fundamentals(ticker)
    except Exception as e:
        LOG.warning("Deep fundamentals fetch failed: %s", e)

    quote_ctx: Dict[str, Any] = {}
    news_yh: List[Dict[str, Any]] = []
    time.sleep(0.8)
    try:
        quote_ctx, news_yh = fetch_yahoo_search_bundle(ticker)
    except (urllib.error.URLError, KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
        LOG.warning("Yahoo search bundle failed (continuing): %s", e)

    news_g: List[Dict[str, Any]] = []
    try:
        news_g = fetch_google_news_rss(f'{meta["short_name"]} stock OR {ticker}', limit=12)
    except (urllib.error.URLError, ET.ParseError, ValueError) as e:
        LOG.debug("Google News RSS skipped: %s", e)

    news_merged = merge_news_headlines(news_yh, news_g, max_items=26)

    return {
        "ticker": ticker,
        "meta": meta,
        "w5y": w5y, "d2y": d2y, "d3m": d3m, "d1m": d1m,
        "last_bar_date": last_bar_date,
        "metrics": metrics,
        "bench_ctx": bench_ctx,
        "fund_pack": fund_pack,
        "quote_ctx": quote_ctx,
        "news_merged": news_merged,
    }
```

- [ ] **Step 6: Rewire `main()` to use `gather_stock_data`**

Replace the body from `LOG.info("→ Fetching market data…")` (line ~1438) through the `news_merged = merge_news_headlines(...)` line (~1518) with:
```python
    LOG.info("→ %s → %s | Ollama model: %s", args.symbol, ticker, args.model)
    try:
        data = gather_stock_data(ticker)
    except (urllib.error.URLError, KeyError, IndexError, ValueError, TypeError) as e:
        LOG.error("Failed to load data: %s", e)
        sys.exit(1)

    meta = data["meta"]
    w5y, d2y, d3m, d1m = data["w5y"], data["d2y"], data["d3m"], data["d1m"]
    last_bar_date = data["last_bar_date"]
    metrics = data["metrics"]
    bench_ctx = data["bench_ctx"]
    fund_pack = data["fund_pack"]
    quote_ctx = data["quote_ctx"]
    news_merged = data["news_merged"]
```
Leave the `user_prompt = build_user_prompt(...)` call and everything after it unchanged.

- [ ] **Step 7: Run the test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_swing.py::test_gather_stock_data_exists -v`
Expected: PASS

- [ ] **Step 8: Regression — confirm `--dump-prompt` still works**

Run: `.venv/bin/python stock_analyze.py -s RELIANCE --dump-prompt 2>/dev/null | head -3`
Expected: prints `=== OLLAMA system ===` (no crash). If Yahoo rate-limits, retry once.

- [ ] **Step 9: Commit**

```bash
git add stock_analyze.py tests/test_swing.py requirements.txt
git commit -m "refactor: extract gather_stock_data() for reuse + add pytest"
```

---

### Task 2: `compute_swing_signals()` — swing-specific signals

Add a pure function that derives swing signals from already-fetched bars + metrics. No network. Fully unit-testable with synthetic bars.

**Files:**
- Modify: `stock_analyze.py` (add `compute_swing_signals`)
- Modify: `tests/test_swing.py`

**Interfaces:**
- Consumes: `OHLCVBar` (existing dataclass), `metrics` dict from `build_metrics_package`.
- Produces: `compute_swing_signals(d3m: List[OHLCVBar], d2y: List[OHLCVBar], metrics: Dict[str, Any]) -> Dict[str, Any]` returning keys:
  `above_sma20` (bool|None), `above_sma50` (bool|None), `trend` (`"up"|"down"|"sideways"`),
  `volume_surge_ratio` (float|None), `volume_confirmed` (bool), `breakout` (bool),
  `consolidating` (bool), `adx_proxy` (float|None).

- [ ] **Step 1: Write failing tests with synthetic bars**

Add to `tests/test_swing.py`:
```python
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
    assert isinstance(sig["breakout"], bool)
    assert isinstance(sig["consolidating"], bool)


def test_swing_signals_volume_surge():
    bars = _uptrend_bars(40)
    bars[-1] = _bar(bars[-1].date, bars[-1].open, bars[-1].high, bars[-1].low, bars[-1].close, v=5000.0)
    metrics = sa.build_metrics_package(bars, bars, bars, bars[-20:])
    sig = sa.compute_swing_signals(bars, bars, metrics)
    assert sig["volume_surge_ratio"] is not None
    assert sig["volume_surge_ratio"] > 1.5
    assert sig["volume_confirmed"] is True
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_swing.py -k swing_signals -v`
Expected: FAIL — `AttributeError: ... has no attribute 'compute_swing_signals'`

- [ ] **Step 3: Implement `compute_swing_signals`**

Add to `stock_analyze.py` (after `extended_technical_indicators`):
```python
def compute_swing_signals(
    d3m: List[OHLCVBar],
    d2y: List[OHLCVBar],
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """Swing-trade signals derived from already-fetched daily bars + metrics. No network."""
    closes = [b.close for b in d3m]
    last = closes[-1] if closes else None
    sma20 = metrics.get("sma20_daily")
    sma50 = metrics.get("sma50_daily")

    above_sma20 = (last > sma20) if (last is not None and sma20) else None
    above_sma50 = (last > sma50) if (last is not None and sma50) else None

    # Trend via 20d slope of close
    trend = "sideways"
    if len(closes) >= 21:
        change = (closes[-1] / closes[-21] - 1) * 100
        if change > 3:
            trend = "up"
        elif change < -3:
            trend = "down"

    # Volume surge: last vs prior 20d average
    vol_ratio = None
    volume_confirmed = False
    if len(d3m) >= 22:
        prior = [b.volume for b in d3m[-21:-1] if b.volume]
        last_v = d3m[-1].volume
        if last_v and prior:
            avg_v = statistics.mean(prior)
            if avg_v:
                vol_ratio = round(float(last_v) / float(avg_v), 3)
                volume_confirmed = vol_ratio >= 1.5 and (last is not None and len(closes) >= 2 and closes[-1] >= closes[-2])

    # Consolidation: tight 20d range; Breakout: last close above prior 20d high
    consolidating = False
    breakout = False
    if len(d3m) >= 21:
        window = d3m[-21:-1]
        hi = max(b.high for b in window)
        lo = min(b.low for b in window)
        if lo and hi:
            band_pct = (hi - lo) / lo * 100
            consolidating = band_pct <= 12
            breakout = last is not None and last > hi

    # ADX proxy: % of last 14 closes that moved in trend direction (0..100)
    adx_proxy = None
    if len(closes) >= 15:
        ups = sum(1 for i in range(-14, 0) if closes[i] > closes[i - 1])
        directional = ups if trend == "up" else (14 - ups)
        adx_proxy = round(directional / 14 * 100, 1)

    return {
        "above_sma20": above_sma20,
        "above_sma50": above_sma50,
        "trend": trend,
        "volume_surge_ratio": vol_ratio,
        "volume_confirmed": volume_confirmed,
        "breakout": breakout,
        "consolidating": consolidating,
        "adx_proxy": adx_proxy,
    }
```

- [ ] **Step 4: Run tests to verify pass**

Run: `.venv/bin/python -m pytest tests/test_swing.py -k swing_signals -v`
Expected: PASS (both tests)

- [ ] **Step 5: Commit**

```bash
git add stock_analyze.py tests/test_swing.py
git commit -m "feat: add compute_swing_signals() for swing setup detection"
```

---

### Task 3: `build_json_report()` + `--json` flag

Assemble a clean JSON report dict from a `gather_stock_data` result, and wire a `--json` CLI flag that prints it to stdout.

**Files:**
- Modify: `stock_analyze.py` (add `build_json_report`, `--json` arg + routing)
- Modify: `tests/test_swing.py`

**Interfaces:**
- Consumes: result dict of `gather_stock_data`; `compute_swing_signals`.
- Produces: `build_json_report(data: Dict[str, Any]) -> Dict[str, Any]` with top-level keys:
  `symbol`, `resolved_ticker`, `as_of`, `meta`, `price`, `indicators`, `volume`,
  `swing_signals`, `benchmark`, `fundamentals`, `news`, `warnings`.

- [ ] **Step 1: Write a failing test using a faked data dict**

Add to `tests/test_swing.py`:
```python
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
                "volume", "swing_signals", "benchmark", "fundamentals", "news", "warnings"):
        assert key in rep
    assert rep["resolved_ticker"] == "RELIANCE.NS"
    assert rep["price"]["last"] is not None
    assert isinstance(rep["news"], list)
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_swing.py::test_build_json_report_shape -v`
Expected: FAIL — no attribute `build_json_report`

- [ ] **Step 3: Implement `build_json_report`**

Add to `stock_analyze.py` (after `compute_swing_signals`):
```python
def build_json_report(data: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble a compact, Claude-friendly JSON report from gather_stock_data output."""
    meta = data["meta"]
    metrics = data["metrics"]
    d3m, d2y = data["d3m"], data["d2y"]
    derived = metrics.get("derived_from_daily_bars", {})
    ext = metrics.get("extended_technicals", {})
    swing = compute_swing_signals(d3m, d2y, metrics)

    last = metrics.get("last_close")
    prev = d3m[-2].close if len(d3m) >= 2 else None
    day_change = round((last / prev - 1) * 100, 3) if (last and prev) else None

    warnings: List[str] = []
    fund = data.get("fund_pack") or {}
    if not _fundamentals_payload_usable(fund):
        warnings.append("fundamentals sparse or unavailable (normal for some tickers)")
    if data.get("bench_ctx", {}).get("note"):
        warnings.append(f"benchmark: {data['bench_ctx']['note']}")

    return {
        "symbol": meta.get("yahoo_symbol", data["ticker"]).replace(".NS", "").replace(".BO", ""),
        "resolved_ticker": data["ticker"],
        "as_of": data["last_bar_date"],
        "meta": {
            "name": meta.get("short_name"),
            "currency": meta.get("currency"),
            "exchange": meta.get("exchange"),
            "sector": fund.get("sector_profile"),
            "industry": fund.get("industry_profile"),
        },
        "price": {
            "last": last,
            "prev_close": prev,
            "day_change_pct": day_change,
            "high_52w": metrics.get("range_52w_high"),
            "low_52w": metrics.get("range_52w_low"),
            "pct_from_52w_high": metrics.get("pct_from_52w_high"),
            "pct_from_52w_low": metrics.get("pct_from_52w_low"),
            "support_20d": metrics.get("approx_support_20d"),
            "resistance_20d": metrics.get("approx_resistance_20d"),
        },
        "indicators": {
            "sma20": metrics.get("sma20_daily"),
            "sma50": metrics.get("sma50_daily"),
            "rsi14": derived.get("rsi14_daily"),
            "atr14": metrics.get("atr14_daily"),
            "macd_line": ext.get("macd_line"),
            "macd_signal": ext.get("macd_signal"),
            "macd_histogram": ext.get("macd_histogram"),
            "bollinger_percent_b": ext.get("bollinger_percent_b"),
            "return_5d_pct": derived.get("return_5d_pct"),
            "return_20d_pct": derived.get("return_20d_pct"),
            "return_60d_pct": derived.get("return_60d_pct"),
        },
        "volume": {
            "last": d3m[-1].volume if d3m else None,
            "surge_ratio": swing.get("volume_surge_ratio"),
        },
        "swing_signals": swing,
        "benchmark": data.get("bench_ctx", {}),
        "fundamentals": fund,
        "news": [
            {
                "title": n.get("title"),
                "source": n.get("publisher") or n.get("source"),
                "published": n.get("published") or n.get("pubDate"),
                "link": n.get("link"),
            }
            for n in (data.get("news_merged") or [])[:20]
        ],
        "warnings": warnings,
    }
```

- [ ] **Step 4: Run test to verify pass**

Run: `.venv/bin/python -m pytest tests/test_swing.py::test_build_json_report_shape -v`
Expected: PASS

- [ ] **Step 5: Add `--json` arg and routing in `main()`**

Add this argument near the other `parser.add_argument` calls:
```python
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print structured JSON data to stdout (no LLM call). For the Claude skill.",
    )
```
Then, immediately after `data = gather_stock_data(ticker)` succeeds and the local vars are unpacked, BEFORE the `user_prompt = build_user_prompt(...)` line, add:
```python
    if args.json:
        report = build_json_report(data)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return
```

- [ ] **Step 6: Integration check — real `--json` run**

Run: `.venv/bin/python stock_analyze.py -s RELIANCE --json 2>/dev/null | .venv/bin/python -c "import sys,json; d=json.load(sys.stdin); print(d['resolved_ticker'], d['price']['last'], d['swing_signals']['trend'])"`
Expected: prints something like `RELIANCE.NS 1xxx.x up` (valid JSON parsed, no stderr noise on stdout). Retry once if Yahoo rate-limits.

- [ ] **Step 7: Commit**

```bash
git add stock_analyze.py tests/test_swing.py
git commit -m "feat: add --json structured data mode (build_json_report)"
```

---

### Task 4: `--screen` batch mode

Add a `--screen SYM1,SYM2,...` flag that fetches JSON reports for many symbols in one process, reusing inter-call sleeps, and emits a JSON array. Failed symbols become `{"symbol", "error"}` entries instead of aborting.

**Files:**
- Modify: `stock_analyze.py` (add `run_screen`, `--screen` arg + routing, make `-s` optional)
- Modify: `tests/test_swing.py`

**Interfaces:**
- Consumes: `gather_stock_data`, `build_json_report`, `normalize_yahoo_ticker`.
- Produces: `run_screen(symbols: List[str]) -> List[Dict[str, Any]]` — list of report dicts and/or `{"symbol", "error"}` dicts.

- [ ] **Step 1: Write failing test (monkeypatched, no network)**

Add to `tests/test_swing.py`:
```python
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
    assert out[2]["resolved_ticker"] == "INFY.NS"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_swing.py::test_run_screen_handles_errors -v`
Expected: FAIL — no attribute `run_screen`

- [ ] **Step 3: Implement `run_screen`**

Add to `stock_analyze.py` (after `build_json_report`):
```python
def run_screen(symbols: List[str]) -> List[Dict[str, Any]]:
    """Fetch JSON reports for many symbols in one process. Failures become error entries."""
    out: List[Dict[str, Any]] = []
    for raw in symbols:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ticker = normalize_yahoo_ticker(raw)
            data = gather_stock_data(ticker)
            out.append(build_json_report(data))
        except Exception as e:
            LOG.warning("Screen: %s failed: %s", raw, e)
            out.append({"symbol": raw, "error": str(e)})
        time.sleep(0.6)
    return out
```

- [ ] **Step 4: Run test to verify pass**

Run: `.venv/bin/python -m pytest tests/test_swing.py::test_run_screen_handles_errors -v`
Expected: PASS

- [ ] **Step 5: Make `-s` optional and add `--screen` routing**

Change the `-s/--symbol` argument: remove `required=True` and add `default=None`.
Add the new argument:
```python
    parser.add_argument(
        "--screen",
        default=None,
        help="Comma-separated symbols to batch-screen as JSON array (no LLM). e.g. TCS,INFY,RELIANCE",
    )
```
Immediately after `args = parser.parse_args()` and `setup_logging(args.log_level)`, add:
```python
    if args.screen:
        symbols = [s for s in args.screen.split(",") if s.strip()]
        reports = run_screen(symbols)
        print(json.dumps(reports, ensure_ascii=False, indent=2, default=str))
        return
    if not args.symbol:
        parser.error("one of -s/--symbol or --screen is required")
```
(Place this BEFORE `ticker = normalize_yahoo_ticker(args.symbol)`.)

- [ ] **Step 6: Integration check — real `--screen` run with a bad symbol**

Run: `.venv/bin/python stock_analyze.py --screen "TCS,NOTAREALSYM123" 2>/dev/null | .venv/bin/python -c "import sys,json; a=json.load(sys.stdin); print(len(a), a[0].get('resolved_ticker'), 'error' in a[1])"`
Expected: prints `2 TCS.NS True` (array of 2; first valid, second an error entry).

- [ ] **Step 7: Run full test suite**

Run: `.venv/bin/python -m pytest tests/test_swing.py -v`
Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
git add stock_analyze.py tests/test_swing.py
git commit -m "feat: add --screen batch JSON mode for multi-symbol screening"
```

---

### Task 5: Create the `swing-analyst` Claude skill

Write the skill that orchestrates the script + WebSearch and produces trade plans. This is the user-facing deliverable.

**Files:**
- Create: `~/.claude/skills/swing-analyst/SKILL.md`

- [ ] **Step 1: Create the skill directory**

Run:
```bash
mkdir -p ~/.claude/skills/swing-analyst
```
Expected: no output.

- [ ] **Step 2: Write `SKILL.md`**

Create `~/.claude/skills/swing-analyst/SKILL.md` with EXACTLY this content:
````markdown
---
name: swing-analyst
description: Use when the user wants Indian-market swing-trade analysis or stock recommendations targeting ~10-20% gains over days-to-a-month. Two modes - analyze a specific NSE/BSE stock by code, or recommend the top swing candidates. NOT for intraday.
---

# Swing Analyst (Indian market)

Produce swing-trade analysis for Indian stocks, targeting **10–20% moves over a few
days to ~1 month**. NOT intraday. Output is **educational, not financial advice**.

You are the brain. The Python tool only fetches data — you do the reasoning and add live
web context.

## Paths (absolute)

- PYTHON: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/.venv/bin/python`
- SCRIPT: `/Users/mohdsuhel/ai-mini-projects/StockAnalayze/stock_analyze.py`

Always run with stderr suppressed so stdout is clean JSON:
`$PYTHON $SCRIPT ... 2>/dev/null`

## Mode detection

- If the user names a concrete stock code (e.g. `RELIANCE`, `NSE:TCS`, `TATAMOTORS`) →
  **Mode 1 (specific stock)**.
- If the user asks generically ("top 10 swing stocks", "what should I buy for swing",
  "recommend stocks") → **Mode 2 (recommendations)**.
- If ambiguous, ask one short clarifying question.

## Mode 1 — Specific stock

1. Run: `$PYTHON $SCRIPT -s <CODE> --json 2>/dev/null`. Parse the JSON.
   If it errors or returns no data, tell the user and suggest the `NSE:<CODE>` form.
2. Do 2–4 **WebSearch** queries for fresh context the script can't know:
   - `<company> share news latest`
   - `<company> Q result date 2026` (upcoming/last earnings)
   - `<company> stock target analyst` / sector sentiment
   Prefer results from the last few weeks. Note dates.
3. Score against **Swing criteria** below.
4. Emit ONE **Output block** (format below).

## Mode 2 — Top recommendations

1. **Discover ~30 candidates** via WebSearch (Indian market, current): momentum/breakout
   lists, "swing trade stocks this week", sector leaders, 52-week-high breakouts, results
   gainers. Collect NSE codes; dedupe to ~30.
2. **Verify** them all in one call:
   `$PYTHON $SCRIPT --screen "SYM1,SYM2,...,SYM30" 2>/dev/null`. Parse the JSON array.
   Skip entries that contain an `error` key (note how many were skipped).
3. **Score** each surviving stock against the Swing criteria. Drop weak/broken setups.
4. **Rank** by setup quality and expected risk-adjusted return; keep the **best 10**.
5. Emit a **summary table** (Symbol · Verdict · Expected % · Confidence · 1-line reason),
   then a full **Output block** for each of the 10.
6. Warn the user up front that screening ~30 names takes a few minutes (Yahoo rate limits).

## Swing criteria (how you score)

Favor a stock when MOST hold (use the JSON `swing_signals`, `indicators`, `benchmark`,
`price`, `fundamentals` + your web findings):

- **Trend**: `swing_signals.trend == "up"`, price above SMA20 & SMA50.
- **Momentum**: RSI ~50–70 and rising; AVOID overbought-extreme (RSI > ~78) into resistance.
- **Volume**: `swing_signals.volume_confirmed` true, or `volume.surge_ratio` ≥ 1.5 on up days.
- **Relative strength**: `benchmark.excess_return_vs_benchmark_pct` > 0 (beating NIFTY).
- **Structure**: `breakout` true, or `consolidating` near resistance with room to run.
- **Fundamentals sanity**: no broken financials, not extremely overvalued vs sector.
- **News/sentiment**: no negative catalyst (fraud, downgrade, guidance cut) in web search.
- **Risk**: a logical stop (below recent swing low / `price.support_20d`, or ~1.5×`atr14`)
  that keeps **risk:reward ≥ ~1:2** for a 10–20% target.

If criteria mostly fail → **WAIT** or **AVOID**, never force into a top-10.

### Computing the trade plan

- **Entry**: current price if breakout confirmed, else the support/pullback zone.
- **Target**: next resistance or +10–20% (state which); cross-check with
  `fundamentals.financial_targets.target_mean_price` if present.
- **Stop-loss**: `price.support_20d` or entry − 1.5×`atr14`, whichever is tighter and logical.
- **Risk:Reward** = (target − entry) / (entry − stop). Aim ≥ 2.
- **Expected % / time**: from distance to target + trend strength (`adx_proxy`); express a
  range and rough weeks. Be honest about uncertainty.

## Output block (per stock)

```
### <SYMBOL> — <Company>
**Verdict:** BUY / WAIT / AVOID   **Confidence:** High / Med / Low
**Expected:** ~X–Y% in ~N weeks   **Risk:Reward:** 1:Z

**Trade plan**
- Entry: <zone>
- Target: <price> (+X%)
- Stop-loss: <price> (−W%)

**Why**
- Technicals: <trend, SMAs, RSI, MACD, breakout, volume>
- Fundamentals: <growth / valuation sanity>
- News/Sentiment: <fresh web findings WITH dates>
- Strength vs NIFTY: <excess return, out/under-performing>

**Risks & catalysts**
- Risks: <overbought / earnings due / sector weakness / high ATR / thin liquidity>
- Catalysts: <results date / event / order win>
```

End EVERY response with:
`_Educational analysis, not financial advice. Verify data and prices before trading._`

## Notes

- Sparse/empty `fundamentals` is normal for some tickers — lean on technicals + web, lower
  confidence; don't fail.
- If `as_of` (last bar date) is several days old (weekend/holiday), state the as-of date.
- Always prefer the freshest WebSearch info for news; the script's news can be stale.
````

- [ ] **Step 3: Verify the skill is discoverable**

Run:
```bash
test -f ~/.claude/skills/swing-analyst/SKILL.md && head -4 ~/.claude/skills/swing-analyst/SKILL.md
```
Expected: prints the YAML frontmatter (`name: swing-analyst`).

- [ ] **Step 4: Commit (repo copy for reference)**

Keep a copy in the repo so it's version-controlled:
```bash
mkdir -p skills/swing-analyst
cp ~/.claude/skills/swing-analyst/SKILL.md skills/swing-analyst/SKILL.md
git add skills/swing-analyst/SKILL.md
git commit -m "feat: add swing-analyst Claude skill"
```

---

### Task 6: README usage note

Document the new flags and the skill.

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Append a usage section to README.md**

Add this section before the "Troubleshooting" section in `README.md`:
```markdown
## Claude skill: swing-analyst (recommended)

A Claude Code skill uses **Claude** (not the local Ollama model) as the analyst, plus live
web search, optimized for **swing trades targeting 10–20% over days-to-a-month** in the
**Indian market**. Installed at `~/.claude/skills/swing-analyst/`.

**Two ways to use it (just ask Claude Code in plain English):**

- **Specific stock:** `analyze RELIANCE for swing` or `/swing-analyst TATAMOTORS`
- **Top recommendations:** `give me top 10 swing stocks`

Under the hood the skill calls the script's new data modes:

```bash
# Structured JSON for one symbol (no LLM call)
.venv/bin/python stock_analyze.py -s RELIANCE --json

# Batch-screen many symbols → JSON array (no LLM call)
.venv/bin/python stock_analyze.py --screen "TCS,INFY,RELIANCE,HDFCBANK"
```

> Educational analysis, not financial advice.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document --json/--screen and the swing-analyst skill"
```

---

## Verification (final, manual)

- [ ] `​.venv/bin/python -m pytest tests/test_swing.py -v` → all pass.
- [ ] `.venv/bin/python stock_analyze.py -s RELIANCE --json 2>/dev/null` → valid JSON with `swing_signals`.
- [ ] `.venv/bin/python stock_analyze.py --screen "TCS,INFY" 2>/dev/null` → JSON array of 2.
- [ ] `.venv/bin/python stock_analyze.py -s RELIANCE --dump-prompt 2>/dev/null | head -1` → still prints `=== OLLAMA system ===` (regression).
- [ ] In a fresh Claude Code session: `analyze TCS for swing` → full output block with live news.
- [ ] In a fresh Claude Code session: `give me top 10 swing stocks` → summary table + 10 blocks.

## Self-Review Notes

- **Spec coverage:** §3.1 `--json` → Task 3; §3.2 swing signals → Task 2; §3.3 `--screen` →
  Task 4; §4 skill (both modes, criteria, output) → Task 5; §8 README → Task 6; refactor
  enabling reuse → Task 1.
- **Backward compat:** `-s` made optional but `parser.error` guards the no-arg case; Ollama
  path and `--dump-prompt` untouched (regression steps verify).
- **Type consistency:** `gather_stock_data` return keys are consumed verbatim by
  `build_json_report` and `run_screen`; `compute_swing_signals` return keys match the skill's
  references (`trend`, `volume_confirmed`, `volume_surge_ratio`, `breakout`, `consolidating`,
  `adx_proxy`, `above_sma20/50`).
- **Note on news keys:** `build_json_report` reads `publisher`/`source` and `published`/`pubDate`
  defensively because the merged-news item shape varies by source; missing keys yield `None`,
  not errors.
```
