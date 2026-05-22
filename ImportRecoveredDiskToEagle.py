#!/usr/bin/env python3
"""
ImportRecoveredDiskToEagle.py
Importe les fichiers de /Volumes/d/SS_VideoGames/A_Trier/_RECOVERED vers Eagle Games/.

- SOURCE : /Volumes/d/SS_VideoGames/A_Trier/_RECOVERED (récursif)
- TMP    : /Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline/
- STATE  : TMP/state_disk.json — tracker les fichiers déjà importés (resume)
- Images  → AVIF CRF25 → Eagle addFromPaths
- Vidéos  → frames ffmpeg 1/10s cap 50 → AVIF CRF25 → Eagle addFromPaths
- 16 workers ProcessPoolExecutor
- NTFS read-only : jamais de suppression de la source
- Classsification par nom de fichier (SortByGame.py + EXTRA_PATTERNS)
- Folder dedup via GET /api/folder/list au démarrage
"""

import json, os, re, shutil, subprocess, sys, time, logging, unicodedata, importlib.util
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from urllib import request as ureq

# ── Config ────────────────────────────────────────────────────────────────────
SOURCE        = Path("/Volumes/d/SS_VideoGames/A_Trier/_RECOVERED")
TMP_ROOT      = Path("/Users/zenray/NTFS_Recovery_ntfsundelete/tmp_disk_pipeline")
STATE_FILE    = TMP_ROOT / "state_disk.json"
EAGLE         = "http://localhost:41595"
AVIFENC       = "/opt/homebrew/bin/avifenc"
FFMPEG        = "/opt/homebrew/bin/ffmpeg"
WORKERS       = 16
GAMES_ROOT_ID = "MIOUGBL8AF4E4"
FRAMES_CAP    = 50
SORT_BY_GAME  = Path("/Users/zenray/Create/Build/Memory/Tools/Recovery/SortByGame.py")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".jxr", ".gif"}
VIDEO_EXTS = {".webm", ".mp4", ".mkv", ".mov", ".avi"}

# ── Logging ───────────────────────────────────────────────────────────────────
TMP_ROOT.mkdir(parents=True, exist_ok=True)
log = logging.getLogger("disk_import")
log.setLevel(logging.DEBUG)
fh = logging.FileHandler(str(TMP_ROOT / "pipeline.log"))
fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
sh = logging.StreamHandler(sys.stdout)
sh.setFormatter(logging.Formatter("[%(asctime)s] %(message)s"))
log.addHandler(fh)
log.addHandler(sh)

