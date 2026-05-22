#!/usr/bin/env python3
"""
FinalCleanupByExt.py
Final pass on /by_ext : import remaining images/videos → Eagle, delete everything else.

Phases:
  1. Convert remaining images (png/jpg/gif/webp/other_img) → AVIF → Eagle
  2. Extract frames from remaining videos (mp4/other_video) → AVIF → Eagle
  3. Wait 120s for Eagle async addFromPaths to finish copying
  4. Delete all temp AVIF
  5. Delete all by_ext sources (imported or not - they've been tried)
  6. Delete non-importable dirs (audio/archive/pdf/office/torrent/_unknown/disc)
  7. Delete nonconvertible quarantine dir
  8. Delete run dirs / latest / tmp dirs
  9. If ntfsundelete is empty → delete it
"""

import json, os, re, shutil, subprocess, sys, time, logging, unicodedata
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from urllib import request as ureq

# ── Config ────────────────────────────────────────────────────────────────────
NTFS_ROOT   = Path("/Users/zenray/NTFS_Recovery_ntfsundelete")
BY_EXT      = NTFS_ROOT / "by_ext"
TMP_ROOT    = NTFS_ROOT / "tmp_final_byext"
EAGLE       = "http://localhost:41595"
AVIFENC     = "/opt/homebrew/bin/avifenc"
FFMPEG      = "/opt/homebrew/bin/ffmpeg"
WORKERS     = 16
FRAMES_CAP  = 50
GAMES_ROOT_ID = "MIOUGBL8AF4E4"
SORT_BY_GAME  = Path("/Users/zenray/Create/Build/Memory/Tools/Recovery/SortByGame.py")
EAGLE_ASYNC_WAIT = 120  # seconds to wait after final Eagle batch

IMAGE_BUCKETS   = ["png", "jpg", "webp", "gif", "other_img"]
VIDEO_BUCKETS   = ["mp4", "other_video"]
SKIP_BUCKETS    = ["audio", "archive", "pdf", "office", "torrent", "_unknown", "disc", "webm"]

DELETE_DIRS = [
    NTFS_ROOT / "nonconvertible_for_eagle_20260522",
    NTFS_ROOT / "run_20260522_131534",
    NTFS_ROOT / "run_20260522_145030_filtered",
    NTFS_ROOT / "latest",
    NTFS_ROOT / "latest_filtered",
    NTFS_ROOT / "tmp_avif_pipeline",
    NTFS_ROOT / "tmp_byext_pipeline",
    NTFS_ROOT / "tmp_disk_pipeline",
    NTFS_ROOT / "sorted_by_game",
]

# ── Logging ───────────────────────────────────────────────────────────────────
TMP_ROOT.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("final_cleanup")
log.setLevel(logging.DEBUG)
fh = logging.FileHandler(str(TMP_ROOT / "final_cleanup.log"))
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

def eagle_get(endpoint):
    req = ureq.Request(f"{EAGLE}{endpoint}")
    with ureq.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())

def get_folder_map():
    try:
        r = eagle_get("/api/folder/list")
    except Exception as e:
        log.warning(f"Could not fetch folder list: {e}")
        return {}
    fm = {}
    def walk(f):
        name = f.get("name",""); fid = f.get("id")
        if name and fid: fm[canon(name)] = fid
        for ch in f.get("children",[]) or []: walk(ch)
    for f in r.get("data",[]): walk(f)
    return fm

def eagle_ensure_folder(name, folder_map):
    key = canon(name)
    if key in folder_map:
        return folder_map[key]
    r = eagle_post("/api/folder/create", {"folderName": name, "parent": GAMES_ROOT_ID})
    fid = r["data"]["id"]
    folder_map[key] = fid
    return fid

def eagle_add_items(items_list, folder_id):
    items = [{"path": p, "name": n, "tags": []} for p, n in items_list]
    return eagle_post("/api/item/addFromPaths", {"items": items, "folderId": folder_id})

# ── Classification ────────────────────────────────────────────────────────────
import importlib.util

def _load_sort_classify():
    spec = importlib.util.spec_from_file_location("sort_by_game", SORT_BY_GAME)
    if spec is None or spec.loader is None:
        return lambda n: None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.classify

def canon(s):
    s = unicodedata.normalize("NFC", s).casefold()
    for a, b in [("ö","o"),("é","e"),("è","e"),("à","a")]: s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "", s)

def strip_numeric_suffix(name):
    return re.sub(r"(\.\d+)+$", "", name)

def effective_ext(path):
    name = path.name.removesuffix("@")
    return Path(strip_numeric_suffix(name)).suffix.lower()

