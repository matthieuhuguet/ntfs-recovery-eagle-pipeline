#!/bin/bash
# FixNtfsAndMount.command
# Efface le flag "dirty/hibernation" sur le volume NTFS /dev/disk4s2
# puis remonte proprement → /Volumes/d devient lisible
# Double-cliquer depuis Finder (Terminal.app avec FDA requis)

set -u
DEVICE="/dev/disk4s2"
NTFSFIX="/usr/local/bin/ntfsfix"
LOG="/Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/ntfsfix.log"

mkdir -p "$(dirname "$LOG")"

log() { echo "[$(date +%H:%M:%S)] $*" | tee -a "$LOG"; }

log "=== FixNtfsAndMount ==="
log "Device : $DEVICE"

# 1. Démonter si monté
if mount | grep -q "$DEVICE"; then
    log "Démontage $DEVICE..."
    diskutil unmount "$DEVICE" 2>&1 | tee -a "$LOG" || \
        sudo diskutil unmount force "$DEVICE" 2>&1 | tee -a "$LOG"
    log "Démonté."
else
    log "Déjà démonté."
fi

# 2. ntfsfix -d (clear dirty/hibernation bit)
log "ntfsfix -d $DEVICE ..."
sudo "$NTFSFIX" -d "$DEVICE" 2>&1 | tee -a "$LOG"
if [[ $? -ne 0 ]]; then
    log "WARN: ntfsfix a eu des erreurs, on tente quand même le remount."
fi

# 3. Remonter
log "Remontage..."
diskutil mount "$DEVICE" 2>&1 | tee -a "$LOG"
sleep 2

# 4. Vérifier
if ls /Volumes/d/SS_VideoGames/ &>/dev/null; then
    N=$(find /Volumes/d/SS_VideoGames/A_Trier/_RECOVERED -type f 2>/dev/null | wc -l | tr -d ' ')
    log "✓ /Volumes/d lisible. _RECOVERED : $N fichiers."
    ls /Volumes/d/SS_VideoGames/A_Trier/_RECOVERED/ 2>/dev/null | head -10
else
    log "✗ /Volumes/d toujours illisible après fix."
    log "Essaie : sudo $NTFSFIX -b $DEVICE && diskutil mount $DEVICE"
fi

log "=== FIN ==="
echo ""
echo "Log: $LOG"
echo "Ferme cette fenêtre quand c'est bon."
