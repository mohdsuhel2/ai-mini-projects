# Manual analysis page — run any skill against your live positions, on demand

Date: 2026-08-12 · Status: approved

## Problem

Every existing skill call in autoIntraday is automated: the trading cycle screens and decides on
its own schedule, the Skill Lab runs a scheduled A/B comparison on synthetic universes, the swing
job walks the holdings book. There is no way to sit down mid-session, look at what is actually
open in the broker right now, point a chosen skill at two or three of those names, and read the
full analysis it produces.

The Skill Lab is the closest thing and is deliberately not this: it is schedule-driven, it forces
a terse structured decision, it disables web search for fairness, and it discards the model's
narrative. What is missing is a manual desk — my real book, my pick of skill, right now, with the
skill's complete reasoning preserved.

## Design

A new top-nav page, `Analyze`, separate from Skill Lab.

### Flow

1. **Fetch positions** authenticates to Groww and calls `get_positions()` — the intraday/MIS book
   only. Delivery holdings are the Swing page's job and are not shown here.
2. The result is persisted to a new `positions` snapshot table so Streamlit's constant reruns
   never re-hit the broker and the fetch survives a browser refresh. Same shape and lifecycle as
   the existing `holdings` table.
3. The positions render in an `st.data_editor` with a leading boolean checkbox column, beside a
   skill dropdown fed by `available_skills()` (discovery-based — a skill installed tomorrow
   appears with no code change).
4. A caption states the cost before you commit: *N selected × ~2 min ≈ M minutes*.
5. **Analyze selected** launches the run. **Top 5 from this skill** launches the alternate mode.

### Execution model

Each skill call is a full agentic run (roughly two minutes), so N selected symbols is an N × 2
minute wait. A blocking spinner would freeze the UI for the duration, so this follows the proven
`swing_job.py` pattern: `analysis_job.py` runs as a detached subprocess, seeding one PENDING row
per symbol and filling each to DONE or ERROR, while the page polls and shows live per-symbol
progress. You can navigate away and come back. A single symbol failing marks only that row ERROR
and the run continues.

### Engine (`manual_engine.py`)

Embeds the chosen skill's `SKILL.md` as the system prompt plus a MANUAL MODE addendum, allows
`Bash(<StockAnalayze venv python>:*)` — one allowlist entry covering every skill's data tool —
and `WebSearch`. Unlike the Skill Lab, web search stays ON: this is a real decision about real
money, not a controlled comparison.

Like `observe.py`, this module never imports a broker client. The safety property is structural,
not a flag: there is no code path here that can place, modify or cancel an order.

One generic schema serves all nine installed skills and any added later:

| field | type | notes |
|---|---|---|
| `symbol` | string | required — the skill names its own picks in top-5 mode |
| `report` | string | the skill's full natural analysis, markdown |
| `verdict` | string | the skill's own call, free text (`BUY NOW`, `HOLD`, `IGNITION_BUY`, …) |
| `conviction` | integer\|null | 0-100 |
| `entry`, `stop`, `target1`, `target2`, `target3`, `risk_reward` | number\|null | |
| `summary` | string | one line |

Levels are nullable throughout, so the overnight-short-scanner leaving them empty is a valid
answer rather than a parse error. Two schemas wrap this item: `ONE_SCHEMA` for a single symbol
and `TOP5_SCHEMA` (`{picks: [item, …≤5]}`) for the top-5 call.

### Top 5

A second button runs the same engine with no symbols and `TOP5_SCHEMA` — a single call in which
the skill runs its own screener or Mode 2 and picks its own names. Results render through exactly
the same components as the per-position path.

### Results

One expander per symbol, each containing `st.tabs(["Formatted", "Raw"])`.

- **Formatted** — verdict badge, conviction, a levels table (entry / stop / targets / R:R) and the
  one-line summary.
- **Raw** — the `report` markdown, then the complete CLI envelope pretty-printed with `st.json`
  (cost, duration, tools run), falling back to `st.code` when the envelope is not JSON. This
  reuses the `_run_output_dialog` precedent.

Runs persist to `analysis_runs` / `analysis_results` in the main store, following the
`swing_runs` / `swing_verdicts` precedent. Re-running the same stocks under a different skill
accumulates results rather than replacing them, so the two can be compared.

## Testing

- **Engine**: both schemas parse; nullable levels tolerated; missing `report` or `verdict` raises;
  the allowlist contains the StockAnalayze python prefix and WebSearch; the skill file is read
  into the system prompt and a missing skill file raises.
- **Store**: results round-trip; `positions` snapshot replaces cleanly; additive migration leaves
  an old DB working.
- **Job**: a per-symbol failure marks that row ERROR without ending the run; top-5 mode seeds rows
  from the skill's own picks.
- **Dashboard**: checkbox selection maps to the right symbols; the formatted card renders verdict
  and levels; the raw tab falls back to `st.code` on non-JSON.

## Out of scope

Placing any order from this page. Multi-skill fan-out within a single run — run again with another
skill; results accumulate. Scheduling (that is Skill Lab's job). Delivery holdings (Swing's job).
