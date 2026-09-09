#!/usr/bin/env bash
#
# Install (or remove) the scheduled sync.
#
#   ./scripts/schedule_sync.sh install    every 6 hours
#   ./scripts/schedule_sync.sh remove
#   ./scripts/schedule_sync.sh status
#
# Writes to the user crontab, touching only lines tagged with the marker below
# so anything else already scheduled is left alone.

set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARK="# manifesting-magic-dashboard-sync"
LINE="0 */6 * * * cd $REPO && .venv/bin/python -m app.sync --days 28 >> $REPO/data/sync.log 2>&1 $MARK"

case "${1:-status}" in
  install)
    ( crontab -l 2>/dev/null | grep -v "$MARK" || true; echo "$LINE" ) | crontab -
    echo "Installed. Syncs every 6 hours, logging to data/sync.log"
    echo "The dashboard reads from SQLite and never blocks on the APIs, so a"
    echo "failed sync leaves the last good data on screen and the footer says so."
    ;;
  remove)
    ( crontab -l 2>/dev/null | grep -v "$MARK" || true ) | crontab -
    echo "Removed."
    ;;
  status)
    if crontab -l 2>/dev/null | grep -q "$MARK"; then
      echo "Scheduled:"; crontab -l | grep "$MARK"
    else
      echo "Not scheduled. Run: ./scripts/schedule_sync.sh install"
    fi
    ;;
  *) echo "usage: $0 [install|remove|status]"; exit 1 ;;
esac
