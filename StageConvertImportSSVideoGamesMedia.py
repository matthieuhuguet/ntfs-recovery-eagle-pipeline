#!/usr/bin/env python3
"""
StageConvertImportSSVideoGamesMedia.py

Workflow simple demandé :
1. déplacer les images + .webm restantes de /Volumes/disk4s2/SS_VideoGames vers le Mac ;
2. convertir localement en AVIF avec parallélisme fort ;
3. importer dans Eagle > Games ;
4. vider le staging local au fur et à mesure des imports acceptés.
"""

import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib import request as ureq

SOURCE_ROOT = Path("/Volumes/disk4s2/SS_VideoGames")
STAGE_ROOT = Path("/Users/zenray/tmp_d_img/staged")
TMP_ROOT = Path("/Users/zenray/tmp_d_img/tmp_avif")
LOG_FILE = Path("/Users/zenray/tmp_d_img/pipeline.log")
STATE_FILE = Path("/Users/zenray/tmp_d_img/state.json")
ACTIVE_TRANSFERS_FILE = Path("/Users/zenray/tmp_d_img/active_transfers.json")

EAGLE = "http://localhost:41595"
GAMES_ROOT_ID = "MIOUGBL8AF4E4"
AVIFENC = "/opt/homebrew/bin/avifenc"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
JXRDEC = "/opt/homebrew/bin/JxrDecApp"

TRANSFER_WORKERS = int(os.environ.get("TRANSFER_WORKERS", "8"))
CONVERT_WORKERS = 16
FRAMES_CAP = 50
EAGLE_COPY_GRACE_SECONDS = 180

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".jxr", ".bmp", ".tif", ".tiff", ".heic"}
VIDEO_EXTS = {".webm"}

BASE_SCRIPT = Path("/Users/zenray/Create/Build/Memory/Tools/Recovery/ImportRecoveredDiskToEagle.py")

STAGE_ROOT.mkdir(parents=True, exist_ok=True)
TMP_ROOT.mkdir(parents=True, exist_ok=True)
LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

log = logging.getLogger("stage_convert_import")
log.setLevel(logging.INFO)
fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s")
fh = logging.FileHandler(LOG_FILE)
fh.setFormatter(fmt)
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
log.handlers.clear()
log.addHandler(fh)
log.addHandler(sh)


