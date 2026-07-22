# Installing the autoIntraday scheduler (launchd)

The LaunchAgent fires `run_cycle_job.py` **every 20 minutes from 09:45, last regular cycle
12:45 IST** (09:45, 10:05, 10:25, 10:45, 11:05, 11:25, 11:45, 12:05, 12:25, 12:45), **plus a
dedicated square-off pass at 15:18** that flattens all positions and cancels resting orders (no new entries). `run_cycle_job`
flags any cycle at/after 15:15 IST as square-off. NSE holidays and the exact trading window are
enforced by the runner's guard, not launchd. Your Mac must be awake at those times for a run to fire.

> **Paper mode needs no credentials.** `GrowwClient.ensure_ready()` skips the broker login in
> paper mode (orders are simulated locally, positions tracked in the DB, prices come from the
> indicator feed), so the plist carries **no Groww keys** — only `AUTOINTRADAY_CONFIG`,
> `CLAUDE_BIN`, `HOME`, and `PATH`. The committed plist is already filled in for this machine;
> the `EDIT ME` steps below only matter if you move the project. The `claude_cli` decision
> backend reads your Claude subscription token from the macOS **login Keychain** — a gui
> LaunchAgent can reach it (verified), but the keychain must be unlocked (i.e. you're logged in).
> Live mode still requires real Groww creds in the plist `EnvironmentVariables` **and** the
> "before LIVE" hardening.

## 1. Edit the plist paths

Open `deploy/com.autointraday.cycle.plist` and fix every line marked `EDIT ME`:
- the venv Python path (`.../autoIntraday/.venv/bin/python`),
- the `run_cycle_job.py` path,
- the two log paths (create the parent dir first: `mkdir -p ~/.autointraday`).

## 2. Set credentials for the LaunchAgent

launchd jobs do NOT inherit your shell env. Put credentials the job needs where launchd can
see them — either add a `<key>EnvironmentVariables</key>` dict to the plist with
`ANTHROPIC_API_KEY`, `GROWW_API_KEY`, `GROWW_TOTP_SECRET` (and `AUTOINTRADAY_DB` — if you
override it, it MUST include a directory component, e.g. `~/.autointraday/foo.db`, not a bare
`foo.db`; plus `INTRADAY_PYTHON`/`INTRADAY_SCRIPT`, `SCREENER_PYTHON`/`SCREENER_SCRIPT` if not default), or
authenticate a persistent credential the job can read. Never commit real keys.

- **Decision backend:** set `DECISION_BACKEND=claude_cli` in the plist `EnvironmentVariables`
  to run decisions on your Claude subscription via `claude -p` (and then do NOT set
  `ANTHROPIC_API_KEY`, which would force API billing). Leave it unset (or `api`) with
  `ANTHROPIC_API_KEY` set to use the pay-per-token API. The `claude` binary must be on the
  job's PATH, or set `CLAUDE_BIN` to its absolute path.

## 3. Install

```bash
mkdir -p ~/.autointraday
cp deploy/com.autointraday.cycle.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.autointraday.cycle.plist
launchctl enable gui/$(id -u)/com.autointraday.cycle
```

## 4. Verify / test now

```bash
launchctl print gui/$(id -u)/com.autointraday.cycle   # inspect the loaded job
launchctl kickstart -k gui/$(id -u)/com.autointraday.cycle   # run once now
tail -f ~/.autointraday/cycle.out.log ~/.autointraday/cycle.err.log
```

Outside market hours the log shows "market closed — skipping" and the run exits cleanly.

## 5. Uninstall

```bash
launchctl bootout gui/$(id -u)/com.autointraday.cycle
rm ~/Library/LaunchAgents/com.autointraday.cycle.plist
```

> Start in **paper** mode (the store config default). Verify several paper cycles in the logs
> and the dashboard before switching `mode` to live — and apply the "before LIVE" hardening
> from the Phase 4 ledger first.
