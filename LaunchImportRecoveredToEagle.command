#!/bin/bash
# Double-click from Finder to run the pipeline.
# Requires Terminal.app to have Full Disk Access.
cd "$(dirname "$0")"
echo "=== ImportRecoveredToEagle ==="
echo "Source: /Users/zenray/.mounty/d/SS_VideoGames/A_Trier/_RECOVERED"
echo "Staging: /Users/zenray/tmp_d_img/staged"
echo ""
python3 ImportRecoveredToEagle.py 2>&1 | tee -a /Users/zenray/tmp_d_img/pipeline.log
echo ""
echo "Done. Press any key to close."
read -n1
