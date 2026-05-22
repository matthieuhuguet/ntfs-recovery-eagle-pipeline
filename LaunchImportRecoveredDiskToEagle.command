#!/bin/bash
# LaunchImportRecoveredDiskToEagle.command
# Double-cliquer pour lancer le pipeline depuis Terminal.app (Full Disk Access requis)

set -e
cd "$(dirname "$0")"

# Mount /Volumes/d if not already mounted
if ! mount | grep -q "/Volumes/d"; then
    echo "[*] Mounting /dev/disk4s2 → /Volumes/d ..."
    diskutil mount /dev/disk4s2
    sleep 2
fi

# Verify source exists
if [ ! -d "/Volumes/d/SS_VideoGames/A_Trier/_RECOVERED" ]; then
    echo "[ERROR] /Volumes/d/SS_VideoGames/A_Trier/_RECOVERED not found!"
    echo "  Vérifier que le dossier existe sur le disque."
    read -p "Appuyer sur Entrée pour quitter..."
    exit 1
fi

mkdir -p /Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline

echo "[*] Starting ImportRecoveredDiskToEagle.py ..."
echo "[*] Log: /Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/pipeline.log"
echo ""

nohup python3 /Users/zenray/Create/Build/Memory/Tools/Recovery/ImportRecoveredDiskToEagle.py \
    >> /Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/pipeline.log 2>&1 &

PID=$!
echo "[*] PID: $PID"
echo "[*] Pour suivre la progression:"
echo "    tail -f /Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/pipeline.log"
echo ""
echo "Process lancé en arrière-plan. Cette fenêtre peut être fermée."
