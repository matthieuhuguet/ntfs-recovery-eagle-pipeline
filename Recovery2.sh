#!/bin/bash
# Recovery2.sh — Second pass ntfsundelete with exclusion list
#
# 1. Unmounts /Users/zenray/.mounty/d (or /Volumes/d)
# 2. Scans MFT with ntfsundelete for deleted media files
# 3. Filters against exclusion list (files already in Eagle)
# 4. Recovers only new files to staging
#
# MUST be run from Terminal.app (Full Disk Access required for raw device)
# ntfsundelete at /usr/local/bin/ntfsundelete (compiled arm64)

set -euo pipefail

# Auto-detect NTFS device (Seagate 8TB)
DEVICE="${NTFS_DEVICE:-}"
if [ -z "$DEVICE" ]; then
    DEVICE=$(diskutil list external 2>/dev/null | grep -i "Windows_NTFS\|Microsoft Basic Data" | awk '{print $NF}' | head -1)
    if [ -n "$DEVICE" ]; then
        DEVICE="/dev/$DEVICE"
    else
        echo "ERROR: No external NTFS partition found. Set NTFS_DEVICE=/dev/diskXsY manually."
        exit 1
    fi
fi
EXCLUSION="/Users/zenray/tmp_d_img/exclusion_list.txt"
STAGING="/Users/zenray/tmp_d_img/recovery2_staged"
SCAN_OUT="/Users/zenray/tmp_d_img/recovery2_scan.txt"
FILTERED="/Users/zenray/tmp_d_img/recovery2_filtered.txt"
LOG="/Users/zenray/tmp_d_img/recovery2.log"
WORKERS=8  # parallel icat/ntfsundelete recoveries

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

mkdir -p "$STAGING"
> "$LOG"

log "=== Recovery2 START ==="
log "Device: $DEVICE"
log "Exclusion list: $(wc -l < "$EXCLUSION") entries"

# --- Step 1: Unmount ---
log "Step 1: Unmounting disk..."
if mount | grep -q "mounty/d"; then
    diskutil unmount force /Users/zenray/.mounty/d 2>/dev/null || true
    log "  Unmounted .mounty/d"
fi
if mount | grep -q "/Volumes/d"; then
    diskutil unmount force /Volumes/d 2>/dev/null || true
    log "  Unmounted /Volumes/d"
fi

# Verify unmounted
if mount | grep -q "$DEVICE"; then
    log "ERROR: $DEVICE still mounted. Cannot proceed."
    exit 1
fi
log "  Device unmounted OK"

# --- Step 2: Scan MFT for deleted files ---
log "Step 2: Scanning MFT for deleted media files..."
log "  This may take 5-15 minutes on 8TB..."

# ntfsundelete -s scans and lists deleted files
# -P = show percentage, -m '*' = match all filenames, -f = force (hibernated partition)
# Output format: Inode  Flags  %age  Date  Time  Size  Filename
log "  Running: ntfsundelete $DEVICE -s -P -p 0 -m '*' -f"
/usr/local/bin/ntfsundelete "$DEVICE" -s -P -p 0 -m '*' -f 2>&1 | tee "$SCAN_OUT.raw" | \
    grep -iE '\.(png|jpg|jpeg|webp|gif|bmp|mp4|webm)' > "$SCAN_OUT" || true
log "  Raw scan output: $(wc -l < "$SCAN_OUT.raw") lines"

TOTAL_DELETED=$(wc -l < "$SCAN_OUT")
log "  Found $TOTAL_DELETED deleted media files"

# --- Step 3: Filter against exclusion list ---
log "Step 3: Filtering against exclusion list..."

# Build a set of excluded basenames (lowercase for case-insensitive matching)
python3 << 'PYEOF'
import os
import re

SCAN = os.environ.get("SCAN_OUT", "/Users/zenray/tmp_d_img/recovery2_scan.txt")
EXCL = os.environ.get("EXCLUSION", "/Users/zenray/tmp_d_img/exclusion_list.txt")
FILT = os.environ.get("FILTERED", "/Users/zenray/tmp_d_img/recovery2_filtered.txt")

# Load exclusion set (lowercase, strip ntfsundelete suffixes)
excluded = set()
with open(EXCL) as f:
    for line in f:
        name = line.strip().lower()
        if name:
            excluded.add(name)
            # Also add without extension variants
            stem = os.path.splitext(name)[0]
            excluded.add(stem)

print(f"Exclusion set: {len(excluded)} entries")

# Parse ntfsundelete scan output and filter
# Format: "Inode    Flags  %age     Date         Time       Size  Filename"
kept = 0
skipped = 0
with open(SCAN) as fin, open(FILT, "w") as fout:
    for line in fin:
        parts = line.strip().split()
        if len(parts) < 7:
            continue
        inode = parts[0]
        pct = parts[2].rstrip("%")
        filename = " ".join(parts[6:])

        # Strip ntfsundelete numeric suffixes for comparison
        clean = filename
        while True:
            stem, ext = os.path.splitext(clean)
            if ext and ext[1:].isdigit():
                clean = stem
            else:
                break

        basename_lower = os.path.basename(clean).lower()

        if basename_lower in excluded:
            skipped += 1
            continue

        # Only recover files with >= 30% recoverable
        try:
            if int(pct) < 30:
                skipped += 1
                continue
        except ValueError:
            pass

        fout.write(f"{inode}\t{pct}\t{filename}\n")
        kept += 1

print(f"Kept: {kept}, Skipped: {skipped}")
PYEOF

FILTERED_COUNT=$(wc -l < "$FILTERED")
log "  After filtering: $FILTERED_COUNT new files to recover"

if [ "$FILTERED_COUNT" -eq 0 ]; then
    log "No new files to recover. Done."
    exit 0
fi

# --- Step 4: Recover filtered files ---
log "Step 4: Recovering $FILTERED_COUNT files to $STAGING..."

RECOVERED=0
FAILED=0

while IFS=$'\t' read -r INODE PCT FILENAME; do
    # Recover by inode
    if /usr/local/bin/ntfsundelete "$DEVICE" -u -i "$INODE" -o "$STAGING/" -f -d "$STAGING" 2>/dev/null; then
        RECOVERED=$((RECOVERED + 1))
    else
        FAILED=$((FAILED + 1))
    fi

    # Progress every 500
    TOTAL=$((RECOVERED + FAILED))
    if [ $((TOTAL % 500)) -eq 0 ]; then
        log "  Recovered $RECOVERED / $TOTAL (failed: $FAILED)"
    fi
done < "$FILTERED"

log "Step 4 DONE: $RECOVERED recovered, $FAILED failed"
log "=== Recovery2 COMPLETE ==="
log "Staged files in: $STAGING"
log "Next: run ImportRecoveredToEagle.py with SOURCE=$STAGING"
