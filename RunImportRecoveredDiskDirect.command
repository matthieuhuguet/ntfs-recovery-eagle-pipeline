#!/bin/bash
set -e

LOG=/Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/pipeline.log

mkdir -p /Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline
cd /Users/zenray/Create/Build/Memory/Tools/Recovery

echo "═══════════════════════════════════════════"
echo "  DIRECT _RECOVERED → Eagle PIPELINE"
echo "═══════════════════════════════════════════"
echo "[*] No shell pre-scan. Python will validate /Volumes/d itself."
echo "[*] Log: $LOG"
echo ""

export DELETE_SOURCE_AFTER_IMPORT=1
export EAGLE_COPY_GRACE_SECONDS=180

nohup /usr/bin/python3 /Users/zenray/Create/Build/Memory/Tools/Recovery/ImportRecoveredDiskToEagle.py \
  >> "$LOG" 2>&1 &

PID=$!
echo "[*] PID: $PID"
echo "[*] Follow with: tail -f $LOG"
echo ""
echo "Pipeline lancé. Cette fenêtre peut être fermée."
