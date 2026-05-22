#!/bin/bash
# Double-clickable launcher → ouvre dans Terminal.app (FDA hérité)
# pour permettre l'accès raw au device NTFS.
cd "$(dirname "$0")"
exec bash ./RecoverWithNtfsundelete.sh
