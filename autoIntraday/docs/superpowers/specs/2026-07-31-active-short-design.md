# activeShort — overnight scan, confirmation-triggered shorts at the open

Date: 2026-07-31
Status: draft for review

## What this is

A third trading mode alongside the intraday engines. The evening before, a new skill scans for
stocks likely to fall the next session. At 09:15 the next morning the bot arms **conditional**
short entries for the highest-confidence names, attaches protection as they fill, and then leaves
them alone for the user to watch manually.

It does not manage the trades, re-decide, or trail. Its whole job is: pick tonight, arm tomorrow,
protect what fills.

## Two constraints that shape everything

**1. Retail shorts in India are intraday-only.** Naked short delivery is prohibited; MIS positions
are auto-squared around 15:20. Whatever the scan predicts, every position closes the same day —
there is no option to carry a short overnight, and the bot must square off rather than rely on the
broker's auto-flatten at an unfavourable price.

**2. An unconfirmed bearish pattern is close to a coin flip.** From the pattern research: a bearish
signal *without* next-day follow-through **fails more than 60% of the time**; with follow-through
it fails around 30%. Bearish engulfing confirms at 64%, evening star 65%, shooting star 59% — all
*with* confirmation. A market order at 09:15 acts before any confirmation exists, and takes the
widest spreads of the day doing it.

So the design does not short at the open. It **arms a trigger below the confirmation level** and
lets the tape decide.

## Architecture

Three jobs, each with one responsibility.

```
 16:00 IST  scan job     -> new skill -> ranked picks -> DB (status PLANNED)
 09:15 IST  arm job      -> SELL SL_M stop-entry per pick -> DB (status ARMED)
 09:20-10:20 protect job -> stop + target on whatever FILLED -> DB (status PROTECTED)
 15:15 IST  squareoff    -> flatten anything still open
```

### 1. The scan (evening, after close)

A new skill, `overnight-short-scanner`, reasoning on daily bars — the intraday skills cannot help
here because at 16:00 there is no intraday tape for tomorrow.

Selection evidence, from the research:

- **Distribution day** — close down >0.2% on volume above the prior day. Institutional selling
  rather than drift.
- **Bearish reversal candle at resistance** — engulfing / evening star / shooting star, and only
  with **RVOL >= 1.5**. Below that there is no distribution behind the pattern.
- **Breadth regime filter** — do not short into a strongly advancing market; advance/decline and
  index trend gate the whole night's list.
- **Event exclusion** — results, ex-dividend, corporate actions override the chart entirely.

Output per candidate: symbol, confidence, the **confirmation level** (the price below which the
short is valid — typically yesterday's low), stop, target, and the reasoning. Ranked by confidence;
the top `max_shorts` are taken.

### 2. The arm (09:15)

For each selected pick, one **SELL `SL_M`** with `trigger_price` = the confirmation level.

This is a stop-ENTRY: the broker arms it and fires only if price trades down through the level.
A name that gaps up or holds firm never triggers and costs nothing. The mechanism already exists in
this codebase — `place_order` documents `SL/SL_M` as broker-armed stop entries.

Orders are placed at 09:15 but the trigger sits below the open, so no fill happens at the
worst-spread minute unless the stock is genuinely breaking down.

### 3. The protect (09:20 onward, until every fill is covered)

**This step is mandatory and is a deliberate departure from "place and forget."**

A stop-loss cannot be attached to a position that does not exist yet. If entries are armed and
never revisited, any that fill are **naked shorts with unbounded upside risk** — an unacceptable
exposure for real money.

So a light job runs every 5 minutes from 09:20 to 10:20 (configurable) and does exactly one thing:
for any entry that has FILLED, place the protective BUY `SL_M` stop and the BUY `LIMIT` target.
It does not re-decide, trail, or exit on signal. Once every armed entry is either filled-and-
protected or still resting, it stops running.

Unfilled entries are cancelled at `arm_expiry` (default 11:00) — a breakdown that has not happened
by late morning is not the setup the scan predicted.

### 4. Square-off (15:15)

Flatten anything still open, rather than leaving it to the broker's 15:20 auto-square at whatever
price it gets.

## Configuration

Mirrors the intraday config, stored in the DB and editable from the UI.

| Key | Default | Meaning |
|---|---|---|
| `active_short_enabled` | `false` | Master switch; ships **off** |
| `active_short_mode` | `paper` | `paper` \| `live` |
| `max_shorts` | `4` | Orders armed per session |
| `capital_per_short` | `25000` | Rupees per position |
| `min_confidence` | `70` | Skill confidence floor |
| `min_rvol` | `1.5` | Distribution-volume floor |
| `stop_pct` | `1.5` | Stop distance above entry, % |
| `target_pct` | `2.5` | Target distance below entry, % |
| `scan_at` | `16:00` | Evening scan time |
| `arm_at` | `09:15` | Morning arm time |
| `arm_expiry` | `11:00` | Cancel unfilled entries |
| `squareoff_at` | `15:15` | Hard flatten |
| `paper_sessions_required` | `10` | Sessions to record before live is allowed |

## Rollout: paper first, enforced in code

`paper_sessions_required` is not advisory. Live mode is **refused** until the scanner has recorded
that many completed sessions, so the hit rate is measurable before any money moves.

Next-session direction prediction is close to a coin flip, and nothing in this spec claims
otherwise. The paper period exists to find out whether this skill has an edge at all. If it does
not, the correct outcome is to delete the feature rather than fund it.

## The page

A new `st.Page("Active Short", url_path="active-short")` matching the existing style.

- **Tonight's picks** — symbol, confidence, confirmation level, stop, target, reasoning
- **This morning** — armed / filled / expired per name, with fill prices
- **History** — per session: picks, how many triggered, and the outcome, so the hit rate is
  visible rather than asserted
- **Controls** — enable, paper/live (locked until the paper period completes), capital, caps

## Testing

- Scan: parsing, ranking, confidence floor, event exclusion, breadth gate blocks the whole night.
- Arm: one order per pick, correct side and trigger, cap respected, nothing armed when disabled.
- Trigger direction: a SELL stop-entry trigger must sit **below** the current price — the mirror of
  the long-side clamp, and rejected outright otherwise.
- Protect: a filled entry gets exactly one stop and one target; an unfilled one gets neither;
  running twice does not double-place.
- Expiry cancels unfilled entries; square-off flattens open ones.
- Live mode refused before `paper_sessions_required` is met.

## Open risks

- **The signal is unproven.** Everything here rests on the scan having predictive value on the next
  session. It may not. The paper gate is the mitigation.
- **A gap-down through the trigger fills far below the intended entry.** `SL_M` is a market order
  once triggered; a stock that opens 4% down fills there, not at the confirmation level. A
  `max_gap_pct` guard should skip names that gap beyond a threshold.
- **Shorts have unbounded loss.** The protect job is what bounds it; if it fails to run, positions
  are naked. It must alert loudly on failure, not fail silently.