# ── State management ──────────────────────────────────────────────────────────
def load_state():
    """Returns set of relative path strings already DONE."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                d = json.load(f)
            return set(d.get("done", []))
        except Exception:
            return set()
    return set()

_state_done: set = set()
_state_lock = None  # populated in main (not needed cross-process, main thread only)

def mark_done(rel_path: str):
    _state_done.add(rel_path)
    # Flush state every 50 new entries
    if len(_state_done) % 50 == 0:
        _flush_state()

def _flush_state():
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump({"done": sorted(_state_done)}, f, indent=2)
    tmp.replace(STATE_FILE)

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

def get_existing_folder_map():
    try:
        r = eagle_get("/api/folder/list")
    except Exception as e:
        log.warning(f"Could not fetch folder list: {e}")
        return {}
    folder_map = {}
    def walk(f):
        name = f.get("name", "")
        fid = f.get("id")
        if name and fid:
            folder_map[canon(name)] = fid
        for child in f.get("children", []) or []:
            walk(child)
    for f in r.get("data", []):
        walk(f)
    return folder_map

def eagle_create_folder(name, parent_id):
    r = eagle_post("/api/folder/create", {"folderName": name, "parent": parent_id})
    return r["data"]["id"]

def eagle_add_items(items_list, folder_id):
    items = [{"path": p, "name": n, "tags": []} for p, n in items_list]
    return eagle_post("/api/item/addFromPaths", {"items": items, "folderId": folder_id})

# ── Classification ─────────────────────────────────────────────────────────────
def _load_sort_classify():
    spec = importlib.util.spec_from_file_location("sort_by_game", SORT_BY_GAME)
    if spec is None or spec.loader is None:
        return lambda n: None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.classify

def normalize_text(s):
    return unicodedata.normalize("NFC", s)

def canon(s):
    s = normalize_text(s).casefold()
    s = s.replace("ö", "o").replace("é", "e").replace("è", "e").replace("à", "a")
    return re.sub(r"[^a-z0-9]+", "", s)

def strip_numeric_suffix(name):
    return re.sub(r"(\.\d+)+$", "", name)

def effective_ext(path):
    name = path.name.removesuffix("@")
    return Path(strip_numeric_suffix(name)).suffix.lower()

def clean_game_name(raw):
    s = normalize_text(raw)
    s = re.sub(r"_+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" ._-")
    titled = s.title()
    for old, new in {"Vii": "VII", "Xvi": "XVI", "Xv": "XV", "Xii": "XII",
                     "Ii": "II", "Iii": "III", "Iv": "IV", "Vi": "VI"}.items():
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
    (re.compile(r"^Ccff7r_Win64_Shipping_Screenshot_", re.I), "Crisis Core Final Fantasy VII Reunion"),
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
    (re.compile(r"^dark souls iii \d{2}_\d{2}_\d{4} ", re.I), "Dark Souls III"),
    (re.compile(r"^a plague tale  innocence screenshot ", re.I), "A Plague Tale Innocence"),
]

def normalize_game_label(game):
    return FOLDER_ALIASES.get(canon(game), game)

def generic_classify(name, sort_classify):
    n = normalize_text(name)
    direct = sort_classify(n)
    if direct:
        return normalize_game_label(direct)
    for rx, game in EXTRA_PATTERNS:
        if rx.match(n):
            return normalize_game_label(game)
    m = re.match(r"^(.+?)_Screenshot_\d{4}\.\d{2}\.\d{2}___", n, re.I)
    if m:
        return normalize_game_label(clean_game_name(m.group(1)))
    m = re.match(r"^(.+?)_\d{14}(?:_\d+)?(?:\.|$)", n, re.I)
    if m:
        return normalize_game_label(clean_game_name(m.group(1)))
    m = re.match(r"^(.+?)\s+\d{2}_\d{2}_\d{4}\s+", n, re.I)
    if m and len(m.group(1)) > 2:
        return normalize_game_label(clean_game_name(m.group(1)))
    return "_Unknown Recovered"

def safe_stem(path):
    stripped = strip_numeric_suffix(path.name.removesuffix("@"))
    stem = Path(stripped).stem
    return normalize_text(stem).replace("/", "_").replace(":", "_")[:180] or "Recovered"

# ── Folder management ─────────────────────────────────────────────────────────
_folder_map = {}

def get_or_create_folder(game):
    key = canon(game)
    if key in _folder_map:
        return _folder_map[key]
    fresh = get_existing_folder_map()
    if key in fresh:
        _folder_map.update(fresh)
        return fresh[key]
    try:
        fid = eagle_create_folder(game, GAMES_ROOT_ID)
        _folder_map[key] = fid
        log.info(f"  Created Eagle folder: {game} [{fid}]")
        return fid
    except Exception as e:
        log.error(f"  Failed to create folder {game}: {e}")
        return GAMES_ROOT_ID

# ── Module-level constants for workers (pickling) ─────────────────────────────
_AVIFENC = str(AVIFENC)
_FFMPEG  = str(FFMPEG)
_FRAMES_CAP = FRAMES_CAP

# ── Image conversion worker ───────────────────────────────────────────────────
def _convert_image(args):
    src_s, dst_s = args
    src, dst = Path(src_s), Path(dst_s)
    dst.parent.mkdir(parents=True, exist_ok=True)
    ext = effective_ext(src)

    def run_avifenc(inp):
        r = subprocess.run(
            [_AVIFENC, "--min", "25", "--max", "25", "--speed", "10", "--jobs", "1",
             str(inp), dst_s],
            capture_output=True, timeout=180)
        return r.returncode == 0 and dst.exists() and dst.stat().st_size > 0

    if ext in (".gif", ".webp", ".jxr"):
        tmp_png = dst.with_suffix(".tmp.png")
        try:
            r = subprocess.run([_FFMPEG, "-y", "-loglevel", "error", "-i", src_s,
                                "-frames:v", "1", str(tmp_png)],
                               capture_output=True, timeout=120)
            if r.returncode != 0 or not tmp_png.exists():
                return False, src_s, dst_s
            return run_avifenc(tmp_png), src_s, dst_s
        finally:
            if tmp_png.exists():
                tmp_png.unlink(missing_ok=True)
    else:
        return run_avifenc(src), src_s, dst_s

# ── Video frame extraction + conversion worker ────────────────────────────────
def _process_video(args):
    src_s, tmp_dir_s, stem_base = args
    src = Path(src_s)
    tmp_dir = Path(tmp_dir_s)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    r = subprocess.run(
        [_FFMPEG, "-y", "-loglevel", "error", "-i", src_s,
         "-vf", f"fps=1/10,select='lte(n\\,{_FRAMES_CAP-1})'",
         "-vsync", "vfr",
         str(tmp_dir / "frame_%04d.png")],
        capture_output=True, timeout=3600)

    frames = sorted(tmp_dir.glob("frame_*.png"))
    if not frames:
        return src_s, [], False

    avif_items = []
    for fp in frames:
        idx = int(fp.stem.split("_")[1])
        t_s = (idx - 1) * 10
        avif_out = tmp_dir / f"{stem_base}_t{t_s:05d}s.avif"
        r2 = subprocess.run(
            [_AVIFENC, "--min", "25", "--max", "25", "--speed", "10", "--jobs", "1",
             str(fp), str(avif_out)],
            capture_output=True, timeout=180)
        if r2.returncode == 0 and avif_out.exists() and avif_out.stat().st_size > 0:
            avif_items.append((str(avif_out), avif_out.stem))
        fp.unlink(missing_ok=True)

    return src_s, avif_items, len(avif_items) > 0

# ── File discovery ─────────────────────────────────────────────────────────────
def discover_files(done_set):
    """Walk SOURCE recursively, return (images, videos) not yet in done_set."""
    images, videos = [], []
    if not SOURCE.exists():
        log.error(f"SOURCE not accessible: {SOURCE}")
        log.error("TCC block? Run from Terminal.app with Full Disk Access.")
        return images, videos

    for root, dirs, files in os.walk(SOURCE):
        # Skip macOS hidden dirs
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for fname in files:
            if fname.startswith("."):
                continue
            fp = Path(root) / fname
            rel = str(fp.relative_to(SOURCE))
            if rel in done_set:
                continue
            ext = effective_ext(fp)
            if ext in IMAGE_EXTS:
                images.append(fp)
            elif ext in VIDEO_EXTS:
                videos.append(fp)
            # else skip (non-media)

    return images, videos

# ── Phase 1: Images ───────────────────────────────────────────────────────────
def phase_images(files, sort_classify):
    log.info(f"  === PHASE 1: IMAGES ({len(files)} files) ===")
    if not files:
        return

    src_meta = {}
    used_dsts = set()
    tmp_img = TMP_ROOT / "img"

    for f in files:
        # If subfolder matches a game name directly, prefer it as hint
        parts = f.relative_to(SOURCE).parts
        # First try the subfolder name as game hint
        if len(parts) > 1:
            subfolder_game = normalize_game_label(clean_game_name(parts[0]))
            game = subfolder_game if subfolder_game != "_Unknown Recovered" else generic_classify(f.name, sort_classify)
        else:
            game = generic_classify(f.name, sort_classify)

        stem = safe_stem(f)
        folder_id = get_or_create_folder(game)
        tmp_dir = tmp_img / canon(game)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        avif_dst = tmp_dir / f"{stem}.avif"
        i = 0
        while str(avif_dst) in used_dsts or avif_dst.exists():
            i += 1
            avif_dst = tmp_dir / f"{stem}_{i}.avif"
        used_dsts.add(str(avif_dst))
        src_meta[str(f)] = (str(avif_dst), folder_id, str(f.relative_to(SOURCE)))

    all_jobs = [(src, avif) for src, (avif, _, _) in src_meta.items()]
    ok_count = fail_count = 0
    eagle_by_folder = {}

    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_convert_image, j): j for j in all_jobs}
        for fut in as_completed(futures):
            try:
                ok, src_s, dst_s = fut.result()
            except Exception as e:
                log.error(f"  Image worker exception: {e}")
                fail_count += 1
                continue
            if ok:
                ok_count += 1
                fid = src_meta[src_s][1]
                rel = src_meta[src_s][2]
                eagle_by_folder.setdefault(fid, []).append((dst_s, Path(dst_s).stem, rel))
            else:
                log.warning(f"  AVIF fail: {Path(src_s).name}")
                fail_count += 1

    log.info(f"  AVIF converted: {ok_count} OK, {fail_count} fail")

    imported = 0
    BATCH = 200
    for fid, items in eagle_by_folder.items():
        for i in range(0, len(items), BATCH):
            batch = items[i:i + BATCH]
            try:
                r = eagle_add_items([(p, n) for p, n, _ in batch], fid)
                log.info(f"  Eagle batch: {r.get('status')} ({len(batch)} items → {fid})")
                imported += len(batch)
                # Mark DONE in state (NTFS is read-only, never delete source)
                for _, _, rel in batch:
                    mark_done(rel)
                # Cleanup AVIF temp only
                for avif_p, _, _ in batch:
                    Path(avif_p).unlink(missing_ok=True)
            except Exception as e:
                log.error(f"  Eagle import error: {e}")
                for avif_p, _, _ in batch:
                    Path(avif_p).unlink(missing_ok=True)

    time.sleep(0.3)
    shutil.rmtree(tmp_img, ignore_errors=True)
    log.info(f"  Images: {imported} imported to Eagle")

# ── Phase 2: Videos ───────────────────────────────────────────────────────────
def phase_videos(files, sort_classify):
    log.info(f"  === PHASE 2: VIDEOS ({len(files)} files) ===")
    if not files:
        return

    jobs = []
    src_to_meta = {}
    for f in files:
        parts = f.relative_to(SOURCE).parts
        if len(parts) > 1:
            subfolder_game = normalize_game_label(clean_game_name(parts[0]))
            game = subfolder_game if subfolder_game != "_Unknown Recovered" else generic_classify(f.name, sort_classify)
        else:
            game = generic_classify(f.name, sort_classify)

        folder_id = get_or_create_folder(game)
        stem = safe_stem(f)
        tmp_dir = TMP_ROOT / "vid" / f"{stem}_{hash(str(f)) % 100000:05d}"
        jobs.append((str(f), str(tmp_dir), stem))
        src_to_meta[str(f)] = (game, folder_id, str(f.relative_to(SOURCE)))

    src_to_tmpdir = {j[0]: j[1] for j in jobs}
    ok_vids = fail_vids = total_frames = 0

    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(_process_video, j): j for j in jobs}
        for fut in as_completed(futures):
            try:
                src_s, avif_items, ok = fut.result()
            except Exception as e:
                log.error(f"  Video worker exception: {e}")
                fail_vids += 1
                continue

            tmp_dir_path = Path(src_to_tmpdir[src_s])

            if not ok or not avif_items:
                log.warning(f"  No frames: {Path(src_s).name}")
                fail_vids += 1
                shutil.rmtree(tmp_dir_path, ignore_errors=True)
                continue

            game, folder_id, rel = src_to_meta[src_s]
            total_frames += len(avif_items)

            BATCH = 200
            all_ok = True
            for i in range(0, len(avif_items), BATCH):
                batch = avif_items[i:i + BATCH]
                try:
                    r = eagle_add_items(batch, folder_id)
                    log.info(f"  [{Path(src_s).name}] Eagle: {r.get('status')} ({len(batch)} frames → {game})")
                except Exception as e:
                    log.error(f"  [{Path(src_s).name}] Eagle error: {e}")
                    all_ok = False

            for avif_path, _ in avif_items:
                Path(avif_path).unlink(missing_ok=True)
            shutil.rmtree(tmp_dir_path, ignore_errors=True)

            if all_ok:
                ok_vids += 1
                mark_done(rel)
            else:
                fail_vids += 1

    time.sleep(0.3)
    shutil.rmtree(TMP_ROOT / "vid", ignore_errors=True)
    log.info(f"  Videos: {ok_vids} OK ({total_frames} AVIF frames), {fail_vids} fail")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    log.info("═" * 55)
    log.info(" DISK _RECOVERED → EAGLE IMPORT PIPELINE")
    log.info(f" Source: {SOURCE}")
    log.info(f" Workers: {WORKERS}, CRF: 25, speed: 10, cap: {FRAMES_CAP} frames/video")
    log.info(f" State : {STATE_FILE}")
    log.info("═" * 55)

    # Check source accessibility
    if not SOURCE.exists():
        log.error(f"SOURCE inaccessible: {SOURCE}")
        log.error("Solutions:")
        log.error("  1. Vérifier que /Volumes/d est monté: diskutil mount /dev/disk4s2")
        log.error("  2. Lancer depuis Terminal.app (Full Disk Access requis pour NTFS)")
        log.error("  3. Double-cliquer LaunchImportRecoveredDiskToEagle.command")
        sys.exit(1)

    # Load resume state
    global _state_done
    _state_done = load_state()
    log.info(f"Resume: {len(_state_done)} files already done (from state_disk.json)")

    # Load Eagle folder map
    log.info("Loading Eagle folder map...")
    _folder_map.update(get_existing_folder_map())
    log.info(f"  {len(_folder_map)} existing folders loaded")

    # Load classifier
    log.info("Loading SortByGame classifier...")
    sort_classify = _load_sort_classify()

    # Discover files
    log.info(f"Scanning {SOURCE} ...")
    images, videos = discover_files(_state_done)
    log.info(f"  Images to process: {len(images)}")
    log.info(f"  Videos to process: {len(videos)}")
    log.info(f"  Already done (skipped): {len(_state_done)}")

    if not images and not videos:
        log.info("Nothing to do. All files already imported or folder is empty.")
        _flush_state()
        return

    start = time.time()

    phase_images(images, sort_classify)
    phase_videos(videos, sort_classify)

    _flush_state()

    elapsed = time.time() - start
    log.info("═" * 55)
    log.info(f" DONE — Total time: {elapsed / 60:.1f} min")
    log.info(f" Total done (cumulative): {len(_state_done)} files")
    log.info("═" * 55)

    try:
        TMP_ROOT.rmdir()
    except OSError:
        pass


if __name__ == "__main__":
    main()
