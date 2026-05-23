#!/usr/bin/env bash
# Recover deleted NTFS files from /Volumes/d/SS_VideoGames.
#
# Run from Terminal.app, not from the Codex shell:
#   sudo bash /Users/zenray/Create/Build/Memory/Tools/Recovery/RecoverSSVideoGamesNTFS.sh
#
# Terminal.app must have Full Disk Access.

set -Eeuo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SOURCE_MOUNT="${SOURCE_MOUNT:-/Volumes/d}"
TARGET_ROOT="${TARGET_ROOT:-SS_VideoGames}"
STAGING_BASE="${STAGING_BASE:-/Users/zenray/Desktop/NTFS_Recovery_SS_VideoGames}"
SESSION_ID="${SESSION_ID:-$(date '+%Y%m%d_%H%M%S')}"
SESSION_DIR="${SESSION_DIR:-$STAGING_BASE/$SESSION_ID}"
RECOVERED_ROOT="$SESSION_DIR/recovered"
LOG="$SESSION_DIR/recovery.log"
STATUS_FILE="$SESSION_DIR/status.txt"
DELETED_ALL="$SESSION_DIR/mft_deleted_all.txt"
DELETED_TARGET="$SESSION_DIR/mft_deleted_${TARGET_ROOT}.txt"
ALL_TARGET="$SESSION_DIR/mft_all_${TARGET_ROOT}.txt"
MANIFEST="$SESSION_DIR/recovery_manifest.tsv"
SUCCESS_LOG="$SESSION_DIR/success.tsv"
FAIL_LOG="$SESSION_DIR/fail.tsv"
FLS_ERR="$SESSION_DIR/fls.err"
JOBS="${JOBS:-16}"
SYNC_BACK="${SYNC_BACK:-0}"
EXPECTED_FINAL_FILES="${EXPECTED_FINAL_FILES:-150000}"
EXPECTED_TOP_DIRS="${EXPECTED_TOP_DIRS:-110}"
TARGET_USER="${TARGET_USER:-${SUDO_USER:-zenray}}"

mkdir -p "$SESSION_DIR" "$RECOVERED_ROOT"
: > "$SUCCESS_LOG"
: > "$FAIL_LOG"
ln -sfn "$SESSION_DIR" "$STAGING_BASE/latest"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"
}

status() {
  printf '%s\n' "$*" > "$STATUS_FILE"
}

die() {
  log "ERROR: $*"
  status "ERROR: $*"
  exit 1
}

require_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    die "Run with sudo from Terminal.app."
  fi
}

require_tools() {
  local missing=0
  for tool in diskutil dd fls icat python3 xargs rsync; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      log "Missing tool: $tool"
      missing=1
    fi
  done
  [[ "$missing" -eq 0 ]] || die "Required tools are missing."
}

resolve_device() {
  if [[ -n "${SOURCE_DEVICE:-}" ]]; then
    DEVICE_NODE="$SOURCE_DEVICE"
  else
    DEVICE_NODE="$(diskutil info "$SOURCE_MOUNT" 2>/dev/null | awk -F: '/Device Node/ {gsub(/^[ \t]+/, "", $2); print $2; exit}')"
  fi

  [[ -n "${DEVICE_NODE:-}" ]] || die "Could not resolve device for $SOURCE_MOUNT."
  DEVICE_ID="$(basename "$DEVICE_NODE")"
  RAW_DEVICE="/dev/r${DEVICE_ID}"
  [[ -e "$RAW_DEVICE" ]] || die "Raw device not found: $RAW_DEVICE."
}

mounts_source() {
  mount | grep -F " on $SOURCE_MOUNT " >/dev/null 2>&1
}

unmount_source_strict() {
  if ! mounts_source; then
    log "$SOURCE_MOUNT is already unmounted."
    return 0
  fi

  status "UNMOUNTING"
  log "Unmounting $SOURCE_MOUNT..."
  diskutil unmount "$SOURCE_MOUNT" 2>&1 | tee -a "$LOG" || true
  sleep 2

  if mounts_source; then
    log "Normal unmount failed. Trying force unmount..."
    diskutil unmount force "$SOURCE_MOUNT" 2>&1 | tee -a "$LOG" || true
    sleep 2
  fi

  if mounts_source; then
    log "Force unmount failed. Open handles:"
    lsof +f -- "$SOURCE_MOUNT" 2>&1 | tee -a "$LOG" || true
    die "Source disk is still mounted. Stop apps using $SOURCE_MOUNT and rerun."
  fi

  log "Source disk unmounted. No writes will hit the source during recovery."
}

remount_source() {
  if mounts_source; then
    return 0
  fi

  status "REMOUNTING"
  log "Remounting $DEVICE_ID..."
  diskutil mount "$DEVICE_ID" 2>&1 | tee -a "$LOG" || true
}

