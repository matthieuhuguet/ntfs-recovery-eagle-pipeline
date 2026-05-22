#!/usr/bin/env python3
"""
Pipeline : sorted_by_game → Eagle References.library/Games/<GameName>/
- PNG/JPG/JPEG/WEBP/JXR → AVIF CRF25 (avifenc --min 25 --max 25 --speed 10)
- WEBM/MP4/MKV → frames 1/10s → AVIF
- Parallélisme : 16 cœurs (M5 Max)
- Résumable : state.json garde la progression par jeu
"""

import os, sys, json, time, re, shutil, subprocess, logging, tempfile
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from urllib import request as ureq
from urllib.error import URLError

# ── Config ──────────────────────────────────────────────────────────────────
SOURCE   = Path("/Users/zenray/NTFS_Recovery_ntfsundelete/sorted_by_game")
TMP_ROOT = Path("/Users/zenray/NTFS_Recovery_ntfsundelete/tmp_avif_pipeline")
EAGLE    = "http://localhost:41595"
AVIFENC  = "/opt/homebrew/bin/avifenc"
FFMPEG   = "/opt/homebrew/bin/ffmpeg"
WORKERS  = 16
# Single game test mode: set to game folder name or None for all
TEST_GAME = None  # run all games

# Existing Eagle "Games" folder ID (do not recreate)
GAMES_FOLDER_ID = "MIOUGBL8AF4E4"

IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.jxr'}
VIDEO_EXTS = {'.webm', '.mp4', '.mkv'}

# ── Logging ──────────────────────────────────────────────────────────────────
TMP_ROOT.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("eagle_import")
log.setLevel(logging.DEBUG)
fh = logging.FileHandler(str(TMP_ROOT / "pipeline.log"))
fh.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(message)s'))
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter('[%(asctime)s] %(message)s'))
log.addHandler(fh)
log.addHandler(sh)

# ── Eagle API ────────────────────────────────────────────────────────────────
def eagle_post(endpoint, body):
    data = json.dumps(body).encode()
    req = ureq.Request(f"{EAGLE}{endpoint}", data=data,
                       headers={"Content-Type": "application/json"})
    with ureq.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def eagle_create_folder(name, parent=None):
    body = {"folderName": name}
    if parent:
        body["parent"] = parent
    r = eagle_post("/api/folder/create", body)
    return r["data"]["id"]

def eagle_add_items(items_list, folder_id):
    """items_list: list of (path_str, name_str)"""
    items = [{"path": p, "name": n, "tags": []} for p, n in items_list]
    return eagle_post("/api/item/addFromPaths", {"items": items, "folderId": folder_id})

# ── File helpers ─────────────────────────────────────────────────────────────
def real_ext(f: Path) -> str:
    """Handle ntfsundelete .1/.2 duplicate suffixes."""
    m = re.match(r'^(.+)\.(\d+)$', f.name)
    if m:
        return Path(m.group(1)).suffix.lower()
    return f.suffix.lower()

def avif_stem(f: Path) -> str:
    """Output stem for AVIF, preserving .1/.2 info."""
    m = re.match(r'^(.+)\.(\d+)$', f.name)
    if m:
        return Path(m.group(1)).stem + f'_dup{m.group(2)}'
    return f.stem

# ── Conversion workers (module-level for pickling) ───────────────────────────
AVIFENC_CMD = AVIFENC  # captured at module level for subprocess workers

def _convert_one(args):
    """
    Worker: convert one image/frame to AVIF.
    args = (src_str, dst_str)
    Returns (src_str, dst_str, ok: bool)
    """
    src_s, dst_s = args
    src, dst = Path(src_s), Path(dst_s)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ext = real_ext(src) if not str(src).endswith('.tmp.png') else '.png'

    def run_avifenc(inp_path):
        r = subprocess.run(
            [AVIFENC_CMD, '-q', '60', '--speed', '10',
             '--jobs', '1', str(inp_path), dst_s],
            capture_output=True, timeout=180)
        return r.returncode == 0

    # WEBP and JXR: decode to PNG first (avifenc may lack codec)
    if ext in ('.webp', '.jxr'):
        tmp_png = dst.with_suffix('.tmp.png')
        try:
            r = subprocess.run(['/opt/homebrew/bin/ffmpeg', '-y', '-i', src_s,
                                str(tmp_png)],
                               capture_output=True, timeout=120)
            if r.returncode != 0:
                return src_s, dst_s, False
            ok = run_avifenc(tmp_png)
            return src_s, dst_s, ok
        finally:
            if tmp_png.exists():
                tmp_png.unlink()
    else:
        return src_s, dst_s, run_avifenc(src)


