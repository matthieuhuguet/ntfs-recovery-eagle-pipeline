#!/bin/bash
# Pass unique ntfsundelete *.webm + *.WEBM, append au staging filtré existant.
set -u

NTFSUNDELETE="/usr/local/bin/ntfsundelete"
DEVICE="/dev/disk12s2"
MOUNT="/Volumes/d"
STAGING="/Users/zenray/NTFS_Recovery_ntfsundelete/latest_filtered"
LOG="${STAGING}/recover.log"
STATUS="${STAGING}/status.txt"

log()  { echo "[$(date +%H:%M:%S)] $*" | tee -a "${LOG}"; }
stat() { echo "$1" > "${STATUS}"; log "STATUS: $1"; }

stat "WEBM_ONLY_INIT"

# Démontage strict
if mount | grep -q "${MOUNT} "; then
  log "Démontage ${MOUNT}..."
  diskutil unmount "${MOUNT}" 2>&1 | tee -a "${LOG}" || sudo diskutil unmount force "${MOUNT}" 2>&1 | tee -a "${LOG}"
fi

for ext in webm WEBM; do
  stat "PASS_${ext}_SKIPJUMP"
  log "Pass *.${ext}..."
  BEFORE=$(find "${STAGING}/recovered" -type f 2>/dev/null | wc -l | tr -d ' ')
  sudo "${NTFSUNDELETE}" -u -m "*.${ext}" -p 30 -f -d "${STAGING}/recovered" "${DEVICE}" 2>&1 | tee -a "${LOG}"
  AFTER=$(find "${STAGING}/recovered" -type f 2>/dev/null | wc -l | tr -d ' ')
  log "Pass *.${ext} fini : +$(( AFTER - BEFORE )) (total ${AFTER})"
done

RESTORED=$(find "${STAGING}/recovered" -type f 2>/dev/null | wc -l | tr -d ' ')
SIZE=$(du -sh "${STAGING}/recovered" 2>/dev/null | awk '{print $1}')
stat "DONE_${RESTORED}_FILES"
log "WEBM passes done. Total: ${RESTORED} fichiers, ${SIZE}"
echo "==============================================="
echo " WEBM PASSES TERMINÉES"
echo " Total : ${RESTORED} fichiers / ${SIZE}"
echo "==============================================="