verify_raw_access() {
  status "RAW_ACCESS_TEST"
  log "Testing raw read access on $RAW_DEVICE..."
  dd if="$RAW_DEVICE" of=/dev/null bs=512 count=1 2>/dev/null || {
    die "Raw device read failed. Give Terminal.app Full Disk Access, reopen Terminal, then rerun."
  }
  log "Raw device access OK."
}

scan_deleted_entries() {
  status "MFT_SCAN_DELETED"
  log "Scanning deleted MFT entries. This can take a while on an 8 TB NTFS volume."
  : > "$DELETED_ALL"
  : > "$DELETED_TARGET"
  : > "$FLS_ERR"

  (
    set +e
    fls -r -d -p -f ntfs "$RAW_DEVICE" 2>"$FLS_ERR" \
      | tee "$DELETED_ALL" \
      | grep -i -- "${TARGET_ROOT}/" > "$DELETED_TARGET"
    exit 0
  ) &

  local pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    sleep 10
    local all_lines target_lines
    all_lines="$(wc -l < "$DELETED_ALL" | tr -d ' ')"
    target_lines="$(wc -l < "$DELETED_TARGET" | tr -d ' ')"
    status "MFT_SCAN_DELETED all=$all_lines target=$target_lines"
    log "MFT deleted scan progress: all=$all_lines target=$target_lines"
  done
  wait "$pid" || true

  local total
  total="$(wc -l < "$DELETED_TARGET" | tr -d ' ')"
  log "Deleted target entries found: $total"
}

scan_all_target_entries_for_audit() {
  status "MFT_SCAN_ALL_TARGET"
  log "Auditing all MFT entries under ${TARGET_ROOT}/ for comparison."
  : > "$ALL_TARGET"
  : > "$FLS_ERR.all"

  (
    set +e
    fls -r -p -f ntfs "$RAW_DEVICE" 2>"$FLS_ERR.all" \
      | grep -i -- "${TARGET_ROOT}/" > "$ALL_TARGET"
    exit 0
  ) &

  local pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    sleep 10
    local target_lines
    target_lines="$(wc -l < "$ALL_TARGET" | tr -d ' ')"
    status "MFT_SCAN_ALL_TARGET target=$target_lines"
    log "MFT all-entry audit progress: target=$target_lines"
  done
  wait "$pid" || true

  local total
  total="$(wc -l < "$ALL_TARGET" | tr -d ' ')"
  log "All target MFT entries found: $total"
}

build_manifest() {
  status "BUILD_MANIFEST"
  log "Building recovery manifest from deleted entries."
  python3 - "$DELETED_TARGET" "$MANIFEST" "$TARGET_ROOT" <<'PY'
import os
import re
import sys

src, dst, target_root = sys.argv[1:4]
line_re = re.compile(r'^(?P<kind>\S+)\s+(?P<deleted>\*)?\s*(?P<inode>[0-9]+(?:-[0-9]+-[0-9]+)?):\s*(?P<path>.*)$')
rows = []
seen = set()

with open(src, 'r', encoding='utf-8', errors='replace') as f:
    for raw in f:
        line = raw.rstrip('\n')
        m = line_re.match(line)
        if not m:
            continue
        kind = m.group('kind')
        if kind.startswith('d/'):
            continue
        inode = m.group('inode')
        path = m.group('path').replace('\\', '/').lstrip('/')
        if not path.lower().startswith(target_root.lower() + '/'):
            continue
        parts = [p for p in path.split('/') if p not in ('', '.')]
        if any(p == '..' for p in parts):
            continue
        clean_path = '/'.join(parts)
        key = (inode, clean_path)
        if key in seen:
            continue
        seen.add(key)
        rows.append((inode, clean_path))

with open(dst, 'w', encoding='utf-8') as out:
    for inode, path in rows:
        out.write(f'{inode}\t{path}\n')
PY

  local total
  total="$(wc -l < "$MANIFEST" | tr -d ' ')"
  log "Manifest files to recover: $total"
  [[ "$total" -gt 0 ]] || die "No recoverable deleted files found for ${TARGET_ROOT}/."
}

recover_one() {
  local row="$1"
  local inode="${row%%$'\t'*}"
  local rel_path="${row#*$'\t'}"
  local dest="$RECOVERED_ROOT/$rel_path"
  local dir base name ext candidate n

  [[ -n "$inode" && -n "$rel_path" && "$rel_path" != "$inode" ]] || return 0

  dir="$(dirname "$dest")"
  base="$(basename "$dest")"
  mkdir -p "$dir"

  candidate="$dest"
  if [[ -e "$candidate" ]]; then
    ext=""
    name="$base"
    if [[ "$base" == *.* ]]; then
      ext=".${base##*.}"
      name="${base%.*}"
    fi
    n=1
    while [[ -e "$candidate" ]]; do
      candidate="$dir/${name}.__mft_${inode}_${n}${ext}"
      n=$((n + 1))
    done
  fi

  if icat -f ntfs "$RAW_DEVICE" "$inode" > "$candidate" 2>/dev/null && [[ -s "$candidate" ]]; then
    printf '%s\t%s\n' "$inode" "${candidate#$RECOVERED_ROOT/}" >> "$SUCCESS_LOG"
  else
    rm -f "$candidate"
    printf '%s\t%s\n' "$inode" "$rel_path" >> "$FAIL_LOG"
  fi
}

