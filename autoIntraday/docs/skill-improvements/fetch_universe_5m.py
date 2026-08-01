"""Fetch 5m bars for a broad liquid NSE universe — the sample for the exhaustion study."""
import json, urllib.request, urllib.parse, time
from datetime import datetime, timezone, timedelta

IST = timezone(timedelta(hours=5, minutes=30))
SYMS = ["RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "SBIN", "AXISBANK", "ITC",
        "LT", "BHARTIARTL", "KOTAKBANK", "HINDUNILVR", "MARUTI", "TATAMOTORS", "TATASTEEL",
        "SUNPHARMA", "WIPRO", "ADANIENT", "BAJFINANCE", "INDUSINDBK", "KALYANKJIL",
        "BALKRISIND", "HINDALCO", "JSWSTEEL", "VEDL", "ONGC", "COALINDIA", "NTPC",
        "POWERGRID", "TECHM", "ULTRACEMCO", "GRASIM", "TITAN", "NESTLEIND", "DIVISLAB"]

out = {}
for s in SYMS:
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(s + ".NS", safe="") + "?interval=5m&range=1mo")
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"})
        d = json.loads(urllib.request.urlopen(req, timeout=40).read().decode())
        res = d["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        days = {}
        for i, t in enumerate(res["timestamp"]):
            c = q["close"][i]
            if c is None or q["open"][i] is None:
                continue
            dt = datetime.fromtimestamp(int(t), tz=timezone.utc).astimezone(IST)
            days.setdefault(dt.date().isoformat(), []).append(
                {"t": dt.strftime("%H:%M"), "o": q["open"][i], "h": q["high"][i],
                 "l": q["low"][i], "c": c, "v": q["volume"][i] or 0})
        out[s] = days
        print(f"  {s}: {len(days)} days", flush=True)
    except Exception as e:
        print(f"  {s}: {type(e).__name__} {e}", flush=True)
    time.sleep(0.25)

json.dump(out, open("universe_5m.json", "w"))
print(f"\n{len(out)} symbols, "
      f"{sum(len(v) for v in out.values())} symbol-days")
