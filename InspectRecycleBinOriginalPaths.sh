#!/usr/bin/env bash
# Decode Windows Recycle Bin $I metadata from the NTFS raw device.
#
# This finds the original paths of recycled files/folders. It is read-only.

set -Eeuo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

DEVICE="${DEVICE:-/dev/rdisk12s2}"
SESSION_DIR="${SESSION_DIR:-/Users/zenray/Desktop/NTFS_Recovery_SS_VideoGames/latest}"
MFT_DELETED="${MFT_DELETED:-$SESSION_DIR/mft_deleted_all.txt}"
OUT_DIR="${OUT_DIR:-$SESSION_DIR/recycle_original_paths}"
OUT_TSV="$OUT_DIR/recycle_i_paths.tsv"
LOG="$OUT_DIR/inspect.log"

mkdir -p "$OUT_DIR"
: > "$OUT_TSV"
: > "$LOG"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"
}

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Run with sudo from Terminal.app." >&2
  exit 1
fi

[[ -f "$MFT_DELETED" ]] || {
  echo "Missing MFT list: $MFT_DELETED" >&2
  exit 1
}

log "Device: $DEVICE"
log "MFT list: $MFT_DELETED"
log "Output: $OUT_TSV"

python3 - "$MFT_DELETED" "$OUT_DIR/i_manifest.tsv" <<'PY'
import re
import sys

src, dst = sys.argv[1:3]
line_re = re.compile(r'^(?P<kind>\S+)\s+\*\s*(?P<inode>[0-9]+(?:-[0-9]+-[0-9]+)?):\s*(?P<path>.*)$')
rows = []
with open(src, 'r', encoding='utf-8', errors='replace') as f:
    for raw in f:
        line = raw.rstrip('\n')
        m = line_re.match(line)
        if not m:
            continue
        path = m.group('path').replace('\\', '/')
        base = path.rsplit('/', 1)[-1]
        if not re.fullmatch(r'\$I[A-Z0-9]{6,}', base, flags=re.IGNORECASE):
            continue
        rows.append((m.group('inode'), path, base, '$R' + base[2:]))

with open(dst, 'w', encoding='utf-8') as out:
    for row in rows:
        out.write('\t'.join(row) + '\n')
PY

count="$(wc -l < "$OUT_DIR/i_manifest.tsv" | tr -d ' ')"
log "Recycle \$I entries found: $count"

while IFS=$'\t' read -r inode recycle_i_path i_name r_name; do
  tmp="$OUT_DIR/${i_name}.bin"
  if ! icat -f ntfs "$DEVICE" "$inode" > "$tmp" 2>>"$LOG"; then
    printf '%s\t%s\t%s\t%s\tERROR_ICAT\n' "$inode" "$recycle_i_path" "$i_name" "$r_name" >> "$OUT_TSV"
    rm -f "$tmp"
    continue
  fi
  python3 - "$tmp" "$inode" "$recycle_i_path" "$i_name" "$r_name" >> "$OUT_TSV" <<'PY'
import datetime as dt
import os
import struct
import sys

bin_path, inode, recycle_i_path, i_name, r_name = sys.argv[1:6]
data = open(bin_path, 'rb').read()
size = ''
deleted_at = ''
original = ''
try:
    if len(data) >= 24:
        size = str(struct.unpack_from('<Q', data, 8)[0])
        ft = struct.unpack_from('<Q', data, 16)[0]
        if ft:
            ts = (ft - 116444736000000000) / 10_000_000
            deleted_at = dt.datetime.fromtimestamp(ts).isoformat(sep=' ', timespec='seconds')
        text = data[24:].decode('utf-16le', errors='ignore')
        original = text.split('\x00', 1)[0]
except Exception as exc:
    original = f'ERROR_PARSE:{exc}'
print('\t'.join([inode, recycle_i_path, i_name, r_name, size, deleted_at, original]))
PY
done < "$OUT_DIR/i_manifest.tsv"

log "Decoded paths:"
cat "$OUT_TSV" | tee -a "$LOG"
log "Done."
