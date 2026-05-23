#!/bin/bash
set -e

LOG=/Users/zenray/tmp_d_img/pipeline.log
LAUNCH_LOG=/Users/zenray/tmp_d_img/launch.log

mkdir -p /Users/zenray/tmp_d_img
cd /Users/zenray/Create/Build/Memory/Tools/Recovery
exec > >(tee -a "$LAUNCH_LOG") 2>&1

echo "═══════════════════════════════════════════"
echo "  SS_VideoGames media → Mac → AVIF → Eagle"
echo "═══════════════════════════════════════════"
echo "[*] Source : /Volumes/disk4s2/SS_VideoGames"
echo "[*] Stage  : /Users/zenray/tmp_d_img/staged"
echo "[*] Log    : $LOG"
echo ""

if [ ! -d /Volumes/disk4s2/SS_VideoGames ]; then
  echo "[ERROR] Source introuvable : /Volumes/disk4s2/SS_VideoGames"
  exit 1
fi

nohup /usr/bin/python3 /Users/zenray/Create/Build/Memory/Tools/Recovery/StageConvertImportSSVideoGamesMedia.py \
  >> "$LOG" 2>&1 &

PID=$!
echo "[*] PID: $PID"
echo "[*] Suivre : tail -f $LOG"
echo ""
echo "Pipeline lancé."
