# TradingView Intraday Assistant

Chrome extension that reads the TradingView chart you have open and gives one
decisive intraday verdict — **BUY NOW / BUY ON PULLBACK / SHORT NOW / WAIT /
NO TRADE** — with entry, stop-loss, targets, risk:reward and the reasons,
scored the way an intraday trader reads a chart (VWAP side, EMA alignment,
RSI momentum, day-range location, session time).

Everything is computed from what is visible on the chart. No external APIs,
no accounts, no data leaves your browser.

## Install (load unpacked)

1. Open `chrome://extensions` in Chrome.
2. Turn on **Developer mode** (top-right toggle).
3. Click **Load unpacked** and select this folder (`tradingview-intraday-assistant`).
4. Pin the extension from the puzzle-piece menu so the icon is always visible.

## Required chart setup (one-time, per layout)

The extension reads indicator values from the chart legend, so those
indicators must be on your chart. Add these via **Indicators** on TradingView
and save the layout:

| Indicator | TradingView name | Settings |
|---|---|---|
| VWAP | Volume Weighted Average Price | default |
| EMA 9 | Moving Average Exponential | length 9 |
| EMA 20 | Moving Average Exponential | length 20 |
| RSI | Relative Strength Index | length 14 |
| Volume | on by default | default |

Missing indicators don't break the extension — it degrades gracefully and
tells you exactly what to add — but the verdict is weaker without them.

## Usage

1. Open a chart, e.g. `https://www.tradingview.com/chart/?symbol=NSE%3AADANIENT`.
2. Use a **5-minute** timeframe (recommended for intraday).
3. Move your mouse **off the candles** (the legend shows the hovered bar's
   values — you want the latest bar).
4. Click the extension icon. The popup shows the verdict, levels, reasons and
   any warnings.

## What the verdict means

- **BUY NOW / SHORT NOW** — evidence is aligned and price is at a reasonable
  location; levels give entry, stop (structure-based with buffer), T1 = 1.5R,
  T2 = 2.5R, plus the day high/low as reference.
- **BUY ON PULLBACK** — trend is up but price is stretched > 1% above VWAP;
  chasing here is poor R:R. The entry zone is the EMA9–VWAP band.
- **WAIT** — evidence is mixed, or it's past 2:45 PM IST (no fresh intraday
  entries), or the stop would be too wide (> 2.5%).
- **NO TRADE** — market closed / weekend.

Session gates (IST): 9:15–9:40 opening-volatility caution, no new entries
after 14:45, square-off reminder from 15:20.

## Caveats

- The legend shows values for the **hovered** bar — don't hover a historical
  candle when you click the icon.
- Legend-only data means current values, no history: no ADX, RVOL or
  multi-timeframe confirmation. Treat this as a fast second opinion, not a
  full analysis engine.
- TradingView UI changes can break scraping; the extension matches legend
  rows by title text to minimize that risk.

**Educational tool — not investment advice. Trade at your own risk.**
