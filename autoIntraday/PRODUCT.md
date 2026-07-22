# autoIntraday

Personal automated intraday trading system for NSE (India). A launchd-scheduled Python bot
screens the market with an LLM decision engine and places paper/live orders via Groww; a
Streamlit dashboard (`dashboard.py`) is the single operator surface: status, positions,
P&L, run history, and controls (pause, capital rules, paper/live mode, schedule).

- **Register:** product. The dashboard serves a single expert operator (the owner) checking
  in between trading cycles, often quickly on a laptop. It must read at a glance and never
  surprise; density over decoration.
- **Users:** one — the owner. No onboarding, no marketing surface.
- **Tone:** calm operations console. Numbers first (₹, quantities, times in IST), states
  color-coded (LIVE=red family, PAPER=green family, PAUSED=amber), everything else neutral.
- **Hard technical constraints:** Streamlit only, CSS injection only (no components, no new
  deps); tables render as markdown (`_md_table`) because PyArrow's `st.dataframe` path
  segfaults (mimalloc) — never reintroduce it; must look right in BOTH Streamlit light and
  dark themes (rgba overlays / theme vars, no hardcoded page backgrounds).
