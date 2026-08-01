# activeShort launchd agents

Five phases, five agents. Separate rather than one daemon so a failure in one cannot strand
another, and so each fires at exactly the time it is meant to.

All times IST. The Mac must be awake — these are `StartCalendarInterval` agents, same as the
intraday cycle.

| Agent | Fires | Phase |
|---|---|---|
| `com.autointraday.ashort.scan` | 16:00 Mon-Fri | `scan` — picks tomorrow's candidates |
| `com.autointraday.ashort.arm` | 09:15 Mon-Fri | `arm` — conditional short entries |
| `com.autointraday.ashort.protect` | 09:20-10:20 every 5 min | `protect` — stops on fills |
| `com.autointraday.ashort.expire` | 11:00 Mon-Fri | `expire` — cancel untriggered |
| `com.autointraday.ashort.squareoff` | 15:15 Mon-Fri | `squareoff` — flatten |

**The protect agent is safety-critical.** A filled short with no stop is unbounded risk. If it
stops firing, positions armed that morning can fill and sit naked. Check its log first if anything
looks wrong.

## Install

Generate the five plists from the template below (substituting the phase and times), then:

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.autointraday.ashort.<phase>.plist
launchctl list | grep ashort           # confirm all five loaded
```

## Template

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.autointraday.ashort.PHASE</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/mohdsuhel/ai-mini-projects/autoIntraday/.venv/bin/python</string>
    <string>/Users/mohdsuhel/ai-mini-projects/autoIntraday/run_active_short.py</string>
    <string>PHASE</string>
  </array>
  <key>WorkingDirectory</key>
  <string>/Users/mohdsuhel/ai-mini-projects/autoIntraday</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key><string>/Users/mohdsuhel</string>
    <key>PATH</key><string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin</string>
    <key>GROWW_GATEWAY_URL</key><string>...</string>
    <key>GROWW_GATEWAY_TOKEN</key><string>...</string>
    <key>CLAUDE_BIN</key><string>/opt/homebrew/bin/claude</string>
  </dict>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>HH</integer><key>Minute</key><integer>MM</integer></dict>
    <!-- repeat for Weekday 2..5 -->
  </array>
  <key>StandardOutPath</key><string>/Users/mohdsuhel/.autointraday/ashort.out.log</string>
  <key>StandardErrorPath</key><string>/Users/mohdsuhel/.autointraday/ashort.err.log</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
```

The `protect` agent needs 13 `StartCalendarInterval` entries per weekday (09:20, 09:25 … 10:20).
Each phase takes its own `fcntl` lock, so an overlapping fire exits cleanly rather than
double-placing.

## Before enabling

activeShort ships **disabled** and in **paper**, and `live_allowed()` refuses real money until the
configured number of paper sessions is recorded. Installing these agents is safe: with
`active_short_enabled = 0` every phase logs "disabled — nothing to do" and exits.

Turn it on from the dashboard's Active Short page once you want the scan running.
