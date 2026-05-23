#!/usr/bin/env python3
"""Convert and import the media already staged on the Mac.

This intentionally skips the external disk transfer phase. It lets us keep
making progress when the mounted NTFS/NFS disk stalls during reads.
"""

import sys
from pathlib import Path


def load_pipeline():
    sys.path.insert(0, str(Path(__file__).parent))
    import StageConvertImportSSVideoGamesMedia as pipeline

    return pipeline


def main():
    pipeline = load_pipeline()
    pipeline.log.info("CONVERT_EXISTING start: using local staging only")
    pipeline.phase_convert_import()
    pipeline.flush_state()
    pipeline.log.info("CONVERT_EXISTING done")


if __name__ == "__main__":
    main()
