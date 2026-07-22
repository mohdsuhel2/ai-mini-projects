#!/usr/bin/env python3
"""Validate the intraday REVERSAL-SHORT hypothesis on ~60 days of 15m bars.

Hypothesis (Fix 1): after a blow-off/climax top (new day-high, overbought, extended
above VWAP, volume climax), shorting the FIRST CONFIRMED LOWER HIGH (stop above day-high)
has positive expectancy AND beats the current rule of waiting for a close below VWAP.

Guard test: does requiring the lower-high confirmation protect against tops that KEEP
running (the TBZ case)? Compare vs shorting the climax bar blindly (no confirmation).

Objective, fixed thresholds, whole universe — no cherry-picking. Reports MFE/MAE
(assumption-free) plus a target/stop simulation, for each entry rule.
"""
import json, urllib.request, datetime, time, pickle, os, statistics, sys

UA = "Mozilla/5.0"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scratchpad", ".ivbt_cache.pkl")


def fetch(sym):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}.NS?range=60d&interval=15m"
    d = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30))
    r = d["chart"]["result"][0]
    ts = r["timestamp"]; q = r["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(ts):
        o, h, l, c, v = q["open"][i], q["high"][i], q["low"][i], q["close"][i], q["volume"][i]
        if None in (o, h, l, c): continue
        ist = datetime.datetime.utcfromtimestamp(t) + datetime.timedelta(hours=5, minutes=30)
        out.append((ist, o, h, l, c, v or 0))
    return out


def load_universe(name):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "universes", f"{name}.txt")
    return [ln.strip() for ln in open(path) if ln.strip() and not ln.startswith("#")]


