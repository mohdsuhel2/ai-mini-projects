#!/usr/bin/env python3
"""Claude usage-window primer. Fires ~1.5h before the first trading cycle so the Claude
subscription's rolling ~5h usage window STARTS early — the window then resets mid-session
(around 1 PM) instead of after the close, giving the trading day a second window's worth of
headroom. A throwaway 'ping' call is all it takes to anchor the window.

Gated by the DB `primer_enabled` flag (toggle it from the dashboard). When disabled this is a
clean no-op. Never places orders, never touches trading state. See
docs/superpowers/specs/2026-07-20-claude-primer-design.md."""
from __future__ import annotations

import logging
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("autointraday.primer")


def main() -> int:
    from settings import load_settings
    settings = load_settings()
    settings.apply_to_environ()

    from store import Store
    os.makedirs(os.path.dirname(settings.db_path), exist_ok=True)
    cfg = Store(settings.db_path).get_config()
    if not cfg.primer_enabled:
        log.info("primer disabled (config.primer_enabled=false) — no-op")
        return 0

    claude_bin = os.environ.get("CLAUDE_BIN", settings.claude_bin or "claude")
    argv = [claude_bin, "-p", "--model", settings.model, "reply with: ok"]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except Exception as e:
        log.error("primer call failed to launch: %s", e)
        return 1
    if proc.returncode != 0:
        log.error("primer call exit %s: %s", proc.returncode, proc.stderr.strip()[:300])
        return 1
    log.info("primer OK — Claude usage window started (reply: %s)",
             proc.stdout.strip()[:80])
    return 0


if __name__ == "__main__":
    sys.exit(main())
