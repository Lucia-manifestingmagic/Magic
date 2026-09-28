#!/usr/bin/env bash
#
# Schedule the sync with launchd instead of cron.
#
#   ./scripts/schedule_launchd.sh install | remove | status | run-now
#
# macOS blocks crontab edits unless the calling app has Full Disk Access, which
# fails with a bare exit code and no explanation. launchd is the supported way
# on macOS and needs no special permission for a user agent.

set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.manifestingmagic.dashboard.sync"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

case "${1:-status}" in
  install)
    mkdir -p "$HOME/Library/LaunchAgents" "$REPO/data"
    cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$REPO/.venv/bin/python</string>
    <string>-m</string><string>app.sync</string>
    <string>--days</string><string>28</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>StartInterval</key><integer>21600</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$REPO/data/sync.log</string>
  <key>StandardErrorPath</key><string>$REPO/data/sync.log</string>
</dict>
</plist>
PLISTEOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "Installed. Runs every 6 hours, logging to data/sync.log"
    echo "Survives reboots. Skips a run if the Mac is asleep and catches up on wake."
    ;;
  remove)
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed."
    ;;
  run-now)
    launchctl start "$LABEL" && echo "Triggered. Watch: tail -f $REPO/data/sync.log"
    ;;
  status)
    if launchctl list | grep -q "$LABEL"; then
      echo "Scheduled:"; launchctl list | grep "$LABEL" | awk '{print "  pid="$1" last_exit="$2" "$3}'
    else
      echo "Not scheduled. Run: ./scripts/schedule_launchd.sh install"
    fi
    ;;
  *) echo "usage: $0 [install|remove|status|run-now]"; exit 1 ;;
esac
