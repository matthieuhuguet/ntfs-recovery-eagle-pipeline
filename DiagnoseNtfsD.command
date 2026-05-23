#!/bin/bash
set -e

DEVICE=/dev/disk4s2
LOG=/Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/ntfs_diagnose.log

mkdir -p /Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline
exec > >(tee -a "$LOG") 2>&1

echo "== NTFS diagnose =="
echo "Time: $(date)"
echo "Device: $DEVICE"
echo

if mount | grep -q "$DEVICE"; then
  diskutil unmount force "$DEVICE" || true
fi

echo "-- ntfs-3g.probe readonly --"
sudo /opt/homebrew/bin/ntfs-3g.probe --readonly "$DEVICE" || true
echo "exit=$?"
echo

echo "-- ntfs-3g.probe readwrite --"
sudo /opt/homebrew/bin/ntfs-3g.probe --readwrite "$DEVICE" || true
echo "exit=$?"
echo

echo "-- ntfsfix no-action --"
sudo /opt/homebrew/bin/ntfsfix -n "$DEVICE" || true
echo "exit=$?"
echo

echo "-- ntfsinfo mount info --"
sudo /opt/homebrew/bin/ntfsinfo -m "$DEVICE" || true
echo

echo "Done. Log: $LOG"
