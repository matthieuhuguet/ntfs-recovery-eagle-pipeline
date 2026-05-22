# NTFS Recovery → Eagle Pipeline

Complete pipeline to recover deleted screenshots/videos from an NTFS disk and import them into [Eagle](https://eagle.cool) as AVIF frames, organized by game.

Built for macOS (Apple Silicon), battle-tested on a 8TB NTFS disk with 150,000+ recovered files.

---

## What it does

1. **Recover** deleted files from NTFS disk using `ntfsundelete` (compiled from source on macOS)
2. **Sort** recovered files by game using filename pattern matching
3. **Convert** images → AVIF (CRF 25, avifenc) and videos → AVIF frame sequences (1 frame/10s)
4. **Import** everything into Eagle's `Games/` folder tree
5. **Clean up** all temp files and source data progressively

Total result on this run: ~150,000 files recovered, ~50 games imported, ~200GB → ~50GB after AVIF conversion.

---

## Prerequisites

**System:**
- macOS with Apple Silicon (M1+)
- Terminal.app must have **Full Disk Access** (System Settings > Privacy > Full Disk Access) — required for raw NTFS device access
- Python 3.11+

**Tools:**
```bash
brew install avifenc ffmpeg
```

**Eagle app** running on `localhost:41595` (Eagle API v4)

**ntfsundelete** — not available via brew on macOS; must be compiled from source (see below)

---

## Building ntfsundelete on macOS

This is the hardest part. Standard brew ntfs-3g is Linux-only. Here is the exact method that works:

```bash
# 1. Install build deps
brew install autoconf automake libtool pkg-config gettext libgcrypt

# 2. Clone Tuxera fork (upstream sourceforge is dead)
git clone --depth 1 https://github.com/tuxera/ntfs-3g.git
cd ntfs-3g

# 3. Fix PATH for Homebrew GNU tools
export PATH="/opt/homebrew/opt/libtool/libexec/gnubin:/opt/homebrew/opt/gettext/bin:$PATH"
export ACLOCAL_PATH="/opt/homebrew/share/aclocal:$ACLOCAL_PATH"

# 4. Bootstrap
./autogen.sh

# 5. Configure — only build ntfsprogs utilities, no FUSE driver needed
./configure \
  --disable-ntfs-3g \
  --disable-ldconfig \
  --without-fuse \
  --disable-crypto \
  --disable-mount-helper

# 6. Build (fast, ~15s on M5 Max)
make -j16

# 7. Install — make install will fail at the end (Linux-only hook), that's OK
sudo make install 2>/dev/null || true

# 8. Install binaries manually
sudo install -m 0755 \
  ntfsprogs/.libs/ntfsundelete \
  ntfsprogs/.libs/ntfsfix \
  ntfsprogs/.libs/ntfsls \
  /usr/local/bin/

# 9. Verify
ntfsundelete -V
# ntfsundelete v2026.2.25 (libntfs-3g) - Recover deleted files from an NTFS Volume.
```

**Key insight:** `ntfsundelete` only needs read-only access to the raw device. It does NOT need FUSE. Disable everything FUSE-related in configure.

---

## macOS TCC gotcha

macOS blocks raw device access (`/dev/diskXsY`) via TCC Privacy framework. Even `sudo` from a terminal without Full Disk Access will get `Operation not permitted`.

**Rule:** Never run ntfsundelete from Claude Code shell or any tool that doesn't have FDA. Use Terminal.app (which has FDA), via `.command` launcher files (double-clickable from Finder).

---

## NTFS hibernation / dirty bit

If Windows wasn't properly shut down before disconnecting the disk, NTFS will be marked as "hibernated" (dirty bit set). macOS may mount it read-only but refuse file access.

Fix with ntfsfix before remounting:

```bash
sudo ntfsfix -d /dev/disk4s2  # clear dirty bit
diskutil mount /dev/disk4s2    # remount
```

See `FixNtfsAndMount.command` — double-click from Finder.

---

## Complete Workflow

### Phase 1 — Recover from NTFS

Double-click `LaunchRecoverWithNtfsundelete.command` in Finder.

This runs `RecoverWithNtfsundelete.sh` which:
- Unmounts the NTFS volume (`diskutil unmount force`)
- Scans with `ntfsundelete -s -P -p 0 -m '*' -f /dev/diskXsY`
- Recovers all deleted files with `ntfsundelete -u -m '*' -p 30 -f -d recovered/`
- Saves to `~/Desktop/NTFS_Recovery/run_TIMESTAMP/recovered/`

For images+videos only (faster, smaller output):

```bash
# Double-click from Finder:
LaunchRecoverFilteredImagesVideos.command
```

**Important flags:**
- `-f` = force (required when NTFS partition is hibernated/dirty)
- `-p 30` = minimum percentage recovered (0–100, lower = more files but more corruption)
- Never run against the source device while mounted

---

### Phase 2 — Sort by game

```bash
python3 SortByGame.py --dry-run --input recovered/ --output sorted_by_game/
# Inspect output, then apply:
python3 SortByGame.py --apply --input recovered/ --output sorted_by_game/
```

Uses 70+ regex patterns anchored to game-specific naming conventions (`_Screenshot_YYYYMMDDHHMMSS.`, `_YYYYMMDDHHMMSS.`, `_W64_Shipping_`, etc.). Strict whitelist — zero false positives.

**ntfsundelete naming quirk:** duplicates get `.1`, `.2`, ... suffixes appended. SortByGame strips these with `re.sub(r"(\.\d+)+$", "", name)` before matching.

---

### Phase 3 — Import sorted files to Eagle

```bash
# Edit config at top of EagleImportGames.py:
# SOURCE = Path("/path/to/sorted_by_game")
# GAMES_FOLDER_ID = "your_eagle_games_folder_id"

nohup python3 EagleImportGames.py >> pipeline.log 2>&1 &
```

- Converts PNG/JPG/JPEG/WEBP → AVIF (CRF 25)
- Extracts video frames at 1 frame/10s (cap 50) → AVIF
- 16 parallel workers
- Resumable via `state.json`
- Deletes source dir after successful import
- **Deferred temp cleanup** — temp AVIF files deleted at the *start of the next game* to give Eagle's async `addFromPaths` API time to copy them

---

### Phase 4 — Import unclassified files (by extension)

After SortByGame, ~8000 remaining files that didn't match any game pattern, bucketed by extension:

```bash
nohup python3 ImportByExtToEagle.py >> pipeline_byext.log 2>&1 &
```

Processes: `png`, `jpg`, `webp`, `gif`, `other_img`, `mp4`, `other_video`  
Skips: `audio`, `archive`, `pdf`, `office`, `torrent`, `_unknown`, `disc`

---

### Phase 5 — Convert .webm already in Eagle → AVIF frames

If you imported raw `.webm` files into Eagle before this pipeline existed:

```bash
python3 ConvertEagleFFXVIWebm.py
```

Finds all `*.webm` in Eagle's `images/` directory, extracts frames, re-imports as AVIF, marks originals as deleted.

---

### Phase 6 — Import from live NTFS volume

If NTFS volume is accessible via `/Volumes/`:

```bash
# Double-click from Finder (requires Terminal.app FDA):
LaunchImportRecoveredDiskToEagle.command
```

Or directly:
```bash
python3 ImportRecoveredDiskToEagle.py
```

Never deletes source (NTFS is read-only). Resumable via `state_disk.json`.

---

### Phase 7 — Final cleanup

```bash
python3 FinalCleanupByExt.py
```

- Tries to import remaining files one last time
- Deletes all sources regardless of success/fail (corrupted files won't import)
- Removes all non-importable categories (audio/archive/pdf etc.)
- Removes all temp and recovery dirs
- Deletes the ntfsundelete root dir if empty

---

### Phase 8 — Repair Eagle folder structure

If duplicate or nested category folders appear in Eagle's `Games/` tree:

```bash
python3 RepairEagleGamesStructure.py        # dry run
python3 RepairEagleGamesStructure.py --apply  # apply
```

Flattens `Games/RPG/GameName/` → `Games/GameName/`, deduplicates, remaps item folder references in `metadata.json`.

---

## Eagle async API gotcha

**`POST /api/item/addFromPaths` is asynchronous.** Eagle queues the copy internally.

If you delete temp source files immediately after the API call, Eagle will try to copy files that no longer exist — producing thousands of ENOENT errors.

**Fix used in EagleImportGames.py:** delete game N's temp dir at the *start of game N+1's processing*, not at the end of game N. For the last game, sleep 120s before cleanup.

---

## AVIF encoding settings

Sweet spot validated on 30 mixed samples (JPG/PNG/WebP):

| CRF | Files gaining size | Average gain |
|-----|-------------------|--------------|
| 20  | 60%               | 78%          |
| 23  | 80%               | 84%          |
| **25** | **100%**       | **88%**      |
| 27  | 100%              | 89%          |

CRF 25 = 100% of files gain, 88% average size reduction. Sweet spot.

```bash
avifenc --min 25 --max 25 --speed 10 --jobs 1 input.png output.avif
# ~3x faster than ffmpeg+libsvtav1 for still images
```

For batch processing: `xargs -P 16 -I{} avifenc ...`

---

## Script reference

| Script | Role |
|--------|------|
| `RecoverWithNtfsundelete.sh` | Full NTFS recovery (all deleted files) |
| `RecoverFilteredImagesVideos.sh` | NTFS recovery filtered to images+videos |
| `RecoverWebmOnly.sh` | NTFS recovery, .webm files only |
| `ResumeWebmOnly.sh` | Resume interrupted webm recovery |
| `InspectRecycleBinOriginalPaths.sh` | Read original paths from $Recycle.Bin metadata |
| `FixNtfsAndMount.command` | Fix NTFS dirty bit + remount |
| `SortByGame.py` | Classify recovered files by game name |
| `EagleImportGames.py` | sorted_by_game → Eagle, AVIF conversion, resumable |
| `ImportByExtToEagle.py` | by_ext buckets → Eagle, AVIF conversion |
| `ImportRecoveredToEagleAvif.py` | ntfsundelete recovered dirs → Eagle |
| `ImportRecoveredDiskToEagle.py` | Live NTFS volume → Eagle (read-only source) |
| `ConvertEagleFFXVIWebm.py` | .webm already in Eagle → AVIF frame sequences |
| `FinalCleanupByExt.py` | Last-pass import + delete all remaining files |
| `RepairEagleGamesStructure.py` | Flatten Eagle Games/ folder tree, dedup |
| `Launch*.command` | Finder-double-clickable launchers for shell scripts |

---

## Hardware context

- Mac M5 Max, 18 cores
- `ProcessPoolExecutor(max_workers=16)` everywhere
- 8TB NTFS external disk (USB)
- Eagle library: ~250GB source → ~136GB after AVIF conversion

---

## License

MIT
