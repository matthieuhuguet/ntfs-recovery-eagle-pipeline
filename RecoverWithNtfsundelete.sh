#!/bin/bash
# Récupération NTFS de /Volumes/d (disk12s2) via ntfsundelete UNIQUEMENT.
# Doit être lancé via Terminal.app (Full Disk Access).
#
# Phases :
#   1. Vérifier ntfsundelete présent
#   2. Démonter /Volumes/d (strict, abort sinon)
#   3. Scan complet → scan_full.txt (toutes entrées supprimées avec %recoverable)
#   4. Restauration en masse vers staging Mac (pas /Volumes/d)
#   5. Audit dossiers + fichiers + recherche screenshots jeux

set -u

NTFSUNDELETE="/usr/local/bin/ntfsundelete"
DEVICE="/dev/disk12s2"
MOUNT="/Volumes/d"
STAGING_ROOT="/Users/zenray/NTFS_Recovery_ntfsundelete"
TS="$(date +%Y%m%d_%H%M%S)"
STAGING="${STAGING_ROOT}/run_${TS}"
LATEST="${STAGING_ROOT}/latest"
STATUS="${STAGING}/status.txt"
LOG="${STAGING}/recover.log"
SCAN="${STAGING}/scan_full.txt"

mkdir -p "${STAGING}/recovered"
ln -sfn "${STAGING}" "${LATEST}"

log()  { echo "[$(date +%H:%M:%S)] $*" | tee -a "${LOG}"; }
stat() { echo "$1" > "${STATUS}"; log "STATUS: $1"; }

stat "PHASE_0_INIT"
log "Staging : ${STAGING}"
log "Device  : ${DEVICE}"
log "Tool    : ${NTFSUNDELETE} ($(${NTFSUNDELETE} -V 2>&1 | head -1))"

# Phase 1 — binaire OK ?
if [[ ! -x "${NTFSUNDELETE}" ]]; then
  log "ERREUR : ${NTFSUNDELETE} introuvable. Abort."
  stat "ABORT_NO_BINARY"
  exit 1
fi

# Phase 2 — démontage strict
stat "PHASE_2_UNMOUNT"
if mount | grep -q "${MOUNT} "; then
  log "Démontage ${MOUNT}..."
  if ! diskutil unmount "${MOUNT}" 2>&1 | tee -a "${LOG}"; then
    log "Force unmount..."
    sudo diskutil unmount force "${MOUNT}" 2>&1 | tee -a "${LOG}" || {
      log "ABORT : impossible de démonter ${MOUNT}."
      stat "ABORT_UNMOUNT_FAILED"
      exit 2
    }
  fi
fi
log "OK : ${MOUNT} démonté."

# Phase 3 — scan complet
stat "PHASE_3_SCAN"
log "Scan ntfsundelete complet (peut prendre 10-30 min)..."
log "Commande : sudo ${NTFSUNDELETE} -s -P -p 0 -m '*' -f ${DEVICE}"
# -s scan, -P percent info, -p 0 = inclure même 0% (on filtre après), -m '*' all, -f force (hibernation)
sudo "${NTFSUNDELETE}" -s -P -p 0 -m '*' -f "${DEVICE}" > "${SCAN}" 2>> "${LOG}" || {
  log "Scan exit non-zero, mais on continue si scan_full.txt a du contenu."
}

SCAN_LINES=$(wc -l < "${SCAN}" | tr -d ' ')
log "Scan terminé : ${SCAN_LINES} lignes dans scan_full.txt"
stat "PHASE_3_DONE_${SCAN_LINES}_LINES"

if [[ "${SCAN_LINES}" -lt 100 ]]; then
  log "ATTENTION : très peu de lignes scannées. Premières lignes :"
  head -30 "${SCAN}" | tee -a "${LOG}"
fi

# Phase 4 — extraire stats pré-restauration
stat "PHASE_4_PRE_AUDIT"
log "Pré-audit du scan :"
awk 'NR>1 && /^[0-9]/ {n++} END{print "Total entrées supprimées :", n+0}' "${SCAN}" | tee -a "${LOG}"
awk 'NR>1 && /^[0-9]/ {
  match($0, /[0-9]+%/);
  if (RSTART>0) {
    p = substr($0, RSTART, RLENGTH-1);
    if (p+0 >= 90) g90++;
    if (p+0 >= 50) g50++;
    if (p+0 >= 1)  g1++;
  }
} END {
  print "Recoverable >=90%:", g90+0;
  print "Recoverable >=50%:", g50+0;
  print "Recoverable  >=1%:", g1+0;
}' "${SCAN}" | tee -a "${LOG}"

# Phase 5 — restauration en masse
stat "PHASE_5_RESTORE"
log "Restauration ntfsundelete vers ${STAGING}/recovered/"
log "Commande : sudo ${NTFSUNDELETE} -u -m '*' -p 30 -f -d ${STAGING}/recovered ${DEVICE}"
# -u undelete, -m '*' tout, -p 30 = >=30% récupérable, -f force, -d destination

sudo "${NTFSUNDELETE}" -u -m '*' -p 30 -f -d "${STAGING}/recovered" "${DEVICE}" 2>&1 | tee -a "${LOG}"

RESTORE_RC=${PIPESTATUS[0]}
log "Restauration exit code : ${RESTORE_RC}"

# Phase 6 — audit final
stat "PHASE_6_AUDIT"
RESTORED_COUNT=$(find "${STAGING}/recovered" -type f 2>/dev/null | wc -l | tr -d ' ')
RESTORED_DIRS=$(find "${STAGING}/recovered" -type d 2>/dev/null | wc -l | tr -d ' ')
RESTORED_SIZE=$(du -sh "${STAGING}/recovered" 2>/dev/null | awk '{print $1}')

log "Fichiers restaurés : ${RESTORED_COUNT}"
log "Dossiers          : ${RESTORED_DIRS}"
log "Taille totale     : ${RESTORED_SIZE}"

log "Recherche signatures jeux dans noms de fichiers :"
for pat in "cyberpunk" "last_of_us" "lastofus" "tlou" "elden_ring" "elden ring" "witcher" "horizon" "rdr2" "red_dead" "gtav" "gta5" "minecraft" "valorant" "fortnite" "spider" "skyrim" "fallout" "doom" "halo" "zelda" "screenshot"; do
  n=$(find "${STAGING}/recovered" -iname "*${pat}*" 2>/dev/null | wc -l | tr -d ' ')
  if [[ "$n" -gt 0 ]]; then
    log "  ${pat} : ${n} fichiers"
  fi
done

stat "DONE_${RESTORED_COUNT}_FILES"
log "Fini. Inspecte ${STAGING}/recovered/"
echo
echo "==============================================="
echo " RÉCUPÉRATION TERMINÉE"
echo " Staging : ${STAGING}/recovered"
echo " Fichiers: ${RESTORED_COUNT}"
echo " Taille  : ${RESTORED_SIZE}"
echo " Log     : ${LOG}"
echo "==============================================="
