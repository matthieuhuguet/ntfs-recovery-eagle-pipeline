#!/usr/bin/env python3
"""
ImportRecoveredToEagle.py — Pipeline simple en 3 phases :
  1. Stage  : copie douce des médias de _RECOVERED vers ~/tmp_d_img/staged/ (1 thread, ménage le disque)
  2. Convert: AVIF CRF 25 avec 16 workers (SSD local, plein gaz)
  3. Import : Eagle API addFromPaths par batch de 200, classé par jeu

Source  : /Users/zenray/.mounty/d/SS_VideoGames/A_Trier/_RECOVERED
Staging : /Users/zenray/tmp_d_img/staged
AVIF tmp: /Users/zenray/tmp_d_img/tmp_avif
State   : /Users/zenray/tmp_d_img/state.json (resume)
Log     : /Users/zenray/tmp_d_img/pipeline.log
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

# --- Config ---
SOURCE = Path("/Users/zenray/.mounty/d/SS_VideoGames/A_Trier/_RECOVERED")
STAGING = Path("/Users/zenray/tmp_d_img/staged")
AVIF_DIR = Path("/Users/zenray/tmp_d_img/tmp_avif")
STATE_FILE = Path("/Users/zenray/tmp_d_img/state.json")
LOG_FILE = Path("/Users/zenray/tmp_d_img/pipeline.log")
EAGLE_URL = "http://localhost:41595"
WORKERS = 16
BATCH_SIZE = 200
EAGLE_PARENT_FOLDER = "Games"

# Extensions média valides (après strip du suffixe ntfsundelete .N)
IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".jfif"}
VID_EXTS = {".mp4", ".webm"}
ALL_MEDIA = IMG_EXTS | VID_EXTS

# --- Game classification (from SortByGame.py) ---
RAW_PATTERNS = [
    (r"^Final_Fantasy_Vii_Rebirth_\d{14}\.", "Final Fantasy VII Rebirth"),
    (r"^Ff7rebirth__[A-Za-z0-9]+\.", "Final Fantasy VII Rebirth"),
    (r"^Final_Fantasy_Vii_Remake_\d{14}\.", "Final Fantasy VII Remake"),
    (r"^Ccff7r_Win64_Shipping_[A-Za-z0-9]+\.", "Crisis Core Final Fantasy VII Reunion"),
    (r"^Final_Fantasy_Xv_Windows_Edition_\d{14}\.", "Final Fantasy XV"),
    (r"^Final_Fantasy_Xvi_\d{14}\.", "Final Fantasy XVI"),
    (r"^Final_Fantasy_Xii_The_Zodiac_Age_\d{14}\.", "Final Fantasy XII The Zodiac Age"),
    (r"^Stranger_Of_Paradise_Final_Fantasy_Origin_\d{14}\.", "Stranger of Paradise Final Fantasy Origin"),
    (r"^B1_Win64_Shipping_[A-Za-z0-9]+\.", "Black Myth Wukong"),
    (r"^Sandfall_[A-Za-z0-9]+\.", "Clair Obscur Expedition 33"),
    (r"^Farlonesails_\d", "Sea of Stars"),
    (r"^Alanwake2_[A-Za-z0-9]+\.", "Alan Wake 2"),
    (r"^Alan_Wake_2_Screenshot_", "Alan Wake 2"),
    (r"^Stray_Screenshot_", "Stray"),
    (r"^Cyberpunk_2077_Screenshot_", "Cyberpunk 2077"),
    (r"^A_Plague_Tale__Requiem_Screenshot_", "A Plague Tale Requiem"),
    (r"(?i)^a_plague_tale__requiem_screenshot_", "A Plague Tale Requiem"),
    (r"(?i)^ence_screenshot_", "A Plague Tale Innocence"),
    (r"^Bloodborne", "Bloodborne"),
    (r"^Elden_Ring_", "Elden Ring"),
    (r"^God_Of_War_Ragnar(?:ö|o)k_", "God of War Ragnarok"),
    (r"^Marvel's_Spider_Man__Miles_Morales_Screenshot_", "Spider-Man Miles Morales"),
    (r"^Marvel's_Spider_Man_Screenshot_", "Spider-Man"),
    (r"^Metaphor_", "Metaphor ReFantazio"),
    (r"^Like_A_Dragon__Ishin!_", "Like a Dragon Ishin"),
    (r"^Yakuza__Like_A_Dragon_", "Yakuza Like A Dragon"),
    (r"^Judgment_\d{14}\.", "Judgment"),
    (r"^The_Centennial_Case__A_Shijima_Story_", "The Centennial Case"),
    (r"^Nier_Automata_", "Nier Automata"),
    (r"^Labyrinth_Of_Yomi_", "Labyrinth of Yomi"),
    (r"^Hogwarts_Legacy___L'h(?:é|e)ritage_De_Poudlard_", "Hogwarts Legacy"),
    (r"^Hogwarts_Legacy_", "Hogwarts Legacy"),
    (r"^yssey_screenshot_", "Assassin's Creed Odyssey"),
    (r"^yssey screenshot ", "Assassin's Creed Odyssey"),
    (r"(?i)^assassin's creed valhalla ", "Assassin's Creed Valhalla"),
    (r"^Baldur's_Gate_3_Screenshot_", "Baldur's Gate 3"),
    (r"^Octopath_Traveler2_Screenshot_", "Octopath Traveler II"),
    (r"^Octopath_Traveler2_\d{14}\.", "Octopath Traveler II"),
    (r"^Nioh_2_The_Complete_Edition_", "Nioh 2"),
    (r"^Starfield_Screenshot_", "Starfield"),
    (r"^Steelrising_Screenshot_", "Steelrising"),
    (r"^Lords_Of_The_Fallen_\(2023\)_", "Lords of the Fallen 2023"),
    (r"^The_Witcher_3_Screenshot_", "The Witcher 3"),
    (r"^The_Witcher_3_\d{14}\.", "The Witcher 3"),
    (r"(?i)^hollow knight \d", "Hollow Knight"),
    (r"^Hitman_Screenshot_", "Hitman"),
    (r"(?i)^hearthstone_screenshot_", "Hearthstone"),
    (r"^Hearthstone__Heroes_Of_Warcraft_Screenshot_", "Hearthstone"),
    (r"^Starcraft_Ii_Screenshot_", "Starcraft II"),
    (r"^Frostpunk_Screenshot_", "Frostpunk"),
    (r"^Forspoken_Screenshot_", "Forspoken"),
    (r"^Forza_Motorsport_Screenshot_", "Forza Motorsport"),
    (r"^Inscryption_Screenshot_", "Inscryption"),
    (r"^Engarde_Screenshot_", "En Garde"),
    (r"^Remnant2_Screenshot_", "Remnant 2"),
    (r"^Layersoffear_Win64_Shipping_[A-Za-z0-9]+\.", "Layers of Fear"),
    (r"^Blasphemous_2_Screenshot_", "Blasphemous 2"),
    (r"^Armored_Core_Vi_Fires_Of_Rubicon_Screenshot_", "Armored Core VI"),
    (r"^Middle_Earth__Shadow_Of_War_Screenshot_", "Middle Earth Shadow of War"),
    (r"^Madden_Nfl_22_Screenshot_", "Madden NFL 22"),
    (r"^Star_Ocean_The_Divine_Force_D(?:é|e)mo_Screenshot_", "Star Ocean The Divine Force Demo"),
    (r"^Star_Ocean_The_Second_Story_R_Demo_Screenshot_", "Star Ocean Second Story R Demo"),
    (r"^Titanfall_2_Screenshot_", "Titanfall 2"),
    (r"^I_Am_Setsuna_Screenshot_", "I Am Setsuna"),
    (r"^Tomb_Raider_\(2013\)_Screenshot_", "Tomb Raider 2013"),
    (r"^Chants_Of_Sennaar_Screenshot_", "Chants of Sennaar"),
    (r"^Ghost_Trick_Demo_Screenshot_", "Ghost Trick Demo"),
    (r"^Grand_Theft_Auto_4_Screenshot_", "Grand Theft Auto IV"),
    (r"^Grand_Theft_Auto_V_Screenshot_", "Grand Theft Auto V"),
    (r"(?i)^dishonored \d", "Dishonored"),
    (r"(?i)^dows_edition_screenshot_", "Final Fantasy XV"),
    (r"(?i)^nant_kingdom_screenshot_", "Ni No Kuni II"),
    (r"(?i)^mplete_edition_screenshot_", "Nioh 2"),
    (r"^Ghost_Of_Tsushima", "Ghost of Tsushima"),
    (r"^Horizon_Zero_Dawn", "Horizon Zero Dawn"),
    (r"^Horizon_Forbidden_West", "Horizon Forbidden West"),
    (r"^The_Last_Of_Us", "The Last of Us"),
    (r"^Tlou2", "The Last of Us Part II"),
    (r"^Uncharted", "Uncharted"),
    (r"^Days_Gone", "Days Gone"),
    (r"^Ratchet_", "Ratchet and Clank"),
    (r"^Returnal", "Returnal"),
    (r"^Deathloop", "Deathloop"),
    (r"^Sifu", "Sifu"),
    (r"^Tunic_Screenshot_", "Tunic"),
    (r"^Hades_Screenshot_", "Hades"),
    (r"^Celeste_Screenshot_", "Celeste"),
    (r"^Outer_Wilds_Screenshot_", "Outer Wilds"),
    (r"^Death_Stranding", "Death Stranding"),
    (r"^Shadow_Of_The_Colossus", "Shadow of the Colossus"),
    (r"^Ico_Screenshot_", "Ico"),
    (r"^The_Pathless_Screenshot_", "The Pathless"),
    (r"^Kena__Bridge_Of_Spirits_Screenshot_", "Kena Bridge of Spirits"),
    (r"^It_Takes_Two_Screenshot_", "It Takes Two"),
    (r"^Sable_Screenshot_", "Sable"),
    (r"^Eastward_Screenshot_", "Eastward"),
    (r"^Sea_Of_Stars_Screenshot_", "Sea of Stars"),
    (r"^Divinity_Original_Sin_2_Screenshot_", "Divinity Original Sin 2"),
    (r"^Disco_Elysium_Screenshot_", "Disco Elysium"),
    (r"^Persona_5_", "Persona 5"),
    (r"^Persona_3_", "Persona 3"),
    (r"^Demon's_Souls", "Demon's Souls"),
    (r"^Dark_Souls", "Dark Souls"),
    (r"^Sekiro", "Sekiro"),
    (r"^Wo_Long", "Wo Long Fallen Dynasty"),
    (r"^Wild_Hearts_Screenshot_", "Wild Hearts"),
    (r"^No_More_Heroes", "No More Heroes"),
    (r"^Bayonetta", "Bayonetta"),
    (r"^Devil_May_Cry", "Devil May Cry"),
    (r"^Resident_Evil", "Resident Evil"),
    (r"^Monster_Hunter", "Monster Hunter"),
    (r"^Dragon's_Dogma", "Dragon's Dogma"),
    (r"^Street_Fighter", "Street Fighter"),
    (r"^Tekken", "Tekken"),
    (r"^Mortal_Kombat", "Mortal Kombat"),
    (r"^Control_Screenshot_", "Control"),
    (r"^Quantum_Break_Screenshot_", "Quantum Break"),
    (r"^Prey_Screenshot_", "Prey"),
    (r"^Bioshock", "Bioshock"),
    (r"^System_Shock", "System Shock"),
    (r"^Deus_Ex", "Deus Ex"),
    (r"^Hi_Fi_Rush_Screenshot_", "Hi-Fi Rush"),
    (r"^Psychonauts_2_Screenshot_", "Psychonauts 2"),
    # Switch screenshots (format Nintendo: YYYYMMDDHHMMSS_C.jpg)
    (r"^\d{14}_[cs]\.", "_Switch Screenshots"),
    # Generic _Screenshot_ pattern (catches remaining games)
    (r"^([A-Z][A-Za-z0-9_'!]+)_Screenshot_\d", None),  # special: extract game name
    # HighresScreenshot (Unreal Engine in-game, not editor icons)
    (r"^HighresScreenshot\d+\.", "_Unreal Screenshots"),
    # OBS recordings
    (r"^OBS \d{4}-\d{2}-\d{2}", "_OBS Recordings"),
]

PATTERNS = [(re.compile(p), name) for p, name in RAW_PATTERNS]


def classify(filename: str) -> str:
    """Return game folder name or '_Unknown' if no match."""
    n = unicodedata.normalize("NFC", filename)
    for rx, game in PATTERNS:
        m = rx.match(n)
        if m:
            if game is None:
                # Extract game name from the generic pattern
                raw = m.group(1).replace("_", " ").strip()
                return raw
            return game
    return "_Unknown Recovered"


def strip_ntfsundelete_suffix(filename: str) -> tuple[str, str]:
    """Strip trailing .N suffixes from ntfsundelete and return (clean_name, real_ext).
    Examples: 'foo.png.1' -> ('foo.png', '.png'), 'bar.jpg' -> ('bar.jpg', '.jpg')
    """
    name = filename
    # Strip trailing numeric suffixes (.1, .2, ..., .131)
    while True:
        stem, ext = os.path.splitext(name)
        if ext and ext[1:].isdigit():
            name = stem
        else:
            break
    _, real_ext = os.path.splitext(name)
    return name, real_ext.lower()


def is_media(filename: str) -> bool:
    """Check if file is a valid media file after stripping ntfsundelete suffixes."""
    _, ext = strip_ntfsundelete_suffix(filename)
    return ext in ALL_MEDIA


def is_image(filename: str) -> bool:
    _, ext = strip_ntfsundelete_suffix(filename)
    return ext in IMG_EXTS


# --- State management ---
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            return {}
    return {}


def save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=1))


def log(msg: str):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# --- Eagle API helpers ---
def eagle_api(endpoint: str, data: dict | None = None) -> dict | None:
    url = f"{EAGLE_URL}{endpoint}"
    try:
        if data is not None:
            body = json.dumps(data).encode()
            req = Request(url, data=body, headers={"Content-Type": "application/json"})
        else:
            req = Request(url)
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except Exception as e:
        log(f"Eagle API error {endpoint}: {e}")
        return None


def eagle_get_or_create_folder(name: str, parent_id: str | None = None) -> str | None:
    """Get existing folder ID or create it. Returns folder ID."""
    # List all folders
    r = eagle_api("/api/folder/list")
    if not r or "data" not in r:
        return None

    def find_folder(folders, target_name, pid):
        for f in folders:
            match_name = f["name"] == target_name
            match_parent = (pid is None) or (f.get("parent") == pid) or (pid == "" and not f.get("parent"))
            if match_name and match_parent:
                return f["id"]
            found = find_folder(f.get("children", []), target_name, pid)
            if found:
                return found
        return None

    fid = find_folder(r["data"], name, parent_id)
    if fid:
        return fid

    # Create
    payload = {"folderName": name}
    if parent_id:
        payload["parent"] = parent_id
    cr = eagle_api("/api/folder/create", payload)
    if cr and "data" in cr:
        return cr["data"].get("id")
    return None


# Cache folder IDs
_folder_cache: dict[str, str] = {}


def get_game_folder_id(game_name: str) -> str | None:
    if game_name in _folder_cache:
        return _folder_cache[game_name]

    # Get or create Games parent
    if "___GAMES___" not in _folder_cache:
        gid = eagle_get_or_create_folder(EAGLE_PARENT_FOLDER, "")
        if not gid:
            log("ERROR: cannot find/create Games folder in Eagle")
            return None
        _folder_cache["___GAMES___"] = gid

    games_id = _folder_cache["___GAMES___"]
    fid = eagle_get_or_create_folder(game_name, games_id)
    if fid:
        _folder_cache[game_name] = fid
    return fid


def eagle_import_batch(paths: list[str], folder_id: str) -> bool:
    """Import a batch of files into Eagle."""
    items = [{"path": p, "folderId": folder_id} for p in paths]
    r = eagle_api("/api/item/addFromPaths", {"items": items})
    return r is not None and r.get("status") == "success"


# --- Workers ---
def convert_to_avif(src_path: str) -> str | None:
    """Convert a single image to AVIF. Returns AVIF path or None on failure."""
    src = Path(src_path)
    # Determine output name (strip ntfsundelete suffix, change ext)
    clean_name, _ = strip_ntfsundelete_suffix(src.name)
    out_stem = Path(clean_name).stem
    out_path = AVIF_DIR / f"{out_stem}.avif"

    # Skip if already exists
    if out_path.exists():
        return str(out_path)

    # Ensure unique name
    if out_path.exists():
        i = 1
        while True:
            candidate = AVIF_DIR / f"{out_stem}__{i}.avif"
            if not candidate.exists():
                out_path = candidate
                break
            i += 1

    try:
        # Decode with ffmpeg to PNG pipe, encode with avifenc
        # For most formats avifenc can read directly (png, jpg, y4m)
        _, ext = strip_ntfsundelete_suffix(src.name)

        if ext in {".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".jfif"}:
            # Decode to PNG first via ffmpeg
            tmp_png = AVIF_DIR / f"_tmp_{os.getpid()}_{out_stem}.png"
            r = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), str(tmp_png)],
                capture_output=True, timeout=30
            )
            if r.returncode != 0:
                tmp_png.unlink(missing_ok=True)
                return None
            encode_src = str(tmp_png)
        else:
            encode_src = str(src)

        r = subprocess.run(
            ["avifenc", "--min", "25", "--max", "25", "--speed", "10", "--jobs", "1",
             encode_src, str(out_path)],
            capture_output=True, timeout=60
        )

        # Cleanup tmp png
        if ext in {".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".jfif"}:
            Path(encode_src).unlink(missing_ok=True)

        if r.returncode != 0 or not out_path.exists():
            out_path.unlink(missing_ok=True)
            return None

        # Only keep if AVIF is smaller
        src_size = src.stat().st_size
        avif_size = out_path.stat().st_size
        if avif_size >= src_size:
            # Keep original, copy to avif dir with original ext
            out_path.unlink(missing_ok=True)
            fallback = AVIF_DIR / f"{out_stem}{ext}"
            if not fallback.exists():
                shutil.copy2(str(src), str(fallback))
            return str(fallback)

        return str(out_path)

    except Exception:
        out_path.unlink(missing_ok=True)
        return None


def extract_video_frames(src_path: str) -> list[str]:
    """Extract frames from video at 1 frame per 10 seconds, convert to AVIF. Max 50 frames."""
    src = Path(src_path)
    clean_name, _ = strip_ntfsundelete_suffix(src.name)
    stem = Path(clean_name).stem
    prefix = AVIF_DIR / f"vid_{stem}"

    frames = []
    tmp_dir = AVIF_DIR / f"_vidtmp_{stem}"
    tmp_dir.mkdir(exist_ok=True)

    try:
        # Extract PNG frames
        r = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
             "-vf", "fps=1/10", "-frames:v", "50",
             str(tmp_dir / "frame_%04d.png")],
            capture_output=True, timeout=120
        )
        if r.returncode != 0:
            return []

        # Convert each frame to AVIF
        for png in sorted(tmp_dir.glob("frame_*.png")):
            idx = png.stem.split("_")[1]
            avif_out = AVIF_DIR / f"vid_{stem}_f{idx}.avif"
            if not avif_out.exists():
                subprocess.run(
                    ["avifenc", "--min", "25", "--max", "25", "--speed", "10", "--jobs", "1",
                     str(png), str(avif_out)],
                    capture_output=True, timeout=30
                )
            png.unlink(missing_ok=True)
            if avif_out.exists():
                frames.append(str(avif_out))
    except Exception:
        pass
    finally:
        # Cleanup tmp dir
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return frames


# --- Main pipeline ---
def main():
    STAGING.mkdir(parents=True, exist_ok=True)
    AVIF_DIR.mkdir(parents=True, exist_ok=True)

    state = load_state()
    log("=" * 60)
    log("Pipeline ImportRecoveredToEagle START")

    # ========== PHASE 1: Stage (gentle, 1 thread) ==========
    log("PHASE 1: Staging media files from _RECOVERED (gentle, 1 thread)")

    # List source files (single ls, already cached by OS)
    src_files = []
    try:
        with os.scandir(SOURCE) as it:
            for entry in it:
                if entry.is_file(follow_symlinks=False) and is_media(entry.name):
                    if entry.name not in state:
                        src_files.append(entry.name)
    except PermissionError:
        log("ERROR: Permission denied reading source. Run from Terminal.app with FDA.")
        return
    except Exception as e:
        log(f"ERROR scanning source: {e}")
        return

    log(f"  {len(src_files)} new media files to stage (skipping {len(state)} already processed)")

    staged_count = 0
    fail_count = 0
    for i, name in enumerate(src_files):
        src = SOURCE / name
        dst = STAGING / name
        try:
            if not dst.exists():
                shutil.copy2(str(src), str(dst))
            state[name] = {"status": "staged"}
            staged_count += 1

            # Progress every 500 files
            if staged_count % 500 == 0:
                log(f"  Staged {staged_count}/{len(src_files)} (fail: {fail_count})")
                save_state(state)

        except Exception as e:
            state[name] = {"status": "stage_fail", "error": str(e)}
            fail_count += 1

            # If disk errors pile up, stop staging
            if fail_count > 50 and fail_count > staged_count * 0.1:
                log(f"  TOO MANY FAILURES ({fail_count}), stopping staging early")
                break

    save_state(state)
    log(f"  PHASE 1 DONE: {staged_count} staged, {fail_count} failed")

    # Delete source files that were staged successfully
    log("  Cleaning staged originals from _RECOVERED...")
    deleted = 0
    for name, info in state.items():
        if info.get("status") == "staged":
            src = SOURCE / name
            try:
                if src.exists():
                    src.unlink()
                    deleted += 1
            except Exception:
                pass
    log(f"  Deleted {deleted} originals from _RECOVERED")

    # ========== PHASE 2: Convert to AVIF (16 workers, local SSD) ==========
    log("PHASE 2: Converting to AVIF (16 workers)")

    to_convert = []
    for name, info in state.items():
        if info.get("status") == "staged":
            path = STAGING / name
            if path.exists():
                to_convert.append((name, str(path)))

    log(f"  {len(to_convert)} files to convert")

    # Separate images and videos
    images = [(n, p) for n, p in to_convert if is_image(n)]
    videos = [(n, p) for n, p in to_convert if not is_image(n)]

    log(f"  Images: {len(images)}, Videos: {len(videos)}")

    # Convert images in parallel
    converted = 0
    convert_fail = 0
    with ProcessPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(convert_to_avif, path): name for name, path in images}
        for fut in as_completed(futures):
            name = futures[fut]
            try:
                result = fut.result()
                if result:
                    game = classify(name)
                    state[name] = {"status": "converted", "avif": result, "game": game}
                    converted += 1
                else:
                    state[name] = {"status": "convert_fail"}
                    convert_fail += 1
            except Exception as e:
                state[name] = {"status": "convert_fail", "error": str(e)}
                convert_fail += 1

            if (converted + convert_fail) % 500 == 0:
                log(f"  Converted {converted}, failed {convert_fail} / {len(images)}")
                save_state(state)

    # Process videos (sequential, each spawns ffmpeg)
    for name, path in videos:
        frames = extract_video_frames(path)
        if frames:
            game = classify(name)
            state[name] = {"status": "converted", "avif": frames[0], "frames": frames, "game": game}
            converted += 1
        else:
            state[name] = {"status": "convert_fail"}
            convert_fail += 1

    save_state(state)
    log(f"  PHASE 2 DONE: {converted} converted, {convert_fail} failed")

    # Clean staging (converted files)
    log("  Cleaning staged files...")
    for name, info in state.items():
        if info.get("status") == "converted":
            staged_file = STAGING / name
            staged_file.unlink(missing_ok=True)
    log("  Staging cleaned")

    # ========== PHASE 3: Import to Eagle ==========
    log("PHASE 3: Importing to Eagle")

    # Check Eagle is running
    r = eagle_api("/api/application/info")
    if not r:
        log("ERROR: Eagle not running. Start Eagle and re-run.")
        save_state(state)
        return

    # Group by game
    by_game: dict[str, list[str]] = {}
    for name, info in state.items():
        if info.get("status") != "converted":
            continue
        game = info.get("game", "_Unknown Recovered")
        paths = info.get("frames", [info.get("avif")])
        paths = [p for p in paths if p and Path(p).exists()]
        if paths:
            by_game.setdefault(game, []).extend(paths)

    total_to_import = sum(len(v) for v in by_game.values())
    log(f"  {total_to_import} files in {len(by_game)} game folders")

    imported = 0
    for game, paths in sorted(by_game.items()):
        folder_id = get_game_folder_id(game)
        if not folder_id:
            log(f"  SKIP {game}: cannot create Eagle folder")
            continue

        # Batch import
        for i in range(0, len(paths), BATCH_SIZE):
            batch = paths[i:i + BATCH_SIZE]
            ok = eagle_import_batch(batch, folder_id)
            if ok:
                imported += len(batch)
            else:
                log(f"  Eagle import fail: {game} batch {i}")

        log(f"  {game}: {len(paths)} imported")

    log(f"  PHASE 3 DONE: {imported} imported to Eagle")

    # Mark all converted as imported
    for name, info in state.items():
        if info.get("status") == "converted":
            info["status"] = "imported"
    save_state(state)

    # Wait for Eagle to finish copying, then clean AVIF temp
    log("  Waiting 180s for Eagle async copy...")
    time.sleep(180)

    log("  Cleaning AVIF temp files...")
    for f in AVIF_DIR.iterdir():
        if f.is_file():
            f.unlink(missing_ok=True)
    log("  AVIF temp cleaned")

    # Final summary
    counts = Counter(v.get("status") for v in state.values())
    log(f"PIPELINE COMPLETE: {dict(counts)}")
    log("=" * 60)


if __name__ == "__main__":
    main()
