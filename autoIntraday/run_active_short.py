#!/usr/bin/env python3
"""activeShort scheduler entry point — one phase per invocation.

    run_active_short.py scan        # evening: pick tomorrow's fall candidates
    run_active_short.py arm         # 09:15  : arm conditional short entries
    run_active_short.py protect     # 09:20+ : attach stops to whatever filled
    run_active_short.py expire      # 11:00  : cancel entries that never triggered
    run_active_short.py squareoff   # 15:15  : flatten anything open

Separate phases rather than a daemon: each runs at a fixed time and does one thing, so a failure
in one cannot strand another. Each takes the same fcntl lock, so two invocations of the SAME phase
can never overlap.

See docs/superpowers/specs/2026-07-31-active-short-design.md.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trading_calendar import IST, is_trading_time, load_holidays

log = logging.getLogger("autointraday.active_short_run")

PHASES = ("scan", "arm", "protect", "expire", "squareoff")
HOLIDAYS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "nse_holidays.txt")
SKILL_PATH = os.path.expanduser("~/.claude/skills/overnight-short-scanner/SKILL.md")
REPO_SKILL = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "skills/overnight-short-scanner/SKILL.md")
SCANNER_TIMEOUT_S = 900


def _lock(phase: str):
    path = os.path.expanduser(f"~/.autointraday/active_short_{phase}.lock")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    handle = open(path, "w")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None
    return handle


def _is_trading_day(day, holidays: set[str]) -> bool:
    """Weekday and not an NSE holiday. trading_calendar only exposes is_trading_time (which also
    gates on the clock), and the scan runs after the close — so the day test is derived here from
    the same two rules rather than reusing a time-of-day check."""
    return day.weekday() < 5 and day.isoformat() not in holidays


def _next_trading_day(today, holidays: set[str]) -> str:
    d = today + timedelta(days=1)
    for _ in range(10):
        if _is_trading_day(d, holidays):
            return d.isoformat()
        d += timedelta(days=1)
    return (today + timedelta(days=1)).isoformat()


def _run_scanner() -> dict:
    """Run the overnight-short-scanner skill headlessly and return its JSON.

    Same shape as the other skill backends: the skill file is the system prompt and the model
    returns the structured payload. Prefers the installed skill, falls back to the repo copy.
    """
    skill = SKILL_PATH if os.path.exists(SKILL_PATH) else REPO_SKILL
    with open(skill, encoding="utf-8") as f:
        system = f.read()
    claude = os.environ.get("CLAUDE_BIN", "claude")
    proc = subprocess.run(
        [claude, "-p", "--output-format", "json", "--model",
         os.environ.get("ACTIVE_SHORT_MODEL", "claude-opus-4-8"),
         "--append-system-prompt", system, "--allowedTools", "WebSearch"],
        input="Scan for stocks likely to FALL during the next full trading session. "
              "Return ONLY the JSON object described in the skill.",
        capture_output=True, text=True, timeout=SCANNER_TIMEOUT_S)
    if proc.returncode != 0:
        raise RuntimeError(f"scanner exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    from claude_cli_engine import _result_text
    text = _result_text(proc.stdout)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise RuntimeError(f"no JSON in scanner reply: {text[:200]!r}")
    return json.loads(text[start:end + 1])


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not argv or argv[0] not in PHASES:
        print(f"usage: run_active_short.py {{{'|'.join(PHASES)}}}", file=sys.stderr)
        return 2
    phase = argv[0]

    handle = _lock(phase)
    if handle is None:
        log.warning("another activeShort %s is running — exiting", phase)
        return 0

    import active_short_job as job
    from active_short_store import ActiveShortStore
    from groww_client import GrowwClient
    from settings import load_settings

    settings = load_settings()
    store = ActiveShortStore(settings.db_path)
    holidays = load_holidays(HOLIDAYS_PATH)
    today = datetime.now(IST).date()

    if phase == "scan":
        trade_date = _next_trading_day(today, holidays)
        n = job.scan(store, _run_scanner, today.isoformat(), trade_date)
        log.info("activeShort scan complete: %d picks for %s", n, trade_date)
        return 0

    if not _is_trading_day(today, holidays):
        log.info("not a trading day — activeShort %s skipped", phase)
        return 0

    trade_date = today.isoformat()
    client = GrowwClient(mode=job._mode(store))          # paper unless the gate has opened
    client.ensure_ready()

    def quote(symbol: str) -> dict:
        return client.get_quote(symbol)

    if phase == "arm":
        log.info("activeShort armed %d entries", job.arm(store, client, trade_date, quote))
    elif phase == "protect":
        log.info("activeShort protected %d fills",
                 job.protect(store, client, trade_date, quote))
    elif phase == "expire":
        log.info("activeShort expired %d unfilled entries",
                 job.expire_unfilled(store, client, trade_date))
    elif phase == "squareoff":
        log.info("activeShort closed %d positions",
                 job.square_off(store, client, trade_date, quote))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
