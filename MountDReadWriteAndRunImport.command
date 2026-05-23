#!/bin/bash
set -e

DEVICE=/dev/disk4s2
MOUNT=/Volumes/d
LOG=/Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/pipeline.log
LAUNCH_LOG=/Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/mount_and_run.log
NTFS3G=/opt/homebrew/bin/ntfs-3g

mkdir -p /Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline
cd /Users/zenray/Create/Build/Memory/Tools/Recovery
exec > >(tee -a "$LAUNCH_LOG") 2>&1

echo "═══════════════════════════════════════════"
echo "  MOUNT D RW + _RECOVERED → Eagle"
echo "═══════════════════════════════════════════"
echo "[*] Device: $DEVICE"
echo "[*] Mount : $MOUNT"
echo "[*] Log   : $LOG"
echo "[*] Launch log: $LAUNCH_LOG"
echo ""

if mount | grep -q "$DEVICE"; then
  echo "[*] Unmount existing $DEVICE mount..."
  diskutil unmount force "$DEVICE" || true
  sleep 2
fi

sudo mkdir -p "$MOUNT"
sudo chown root:wheel "$MOUNT"
sudo chmod 755 "$MOUNT"

echo "[*] Mount ntfs-3g read-write..."
sudo "$NTFS3G" "$DEVICE" "$MOUNT" \
  -o local,allow_other,negative_vncache,auto_xattr,auto_cache,noatime,windows_names,streams_interface=openxattr,inherit,uid="$(id -u)",gid="$(id -g)",big_writes,remove_hiberfile

echo "[*] Mounted. Starting Python pipeline without shell pre-scan..."

export DELETE_SOURCE_AFTER_IMPORT=1
export EAGLE_COPY_GRACE_SECONDS=180

nohup /usr/bin/python3 /Users/zenray/Create/Build/Memory/Tools/Recovery/ImportRecoveredDiskToEagle.py \
  >> "$LOG" 2>&1 &

PID=$!
echo "[*] PID: $PID"
echo "[*] Follow with: tail -f $LOG"
echo ""
echo "La fenêtre peut être fermée."