def clean_game_name(raw):
    s = unicodedata.normalize("NFC", raw)
    s = re.sub(r"_+", " ", s); s = re.sub(r"\s+", " ", s).strip(" ._-")
    titled = s.title()
    for old, new in {"Vii":"VII","Xvi":"XVI","Xv":"XV","Xii":"XII","Ii":"II","Iii":"III","Iv":"IV","Vi":"VI"}.items():
        titled = re.sub(rf"\b{old}\b", new, titled)
    return titled

FOLDER_ALIASES = {
    "aplaguetalerequiem": "A Plague Tale Requiem",
    "aplaguetaleinnocence": "A Plague Tale Innocence",
    "thelastofuspartii": "The Last Of Us II",
    "finalfantasyxvidemo": "Final Fantasy XVI",
    "demofinalfantasyxvi": "Final Fantasy XVI",
    "finalfantasyxvwindowsedition": "Final Fantasy XV",
    "godofwarragnarok": "God of War Ragnarök",
    "godofwarragnaroek": "God of War Ragnarök",
    "clairobscurexpedition33": "Expedition 33",
    "hogwartslegacylheritagedepoudlard": "Hogwarts Legacy",
    "residentevil4biohazard4": "Resident Evil 4",
    "residentevilvillagebiohazardvillage": "Resident Evil Village",
    "marvelsspiderman2": "Marvel's Spider-Man 2",
    "sofrubicon": "Armored Core VI",
    "demonsouls": "Demon's Souls",
    "demonssouls": "Demon's Souls",
}

EXTRA_PATTERNS = [
    (re.compile(r"^1145350_\d{14}_\d+", re.I), "Hades II"),
    (re.compile(r"^2679460_\d{14}_\d+", re.I), "Metaphor ReFantazio"),
    (re.compile(r"^3014330_\d{14}_\d+", re.I), "Octopath Traveler"),
    (re.compile(r"^3564740_\d{14}_\d+", re.I), "Where Winds Meet"),
    (re.compile(r"^Ccff7r_Win64_Shipping_Screenshot_", re.I), "Crisis Core FFVII Reunion"),
    (re.compile(r"^13_Sentinels__Aegis_Rim_\d{14}", re.I), "13 Sentinels Aegis Rim"),
    (re.compile(r"^Ghost_Of_Tsushima_\d{14}", re.I), "Ghost Of Tsushima"),
    (re.compile(r"^Horizon_Forbidden_West_\d{14}", re.I), "Horizon Forbidden West"),
    (re.compile(r"^The_Last_Of_Us.*Part_Ii_\d{14}", re.I), "The Last Of Us II"),
    (re.compile(r"^God_Of_War_\d{14}", re.I), "God of War"),
    (re.compile(r"^Resident_Evil_4_\d{14}", re.I), "Resident Evil 4"),
    (re.compile(r"^Resident_Evil_7_Screenshot_", re.I), "Resident Evil 7"),
    (re.compile(r"^Resident_Evil_3_Remake_Screenshot_", re.I), "Resident Evil 3 Remake"),
    (re.compile(r"^Ratchet___Clank__Rift_Apart_\d{14}", re.I), "Ratchet And Clank Rift Apart"),
    (re.compile(r"^Persona_5_Royal_\d{14}", re.I), "Persona 5 Royal"),
    (re.compile(r"^Returnal_\d{14}", re.I), "Returnal"),
    (re.compile(r"^Monster_Hunter_Rise_Screenshot_", re.I), "Monster Hunter Rise"),
    (re.compile(r"^Village_\d{2}_\d{2}_\d{4}_", re.I), "Resident Evil Village"),
    (re.compile(r"^Days_Gone_Screenshot_", re.I), "Days Gone"),
    (re.compile(r"^Arise_Screenshot_", re.I), "Tales Of Arise"),
    (re.compile(r"^Final_Fantasy_Xv_Windows_Edition_\d{2}_\d{2}_\d{4}_", re.I), "Final Fantasy XV"),
    (re.compile(r"^De.mo_Final_Fantasy_Xvi_", re.I), "Final Fantasy XVI"),
    (re.compile(r"^Demon's_Souls_\d{14}", re.I), "Demon's Souls"),
]

def normalize_game_label(game):
    return FOLDER_ALIASES.get(canon(game), game)

def generic_classify(name, sort_classify):
    n = unicodedata.normalize("NFC", name)
    direct = sort_classify(n)
    if direct: return normalize_game_label(direct)
    for rx, game in EXTRA_PATTERNS:
        if rx.match(n): return normalize_game_label(game)
    m = re.match(r"^(.+?)_Screenshot_\d{4}\.\d{2}\.\d{2}___", n, re.I)
    if m: return normalize_game_label(clean_game_name(m.group(1)))
    m = re.match(r"^(.+?)_\d{14}(?:_\d+)?(?:\.|$)", n, re.I)
    if m: return normalize_game_label(clean_game_name(m.group(1)))
    m = re.match(r"^(.+?)\s+\d{2}_\d{2}_\d{4}\s+", n, re.I)
    if m and len(m.group(1)) > 2: return normalize_game_label(clean_game_name(m.group(1)))
    return "_Unknown Recovered"

