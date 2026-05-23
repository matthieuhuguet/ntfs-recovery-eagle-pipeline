#!/bin/bash
set -e

DEVICE=/dev/disk4s2
LOG=/Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/pipeline.log
LAUNCH_LOG=/Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/mount_readonly_and_run.log

mkdir -p /Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline
cd /Users/zenray/Create/Build/Memory/Tools/Recovery
exec > >(tee -a "$LAUNCH_LOG") 2>&1

echo "═══════════════════════════════════════════"
echo "  MOUNT D RO + _RECOVERED → Eagle"
echo "═══════════════════════════════════════════"
echo "[*] Device: $DEVICE"
echo "[*] Log   : $LOG"
echo "[*] Launch log: $LAUNCH_LOG"
echo ""

if mount | grep -q "$DEVICE"; then
  echo "[*] Unmount existing $DEVICE mount..."
  diskutil unmount force "$DEVICE" || true
  sleep 2
fi

echo "[*] Mount native macOS NTFS read-only..."
diskutil mount "$DEVICE"

echo "[*] Mounted read-only. Starting Python pipeline without source deletion..."

export DELETE_SOURCE_AFTER_IMPORT=0
export EAGLE_COPY_GRACE_SECONDS=180

nohup /usr/bin/python3 /Users/zenray/Create/Build/Memory/Tools/Recovery/ImportRecoveredDiskToEagle.py \
  >> "$LOG" 2>&1 &

PID=$!
echo "[*] PID: $PID"
echo "[*] Follow with: tail -f $LOG"
echo ""
echo "Import lancé. La suppression source sera une passe séparée."
