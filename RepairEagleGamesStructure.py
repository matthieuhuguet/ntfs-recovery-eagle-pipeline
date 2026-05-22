#!/usr/bin/env python3
"""
Repair Eagle References.library folder structure after recovered imports.

Goal:
- one top-level Games folder
- direct game folders only: Games/<GameName>
- no category folders such as RPG/Open World/Multiplayer under Games
- recovered R*.info items reclassified into direct game folders
- copied files remain user-readable and user-writable
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import stat
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

LIBRARY = Path("/Users/zenray/Create/Media/References/References.library")
IMAGES_DIR = LIBRARY / "images"
ROOT_METADATA = LIBRARY / "metadata.json"
MTIME_JSON = LIBRARY / "mtime.json"
LOG_DIR = Path("/Users/zenray/Create/Build/Memory/Tools/Recovery/logs")
IMPORT_SCRIPT = Path("/Users/zenray/Create/Build/Memory/Tools/Recovery/ImportRecoveredToEagleAvif.py")

CATEGORY_NAMES = {
    "action adventure",
    "action-adventure",
    "beat em up",
    "beat 'em up",
    "beat them up",
    "jrpg",
    "multiplayer",
    "open world",
    "rpg",
    "tactical rpg",
}


def now_ms() -> int:
    return int(time.time() * 1000)


def load_import_helpers():
    spec = importlib.util.spec_from_file_location("import_recovered_to_eagle_avif", IMPORT_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot import {IMPORT_SCRIPT}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def canon(s: str) -> str:
    s = s.casefold()
    s = s.replace("ö", "o").replace("é", "e").replace("è", "e").replace("à", "a")
    return re.sub(r"[^a-z0-9]+", "", s)


def category_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.casefold()).strip()


def read_json(path: Path, fallback: Any = None) -> Any:
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_atomic(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    ensure_user_rw(path)


def ensure_user_rw(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return
    wanted = stat.S_IRUSR | stat.S_IWUSR
    if path.is_dir():
        wanted |= stat.S_IXUSR
    os.chmod(path, mode | wanted)


def chmod_tree_user_rw(path: Path) -> int:
    touched = 0
    if not path.exists():
        return touched
    ensure_user_rw(path)
    touched += 1
    if path.is_dir():
        for child in path.rglob("*"):
            ensure_user_rw(child)
            touched += 1
    return touched


def backup_metadata(label: str) -> Path:
    dst = LIBRARY / "backup" / f"{label}_{time.strftime('%Y%m%d_%H%M%S')}"
    dst.mkdir(parents=True, exist_ok=True)
    for name in ("metadata.json", "mtime.json", "tags.json", "saved-filters.json"):
        src = LIBRARY / name
        if src.exists():
            shutil.copy2(src, dst / name)
    return dst


def merge_children_by_name(target: dict[str, Any], source: dict[str, Any], remap: dict[str, str]) -> None:
    existing = {canon(child.get("name", "")): child for child in target.get("children", []) or []}
    for child in source.get("children", []) or []:
        key = canon(child.get("name", ""))
        if key in existing and child.get("id") != existing[key].get("id"):
            old_id = child.get("id")
            new_id = existing[key].get("id")
            if old_id and new_id:
                remap[old_id] = new_id
            merge_children_by_name(existing[key], child, remap)
        else:
            target.setdefault("children", []).append(child)


def collect_direct_games(folder: dict[str, Any], out: list[dict[str, Any]], remap: dict[str, str]) -> None:
    for child in folder.get("children", []) or []:
        name = child.get("name", "")
        if category_key(name) in CATEGORY_NAMES:
            if child.get("id"):
                remap.setdefault(child["id"], "")
            collect_direct_games(child, out, remap)
        else:
            out.append(child)


def ensure_folder_template(folder_id: str, name: str) -> dict[str, Any]:
    return {
        "id": folder_id,
        "name": name,
        "description": "",
        "children": [],
        "modificationTime": now_ms(),
        "tags": [],
        "iconColor": "blue",
        "password": "",
        "passwordTips": "",
    }


def ensure_direct_game(
    game: str,
    games: dict[str, Any],
    folder_map: dict[str, str],
    id_pool: set[str],
    helpers,
    stats: Counter[str],
) -> str:
    display = helpers.normalize_game_label(game)
    key = helpers.canon(display)
    if key in folder_map:
        return folder_map[key]
    folder_id = helpers.make_id(id_pool)
    folder = ensure_folder_template(folder_id, display)
    games.setdefault("children", []).append(folder)
    games["children"].sort(key=lambda f: (f.get("name") or "").casefold())
    folder_map[key] = folder_id
    stats["created_game_folders"] += 1
    return folder_id


def rebuild_games(root: dict[str, Any], helpers) -> tuple[dict[str, str], dict[str, Any], dict[str, str], set[str], Counter[str]]:
    folders = root.get("folders", [])
    games_roots = [folder for folder in folders if folder.get("name") == "Games"]
    if not games_roots:
        raise RuntimeError("No top-level Games folder in Eagle metadata")

    canonical = games_roots[0]
    direct_candidates: list[dict[str, Any]] = []
    remap: dict[str, str] = {}
    stats: Counter[str] = Counter()

    for games in games_roots:
        collect_direct_games(games, direct_candidates, remap)
        if games is not canonical and games.get("id"):
            remap[games["id"]] = canonical["id"]
            stats["duplicate_games_roots"] += 1

    direct_by_key: dict[str, dict[str, Any]] = {}
    direct_children: list[dict[str, Any]] = []
    for folder in direct_candidates:
        key = canon(folder.get("name", ""))
        if not key:
            continue
        if key in direct_by_key:
            old_id = folder.get("id")
            new_id = direct_by_key[key].get("id")
            if old_id and new_id:
                remap[old_id] = new_id
            merge_children_by_name(direct_by_key[key], folder, remap)
            stats["duplicate_game_folders"] += 1
        else:
            direct_by_key[key] = folder
            direct_children.append(folder)

    canonical["children"] = sorted(direct_children, key=lambda f: (f.get("name") or "").casefold())
    canonical["modificationTime"] = now_ms()
    root["folders"] = [folder for folder in folders if folder.get("name") != "Games" or folder is canonical]

    id_pool = {folder.get("id") for folder in direct_children if folder.get("id")}
    id_pool.add(canonical.get("id"))
    folder_map = {canon(folder.get("name", "")): folder.get("id") for folder in direct_children if folder.get("id")}
    unknown_id = ensure_direct_game("_Unknown Recovered", canonical, folder_map, id_pool, helpers, stats)
    for old_id, new_id in list(remap.items()):
        if not new_id:
            remap[old_id] = unknown_id

    return remap, canonical, folder_map, id_pool, stats


def source_name_for_item(data: dict[str, Any], meta_path: Path) -> str:
    annotation = data.get("annotation") or ""
    m = re.search(r"Recovered from ([^;]+);", annotation)
    if m:
        return m.group(1)
    name = data.get("name") or meta_path.parent.name.removesuffix(".info")
    name = re.sub(r"_t\d{5}s$", "", name)
    ext = data.get("ext") or ""
    return f"{name}.{ext}" if ext else name


def repair_items(
    remap: dict[str, str],
    games: dict[str, Any],
    folder_map: dict[str, str],
    id_pool: set[str],
    helpers,
    dry_run: bool,
) -> Counter[str]:
    sort_classify = helpers.load_sort_classifier()
    stats: Counter[str] = Counter()
    mtime = read_json(MTIME_JSON, {})
    changed_dirs: set[Path] = set()

    for meta in IMAGES_DIR.glob("*.info/metadata.json"):
        try:
            data = read_json(meta, {})
        except Exception:
            stats["unreadable_metadata"] += 1
            continue

        changed = False
        folders = data.get("folders") or []
        new_folders = [remap.get(fid, fid) for fid in folders]
        if new_folders != folders:
            data["folders"] = new_folders
            changed = True
            stats["remapped_folder_refs"] += 1

        item_id = data.get("id") or meta.parent.name.removesuffix(".info")
        if meta.parent.name.startswith("R"):
            source_name = source_name_for_item(data, meta)
            game = helpers.generic_classify(source_name, sort_classify)
            folder_id = ensure_direct_game(game, games, folder_map, id_pool, helpers, stats)
            if data.get("folders") != [folder_id]:
                data["folders"] = [folder_id]
                changed = True
                stats["reclassified_recovered_items"] += 1
                stats[f"game::{game}"] += 1

        if changed:
            stamp = now_ms()
            data["modificationTime"] = stamp
            data["lastModified"] = stamp
            mtime[item_id] = stamp
            changed_dirs.add(meta.parent)
            if not dry_run:
                write_json_atomic(meta, data)

    if not dry_run:
        write_json_atomic(MTIME_JSON, mtime)
        for info_dir in changed_dirs:
            chmod_tree_user_rw(info_dir)
    stats["changed_item_dirs"] = len(changed_dirs)
    return stats


def verify_permissions() -> dict[str, int]:
    bad_owner_or_perms = 0
    checked = 0
    for path in IMAGES_DIR.glob("R*.info"):
        for item in [path, *path.iterdir()]:
            checked += 1
            st = item.stat()
            if not bool(st.st_mode & stat.S_IRUSR and st.st_mode & stat.S_IWUSR):
                bad_owner_or_perms += 1
    for path in (ROOT_METADATA, MTIME_JSON, LIBRARY, IMAGES_DIR):
        checked += 1
        st = path.stat()
        if not bool(st.st_mode & stat.S_IRUSR and st.st_mode & stat.S_IWUSR):
            bad_owner_or_perms += 1
    return {"checked": checked, "bad": bad_owner_or_perms}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    dry_run = not args.apply

    helpers = load_import_helpers()
    root = read_json(ROOT_METADATA, {})
    backup = None
    if not dry_run:
        backup = backup_metadata("repair_games_direct")

    remap, games, folder_map, id_pool, folder_stats = rebuild_games(root, helpers)
    item_stats = repair_items(remap, games, folder_map, id_pool, helpers, dry_run)
    if not dry_run:
        write_json_atomic(ROOT_METADATA, root)
        chmod_tree_user_rw(ROOT_METADATA)
        chmod_tree_user_rw(MTIME_JSON)
        chmod_tree_user_rw(IMAGES_DIR)

    top_games = [folder for folder in root.get("folders", []) if folder.get("name") == "Games"]
    category_children = [
        child.get("name")
        for child in (games.get("children") or [])
        if category_key(child.get("name", "")) in CATEGORY_NAMES
    ]
    permissions = verify_permissions()

    report = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dry_run": dry_run,
        "library": str(LIBRARY),
        "backup": str(backup) if backup else None,
        "games_root_id": games.get("id"),
        "top_level_games_count": len(top_games),
        "direct_game_folder_count": len(games.get("children") or []),
        "remaining_category_children": category_children,
        "folder_remap_count": len(remap),
        "folder_stats": dict(folder_stats),
        "item_stats": dict(item_stats),
        "permissions": permissions,
    }
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOG_DIR / f"repair_eagle_games_structure_{time.strftime('%Y%m%d_%H%M%S')}.json"
    if not dry_run:
        write_json_atomic(report_path, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if permissions["bad"] == 0 and not category_children and len(top_games) == 1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
