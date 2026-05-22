#!/bin/bash
# Récupération NTFS filtrée par extensions images/vidéos uniquement.
# 5 passes séquentielles ntfsundelete : png, jpg, jpeg, webp, webm.
# Lancer via Terminal.app (FDA).

set -u

NTFSUNDELETE="/usr/local/bin/ntfsundelete"
DEVICE="$(diskutil info /Volumes/d 2>/dev/null | awk -F': *' '/Device Node/{print $2}' | tr -d ' ')"
[[ -z "${DEVICE}" ]] && DEVICE="/dev/disk4s2"
MOUNT="/Volumes/d"
STAGING_ROOT="/Users/zenray/NTFS_Recovery_ntfsundelete"
TS="$(date +%Y%m%d_%H%M%S)"
STAGING="${STAGING_ROOT}/run_${TS}_filtered"
LATEST="${STAGING_ROOT}/latest_filtered"
STATUS="${STAGING}/status.txt"
LOG="${STAGING}/recover.log"
PERCENT=30

EXTS=(png jpg jpeg webp webm PNG JPG JPEG WEBP WEBM)

mkdir -p "${STAGING}/recovered"
ln -sfn "${STAGING}" "${LATEST}"

log()  { echo "[$(date +%H:%M:%S)] $*" | tee -a "${LOG}"; }
stat() { echo "$1" > "${STATUS}"; log "STATUS: $1"; }

stat "INIT"
log "Staging : ${STAGING}"
log "Device  : ${DEVICE}"
log "Tool    : ${NTFSUNDELETE} ($(${NTFSUNDELETE} -V 2>&1 | head -1))"

# Démontage strict
if mount | grep -q "${MOUNT} "; then
  log "Démontage ${MOUNT}..."
  diskutil unmount "${MOUNT}" 2>&1 | tee -a "${LOG}" || {
    sudo diskutil unmount force "${MOUNT}" 2>&1 | tee -a "${LOG}" || {
      stat "ABORT_UNMOUNT_FAILED"; exit 2
    }
  }
fi
log "OK : ${MOUNT} démonté."

# 5 passes séquentielles, 10 si on compte les variantes uppercase
for ext in "${EXTS[@]}"; do
  stat "PASS_${ext}"
  log "Pass *.${ext} (recoverable >= ${PERCENT}%)..."
  COUNT_BEFORE=$(find "${STAGING}/recovered" -type f 2>/dev/null | wc -l | tr -d ' ')
  sudo "${NTFSUNDELETE}" -u -m "*.${ext}" -p "${PERCENT}" -f -d "${STAGING}/recovered" "${DEVICE}" 2>&1 | tee -a "${LOG}"
  COUNT_AFTER=$(find "${STAGING}/recovered" -type f 2>/dev/null | wc -l | tr -d ' ')
  DELTA=$(( COUNT_AFTER - COUNT_BEFORE ))
  SIZE=$(du -sh "${STAGING}/recovered" 2>/dev/null | awk '{print $1}')
  log "Pass *.${ext} fini : +${DELTA} fichiers (total ${COUNT_AFTER}, ${SIZE})"
done

# Audit final
stat "AUDIT"
RESTORED_COUNT=$(find "${STAGING}/recovered" -type f 2>/dev/null | wc -l | tr -d ' ')
RESTORED_SIZE=$(du -sh "${STAGING}/recovered" 2>/dev/null | awk '{print $1}')
log "Total final : ${RESTORED_COUNT} fichiers, ${RESTORED_SIZE}"

log "Signatures jeux :"
for pat in "cyberpunk" "last_of_us" "lastofus" "tlou" "elden_ring" "elden ring" "witcher" "horizon" "rdr2" "red_dead" "gta" "minecraft" "spider" "skyrim" "fallout" "doom" "halo" "zelda" "screenshot" "final_fantasy" "alanwake" "bloodborne" "stray" "god_of_war" "plague_tale"; do
  n=$(find "${STAGING}/recovered" -iname "*${pat}*" 2>/dev/null | wc -l | tr -d ' ')
  if [[ "$n" -gt 0 ]]; then
    log "  ${pat} : ${n}"
  fi
done

stat "DONE_${RESTORED_COUNT}_FILES"
echo
echo "==============================================="
echo " RÉCUPÉRATION FILTRÉE TERMINÉE"
echo " Staging : ${STAGING}/recovered"
echo " Fichiers: ${RESTORED_COUNT}"
echo " Taille  : ${RESTORED_SIZE}"
echo "==============================================="