def load_base():
    spec = importlib.util.spec_from_file_location("recovered_import_base", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {BASE_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


base = load_base()
sort_classify = base._load_sort_classify()


def load_state():
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


state = load_state()
state.setdefault("staged", [])
state.setdefault("imported", [])
state.setdefault("failed", [])
staged_done = set(state["staged"])
imported_done = set(state["imported"])
state_lock = threading.Lock()
active_lock = threading.Lock()
active_transfers = {}


def flush_state():
    with state_lock:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
        tmp.replace(STATE_FILE)


def write_active_transfers():
    tmp = ACTIVE_TRANSFERS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(active_transfers, indent=2, sort_keys=True))
    tmp.replace(ACTIVE_TRANSFERS_FILE)


def set_active_transfer(rel: str, src: Path):
    with active_lock:
        active_transfers[str(threading.get_ident())] = {
            "rel": rel,
            "src": str(src),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        write_active_transfers()


def clear_active_transfer():
    with active_lock:
        active_transfers.pop(str(threading.get_ident()), None)
        write_active_transfers()


def ext_for(path: Path) -> str:
    return base.effective_ext(path)


def is_target(path: Path) -> bool:
    ext = ext_for(path)
    return ext in IMAGE_EXTS or ext in VIDEO_EXTS


def discover_source_files():
    files = []
    for root, dirs, names in os.walk(SOURCE_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            if name.startswith("."):
                continue
            path = Path(root) / name
            if is_target(path):
                files.append(path)
    return files


def prune_empty_dirs(path: Path, stop: Path):
    try:
        cur = path
        while cur != stop and stop in cur.parents:
            cur.rmdir()
            cur = cur.parent
    except OSError:
        pass


def copy_verify_delete(src: Path):
    rel = str(src.relative_to(SOURCE_ROOT))
    if rel in staged_done:
        return True, rel, "already"

    dst = STAGE_ROOT / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".copying")

    set_active_transfer(rel, src)
    try:
        if tmp.exists():
            tmp.unlink()
        shutil.copy2(src, tmp)
        if tmp.stat().st_size != src.stat().st_size:
            raise OSError("copy size mismatch")
        tmp.replace(dst)
        src.unlink()
        prune_empty_dirs(src.parent, SOURCE_ROOT)
        with state_lock:
            staged_done.add(rel)
            state["staged"].append(rel)
        return True, rel, "moved"
    except Exception as e:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        return False, rel, str(e)
    finally:
        clear_active_transfer()


def stage_from_disk():
    log.info("═" * 55)
    log.info("PHASE 1 — move remaining media from disk to Mac")
    log.info(f"Source: {SOURCE_ROOT}")
    log.info(f"Stage : {STAGE_ROOT}")
    log.info("Target extensions: images + .webm")
    log.info(f"Transfer workers: {TRANSFER_WORKERS}")
    log.info("═" * 55)

    files = discover_source_files()
    log.info(f"Found target files on disk: {len(files)}")
    if not files:
        return

    moved = failed = already = 0
    last_flush = 0
    with ThreadPoolExecutor(max_workers=TRANSFER_WORKERS) as ex:
        futures = {ex.submit(copy_verify_delete, f): f for f in files}
        for idx, fut in enumerate(as_completed(futures), 1):
            ok, rel, msg = fut.result()
            if ok and msg == "already":
                already += 1
            elif ok:
                moved += 1
            else:
                failed += 1
                log.warning(f"Move failed: {rel} — {msg}")

            if idx - last_flush >= 200:
                flush_state()
                last_flush = idx
                log.info(f"  staged progress: {idx}/{len(files)} moved={moved} already={already} failed={failed}")

    flush_state()
    log.info(f"Stage done: moved={moved}, already={already}, failed={failed}")


def discover_staged_files():
    files = []
    for root, dirs, names in os.walk(STAGE_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in names:
            if name.startswith(".") or name.endswith(".copying"):
                continue
            path = Path(root) / name
            rel = str(path.relative_to(STAGE_ROOT))
            if rel in imported_done:
                continue
            if is_target(path):
                files.append(path)
    return files


def game_for(path: Path):
    rel_parts = path.relative_to(STAGE_ROOT).parts
    game = base.generic_classify(path.name, sort_classify)
    if game == "_Unknown Recovered":
        game = base.folder_hint_game(rel_parts) or game
    return game


def convert_image(job):
    src_s, dst_s = job
    src = Path(src_s)
    dst = Path(dst_s)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ext = ext_for(src)

    try:
        if ext == ".jxr":
            tmp_tif = dst.with_suffix(".tmp.tif")
            tmp_png = dst.with_suffix(".tmp.png")
            try:
                r = subprocess.run(
                    [JXRDEC, "-i", src_s, "-o", str(tmp_tif), "-c", "22"],
                    capture_output=True,
                    timeout=180,
                )
                if r.returncode != 0 or not tmp_tif.exists():
                    return False, src_s, dst_s
                r_png = subprocess.run(
                    [FFMPEG, "-y", "-loglevel", "error", "-i", str(tmp_tif), str(tmp_png)],
                    capture_output=True,
                    timeout=180,
                )
                if r_png.returncode != 0 or not tmp_png.exists():
                    return False, src_s, dst_s
                r2 = subprocess.run(
                    [AVIFENC, "--min", "25", "--max", "25", "--speed", "10", "--jobs", "1", str(tmp_png), dst_s],
                    capture_output=True,
                    timeout=180,
                )
            finally:
                tmp_tif.unlink(missing_ok=True)
                tmp_png.unlink(missing_ok=True)
        elif ext in {".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}:
            tmp_png = dst.with_suffix(".tmp.png")
            try:
                r = subprocess.run(
                    [FFMPEG, "-y", "-loglevel", "error", "-i", src_s, "-frames:v", "1", str(tmp_png)],
                    capture_output=True,
                    timeout=180,
                )
                if r.returncode != 0 or not tmp_png.exists():
                    return False, src_s, dst_s
                inp = tmp_png
                r2 = subprocess.run(
                    [AVIFENC, "--min", "25", "--max", "25", "--speed", "10", "--jobs", "1", str(inp), dst_s],
                    capture_output=True,
                    timeout=180,
                )
            finally:
                tmp_png.unlink(missing_ok=True)
        else:
            r2 = subprocess.run(
                [AVIFENC, "--min", "25", "--max", "25", "--speed", "10", "--jobs", "1", src_s, dst_s],
                capture_output=True,
                timeout=180,
            )

        ok = r2.returncode == 0 and dst.exists() and dst.stat().st_size > 0
        if ok:
            try:
                shutil.copystat(src, dst)
            except Exception:
                pass
        return ok, src_s, dst_s
    except Exception:
        return False, src_s, dst_s


def convert_video(job):
    src_s, tmp_dir_s, stem = job
    src = Path(src_s)
    tmp_dir = Path(tmp_dir_s)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    try:
        subprocess.run(
            [
                FFMPEG,
                "-y",
                "-loglevel",
                "error",
                "-t",
                str(FRAMES_CAP * 10),
                "-i",
                src_s,
                "-vf",
                "fps=1/10",
                "-frames:v",
                str(FRAMES_CAP),
                "-vsync",
                "vfr",
                str(tmp_dir / "frame_%04d.png"),
            ],
            capture_output=True,
            timeout=3600,
        )
        frames = sorted(tmp_dir.glob("frame_*.png"))
        avifs = []
        for frame in frames:
            idx = int(frame.stem.split("_")[1])
            out = tmp_dir / f"{stem}_t{(idx - 1) * 10:05d}s.avif"
            r = subprocess.run(
                [AVIFENC, "--min", "25", "--max", "25", "--speed", "10", "--jobs", "1", str(frame), str(out)],
                capture_output=True,
                timeout=180,
            )
            frame.unlink(missing_ok=True)
            if r.returncode == 0 and out.exists() and out.stat().st_size > 0:
                try:
                    shutil.copystat(src, out)
                except Exception:
                    pass
                avifs.append((str(out), out.stem))
        return src_s, avifs, bool(avifs)
    except Exception:
        return src_s, [], False


def eagle_post(endpoint, body):
    data = json.dumps(body).encode()
    req = ureq.Request(f"{EAGLE}{endpoint}", data=data, headers={"Content-Type": "application/json"})
    with ureq.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def eagle_get(endpoint):
    req = ureq.Request(f"{EAGLE}{endpoint}")
    with ureq.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def get_folder_map():
    folder_map = {}
    r = eagle_get("/api/folder/list")

    def walk(folder):
        name = folder.get("name", "")
        fid = folder.get("id")
        if name and fid:
            folder_map[base.canon(name)] = fid
        for child in folder.get("children", []) or []:
            walk(child)

    for folder in r.get("data", []):
        walk(folder)
    return folder_map


folder_map = {}


def get_or_create_folder(name):
    key = base.canon(name)
    if key in folder_map:
        return folder_map[key]
    r = eagle_post("/api/folder/create", {"folderName": name, "parent": GAMES_ROOT_ID})
    fid = r["data"]["id"]
    folder_map[key] = fid
    log.info(f"Created Eagle folder: {name} [{fid}]")
    return fid


def eagle_add_items(items, folder_id):
    body = {"items": [{"path": p, "name": n, "tags": []} for p, n in items], "folderId": folder_id}
    return eagle_post("/api/item/addFromPaths", body)


def cleanup_stage_source(src_s):
    src = Path(src_s)
    rel = str(src.relative_to(STAGE_ROOT))
    try:
        src.unlink()
        prune_empty_dirs(src.parent, STAGE_ROOT)
    except FileNotFoundError:
        pass
    imported_done.add(rel)
    state["imported"].append(rel)


def phase_convert_import():
    global folder_map
    folder_map = get_folder_map()

    files = discover_staged_files()
    images = [f for f in files if ext_for(f) in IMAGE_EXTS]
    videos = [f for f in files if ext_for(f) in VIDEO_EXTS]
    log.info("═" * 55)
    log.info("PHASE 2 — convert local staging to AVIF and import Eagle")
    log.info(f"Staged images: {len(images)}")
    log.info(f"Staged webm  : {len(videos)}")
    log.info("═" * 55)

    image_jobs = {}
    for src in images:
        game = game_for(src)
        stem = base.safe_stem(src)
        out_dir = TMP_ROOT / "img" / base.canon(game)
        out = out_dir / f"{stem}.avif"
        n = 0
        while out.exists():
            n += 1
            out = out_dir / f"{stem}_{n}.avif"
        image_jobs[str(src)] = (game, str(out), str(src.relative_to(STAGE_ROOT)))

    converted_by_game = {}
    ok_img = fail_img = 0
    with ProcessPoolExecutor(max_workers=CONVERT_WORKERS) as ex:
        futures = {ex.submit(convert_image, (src, meta[1])): src for src, meta in image_jobs.items()}
        for idx, fut in enumerate(as_completed(futures), 1):
            ok, src_s, dst_s = fut.result()
            game, _, rel = image_jobs[src_s]
            if ok:
                ok_img += 1
                converted_by_game.setdefault(game, []).append((dst_s, Path(dst_s).stem, src_s))
            else:
                fail_img += 1
                state["failed"].append(rel)
            if idx % 1000 == 0:
                log.info(f"  image conversion: {idx}/{len(image_jobs)} ok={ok_img} fail={fail_img}")
                flush_state()

    log.info(f"Image conversion done: ok={ok_img}, fail={fail_img}")

    imported = 0
    for game, items in converted_by_game.items():
        folder_id = get_or_create_folder(game)
        for start in range(0, len(items), 200):
            batch = items[start : start + 200]
            r = eagle_add_items([(p, n) for p, n, _ in batch], folder_id)
            log.info(f"Eagle batch: {r.get('status')} ({len(batch)} items → {game})")
            for _, _, src_s in batch:
                cleanup_stage_source(src_s)
            imported += len(batch)
            flush_state()

    if imported:
        log.info(f"Waiting {EAGLE_COPY_GRACE_SECONDS}s before cleaning image AVIF temp")
        time.sleep(EAGLE_COPY_GRACE_SECONDS)
    shutil.rmtree(TMP_ROOT / "img", ignore_errors=True)
    log.info(f"Images imported: {imported}")

    video_jobs = []
    video_meta = {}
    for src in videos:
        game = game_for(src)
        stem = base.safe_stem(src)
        tmp_dir = TMP_ROOT / "vid" / f"{base.canon(game)}_{stem}_{abs(hash(str(src))) % 100000}"
        video_jobs.append((str(src), str(tmp_dir), stem))
        video_meta[str(src)] = (game, str(src.relative_to(STAGE_ROOT)))

    ok_vid = fail_vid = frames_total = 0
    with ProcessPoolExecutor(max_workers=CONVERT_WORKERS) as ex:
        futures = {ex.submit(convert_video, job): job for job in video_jobs}
        for fut in as_completed(futures):
            src_s, avif_items, ok = fut.result()
            game, rel = video_meta[src_s]
            if not ok:
                fail_vid += 1
                state["failed"].append(rel)
                flush_state()
                continue
            folder_id = get_or_create_folder(game)
            for start in range(0, len(avif_items), 200):
                batch = avif_items[start : start + 200]
                r = eagle_add_items(batch, folder_id)
                log.info(f"Eagle webm batch: {r.get('status')} ({len(batch)} frames → {game})")
            cleanup_stage_source(src_s)
            frames_total += len(avif_items)
            ok_vid += 1
            flush_state()

    if frames_total:
        log.info(f"Waiting {EAGLE_COPY_GRACE_SECONDS}s before cleaning video AVIF temp")
        time.sleep(EAGLE_COPY_GRACE_SECONDS)
    shutil.rmtree(TMP_ROOT / "vid", ignore_errors=True)
    log.info(f"Videos imported: {ok_vid} OK, {fail_vid} fail, {frames_total} frames")


def main():
    start = time.time()
    log.info("START StageConvertImportSSVideoGamesMedia")
    stage_from_disk()
    phase_convert_import()
    flush_state()
    elapsed = (time.time() - start) / 60
    log.info("═" * 55)
    log.info(f"DONE in {elapsed:.1f} min")
    log.info(f"State: staged={len(state['staged'])}, imported={len(state['imported'])}, failed={len(state['failed'])}")
    log.info("═" * 55)


if __name__ == "__main__":
    main()
