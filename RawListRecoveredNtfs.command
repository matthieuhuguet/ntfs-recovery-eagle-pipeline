#!/bin/bash
set -e

DEVICE=/dev/disk4s2
NTFS_PATH=/SS_VideoGames/A_Trier/_RECOVERED
OUT=/Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/raw_ntfsls_recovered.txt
LOG=/Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/raw_ntfsls_recovered.log

mkdir -p /Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline
exec > >(tee -a "$LOG") 2>&1

echo "== Raw ntfsls _RECOVERED =="
echo "Time: $(date)"
echo "Device: $DEVICE"
echo "Path: $NTFS_PATH"
echo

if mount | grep -q "$DEVICE"; then
  diskutil unmount force "$DEVICE" || true
fi

echo "-- non-recursive preview --"
sudo /opt/homebrew/bin/ntfsls -l -p "$NTFS_PATH" "$DEVICE" | sed -n '1,80p'
echo

echo "-- recursive list to $OUT --"
sudo /opt/homebrew/bin/ntfsls -R -p "$NTFS_PATH" "$DEVICE" > "$OUT"
wc -l "$OUT"
echo "Done."
