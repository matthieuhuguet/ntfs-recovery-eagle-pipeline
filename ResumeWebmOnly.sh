#!/bin/bash
# Reprend uniquement les passes webm/WEBM dans le staging existant.
# Skip les inodes webm déjà restaurés (resume depuis last_inode+1).
set -u

NTFSUNDELETE="/usr/local/bin/ntfsundelete"
DEVICE="/dev/disk4s2"
MOUNT="/Volumes/d"
STAGING="/Users/zenray/NTFS_Recovery_ntfsundelete/run_20260522_145030_filtered"
LATEST="/Users/zenray/NTFS_Recovery_ntfsundelete/latest_filtered"
RECOVERED="${STAGING}/recovered"
LOG="${STAGING}/recover.log"
STATUS="${STAGING}/status.txt"
PERCENT=30
RESUME_INODE=1157247

log()  { echo "[$(date +%H:%M:%S)] $*" | tee -a "${LOG}"; }
stat() { echo "$1" > "${STATUS}"; log "STATUS: $1"; }

ln -sfn "${STAGING}" "${LATEST}"
stat "RESUME_INIT"
log "Staging : ${STAGING}"
log "Device  : ${DEVICE}"
log "Resume webm depuis inode : ${RESUME_INODE}"

# Démontage strict
if mount | grep -q "${MOUNT} "; then
  log "Démontage ${MOUNT}..."
  diskutil unmount "${MOUNT}" 2>&1 | tee -a "${LOG}" || {
    sudo diskutil unmount force "${MOUNT}" 2>&1 | tee -a "${LOG}" || {
      stat "ABORT_UNMOUNT_FAILED"; exit 2
    }
  }
fi
log "OK : démonté."

# Pass webm minuscule (resume depuis 1157247)
stat "PASS_webm_resume_${RESUME_INODE}"
log "Pass *.webm depuis inode ${RESUME_INODE}..."
COUNT_BEFORE=$(find "${RECOVERED}" -type f 2>/dev/null | wc -l | tr -d ' ')
sudo "${NTFSUNDELETE}" -u -i "${RESUME_INODE}-100000000" -m '*.webm' -p "${PERCENT}" -f -d "${RECOVERED}" "${DEVICE}" 2>&1 | tee -a "${LOG}"
COUNT_AFTER=$(find "${RECOVERED}" -type f 2>/dev/null | wc -l | tr -d ' ')
log "Pass webm fini : +$(( COUNT_AFTER - COUNT_BEFORE )) fichiers (total ${COUNT_AFTER})"

# Pass WEBM majuscule (full, on a peut-être rien fait avant)
stat "PASS_WEBM_full"
log "Pass *.WEBM full..."
COUNT_BEFORE=${COUNT_AFTER}
sudo "${NTFSUNDELETE}" -u -m '*.WEBM' -p "${PERCENT}" -f -d "${RECOVERED}" "${DEVICE}" 2>&1 | tee -a "${LOG}"
COUNT_AFTER=$(find "${RECOVERED}" -type f 2>/dev/null | wc -l | tr -d ' ')
log "Pass WEBM fini : +$(( COUNT_AFTER - COUNT_BEFORE )) fichiers (total ${COUNT_AFTER})"

# Audit final
stat "AUDIT"
RESTORED_COUNT=$(find "${RECOVERED}" -type f 2>/dev/null | wc -l | tr -d ' ')
RESTORED_SIZE=$(du -sh "${RECOVERED}" 2>/dev/null | awk '{print $1}')
log "Total final : ${RESTORED_COUNT} fichiers, ${RESTORED_SIZE}"

for pat in cyberpunk last_of_us tlou elden_ring witcher horizon rdr red_dead gta minecraft spider skyrim fallout doom halo zelda screenshot final_fantasy alanwake bloodborne stray god_of_war plague_tale; do
  n=$(find "${RECOVERED}" -iname "*${pat}*" 2>/dev/null | wc -l | tr -d ' ')
  [[ "$n" -gt 0 ]] && log "  ${pat} : ${n}"
done

stat "DONE_${RESTORED_COUNT}_FILES"
echo
echo "==============================================="
echo " RESUME WEBM TERMINÉ"
echo " Staging : ${RECOVERED}"
echo " Fichiers: ${RESTORED_COUNT}"
echo " Taille  : ${RESTORED_SIZE}"
echo "==============================================="
