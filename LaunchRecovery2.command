#!/bin/bash
# Double-click from Finder to run Recovery 2.
# Requires Terminal.app with Full Disk Access.
cd "$(dirname "$0")"
echo "=== Recovery 2 — NTFS Recovery with Exclusion List ==="
echo "Will unmount disk, scan MFT, filter known files, recover new ones."
echo ""
sudo bash Recovery2.sh 2>&1 | tee -a /Users/zenray/tmp_d_img/recovery2.log
echo ""
echo "Done. Press any key to close."
read -n1