def ema(vals, n):
    k = 2 / (n + 1); out = []; e = None
    for v in vals:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, n=14):
    out = [None] * len(closes)
    if len(closes) < n + 1: return out
    gains = [max(closes[i] - closes[i-1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i-1] - closes[i], 0) for i in range(1, len(closes))]
    ag = sum(gains[:n]) / n; al = sum(losses[:n]) / n
    for i in range(n, len(closes)):
        if i > n:
            ag = (ag * (n-1) + gains[i-1]) / n
            al = (al * (n-1) + losses[i-1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def build(bars):
    """Attach continuous EMA9/20 + RSI14 and per-day VWAP/day-high/low/avgvol."""
    closes = [b[4] for b in bars]
    e9 = ema(closes, 9); e20 = ema(closes, 20); rs = rsi(closes, 14)
    rows = []
    cur_day = None; cum_pv = 0.0; cum_v = 0.0; dh = -1e9; dl = 1e9; vols = []
    for i, b in enumerate(bars):
        dt, o, h, l, c, v = b
        day = dt.strftime("%Y-%m-%d")
        if day != cur_day:
            cur_day = day; cum_pv = 0.0; cum_v = 0.0; dh = -1e9; dl = 1e9; vols = []
        tp = (h + l + c) / 3
        cum_pv += tp * v; cum_v += v
        vwap = cum_pv / cum_v if cum_v else c
        dh = max(dh, h); dl = min(dl, l); vols.append(v)
        avgv = sum(vols) / len(vols)
        rows.append(dict(i=i, dt=dt, day=day, o=o, h=h, l=l, c=c, v=v,
                         vwap=vwap, dh=dh, dl=dl, avgv=avgv,
                         e9=e9[i], e20=e20[i], rsi=rs[i], nbar=len(vols)))
    return rows


def day_groups(rows):
    g = {}
    for r in rows:
        g.setdefault(r["day"], []).append(r)
    return g


def forward(day_rows, k_entry, entry_px, stop_px):
    """From bar index k_entry+1 to EOD: MFE(down), MAE(up), and target/stop sim.
    target = VWAP at entry (short covers at VWAP); stop = stop_px (above day high)."""
    fwd = day_rows[k_entry + 1:]
    if not fwd:
        return None
    lo = min(r["l"] for r in fwd); hi = max(r["h"] for r in fwd)
    mfe = (entry_px - lo) / entry_px * 100   # favorable = price falls
    mae = (entry_px - hi) / entry_px * 100   # adverse = price rises (negative)
    tgt = day_rows[k_entry]["vwap"]           # cover at VWAP
    res = "OPEN"
    for r in fwd:
        if r["h"] >= stop_px: res = "STOP"; break
        if r["l"] <= tgt: res = "TARGET"; break
    e20 = day_rows[k_entry]["e20"]
    conf = e20 is not None and entry_px < e20    # below EMA20 = confirmed downtrend (Fix-2 filter)
    return dict(mfe=round(mfe, 2), mae=round(mae, 2), tgt=round(tgt, 2), res=res,
                tgt_pct=round((entry_px - tgt) / entry_px * 100, 2), conf=conf)


def find_setups(day_rows):
    """Return list of setups per day. A climax top then (a) lower-high short, (b) vwap-loss short,
    (c) blind climax short. Only ONE climax per day (the first qualifying)."""
    setups = []
    n = len(day_rows)
    climax_k = None
    for k in range(2, n - 3):                          # need >=3 bars of forward room
        r = day_rows[k]
        if r["rsi"] is None: continue
        new_high = r["h"] >= r["dh"] - 1e-9            # fresh day high on this bar
        ext = (r["c"] - r["vwap"]) / r["vwap"] * 100 >= 4.0   # real parabola: >4% above VWAP
        ob = r["rsi"] >= 75
        vclimax = r["avgv"] > 0 and r["v"] >= 3.0 * r["avgv"]
        if new_high and ext and ob and vclimax:
            climax_k = k; break
    if climax_k is None:
        return setups
    T = day_rows[climax_k]
    stop = T["dh"] * 1.002
    # (a) anticipatory: first LOWER HIGH + down close within next 3 bars
    lh_k = None
    for k in range(climax_k + 1, min(climax_k + 5, n)):
        r = day_rows[k]
        if r["h"] < T["h"] and r["c"] < r["o"]:   # lower high + red bar
            lh_k = k; break
    # (b) vwap-loss: first bar that CLOSES below VWAP after climax
    vl_k = None
    for k in range(climax_k + 1, n):
        if day_rows[k]["c"] < day_rows[k]["vwap"]:
            vl_k = k; break
    # (c) blind: short at climax close (no confirmation)
    out = dict(day=T["day"], climax_time=T["dt"].strftime("%H:%M"),
               climax_high=round(T["h"], 2), climax_close=round(T["c"], 2),
               ext_pct=round((T["c"] - T["vwap"]) / T["vwap"] * 100, 2), rsi=round(T["rsi"], 1),
               vratio=round(T["v"] / T["avgv"], 1))
    out["blind"] = forward(day_rows, climax_k, T["c"], stop)
    out["lh"] = forward(day_rows, lh_k, day_rows[lh_k]["c"], stop) if lh_k is not None else None
    out["vl"] = forward(day_rows, vl_k, day_rows[vl_k]["c"], stop) if vl_k is not None else None
    out["lh_bars_after"] = (lh_k - climax_k) if lh_k is not None else None
    out["vl_bars_after"] = (vl_k - climax_k) if vl_k is not None else None
    setups.append(out)
    return setups


def summarize(name, rows):
    rows = [r for r in rows if r]
    if not rows:
        print(f"  {name}: n=0"); return
    mfe = [r["mfe"] for r in rows]; mae = [r["mae"] for r in rows]
    tgt = sum(1 for r in rows if r["res"] == "TARGET"); stp = sum(1 for r in rows if r["res"] == "STOP")
    opn = sum(1 for r in rows if r["res"] == "OPEN")
    print(f"  {name:10} n={len(rows):3}  TARGET={tgt:3} STOP={stp:3} OPEN={opn:3}  "
          f"win%={tgt/len(rows)*100:4.0f}  avgMFE={statistics.mean(mfe):+.2f}%  avgMAE={statistics.mean(mae):+.2f}%  "
          f"medMFE={statistics.median(mfe):+.2f}%")


def main():
    uni = sys.argv[1] if len(sys.argv) > 1 else "nifty100"
    symbols = load_universe(uni)
    cache = {}
    if os.path.exists(CACHE):
        cache = pickle.load(open(CACHE, "rb"))
    need = [s for s in symbols if s not in cache]
    for i, s in enumerate(need):
        try:
            cache[s] = fetch(s)
        except Exception:
            cache[s] = None
        sys.stderr.write(f"\rfetch {i+1}/{len(need)} {s}      "); sys.stderr.flush()
        time.sleep(0.25)
    sys.stderr.write("\n")
    pickle.dump(cache, open(CACHE, "wb"))

    all_setups = []
    for s in symbols:
        bars = cache.get(s)
        if not bars or len(bars) < 60:
            continue
        rows = build(bars)
        for day, drows in day_groups(rows).items():
            all_setups.extend(find_setups(drows))

    print(f"\nuniverse={uni}  symbols={len(symbols)}  blow-off setups found={len(all_setups)}\n")
    # continuation risk: after the climax, how often does BLIND short stop out?
    print("=== ENTRY-RULE COMPARISON (short after a blow-off climax top) ===")
    summarize("BLIND(climax)", [s["blind"] for s in all_setups])
    summarize("LOWER-HIGH", [s["lh"] for s in all_setups if s["lh"]])
    summarize("VWAP-LOSS", [s["vl"] for s in all_setups if s["vl"]])
    # coverage: how many setups produced each entry
    nlh = sum(1 for s in all_setups if s["lh"]); nvl = sum(1 for s in all_setups if s["vl"])
    print(f"\n  coverage: lower-high entry fired {nlh}/{len(all_setups)}, vwap-loss fired {nvl}/{len(all_setups)}")
    # For setups where BOTH fired, paired comparison of MFE
    both = [(s["lh"], s["vl"]) for s in all_setups if s["lh"] and s["vl"]]
    if both:
        lh_better = sum(1 for a, b in both if a["mfe"] > b["mfe"])
        print(f"  paired (both fired, n={len(both)}): lower-high captured more decline in {lh_better} "
              f"({lh_better/len(both)*100:.0f}%); avg entry-timing edge "
              f"{statistics.mean([a['mae'] for a,b in both]) - statistics.mean([b['mae'] for a,b in both]):+.2f}% MAE")
    # Fix-2: does 'below EMA20 at entry' (confirmed) beat 'above EMA20' (unconfirmed fade)?
    print("\n=== FIX-2: confirmation filter (price below EMA20 at entry = confirmed downtrend) ===")
    for lbl, key in [("LOWER-HIGH", "lh"), ("BLIND", "blind")]:
        es = [s[key] for s in all_setups if s[key]]
        conf = [e for e in es if e["conf"]]; unc = [e for e in es if not e["conf"]]
        def wr(x): return f"n={len(x):2} win%={sum(1 for e in x if e['res']=='TARGET')/len(x)*100:3.0f} stop%={sum(1 for e in x if e['res']=='STOP')/len(x)*100:3.0f} avgMFE={statistics.mean([e['mfe'] for e in x]):+.2f} avgMAE={statistics.mean([e['mae'] for e in x]):+.2f}" if x else "n=0"
        print(f"  {lbl:11} confirmed(<EMA20): {wr(conf)}")
        print(f"  {lbl:11} unconfirmed(>EMA20): {wr(unc)}")
    # dump a few examples
    print("\n=== sample setups ===")
    for s in all_setups[:60]:
        print(f"  {s['day']} climax {s['climax_time']} hi{s['climax_high']} ext{s['ext_pct']}% rsi{s['rsi']} v{s['vratio']}x "
              f"| LH {s['lh']['res'] if s['lh'] else '--'} mfe{s['lh']['mfe'] if s['lh'] else '--'} "
              f"| VL {s['vl']['res'] if s['vl'] else '--'} mfe{s['vl']['mfe'] if s['vl'] else '--'} "
              f"| BLIND {s['blind']['res'] if s['blind'] else '--'}")


if __name__ == "__main__":
    main()
