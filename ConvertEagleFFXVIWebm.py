#!/usr/bin/env python3
"""
Convert FFXVI .webm files already stored raw in Eagle library → AVIF frames.
- Finds all *.webm in Eagle IMAGES_DIR
- Extracts frames 1/10s (cap 50) → AVIF
- Imports frames via Eagle addFromPaths into same folder
- Marks original webm item as deleted (isDeleted=true in metadata.json)
- 16 workers (one per webm file)
"""

import json, os, re, shutil, subprocess, sys, time, logging
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from urllib import request as ureq

# ── Config ────────────────────────────────────────────────────────────────────
IMAGES_DIR = Path("/Users/zenray/Create/Media/References/References.library/images")
TMP_ROOT   = Path("/tmp/ffxvi_webm_convert")
EAGLE      = "http://localhost:41595"
AVIFENC    = "/opt/homebrew/bin/avifenc"
FFMPEG     = "/opt/homebrew/bin/ffmpeg"
WORKERS    = 16
FRAMES_CAP = 50

# ── Logging ───────────────────────────────────────────────────────────────────
TMP_ROOT.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("ffxvi_webm")
log.setLevel(logging.DEBUG)
fh = logging.FileHandler(str(TMP_ROOT / "ffxvi_webm.log"))
fh.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s %(message)s'))
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter('[%(asctime)s] %(message)s'))
log.addHandler(fh)
log.addHandler(sh)

# ── Eagle API ─────────────────────────────────────────────────────────────────
def eagle_post(endpoint, body):
    data = json.dumps(body).encode()
    req = ureq.Request(f"{EAGLE}{endpoint}", data=data,
                       headers={"Content-Type": "application/json"})
    with ureq.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def eagle_add_items(items_list, folder_id):
    items = [{"path": p, "name": n, "tags": []} for p, n in items_list]
    return eagle_post("/api/item/addFromPaths", {"items": items, "folderId": folder_id})

def eagle_delete_item(item_id):
    """Try Eagle API delete; fallback to isDeleted flag in metadata.json."""
    try:
        r = eagle_post("/api/item/delete", {"ids": [item_id]})
        return r.get("status") == "success"
    except Exception:
        return False

# ── Worker (module-level for pickling) ───────────────────────────────────────
_AVIFENC = AVIFENC
_FFMPEG  = FFMPEG
_FRAMES_CAP = FRAMES_CAP