recover_manifest_parallel() {
  local total
  total="$(wc -l < "$MANIFEST" | tr -d ' ')"
  status "RECOVERING total=$total ok=0 fail=0"
  log "Recovering with icat in parallel: jobs=$JOBS total=$total"

  export RAW_DEVICE RECOVERED_ROOT SUCCESS_LOG FAIL_LOG
  export -f recover_one

  while IFS= read -r row; do
    printf '%s\0' "$row"
  done < "$MANIFEST" | xargs -0 -P "$JOBS" -n 1 bash -c 'recover_one "$1"' _ &
  local pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    sleep 10
    local ok fail
    ok="$(wc -l < "$SUCCESS_LOG" | tr -d ' ')"
    fail="$(wc -l < "$FAIL_LOG" | tr -d ' ')"
    status "RECOVERING total=$total ok=$ok fail=$fail"
    log "Recovery progress: ok=$ok fail=$fail total=$total"
  done
  wait "$pid" || true

  local ok fail
  ok="$(wc -l < "$SUCCESS_LOG" | tr -d ' ')"
  fail="$(wc -l < "$FAIL_LOG" | tr -d ' ')"
  log "Recovery complete: ok=$ok fail=$fail total=$total"
}

sync_back_if_requested() {
  if [[ "$SYNC_BACK" != "1" ]]; then
    log "SYNC_BACK=0, leaving recovered files staged only: $RECOVERED_ROOT/$TARGET_ROOT"
    return 0
  fi

  remount_source
  local source_tree="$RECOVERED_ROOT/$TARGET_ROOT/"
  local dest_tree="$SOURCE_MOUNT/$TARGET_ROOT/"
  [[ -d "$source_tree" ]] || die "Recovered tree missing: $source_tree"
  [[ -d "$dest_tree" ]] || die "Destination tree missing after remount: $dest_tree"

  status "SYNC_BACK"
  log "Syncing recovered files back with --ignore-existing."
  rsync -a --ignore-existing --progress "$source_tree" "$dest_tree" 2>&1 | tee -a "$LOG"
}

final_report() {
  local ok fail stage_files stage_dirs dest_files dest_dirs
  ok="$(wc -l < "$SUCCESS_LOG" | tr -d ' ')"
  fail="$(wc -l < "$FAIL_LOG" | tr -d ' ')"
  stage_files="$(find "$RECOVERED_ROOT/$TARGET_ROOT" -type f 2>/dev/null | wc -l | tr -d ' ')"
  stage_dirs="$(find "$RECOVERED_ROOT/$TARGET_ROOT" -mindepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"

  if mounts_source && [[ -d "$SOURCE_MOUNT/$TARGET_ROOT" ]]; then
    dest_files="$(find "$SOURCE_MOUNT/$TARGET_ROOT" -type f 2>/dev/null | wc -l | tr -d ' ')"
    dest_dirs="$(find "$SOURCE_MOUNT/$TARGET_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l | tr -d ' ')"
  else
    dest_files="unmounted"
    dest_dirs="unmounted"
  fi

  {
    echo "OK=$ok"
    echo "FAIL=$fail"
    echo "STAGED_FILES=$stage_files"
    echo "STAGED_DIRS=$stage_dirs"
    echo "DEST_FILES=$dest_files"
    echo "DEST_TOP_DIRS=$dest_dirs"
    echo "EXPECTED_FINAL_FILES=$EXPECTED_FINAL_FILES"
    echo "EXPECTED_TOP_DIRS=$EXPECTED_TOP_DIRS"
    echo "SESSION_DIR=$SESSION_DIR"
  } > "$SESSION_DIR/final_report.txt"

  chown -R "$TARGET_USER" "$SESSION_DIR" 2>/dev/null || true
  status "DONE ok=$ok fail=$fail staged_files=$stage_files dest_files=$dest_files"
  log "Final report written to $SESSION_DIR/final_report.txt"
}

main() {
  require_root
  require_tools
  resolve_device

  log "=== NTFS ${TARGET_ROOT} recovery v2 ==="
  log "Source mount: $SOURCE_MOUNT"
  log "Device: $DEVICE_NODE"
  log "Raw device: $RAW_DEVICE"
  log "Session: $SESSION_DIR"
  log "Jobs: $JOBS"
  log "Sync back: $SYNC_BACK"

  trap remount_source EXIT

  verify_raw_access
  unmount_source_strict
  scan_deleted_entries
  scan_all_target_entries_for_audit
  build_manifest
  recover_manifest_parallel
  sync_back_if_requested
  final_report

  log "=== DONE ==="
}

main "$@"
