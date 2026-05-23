#!/usr/bin/env bash
# Launch the safer NTFS recovery from Terminal.app.

set -euo pipefail

SCRIPT="/Users/zenray/Create/Build/Memory/Tools/Recovery/RecoverSSVideoGamesNTFS.sh"

echo "Stopping previous unsafe recovery attempts if they are still running..."
sudo pkill -f '[t]erminal_recovery.sh' 2>/dev/null || true
sudo pkill -f '[f]ls -r .*rdisk12s2' 2>/dev/null || true
sudo pkill -f '[i]cat -f ntfs /dev/rdisk12s2' 2>/dev/null || true

echo "Starting safer v2 recovery."
echo "Recovered files will stay staged first; no sync back to /Volumes/d yet."
echo "Latest status: /Users/zenray/Desktop/NTFS_Recovery_SS_VideoGames/latest/status.txt"

sudo env JOBS="${JOBS:-16}" SYNC_BACK="${SYNC_BACK:-0}" bash "$SCRIPT"
