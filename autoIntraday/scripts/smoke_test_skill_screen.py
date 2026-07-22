#!/usr/bin/env python3
"""Manual, not-CI smoke test: run ONE real skill-screen call (claude -p on the subscription,
agentic: movers screener + indicator tool via Bash) and print the top-5. Costs one long Opus
session; run during market hours for meaningful output.

Usage: .venv/bin/python scripts/smoke_test_skill_screen.py [EXCLUDE_SYMBOL ...]
"""
from __future__ import annotations

import sys

sys.path.insert(0, ".")

from settings import load_settings
load_settings().apply_to_environ()

from engine_factory import make_screen_engine


def main() -> None:
    engine = make_screen_engine(use_web_search=True)
    if engine is None:
        print("SCREEN_MODE=classic — nothing to smoke test; set SCREEN_MODE=skill")
        sys.exit(1)
    exclude = [s.upper() for s in sys.argv[1:]]
    print(f"running one skill screen (exclude={exclude or 'none'}) — takes several minutes…")
    results = engine.screen(exclude_symbols=exclude)
    if not results:
        print("skill screen: OK — empty top-5 (no edge right now)")
        return
    print(f"skill screen: OK — {len(results)} candidate(s):")
    for symbol, d in results:
        print(f"  {symbol:12s} {d.action:16s} q={d.trade_quality} conf={d.confidence} "
              f"entry={d.entry} stop={d.stop_loss} t1={d.target1} rr={d.risk_reward}")


if __name__ == "__main__":
    main()