def safe_stem(path):
    stripped = strip_numeric_suffix(path.name.removesuffix("@"))
    stem = Path(stripped).stem
    return unicodedata.normalize("NFC", stem).replace("/","_").replace(":","_")[:180] or "file"

# ── Conversion workers ────────────────────────────────────────────────────────
_AVIFENC = AVIFENC
_FFMPEG  = FFMPEG
_FRAMES_CAP = FRAMES_CAP

def _convert_image(args):
    src_s, dst_s = args
    src, dst = Path(src_s), Path(dst_s)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ext = effective_ext(src)
    def run_avifenc(inp):
        r = subprocess.run([_AVIFENC,"-q","60","--speed","10","--jobs","1",str(inp),dst_s],
                           capture_output=True, timeout=180)
        return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0
    if ext in (".gif",".webp",".jxr"):
        tmp_png = dst.with_suffix(".tmp.png")
        try:
            r = subprocess.run([_FFMPEG,"-y","-loglevel","error","-i",src_s,"-frames:v","1",str(tmp_png)],
                               capture_output=True, timeout=120)
            if r.returncode != 0 or not tmp_png.exists(): return False, src_s, dst_s
            return run_avifenc(tmp_png), src_s, dst_s
        finally:
            if tmp_png.exists(): tmp_png.unlink(missing_ok=True)
    else:
        return run_avifenc(src), src_s, dst_s