# ── Main pipeline ────────────────────────────────────────────────────────────
def process_game(game_dir: Path, games_eagle_id: str, game_eagle_id: str, state: dict, state_file: Path):
    name = game_dir.name
    game_tmp = TMP_ROOT / name
    game_tmp.mkdir(parents=True, exist_ok=True)

    files = [f for f in game_dir.iterdir() if f.is_file()]
    image_jobs = []   # (src_str, dst_str)
    video_files = []

    for f in files:
        ext = real_ext(f)
        if ext in IMAGE_EXTS:
            stem = avif_stem(f)
            dst = game_tmp / f"{stem}.avif"
            # Disambiguate collision (two files → same avif stem)
            i = 0
            while dst.exists() or any(j[1] == str(dst) for j in image_jobs):
                i += 1
                dst = game_tmp / f"{stem}_{i}.avif"
            image_jobs.append((str(f), str(dst)))
        elif ext in VIDEO_EXTS:
            video_files.append(f)
        # else: skip (.txt, junk, etc.)

    log.info(f"  [{name}] images={len(image_jobs)} videos={len(video_files)}")

    # ── Phase 1 : extract video frames (sequential ffmpeg, then parallel AVIF)
    extra_img_jobs = []
    for vf in video_files:
        tmp_frames = game_tmp / f"_frames_{vf.stem}"
        tmp_frames.mkdir(parents=True, exist_ok=True)
        try:
            r = subprocess.run(
                [FFMPEG, '-y', '-i', str(vf), '-vf', 'fps=1/10',
                 str(tmp_frames / 'frame_%04d.png')],
                capture_output=True, timeout=3600)
            if r.returncode != 0:
                log.warning(f"  [{name}] ffmpeg failed: {vf.name}")
                continue
            frame_pngs = sorted(tmp_frames.glob('frame_*.png'))
            for fp in frame_pngs:
                idx = int(fp.stem.split('_')[1])
                t_s = idx * 10
                avif_out = game_tmp / f"{vf.stem}_t{t_s:05d}s.avif"
                extra_img_jobs.append((str(fp), str(avif_out)))
            log.info(f"  [{name}] {vf.name} → {len(frame_pngs)} frames")
        except Exception as e:
            log.warning(f"  [{name}] video error {vf.name}: {e}")

    all_jobs = image_jobs + extra_img_jobs
    log.info(f"  [{name}] total AVIF jobs: {len(all_jobs)}")

    # ── Phase 2 : parallel AVIF conversion ────────────────────────────────────
    eagle_items = []
    ok_count = fail_count = 0

    if all_jobs:
        with ProcessPoolExecutor(max_workers=WORKERS) as ex:
            futures = {ex.submit(_convert_one, j): j for j in all_jobs}
            for fut in as_completed(futures):
                try:
                    src_s, dst_s, ok = fut.result()
                except Exception as e:
                    log.error(f"  [{name}] conversion exception: {e}")
                    fail_count += 1
                    continue
                if ok:
                    eagle_items.append((dst_s, Path(dst_s).stem))
                    ok_count += 1
                else:
                    log.warning(f"  [{name}] AVIF fail: {Path(src_s).name}")
                    fail_count += 1

    log.info(f"  [{name}] converted: {ok_count} OK, {fail_count} fail")

    # ── Phase 3 : import to Eagle (batches of 200) ────────────────────────────
    if eagle_items:
        BATCH = 200
        for i in range(0, len(eagle_items), BATCH):
            batch = eagle_items[i:i+BATCH]
            try:
                r = eagle_add_items(batch, game_eagle_id)
                log.info(f"  [{name}] Eagle batch {i//BATCH+1}/{-(-len(eagle_items)//BATCH)}: "
                         f"{r.get('status')} ({len(batch)} items)")
            except Exception as e:
                log.error(f"  [{name}] Eagle import error: {e}")

    # ── Phase 4 : delete source from sorted_by_game (Eagle has it now) ────────
    # NOTE: game_tmp is NOT deleted here — caller deletes it at the START of the
    # NEXT game loop iteration, giving Eagle time to finish copying the async queue.
    shutil.rmtree(game_dir, ignore_errors=True)
    log.info(f"  [{name}] source deleted: {game_dir}")

    return ok_count, fail_count, game_tmp


