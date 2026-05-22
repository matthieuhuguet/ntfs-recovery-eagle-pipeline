#!/usr/bin/env python3
"""
Import recovered screenshots/videos into the Eagle References library.

Fixed scope:
- read only the two requested recovered folders
- never scan sorted_by_game or run_20260522_171904_filtered
- images become AVIF CRF/QP 25
- .webm/.mkv become visible AVIF frames, one frame every 10 seconds
- delete a source file only after its Eagle item metadata exists
- quarantine non-convertible files instead of silently dropping them
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LIBRARY = Path("/Users/zenray/Create/Media/References/References.library")
IMAGES_DIR = LIBRARY / "images"
ROOT_METADATA = LIBRARY / "metadata.json"
MTIME_JSON = LIBRARY / "mtime.json"

SOURCES = [
    Path("/Users/zenray/NTFS_Recovery_ntfsundelete/run_20260522_131534/recovered"),
    Path("/Users/zenray/NTFS_Recovery_ntfsundelete/run_20260522_145030_filtered/recovered"),
]

EXCLUDED = [
    Path("/Users/zenray/NTFS_Recovery_ntfsundelete/sorted_by_game"),
    Path("/Users/zenray/NTFS_Recovery_ntfsundelete/run_20260522_171904_filtered"),
]

QUARANTINE = Path("/Users/zenray/NTFS_Recovery_ntfsundelete/nonconvertible_for_eagle_20260522")
LOG_DIR = Path("/Users/zenray/Create/Build/Memory/Tools/Recovery/logs")
SORT_BY_GAME = Path("/Users/zenray/Create/Build/Memory/Tools/Recovery/SortByGame.py")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".jfif", ".webp"}
VIDEO_EXTS = {".webm", ".mkv"}
GENERIC_FOLDER_NAMES = {
    "games",
    "videos",
    "best of",
    "rpg",
    "jrpg",
    "tactical rpg",
    "open world",
    "action-adventure",
    "beat 'em up",
    "multiplayer",
}

EXTRA_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^1145350_\d{14}_\d+", re.I), "Hades II"),
    (re.compile(r"^2679460_\d{14}_\d+", re.I), "Metaphor ReFantazio"),
    (re.compile(r"^3014330_\d{14}_\d+", re.I), "Octopath Traveler 0"),
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
    (re.compile(r"^Dragon's_Dogma__Dark_Arisen_Screenshot_", re.I), "Dragon's Dogma Dark Arisen"),
    (re.compile(r"^Monster_Hunter_Rise_Screenshot_", re.I), "Monster Hunter Rise"),
    (re.compile(r"^Valkyria4_X64_Screenshot_", re.I), "Valkyria Chronicles 4"),
    (re.compile(r"^Village_\d{2}_\d{2}_\d{4}_", re.I), "Resident Evil Village"),
    (re.compile(r"^Days_Gone_Screenshot_", re.I), "Days Gone"),
    (re.compile(r"^Divinity_Original_Sin_2__Definitive_Edition_Screenshot_", re.I), "Divinity Original Sin 2"),
    (re.compile(r"^Arise_Screenshot_", re.I), "Tales Of Arise"),
    (re.compile(r"^Sifu_Win64_Shipping_Screenshot_", re.I), "Sifu"),
    (re.compile(r"^Retroarch_Screenshot_", re.I), "Retroarch"),
    (re.compile(r"^Narakabladepoint_Screenshot_", re.I), "Naraka Bladepoint"),
    (re.compile(r"^Dangan3win_Screenshot_", re.I), "Danganronpa V3"),
    (re.compile(r"^Final_Fantasy_Xv_Windows_Edition_\d{2}_\d{2}_\d{4}_", re.I), "Final Fantasy XV"),
    (re.compile(r"^De.mo_Final_Fantasy_Xvi_", re.I), "Final Fantasy XVI"),
    (re.compile(r"^Valkyrie_Elysium_Demo_Version_\d{14}", re.I), "Valkyrie Elysium"),
    (re.compile(r"^VALKYRIE ELYSIUM DEMO VERSION_\d{14}", re.I), "Valkyrie Elysium"),
    (re.compile(r"^dragon quest xi s .* \d{2}_\d{2}_\d{4} ", re.I), "Dragon Quest XI S"),
    (re.compile(r"^Demon's_Souls_\d{14}", re.I), "Demon's Souls"),
    (re.compile(r"^dark souls iii \d{2}_\d{2}_\d{4} ", re.I), "Dark Souls III"),
    (re.compile(r"^disco elysium \d{2}_\d{2}_\d{4} ", re.I), "Disco Elysium"),
    (re.compile(r"^it takes two\s+\d{2}_\d{2}_\d{4} ", re.I), "It Takes Two"),
    (re.compile(r"^a plague tale  innocence screenshot ", re.I), "A Plague Tale Innocence"),
]

FOLDER_ALIASES = {
    "aplaguetalerequiem": "A Plague Tale Requiem",
    "aplaguetaleinnocence": "A Plague Tale Innocence",
    "plagueproject": "A Plague Tale Requiem",
    "thelastofuspartii": "The Last Of Us II",
    "finalfantasyxvidemo": "Final Fantasy XVI",
    "demofinalfantasyxvi": "Final Fantasy XVI",
    "finalfantasyxvwindowsedition": "Final Fantasy XV",
    "finalfantasyxiii": "Final Fantasy XIII",
    "ffivremastered": "Final Fantasy IV",
    "godofwarragnarok": "God of War Ragnarök",
    "godofwarragnaroek": "God of War Ragnarök",
    "clairobscurexpedition33": "Expedition 33",
    "finjitunictys0ffscxatjj": "Tunic",
    "nmh3win64shipping": "No More Heroes 3",
    "lis": "Life Is Strange",
    "rim": "Rime",
    "demonsouls": "Demon's Souls",
    "demonssouls": "Demon's Souls",
    "likeadragonishin": "Like a Dragon Ishin",
    "likeadragonishin!": "Like a Dragon Ishin",
    "hogwartslegacylheritagedepoudlard": "Hogwarts Legacy",
    "residentevil4biohazard4": "Resident Evil 4",
    "residentevilvillagebiohazardvillage": "Resident Evil Village",
    "deadspace": "Dead Space",
    "darksoulsremastered": "Dark Souls Remastered",
    "saltandsacrificep2p": "Salt and Sacrifice",
    "losteidolons": "Lost Eidolons",
    "haveanicedeath": "Have a Nice Death",
    "monsterhunterrisedemo": "Monster Hunter Rise",
    "layersoffearwin64shipping": "Layers of Fear",
    "seahorizonwin64shipping": "Sea Horizon",
    "flateyewin64": "Flat Eye",
    "endlingwin64shipping": "Endling",
    "potionomicswin64shipping": "Potionomics",
    "systemreshockwin64shipping": "System Shock",
    "dw9emp": "Dynasty Warriors 9 Empires",
    "dishonored2": "Dishonored 2",
    "marvelsspiderman2": "Marvel's Spider-Man 2",
    "wildheartstrialexe": "Wild Hearts",
    "wingdk": "_Unknown Recovered",
    "baseprofile": "_Unknown Recovered",
    "desktop": "_Unknown Recovered",
    "make": "_Unknown Recovered",
    "st": "_Unknown Recovered",
    "ii": "_Unknown Recovered",
    "vi": "_Unknown Recovered",
    "sunflower": "_Unknown Recovered",
    "deadcellsgoogledocsgooglechrome": "Dead Cells",
    "deadcellslogofontsforumdafontcomgooglechrome": "Dead Cells",
    "sthemesongsostdlcyoutubegooglechrome": "_Unknown Recovered",
    "sofrubicon": "Armored Core VI",
}


@dataclass(frozen=True)
class Job:
    src: str
    kind: str
    game: str
    folder_id: str
    item_name: str


def now_ms() -> int:
    return int(time.time() * 1000)


def load_sort_classifier():
    spec = importlib.util.spec_from_file_location("sort_by_game", SORT_BY_GAME)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {SORT_BY_GAME}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.classify


def normalize_text(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def canon(s: str) -> str:
    s = normalize_text(s).casefold()
    s = s.replace("ö", "o").replace("é", "e").replace("è", "e").replace("à", "a")
    return re.sub(r"[^a-z0-9]+", "", s)


def strip_numeric_suffix(name: str) -> str:
    return re.sub(r"(\.\d+)+$", "", name)


def effective_ext(path: Path) -> str:
    name = path.name.removesuffix("@")
    return Path(strip_numeric_suffix(name)).suffix.lower()


def clean_game_name(raw: str) -> str:
    s = normalize_text(raw)
    s = re.sub(r"_+", " ", s)
    s = re.sub(r"\s+", " ", s).strip(" ._-")
    titled = s.title()
    replacements = {
        "Vii": "VII",
        "Xvi": "XVI",
        "Xv": "XV",
        "Xii": "XII",
        "Ii": "II",
        "Iii": "III",
        "Iv": "IV",
        "Vi": "VI",
        "Nfl": "NFL",
        "Rpg": "RPG",
    }
    for old, new in replacements.items():
        titled = re.sub(rf"\b{old}\b", new, titled)
    return titled


def normalize_game_label(game: str) -> str:
    return FOLDER_ALIASES.get(canon(game), game)


def generic_classify(name: str, sort_classify) -> str:
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


def safe_item_name(path: Path) -> str:
    stripped = strip_numeric_suffix(path.name.removesuffix("@"))
    stem = Path(stripped).stem
    cleaned = normalize_text(stem).replace("/", "_").replace(":", "_")
    return cleaned[:180] or "Recovered"


def make_id(existing: set[str] | None = None) -> str:
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    existing = existing if existing is not None else set()
    while True:
        rid = "R" + "".join(random.choice(alphabet) for _ in range(12))
        if rid not in existing:
            existing.add(rid)
            return rid


def find_games_folder(root: dict[str, Any]) -> dict[str, Any]:
    for folder in root.get("folders", []):
        if folder.get("name") == "Games":
            return folder
    raise RuntimeError("Games folder not found in Eagle metadata")


def walk_folders(folder: dict[str, Any]):
    yield folder
    for child in folder.get("children", []) or []:
        yield from walk_folders(child)


def load_folder_map(root: dict[str, Any], id_pool: set[str]) -> tuple[dict[str, str], dict[str, Any]]:
    games = find_games_folder(root)
    folder_map: dict[str, str] = {}
    for folder in walk_folders(games):
        fid = folder.get("id")
        name = folder.get("name") or ""
        if fid:
            id_pool.add(fid)
        if name.casefold() not in GENERIC_FOLDER_NAMES:
            folder_map[canon(name)] = fid
    return folder_map, games


def ensure_game_folder(game: str, folder_map: dict[str, str], games_folder: dict[str, Any], id_pool: set[str], dry_run: bool) -> str:
    alias = normalize_game_label(game)
    key = canon(alias)
    if key in folder_map:
        return folder_map[key]
    fid = make_id(id_pool)
    folder_map[key] = fid
    if not dry_run:
        games_folder.setdefault("children", []).append(
            {
                "id": fid,
                "name": alias,
                "description": "",
                "children": [],
                "modificationTime": now_ms(),
                "tags": [],
                "iconColor": "blue",
                "password": "",
                "passwordTips": "",
            }
        )
        games_folder["modificationTime"] = now_ms()
    return fid


def existing_eagle_index() -> tuple[set[str], set[str], set[str]]:
    item_names: set[str] = set()
    file_names: set[str] = set()
    ids: set[str] = set()
    for meta in IMAGES_DIR.glob("*.info/metadata.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        item_id = data.get("id") or meta.parent.name.removesuffix(".info")
        ids.add(item_id)
        name = data.get("name") or ""
        ext = data.get("ext") or ""
        if name:
            item_names.add(name)
            if ext:
                item_names.add(f"{name}.{ext}")
        for f in meta.parent.iterdir():
            if f.is_file() and f.name != "metadata.json":
                file_names.add(f.name)
                file_names.add(f.stem)
    return item_names, file_names, ids


def already_in_eagle(path: Path, item_names: set[str], file_names: set[str]) -> bool:
    b = path.name
    stripped = strip_numeric_suffix(b)
    stem = path.stem
    stripped_stem = Path(stripped).stem
    return b in file_names or stem in file_names or stem in item_names or stripped in file_names or stripped_stem in item_names


def backup_metadata() -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dst = LIBRARY / "backup" / f"import_recovered_{stamp}"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("metadata.json", "mtime.json", "tags.json", "saved-filters.json"):
        src = LIBRARY / name
        if src.exists():
            shutil.copy2(src, dst / name)
    return dst


def write_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def probe_image_size(path: Path) -> tuple[int, int]:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(path),
        ],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        return 0, 0
    match = re.search(r"(\d+)x(\d+)", proc.stdout)
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def make_thumbnail(src_avif: Path, thumb: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-nostdin", "-i", str(src_avif), "-vf", "scale='min(512,iw)':-2", str(thumb)],
        check=False,
    )


def write_item_metadata(info_dir: Path, item_id: str, name: str, folder_id: str, avif: Path, annotation: str = "") -> None:
    width, height = probe_image_size(avif)
    data = {
        "id": item_id,
        "name": name,
        "size": avif.stat().st_size,
        "btime": now_ms(),
        "mtime": now_ms(),
        "ext": "avif",
        "tags": [],
        "folders": [folder_id],
        "isDeleted": False,
        "url": "",
        "annotation": annotation,
        "modificationTime": now_ms(),
        "height": height,
        "width": width,
        "lastModified": now_ms(),
        "palettes": [],
    }
    write_json_atomic(info_dir / "metadata.json", data)


def quarantine_failed_source(src: Path) -> None:
    try:
        QUARANTINE.mkdir(parents=True, exist_ok=True)
        dst = QUARANTINE / src.name
        if dst.exists():
            dst = QUARANTINE / f"{src.stem}__failed{time.time_ns()}{src.suffix}"
        os.rename(src, dst)
    except Exception:
        pass


def convert_image(job: Job) -> dict[str, Any]:
    src = Path(job.src)
    item_id = make_id()
    info_dir = IMAGES_DIR / f"{item_id}.info"
    info_dir.mkdir(parents=True, exist_ok=False)
    out = info_dir / f"{job.item_name}.avif"
    thumb = info_dir / f"{job.item_name}_thumbnail.png"
    ext = effective_ext(src)
    input_format = "png" if ext == ".png" else "jpeg" if ext in {".jpg", ".jpeg", ".jfif"} else "auto"
    cmd = [
        "avifenc",
        "--stdin",
        "--input-format",
        input_format,
        "--min",
        "25",
        "--max",
        "25",
        "--speed",
        "10",
        "--jobs",
        "1",
        str(out),
    ]
    with src.open("rb") as fh:
        proc = subprocess.run(cmd, stdin=fh, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        shutil.rmtree(info_dir, ignore_errors=True)
        quarantine_failed_source(src)
        return {"status": "failed", "src": str(src), "reason": proc.stderr.decode("utf-8", "ignore")[-400:]}
    make_thumbnail(out, thumb)
    write_item_metadata(info_dir, item_id, job.item_name, job.folder_id, out)
    src.unlink()
    return {"status": "ok", "kind": "image", "src": str(src), "game": job.game, "items": 1, "bytes": out.stat().st_size, "id": item_id}


def ffprobe_duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nokey=1:noprint_wrappers=1", str(path)],
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ffprobe failed")
    return float(proc.stdout.strip())


def convert_video(job: Job) -> dict[str, Any]:
    src = Path(job.src)
    try:
        duration = ffprobe_duration(src)
    except Exception as exc:
        return {"status": "failed", "src": str(src), "reason": str(exc)}
    stamps = list(range(0, max(1, math.ceil(duration)), 10)) or [0]
    created: list[str] = []
    total = 0
    for ts in stamps:
        item_id = make_id()
        frame_name = f"{job.item_name}_t{ts:05d}s"
        info_dir = IMAGES_DIR / f"{item_id}.info"
        info_dir.mkdir(parents=True, exist_ok=False)
        out = info_dir / f"{frame_name}.avif"
        thumb = info_dir / f"{frame_name}_thumbnail.png"
        ff = subprocess.Popen(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-nostdin",
                "-ss",
                str(ts),
                "-i",
                str(src),
                "-frames:v",
                "1",
                "-pix_fmt",
                "yuv420p",
                "-f",
                "yuv4mpegpipe",
                "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        av = subprocess.run(
            [
                "avifenc",
                "--stdin",
                "--input-format",
                "y4m",
                "--min",
                "25",
                "--max",
                "25",
                "--speed",
                "10",
                "--jobs",
                "1",
                str(out),
            ],
            stdin=ff.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        if ff.stdout:
            ff.stdout.close()
        _, fferr = ff.communicate()
        if ff.returncode != 0 or av.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            shutil.rmtree(info_dir, ignore_errors=True)
            for cid in created:
                shutil.rmtree(IMAGES_DIR / f"{cid}.info", ignore_errors=True)
            err = (fferr or b"").decode("utf-8", "ignore") + (av.stderr or b"").decode("utf-8", "ignore")
            quarantine_failed_source(src)
            return {"status": "failed", "src": str(src), "reason": err[-500:]}
        make_thumbnail(out, thumb)
        write_item_metadata(
            info_dir,
            item_id,
            frame_name,
            job.folder_id,
            out,
            annotation=f"Recovered from {src.name}; source duration {duration:.2f}s; frame at {ts}s.",
        )
        created.append(item_id)
        total += out.stat().st_size
    src.unlink()
    return {"status": "ok", "kind": "video", "src": str(src), "game": job.game, "items": len(created), "bytes": total, "duration": duration}


def quarantine_file(path: Path, reason: str, dry_run: bool) -> tuple[str, int]:
    size = path.stat().st_size if path.exists() else 0
    if not dry_run:
        QUARANTINE.mkdir(parents=True, exist_ok=True)
        dst = QUARANTINE / path.name
        if dst.exists():
            dst = QUARANTINE / f"{path.stem}__dup{time.time_ns()}{path.suffix}"
        os.rename(path, dst)
    return reason, size


def scan_sources() -> list[Path]:
    files: list[Path] = []
    excluded_resolved = [p.resolve(strict=False) for p in EXCLUDED]
    for source in SOURCES:
        if not source.exists():
            continue
        for p in source.rglob("*"):
            if not p.is_file():
                continue
            rp = p.resolve(strict=False)
            if any(rp == ex or ex in rp.parents for ex in excluded_resolved):
                raise RuntimeError(f"Excluded path reached unexpectedly: {p}")
            files.append(p)
    return files


def process_jobs(jobs: list[Job], workers: int) -> tuple[Counter[str], list[dict[str, Any]]]:
    counts: Counter[str] = Counter()
    results: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(convert_video if job.kind == "video" else convert_image, job) for job in jobs]
        for fut in as_completed(futs):
            try:
                res = fut.result()
            except Exception as exc:
                res = {"status": "failed", "src": "worker_exception", "reason": repr(exc)}
            results.append(res)
            counts[res.get("status", "unknown")] += 1
            if res.get("status") == "ok":
                counts[f"ok_{res.get('kind')}"] += 1
                counts[f"items_{res.get('kind')}"] += int(res.get("items", 0))
    return counts, results


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workers", type=int, default=16)
    args = parser.parse_args()
    dry_run = not args.apply

    sort_classify = load_sort_classifier()
    files = scan_sources()
    root = json.loads(ROOT_METADATA.read_text(encoding="utf-8"))
    mtime = json.loads(MTIME_JSON.read_text(encoding="utf-8")) if MTIME_JSON.exists() else {}
    item_names, file_names, id_pool = existing_eagle_index()
    folder_map, games_folder = load_folder_map(root, id_pool)

    duplicates: list[Path] = []
    media: list[tuple[Path, str]] = []
    quarantine: list[Path] = []
    for p in files:
        if already_in_eagle(p, item_names, file_names):
            duplicates.append(p)
            continue
        ext = effective_ext(p)
        if ext in IMAGE_EXTS:
            media.append((p, "image"))
        elif ext in VIDEO_EXTS:
            media.append((p, "video"))
        else:
            quarantine.append(p)

    jobs: list[Job] = []
    game_counts: Counter[str] = Counter()
    for p, kind in media:
        game = generic_classify(p.name, sort_classify)
        folder_id = ensure_game_folder(game, folder_map, games_folder, id_pool, dry_run)
        jobs.append(Job(str(p), kind, game, folder_id, safe_item_name(p)))
        game_counts[game] += 1

    print(f"Source files: {len(files)}")
    print(f"Already in Eagle: {len(duplicates)}")
    print(f"Convertible jobs: {len(jobs)}")
    print(f"Quarantine non-media: {len(quarantine)}")
    print("Top games:")
    for game, count in game_counts.most_common(30):
        print(f"  {count:6d}  {game}")
    if dry_run:
        print("Dry run only. Re-run with --apply.")
        return 0

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    backup = backup_metadata()
    print(f"Metadata backup: {backup}")

    duplicate_bytes = 0
    for p in duplicates:
        try:
            duplicate_bytes += p.stat().st_size
            p.unlink()
        except FileNotFoundError:
            pass

    quarantine_bytes = 0
    quarantine_counts: Counter[str] = Counter()
    for p in quarantine:
        reason, size = quarantine_file(p, "non_media_extension", dry_run=False)
        quarantine_counts[reason] += 1
        quarantine_bytes += size

    write_json_atomic(ROOT_METADATA, root)

    t0 = time.time()
    counts, results = process_jobs(jobs, args.workers)
    elapsed = time.time() - t0

    for meta in IMAGES_DIR.glob("R*.info/metadata.json"):
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
            mtime[data["id"]] = data.get("modificationTime", now_ms())
        except Exception:
            continue
    write_json_atomic(MTIME_JSON, mtime)

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sources": [str(p) for p in SOURCES],
        "library": str(LIBRARY),
        "metadata_backup": str(backup),
        "source_files_start": len(files),
        "duplicates_removed": len(duplicates),
        "duplicates_removed_bytes": duplicate_bytes,
        "quarantine_count": len(quarantine),
        "quarantine_bytes": quarantine_bytes,
        "quarantine_path": str(QUARANTINE),
        "jobs": len(jobs),
        "workers": args.workers,
        "elapsed_seconds": elapsed,
        "counts": dict(counts),
        "top_games": game_counts.most_common(),
        "failed": [r for r in results if r.get("status") != "ok"],
    }
    report_path = LOG_DIR / f"import_recovered_to_eagle_{time.strftime('%Y%m%d_%H%M%S')}.json"
    write_json_atomic(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2)[:8000])
    print(f"Report: {report_path}")
    return 0 if counts.get("failed", 0) == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
