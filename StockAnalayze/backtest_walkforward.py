#!/usr/bin/env python3
"""Walk-forward validation + prediction journal for the swing-analyst skill.

Runs the HARDENED swing BUY rule across a whole universe (default: universes/nifty100.txt)
at many past as-of dates, using the same as-of truncation the scripts' --asof uses, then
measures forward 10- and 20-day returns. Writes every signal to a JSONL journal so hit-rate
can be tracked over time, and prints BUY-vs-WAIT and gate-isolation stats.

Usage:
  .venv/bin/python backtest_walkforward.py [--universe nifty100] [--fwd 10] [--step 10] \
      [--start 2025-04-01] [--journal swing_journal.jsonl] [--refetch]

Fetches are cached to scratchpad/.wf_cache.pkl; pass --refetch to rebuild.
"""
import os, sys, json, time, pickle, argparse, statistics
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stock_analyze as SA

BLOCKED = {"distribution-risk", "overbought-into-resistance", "into-resistance", "extended-no-volume"}
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "scratchpad", ".wf_cache.pkl")


def load_universe(name):
    path = name if os.path.isfile(name) else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "universes", f"{name}.txt")
    syms = []
    for ln in open(path):
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            syms.append(ln)
    return syms


def fetch(tk):
    for _ in range(3):
        try:
            w = SA.fetch_yahoo_chart(tk, "5y", "1wk")
            d = SA.fetch_yahoo_chart(tk, "2y", "1d")
            if d and len(d) > 60:
                return w, d
        except Exception:
            time.sleep(1.5)
    return None, None


def build_cache(symbols, refetch):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    cache = {}
    if os.path.exists(CACHE) and not refetch:
        cache = pickle.load(open(CACHE, "rb"))
    need = [s for s in symbols + ["^NSEI"] if s not in cache]
    for i, s in enumerate(need):
        tk = s if s.startswith("^") else s + ".NS"
        w, d = fetch(tk)
        if d:
            cache[s] = (w, d)
        sys.stderr.write(f"\rfetch {i+1}/{len(need)} {s}      ")
        sys.stderr.flush()
        time.sleep(0.3)
    sys.stderr.write("\n")
    pickle.dump(cache, open(CACHE, "wb"))
    return cache


def trunc(bars, asof):
    return [b for b in bars if b.date <= asof]


def report(tk, w5y, d2y, bench, asof):
    d2 = trunc(d2y, asof)
    if len(d2) < 55:
        return None
    d3m, d1m, w5 = d2[-65:], d2[-22:], trunc(w5y, asof)
    m = SA.build_metrics_package(w5, d2, d3m, d1m)
    m["derived_from_daily_bars"] = SA.derived_technical_factors(d3m)
    m["extended_technicals"] = SA.extended_technical_indicators(d3m)
    bc, rg = {}, {}
    if bench:
        bt = trunc(bench, asof)[-65:]
        try:
            bc = SA.relative_vs_benchmark(d3m, bt, "^NSEI")
            rg = SA.market_regime(bt, "^NSEI")
        except Exception:
            pass
    data = {"ticker": tk, "last_bar_date": d2[-1].date,
            "meta": {"yahoo_symbol": tk, "short_name": tk, "currency": "INR", "exchange": "NSE"},
            "metrics": m, "w5y": w5, "d2y": d2, "d3m": d3m, "d1m": d1m,
            "fund_pack": {}, "bench_ctx": bc, "market_regime": rg, "news_merged": [], "quote_ctx": {}}
    return SA.build_json_report(data)


def fwd(d2y, asof, n):
    idx = max(i for i, b in enumerate(d2y) if b.date <= asof)
    f = d2y[idx + 1:idx + 1 + n]
    if len(f) < n:
        return None
    e = d2y[idx].close
    return {"ret": round((f[-1].close / e - 1) * 100, 2),
            "mae": round((min(b.low for b in f) / e - 1) * 100, 2),
            "mfe": round((max(b.high for b in f) / e - 1) * 100, 2)}