def main():
    log.info("═══════════════════════════════════════════════════")
    log.info(" EAGLE IMPORT PIPELINE — sorted_by_game → Games/")
    log.info(f" Workers: {WORKERS}, CRF: 25, speed: 10")
    log.info("═══════════════════════════════════════════════════")

    # ── Resume state ──────────────────────────────────────────────────────────
    state_file = TMP_ROOT / "state.json"
    state = {}
    if state_file.exists():
        state = json.loads(state_file.read_text())
        done = [k for k, v in state.items() if v == "DONE" and not k.startswith("__")]
        log.info(f"Resuming: {len(done)} games already done")

    # ── Use existing "Games" folder ────────────────────────────────────────────
    games_id = GAMES_FOLDER_ID
    state["__games_id__"] = games_id
    log.info(f"Using existing Eagle 'Games' folder: {games_id}")

    # ── Enumerate game dirs ───────────────────────────────────────────────────
    game_dirs = sorted([d for d in SOURCE.iterdir() if d.is_dir()])
    if TEST_GAME:
        game_dirs = [d for d in game_dirs if d.name == TEST_GAME]
        log.info(f"TEST MODE: {TEST_GAME}")
    log.info(f"Games to process: {len(game_dirs)}")

    total_ok = total_fail = 0
    start = time.time()
    # Deferred cleanup: temp dir of the PREVIOUS game.
    # Deleted at the START of the next game's processing so Eagle has the full
    # processing time of one game to finish its async addFromPaths copy queue.
    prev_tmp: Path | None = None

    for idx, game_dir in enumerate(game_dirs, 1):
        name = game_dir.name

        # ── Deferred cleanup of previous game's temp dir ────────────────────
        if prev_tmp is not None and prev_tmp.exists():
            shutil.rmtree(prev_tmp, ignore_errors=True)
            log.info(f"  [deferred cleanup] removed {prev_tmp.name}")
            prev_tmp = None

        if state.get(name) == "DONE":
            log.info(f"[{idx}/{len(game_dirs)}] SKIP (done): {name}")
            # Delete source if still present (pipeline restarted after adding deletion)
            if game_dir.exists():
                shutil.rmtree(game_dir, ignore_errors=True)
                log.info(f"  [{name}] source cleaned up (was already done)")
            continue

        log.info(f"\n[{idx}/{len(game_dirs)}] ── {name} ──")

        # Create Eagle subfolder if needed
        game_eagle_id = state.get(f"__id__{name}")
        if not game_eagle_id:
            game_eagle_id = eagle_create_folder(name, parent=games_id)
            state[f"__id__{name}"] = game_eagle_id
            state_file.write_text(json.dumps(state, indent=2))

        try:
            ok, fail, game_tmp = process_game(game_dir, games_id, game_eagle_id, state, state_file)
            total_ok += ok
            total_fail += fail
            state[name] = "DONE"
            state_file.write_text(json.dumps(state, indent=2))
            elapsed = time.time() - start
            log.info(f"  ✓ {name} ({ok} AVIF, {fail} fail) — {elapsed:.0f}s elapsed")
            prev_tmp = game_tmp  # will be deleted at start of next iteration
        except Exception as e:
            log.error(f"  ✗ {name} FAILED: {e}")
            import traceback; traceback.print_exc()

    # ── Final deferred cleanup (last game) ────────────────────────────────────
    if prev_tmp is not None and prev_tmp.exists():
        log.info(f"  [final deferred cleanup] waiting 120s for Eagle to finish async copy...")
        time.sleep(120)
        shutil.rmtree(prev_tmp, ignore_errors=True)
        log.info(f"  [final deferred cleanup] removed {prev_tmp.name}")

    elapsed = time.time() - start
    log.info(f"\n{'═'*50}")
    log.info(f" DONE — {total_ok} AVIF imported, {total_fail} errors")
    log.info(f" Total time: {elapsed/60:.1f} min")

    # Cleanup TMP_ROOT if empty
    try:
        TMP_ROOT.rmdir()
    except OSError:
        pass  # not empty, leave it


if __name__ == "__main__":
    main()
