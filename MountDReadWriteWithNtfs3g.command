#!/bin/zsh
set -euo pipefail

DEVICE="/dev/disk4s2"
MOUNTPOINT="/Volumes/d"
LOG_DIR="/Users/zenray/Create/Build/Memory/Tools/Recovery/logs"
LOG="$LOG_DIR/mount_d_rw_$(date +%Y%m%d_%H%M%S).log"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG") 2>&1

echo "== Mount /Volumes/d read-write with ntfs-3g =="
echo "Time: $(date)"
echo "Device: $DEVICE"
echo "Mount point: $MOUNTPOINT"
echo "Log: $LOG"
echo

if [ ! -e "$DEVICE" ]; then
  echo "ERROR: $DEVICE does not exist. Run: diskutil list external"
  exit 2
fi

echo "-- Current disk state --"
diskutil info "$DEVICE" || true
echo

echo "-- Unmount native macOS NTFS mount if present --"
if mount | grep -q " on $MOUNTPOINT "; then
  diskutil unmount "$MOUNTPOINT" || sudo /sbin/umount "$MOUNTPOINT"
else
  diskutil unmount "$DEVICE" || true
fi

sudo /bin/mkdir -p "$MOUNTPOINT"
sudo /usr/sbin/chown root:wheel "$MOUNTPOINT"
sudo /bin/chmod 755 "$MOUNTPOINT"

echo
echo "-- Probe read-write state (no write) --"
sudo /opt/homebrew/bin/ntfs-3g.probe --readwrite "$DEVICE" || true

COMMON_OPTS=(
  -o volname=d
  -o local
  -o negative_vncache
  -o auto_xattr
  -o auto_cache
  -o noatime
  -o windows_names
  -o streams_interface=openxattr
  -o inherit
  -o uid="$(id -u)"
  -o gid="$(id -g)"
  -o allow_other
  -o big_writes
)

echo
echo "-- Attempt 1: normal read-write mount --"
set +e
sudo /opt/homebrew/bin/ntfs-3g "${COMMON_OPTS[@]}" "$DEVICE" "$MOUNTPOINT"
STATUS=$?
set -e

if [ "$STATUS" -ne 0 ]; then
  echo
  echo "Normal mount failed with status $STATUS."
  echo "Trying with remove_hiberfile because read-write was explicitly requested."
  echo "This discards only the saved Windows hibernation session if present."
  echo
  sudo /opt/homebrew/bin/ntfs-3g "${COMMON_OPTS[@]}" -o recover -o remove_hiberfile "$DEVICE" "$MOUNTPOINT"
fi

echo
echo "-- Verification --"
mount | grep -iE "($MOUNTPOINT|ntfs)" || true
diskutil info "$MOUNTPOINT" || true

TEST_FILE="$MOUNTPOINT/.codex_write_test_$(date +%s)"
echo "write test" > "$TEST_FILE"
ls -l "$TEST_FILE"
rm -f "$TEST_FILE"

echo
echo "SUCCESS: $MOUNTPOINT is mounted read-write."
echo "Log saved to: $LOG"
echo
read -r "?Press Enter to close this window..."