def _process_one_webm(args):
    """
    args = (webm_path_str, folder_id, item_id, stem_base, tmp_dir_str)
    Returns (item_id, webm_path_str, [(avif_path, avif_stem), ...], ok)
    """
    webm_s, folder_id, item_id, stem_base, tmp_dir_s = args
    webm = Path(webm_s)
    tmp_dir = Path(tmp_dir_s)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    # Extract frames at 1/10s, cap at FRAMES_CAP
    r = subprocess.run(
        [_FFMPEG, "-y", "-loglevel", "error", "-i", webm_s,
         "-vf", f"fps=1/10,select='lte(n\\,{_FRAMES_CAP-1})'",
         "-vsync", "vfr",
         str(tmp_dir / "frame_%04d.png")],
        capture_output=True, timeout=3600)

    frames = sorted(tmp_dir.glob("frame_*.png"))
    if not frames:
        # Try alternate: just grab all frames with fps=1/10
        r2 = subprocess.run(
            [_FFMPEG, "-y", "-loglevel", "error", "-i", webm_s,
             "-vf", "fps=1/10",
             "-frames:v", str(_FRAMES_CAP),
             str(tmp_dir / "frame_%04d.png")],
            capture_output=True, timeout=3600)
        frames = sorted(tmp_dir.glob("frame_*.png"))

    if not frames:
        return item_id, webm_s, [], False

    avif_items = []
    for fp in frames:
        idx = int(fp.stem.split("_")[1])
        t_s = (idx - 1) * 10
        avif_name = f"{stem_base}_t{t_s:05d}s"
        avif_out = tmp_dir / f"{avif_name}.avif"
        rc = subprocess.run(
            [_AVIFENC, "-q", "60", "--speed", "10", "--jobs", "1", str(fp), str(avif_out)],
            capture_output=True, timeout=180)
        if rc.returncode == 0 and avif_out.exists() and avif_out.stat().st_size > 0:
            avif_items.append((str(avif_out), avif_name))
        fp.unlink(missing_ok=True)

    return item_id, webm_s, avif_items, len(avif_items) > 0


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("═══════════════════════════════════════════════════")
    log.info(" EAGLE WEBM → AVIF FRAMES CONVERTER")
    log.info(f" Workers: {WORKERS}, frames cap: {FRAMES_CAP}")
    log.info("═══════════════════════════════════════════════════")

    # Discover all .webm in Eagle library
    webm_files = list(IMAGES_DIR.glob("*.info/*.webm"))
    log.info(f"Found {len(webm_files)} .webm files in Eagle library")

    if not webm_files:
        log.info("Nothing to do.")
        return

    # Build jobs
    jobs = []
    for webm_path in webm_files:
        info_dir = webm_path.parent
        meta_path = info_dir / "metadata.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning(f"  Can't read metadata {meta_path}: {e}")
            continue

        folder_id = (meta.get("folders") or ["MIOUGBL8AF4E4"])[0]
        item_id = meta.get("id") or info_dir.name.removesuffix(".info")
        stem_base = webm_path.stem[:180]
        tmp_dir = TMP_ROOT / item_id

        jobs.append((str(webm_path), folder_id, item_id, stem_base, str(tmp_dir)))

    log.info(f"  {len(jobs)} valid jobs built")
    start = time.time()

    ok_count = fail_count = total_frames = 0

    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_process_one_webm, j): j for j in jobs}
        for fut in as_completed(futures):
            try:
                item_id, webm_s, avif_items, ok = fut.result()
            except Exception as e:
                log.error(f"  Worker exception: {e}")
                fail_count += 1
                continue

            webm_path = Path(webm_s)
            info_dir = webm_path.parent
            meta_path = info_dir / "metadata.json"

            if not ok or not avif_items:
                log.warning(f"  No frames extracted: {webm_path.name}")
                fail_count += 1
                # Cleanup tmp
                shutil.rmtree(TMP_ROOT / item_id, ignore_errors=True)
                continue

            # Import AVIF frames to Eagle
            folder_id = (json.loads(meta_path.read_text()).get("folders") or ["MIOUGBL8AF4E4"])[0]
            try:
                r = eagle_add_items(avif_items, folder_id)
                log.info(f"  {webm_path.name} → {len(avif_items)} frames: {r.get('status')}")
                total_frames += len(avif_items)
            except Exception as e:
                log.error(f"  Eagle import error for {webm_path.name}: {e}")
                fail_count += 1
                for avif_p, _ in avif_items:
                    Path(avif_p).unlink(missing_ok=True)
                shutil.rmtree(TMP_ROOT / item_id, ignore_errors=True)
                continue

            # Cleanup temp AVIFs
            for avif_p, _ in avif_items:
                Path(avif_p).unlink(missing_ok=True)
            shutil.rmtree(TMP_ROOT / item_id, ignore_errors=True)

            # Mark original webm item as deleted
            # Try Eagle API delete first
            deleted_via_api = eagle_delete_item(item_id)
            if deleted_via_api:
                log.info(f"  [{item_id}] deleted via Eagle API")
            else:
                # Fallback: mark isDeleted in metadata.json + unlink webm file
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    meta["isDeleted"] = True
                    meta["modificationTime"] = int(time.time() * 1000)
                    tmp = meta_path.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
                    os.replace(tmp, meta_path)
                    webm_path.unlink(missing_ok=True)
                    log.info(f"  [{item_id}] marked isDeleted in metadata.json")
                except Exception as e2:
                    log.error(f"  [{item_id}] cleanup failed: {e2}")

            ok_count += 1

    elapsed = time.time() - start
    log.info(f"\n{'═'*50}")
    log.info(f" DONE — {ok_count} webm converted ({total_frames} AVIF frames), {fail_count} fail")
    log.info(f" Total time: {elapsed:.0f}s")

    try:
        TMP_ROOT.rmdir()
    except OSError:
        pass


if __name__ == "__main__":
    main()