def _process_video(args):
    src_s, tmp_dir_s, stem = args
    src, tmp_dir = Path(src_s), Path(tmp_dir_s)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    r = subprocess.run([_FFMPEG,"-y","-loglevel","error","-i",src_s,
                        "-vf",f"fps=1/10","-frames:v",str(_FRAMES_CAP),
                        str(tmp_dir/"frame_%04d.png")],
                       capture_output=True, timeout=3600)
    frames = sorted(tmp_dir.glob("frame_*.png"))
    avif_items = []
    for fp in frames:
        idx = int(fp.stem.split("_")[1])
        t_s = (idx-1)*10
        avif_name = f"{stem}_t{t_s:05d}s"
        avif_out = tmp_dir / f"{avif_name}.avif"
        rc = subprocess.run([_AVIFENC,"-q","60","--speed","10","--jobs","1",str(fp),str(avif_out)],
                            capture_output=True, timeout=180)
        if rc.returncode == 0 and avif_out.exists() and avif_out.stat().st_size > 0:
            avif_items.append((str(avif_out), avif_name))
        fp.unlink(missing_ok=True)
    return src_s, avif_items, len(avif_items) > 0


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("═══════════════════════════════════════════════════")
    log.info(" FINAL CLEANUP : by_ext → Eagle + delete all")
    log.info("═══════════════════════════════════════════════════")
    start = time.time()

    sort_classify = _load_sort_classify()
    folder_map = get_folder_map()
    log.info(f"Eagle folders loaded: {len(folder_map)}")

    # Collect all pending AVIF paths to delete after Eagle async wait
    # (avif_path_str, folder_id, item_name, src_path_str)
    all_eagle_items = []  # (avif_path_str, item_name, folder_id, src_path_str)
    img_ok = img_fail = 0
    vid_ok = vid_fail = 0

    # ── Phase 1 : images ──────────────────────────────────────────────────────
    log.info("\n── Phase 1 : images ──")
    image_jobs = []
    src_to_meta = {}  # src_str → (dst_str, folder_id, name)

    for bucket in IMAGE_BUCKETS:
        bucket_dir = BY_EXT / bucket
        if not bucket_dir.exists(): continue
        for f in bucket_dir.iterdir():
            if not f.is_file(): continue
            stem = safe_stem(f)
            dst = TMP_ROOT / "images" / f"{stem}.avif"
            i = 0
            while dst.exists() or str(dst) in (v[0] for v in src_to_meta.values()):
                i += 1; dst = TMP_ROOT / "images" / f"{stem}_{i}.avif"
            game = generic_classify(f.name, sort_classify)
            fid = eagle_ensure_folder(game, folder_map)
            image_jobs.append((str(f), str(dst)))
            src_to_meta[str(f)] = (str(dst), fid, stem)

    log.info(f"Image jobs: {len(image_jobs)}")
    (TMP_ROOT / "images").mkdir(parents=True, exist_ok=True)

    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_convert_image, j): j for j in image_jobs}
        for fut in as_completed(futures):
            try:
                ok, src_s, dst_s = fut.result()
            except Exception as e:
                log.error(f"  image worker error: {e}")
                img_fail += 1; continue
            if ok:
                _, fid, name = src_to_meta[src_s]
                all_eagle_items.append((dst_s, name, fid, src_s))
                img_ok += 1
            else:
                img_fail += 1

    log.info(f"Images: {img_ok} ok, {img_fail} fail")

    # ── Phase 2 : videos ──────────────────────────────────────────────────────
    log.info("\n── Phase 2 : videos ──")
    video_jobs = []
    for bucket in VIDEO_BUCKETS:
        bucket_dir = BY_EXT / bucket
        if not bucket_dir.exists(): continue
        for f in bucket_dir.iterdir():
            if not f.is_file(): continue
            stem = safe_stem(f)
            tmp_dir = TMP_ROOT / "videos" / stem
            video_jobs.append((str(f), str(tmp_dir), stem))

    log.info(f"Video jobs: {len(video_jobs)}")
    src_to_game = {j[0]: generic_classify(Path(j[0]).name, sort_classify) for j in video_jobs}

    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_process_video, j): j for j in video_jobs}
        for fut in as_completed(futures):
            try:
                src_s, avif_items, ok = fut.result()
            except Exception as e:
                log.error(f"  video worker error: {e}")
                vid_fail += 1; continue
            if ok and avif_items:
                game = src_to_game.get(src_s, "_Unknown Recovered")
                fid = eagle_ensure_folder(game, folder_map)
                for avif_path, avif_name in avif_items:
                    all_eagle_items.append((avif_path, avif_name, fid, src_s))
                vid_ok += 1
                log.info(f"  {Path(src_s).name} → {len(avif_items)} frames")
            else:
                vid_fail += 1

    log.info(f"Videos: {vid_ok} ok, {vid_fail} fail")

    # ── Phase 3 : batch import to Eagle ───────────────────────────────────────
    log.info(f"\n── Phase 3 : Eagle import ({len(all_eagle_items)} items) ──")
    BATCH = 200
    # Group by folder_id
    by_folder: dict[str, list] = {}
    for avif_path, name, fid, src_s in all_eagle_items:
        by_folder.setdefault(fid, []).append((avif_path, name))

    batches_sent = 0
    for fid, items in by_folder.items():
        for i in range(0, len(items), BATCH):
            batch = items[i:i+BATCH]
            try:
                r = eagle_add_items(batch, fid)
                batches_sent += 1
                log.info(f"  Batch {batches_sent}: {len(batch)} items → Eagle ({r.get('status')})")
            except Exception as e:
                log.error(f"  Eagle import error: {e}")

    # ── Phase 4 : wait for Eagle async copy ───────────────────────────────────
    log.info(f"\n── Phase 4 : waiting {EAGLE_ASYNC_WAIT}s for Eagle async copy ──")
    time.sleep(EAGLE_ASYNC_WAIT)

    # ── Phase 5 : delete temp AVIF ────────────────────────────────────────────
    log.info("── Phase 5 : delete temp AVIF ──")
    shutil.rmtree(TMP_ROOT, ignore_errors=True)

    # ── Phase 6 : delete all by_ext source files ──────────────────────────────
    log.info("── Phase 6 : delete by_ext sources ──")
    for bucket in list(IMAGE_BUCKETS) + list(VIDEO_BUCKETS) + list(SKIP_BUCKETS):
        d = BY_EXT / bucket
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            log.info(f"  deleted by_ext/{bucket}")
    # Delete remaining by_ext dir
    shutil.rmtree(BY_EXT, ignore_errors=True)
    log.info("  deleted by_ext/")

    # ── Phase 7 : delete other dirs ───────────────────────────────────────────
    log.info("── Phase 7 : delete recovery dirs ──")
    for d in DELETE_DIRS:
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
            log.info(f"  deleted {d.name}")

    # ── Phase 8 : final check and delete ntfsundelete if empty ────────────────
    log.info("── Phase 8 : final ntfsundelete cleanup ──")
    remaining = list(NTFS_ROOT.iterdir()) if NTFS_ROOT.exists() else []
    log.info(f"  Remaining in ntfsundelete: {[x.name for x in remaining]}")
    if not remaining:
        NTFS_ROOT.rmdir()
        log.info("  ntfsundelete deleted (was empty)")
    else:
        log.info(f"  {len(remaining)} items remain — not deleting root")
        for item in remaining:
            log.info(f"    {item.name} ({item.stat().st_size if item.is_file() else 'dir'})")

    elapsed = time.time() - start
    log.info(f"\n{'═'*50}")
    log.info(f" DONE — {img_ok} images, {vid_ok} videos imported to Eagle")
    log.info(f" {img_fail} img fail, {vid_fail} vid fail (files deleted regardless)")
    log.info(f" Total time: {elapsed/60:.1f} min")


if __name__ == "__main__":
    main()