def hardened_buy(sw):
    """The hardened swing BUY rule — momentum path OR the validated buy-the-dip path."""
    eq = sw.get("entry_quality", {}); ind = sw["indicators"]; sg = sw["swing_signals"]
    bm = sw.get("benchmark", {})
    if eq.get("dip_buy"):          # 2nd path: pullback-in-uptrend, risk-on, oversold, volatile (~2x base)
        return True
    if sg.get("trend") != "up":
        return False
    if eq.get("entry_grade") in BLOCKED:
        return False
    if eq.get("distribution_risk") or eq.get("chase_into_resistance") or eq.get("into_resistance"):
        return False
    rsi = ind.get("rsi14")
    if rsi is None or not (50 <= rsi <= 78):
        return False
    if (ind.get("macd_histogram") or -1) <= 0:
        return False
    if not eq.get("volume_ok"):                       # robust 5v20 volume (softened rule)
        return False
    if eq.get("low_volatility_grinder"):              # ATR<2.5% -> no tradeable swing edge (JSWCEMENT-type)
        return False
    if (bm.get("excess_return_vs_benchmark_pct") or 0) <= 0:
        return False
    return True


def asof_dates(bench_d2y, start, step, fwd_max):
    dates = [b.date for b in bench_d2y]
    i = next((k for k, x in enumerate(dates) if x >= start), None)
    if i is None:
        return []
    out = []
    while i < len(dates) - fwd_max - 1:
        out.append(dates[i]); i += step
    return out


def stats(rows, key):
    rs = [key(r)["ret"] for r in rows]; mae = [key(r)["mae"] for r in rows]
    if not rs:
        return "n=0"
    win = sum(1 for r in rs if r > 0) / len(rs) * 100
    return (f"n={len(rs):4}  avg={statistics.mean(rs):+.2f}%  median={statistics.median(rs):+.2f}%  "
            f"win%={win:.0f}  avgMAE={statistics.mean(mae):+.2f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", default="nifty100")
    ap.add_argument("--fwd", type=int, default=10)
    ap.add_argument("--fwd2", type=int, default=20)
    ap.add_argument("--step", type=int, default=10)
    ap.add_argument("--start", default="2025-01-15")
    ap.add_argument("--journal", default="swing_journal.jsonl")
    ap.add_argument("--refetch", action="store_true")
    a = ap.parse_args()

    symbols = load_universe(a.universe)
    cache = build_cache(symbols, a.refetch)
    bench = cache.get("^NSEI", (None, None))[1]
    dates = asof_dates(bench, a.start, a.step, max(a.fwd, a.fwd2))
    print(f"universe={a.universe} stocks={len(symbols)} cached={len(cache)-1} "
          f"as-of dates={len(dates)} ({dates[0]}..{dates[-1]}) fwd={a.fwd}/{a.fwd2}d")

    buys, waits, blocked, notblocked = [], [], [], []
    regime_buy = {}
    jf = open(a.journal, "w")
    n_eval = 0
    for sym in symbols:
        if sym not in cache:
            continue
        w5y, d2y = cache[sym]
        for d in dates:
            sw = report(sym + ".NS", w5y, d2y, bench, d)
            if not sw:
                continue
            f1 = fwd(d2y, d, a.fwd); f2 = fwd(d2y, d, a.fwd2)
            if not f1 or not f2:
                continue
            n_eval += 1
            eq = sw.get("entry_quality", {}); ind = sw["indicators"]; sg = sw["swing_signals"]
            reg = (sw.get("market_regime") or {}).get("regime")
            is_buy = hardened_buy(sw)
            rec = {"asof": d, "symbol": sym, "verdict": "BUY" if is_buy else "WAIT",
                   "entry_grade": eq.get("entry_grade"), "regime": reg,
                   "rsi": ind.get("rsi14"), "macd_hist": ind.get("macd_histogram"),
                   "trend": sg.get("trend"), "volume_ok": eq.get("volume_ok"),
                   "fwd_ret": f1["ret"], "fwd_mae": f1["mae"],
                   f"fwd{a.fwd2}_ret": f2["ret"]}
            jf.write(json.dumps(rec) + "\n")
            (blocked if eq.get("entry_grade") in BLOCKED else notblocked).append(f1)
            if is_buy:
                buys.append(f1); regime_buy[reg] = regime_buy.get(reg, 0) + 1
            else:
                waits.append(f1)
    jf.close()

    print(f"\nevaluated {n_eval} (stock,date) signals -> journal: {a.journal}\n")
    print(f"=== HARDENED BUY vs WAIT  (forward {a.fwd}-day) ===")
    print("  BUY :", stats(buys, lambda x: x))
    print("  WAIT:", stats(waits, lambda x: x))
    print(f"\n=== GATE ISOLATION: BLOCKED-grade vs NOT ===")
    print("  BLOCKED    :", stats(blocked, lambda x: x))
    print("  NOT-BLOCKED:", stats(notblocked, lambda x: x))
    print(f"\n  BUY signals by market regime: {regime_buy}")


if __name__ == "__main__":
    main()
