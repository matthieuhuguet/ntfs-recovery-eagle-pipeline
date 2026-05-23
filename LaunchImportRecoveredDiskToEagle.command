#!/bin/bash
# LaunchImportRecoveredDiskToEagle.command
# Ouvrir depuis Finder (Terminal.app FDA requis pour raw device + NTFS)

set -e
cd "$(dirname "$0")"

DEVICE=/dev/disk4s2
MOUNT=/Volumes/d
NTFS3G=/opt/homebrew/bin/ntfs-3g
LOG=/Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/pipeline.log

echo "═══════════════════════════════════════════"
echo "  _RECOVERED → Eagle  PIPELINE LAUNCHER"
echo "═══════════════════════════════════════════"

# 1. Monter en read-write via ntfs-3g si nécessaire (nécessite Terminal.app FDA)
if mount | grep -q "$DEVICE.*macfuse"; then
    echo "[*] $DEVICE est déjà monté via macFUSE/ntfs-3g."
elif mount | grep -q "$DEVICE"; then
    echo "[*] Démontage mount natif read-only..."
    diskutil unmount "$DEVICE" || diskutil unmount force "$DEVICE"
    sleep 1
    echo "[*] Montage ntfs-3g read-write sur $MOUNT ..."
    mkdir -p "$MOUNT"
    sudo "$NTFS3G" "$DEVICE" "$MOUNT" \
        -o local,allow_other,auto_xattr,auto_cache,noatime,windows_names,streams_interface=openxattr,inherit,uid="$(id -u)",gid="$(id -g)",big_writes,remove_hiberfile
else
    echo "[*] Montage ntfs-3g read-write sur $MOUNT ..."
    mkdir -p "$MOUNT"
    sudo "$NTFS3G" "$DEVICE" "$MOUNT" \
        -o local,allow_other,auto_xattr,auto_cache,noatime,windows_names,streams_interface=openxattr,inherit,uid="$(id -u)",gid="$(id -g)",big_writes,remove_hiberfile
fi
echo "[*] Montage OK — vérification accès..."

# 3. Vérifier le dossier sans pré-scan coûteux.
# Le script Python fait son propre scan et logge le nombre de fichiers.
if [ ! -d "$MOUNT/SS_VideoGames/A_Trier/_RECOVERED" ]; then
    echo "[ERROR] Dossier introuvable : $MOUNT/SS_VideoGames/A_Trier/_RECOVERED"
    exit 1
fi
echo "[*] Dossier _RECOVERED présent. Scan détaillé confié au pipeline Python."

# 4. Lancer le pipeline
mkdir -p /Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline
echo ""
echo "[*] Lancement ImportRecoveredDiskToEagle.py ..."
echo "[*] Log : $LOG"
echo ""

export DELETE_SOURCE_AFTER_IMPORT=1
export EAGLE_COPY_GRACE_SECONDS=180

nohup python3 /Users/zenray/Create/Build/Memory/Tools/Recovery/ImportRecoveredDiskToEagle.py \
    >> "$LOG" 2>&1 &

PID=$!
echo "[*] PID: $PID"
echo ""
echo "[*] Suivre la progression :"
echo "    tail -f $LOG"
echo ""
echo "Pipeline lancé. Cette fenêtre peut être fermée."
