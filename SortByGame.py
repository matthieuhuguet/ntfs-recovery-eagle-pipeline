#!/usr/bin/env python3
"""
SortByGame.py — Trie les fichiers récupérés par ntfsundelete vers des sous-dossiers par jeu.

Source  : /Users/zenray/NTFS_Recovery_ntfsundelete/run_20260522_131534/recovered/
Dest    : /Users/zenray/NTFS_Recovery_ntfsundelete/sorted_by_game/<NomDuJeu>/

Mode haute confiance : zéro faux positif. Patterns ancrés strictement
(préfixe + séparateur ou suffixe "Screenshot"/timestamp) pour éviter
de classifier du code ("gta_vehicle.sps", "Doom.h", "Halo.cpp"…).

Parallélisme : ProcessPoolExecutor(max_workers=16) sur Mac M5 Max 18 cores.
Move = os.rename (O(1) sur même FS).
"""

from __future__ import annotations

import os
import re
import sys
import time
import unicodedata
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

SRC = Path("/Users/zenray/NTFS_Recovery_ntfsundelete/run_20260522_131534/recovered")
DEST = Path("/Users/zenray/NTFS_Recovery_ntfsundelete/sorted_by_game")
REPORT = DEST / "_report.txt"
DRY_RUN_N = 100  # nombre de mappings à montrer en preview

# (regex compilable, nom de jeu Title Case)
# Conventions :
#  - On ancre par ^.
#  - Séparateur post-titre = `_Screenshot`, `_20YY`, `_<hash>_W64_Shipping`, ` <date>`, etc.
#  - On préfère un faux négatif à un faux positif.
RAW_PATTERNS: list[tuple[str, str]] = [
    # Final Fantasy — pattern timestamp YYYYMMDDHHMMSS post titre
    (r"^Final_Fantasy_Vii_Rebirth_\d{14}\.", "Final Fantasy VII Rebirth"),
    (r"^Ff7rebirth__[A-Za-z0-9]+\.", "Final Fantasy VII Rebirth"),
    (r"^Final_Fantasy_Vii_Remake_\d{14}\.", "Final Fantasy VII Remake"),
    (r"^Ccff7r_Win64_Shipping_[A-Za-z0-9]+\.", "Crisis Core Final Fantasy VII Reunion"),
    (r"^Final_Fantasy_Xv_Windows_Edition_\d{14}\.", "Final Fantasy XV"),
    (r"^Final_Fantasy_Xvi_\d{14}\.", "Final Fantasy XVI"),
    (r"^Final_Fantasy_Xii_The_Zodiac_Age_\d{14}\.", "Final Fantasy XII The Zodiac Age"),
    (r"^Stranger_Of_Paradise_Final_Fantasy_Origin_\d{14}\.", "Stranger of Paradise Final Fantasy Origin"),
    # Black Myth Wukong (exécutable B1_Win64_Shipping)
    (r"^B1_Win64_Shipping_[A-Za-z0-9]+\.", "Black Myth Wukong"),
    # Clair Obscur Expedition 33
    (r"^Sandfall_[A-Za-z0-9]+\.", "Clair Obscur Expedition 33"),
    (r"^Farlonesails_\d", "Sea of Stars"),
    # Alan Wake / Stray / Cyberpunk / Plague Tale
    (r"^Alanwake2_[A-Za-z0-9]+\.", "Alan Wake 2"),
    (r"^Alan_Wake_2_Screenshot_", "Alan Wake 2"),
    (r"^Stray_Screenshot_", "Stray"),
    (r"^Cyberpunk_2077_Screenshot_", "Cyberpunk 2077"),
    (r"^A_Plague_Tale__Requiem_Screenshot_", "A Plague Tale Requiem"),
    (r"(?i)^a_plague_tale__requiem_screenshot_", "A Plague Tale Requiem"),
    (r"(?i)^ence_screenshot_", "A Plague Tale Innocence"),
    # Souls / FromSoftware
    (r"^Bloodborne", "Bloodborne"),
    (r"^Elden_Ring_", "Elden Ring"),
    # God of War / Spider-Man
    (r"^God_Of_War_Ragnar(ö|o)k_", "God of War Ragnarök"),
    (r"^Marvel's_Spider_Man__Miles_Morales_Screenshot_", "Spider-Man Miles Morales"),
    (r"^Marvel's_Spider_Man_Screenshot_", "Spider-Man"),
    # Metaphor / Yakuza / Like a Dragon
    (r"^Metaphor_", "Metaphor ReFantazio"),
    (r"^Like_A_Dragon__Ishin!_", "Like a Dragon Ishin"),
    (r"^Yakuza__Like_A_Dragon_", "Yakuza Like A Dragon"),
    (r"^Judgment_\d{14}\.", "Judgment"),
    # Centennial / Nier / Labyrinth
    (r"^The_Centennial_Case__A_Shijima_Story_", "The Centennial Case A Shijima Story"),
    (r"^Nier_Automata_", "Nier Automata"),
    (r"^Labyrinth_Of_Yomi_", "Labyrinth of Yomi"),
    # Hogwarts Legacy (avec accents et NFC/NFD)
    (r"^Hogwarts_Legacy___L'h(é|e)ritage_De_Poudlard_", "Hogwarts Legacy"),
    (r"^Hogwarts_Legacy_", "Hogwarts Legacy"),
    # AC Odyssey tronqué + AC Valhalla
    (r"^yssey_screenshot_", "Assassin's Creed Odyssey"),
    (r"^yssey screenshot ", "Assassin's Creed Odyssey"),
    (r"(?i)^assassin's creed valhalla ", "Assassin's Creed Valhalla"),
    # Baldur / Octopath / Nioh / Starfield / Steelrising
    (r"^Baldur's_Gate_3_Screenshot_", "Baldur's Gate 3"),
    (r"^Octopath_Traveler2_Screenshot_", "Octopath Traveler II"),
    (r"^Octopath_Traveler2_\d{14}\.", "Octopath Traveler II"),
    (r"^Nioh_2_The_Complete_Edition_", "Nioh 2"),
    (r"^Starfield_Screenshot_", "Starfield"),
    (r"^Steelrising_Screenshot_", "Steelrising"),
    # Lords of the Fallen 2023
    (r"^Lords_Of_The_Fallen_\(2023\)_", "Lords of the Fallen 2023"),
    # Witcher (préfixe spécifique, jamais "witcher.h")
    (r"^The_Witcher_3_Screenshot_", "The Witcher 3"),
    (r"^The_Witcher_3_\d{14}\.", "The Witcher 3"),
    # Hollow Knight (avec espace)
    (r"(?i)^hollow knight \d", "Hollow Knight"),
    # Hitman / Hearthstone / Starcraft (jeux à demander capitalisation, screenshot suffix)
    (r"^Hitman_Screenshot_", "Hitman"),
    (r"(?i)^hearthstone_screenshot_", "Hearthstone"),
    (r"^Hearthstone__Heroes_Of_Warcraft_Screenshot_", "Hearthstone"),
    (r"^Starcraft_Ii_Screenshot_", "Starcraft II"),
    # Frostpunk / Forspoken / Forza / Inscryption / Engarde / Remnant / Layersoffear
    (r"^Frostpunk_Screenshot_", "Frostpunk"),
    (r"^Forspoken_Screenshot_", "Forspoken"),
    (r"^Forza_Motorsport_Screenshot_", "Forza Motorsport"),
    (r"^Inscryption_Screenshot_", "Inscryption"),
    (r"^Engarde_Screenshot_", "En Garde"),
    (r"^Remnant2_Screenshot_", "Remnant 2"),
    (r"^Layersoffear_Win64_Shipping_[A-Za-z0-9]+\.", "Layers of Fear"),
    # Blasphemous / Armored Core / Middle-earth / Madden / Star Ocean
    (r"^Blasphemous_2_Screenshot_", "Blasphemous 2"),
    (r"^Armored_Core_Vi_Fires_Of_Rubicon_Screenshot_", "Armored Core VI"),
    (r"^Middle_Earth__Shadow_Of_War_Screenshot_", "Middle Earth Shadow of War"),
    (r"^Madden_Nfl_22_Screenshot_", "Madden NFL 22"),
    (r"^Star_Ocean_The_Divine_Force_D(é|e)mo_Screenshot_", "Star Ocean The Divine Force Demo"),
    (r"^Star_Ocean_The_Second_Story_R_Demo_Screenshot_", "Star Ocean Second Story R Demo"),
    # Titanfall / Setsuna / Tomb Raider / Chants / Ghost Trick
    (r"^Titanfall_2_Screenshot_", "Titanfall 2"),
    (r"^I_Am_Setsuna_Screenshot_", "I Am Setsuna"),
    (r"^Tomb_Raider_\(2013\)_Screenshot_", "Tomb Raider 2013"),
    (r"^Chants_Of_Sennaar_Screenshot_", "Chants of Sennaar"),
    (r"^Ghost_Trick_Demo_Screenshot_", "Ghost Trick Demo"),
    # GTA — pattern strict (jamais gta_vehicle.sps)
    (r"^Grand_Theft_Auto_4_Screenshot_", "Grand Theft Auto IV"),
    (r"^Grand_Theft_Auto_V_Screenshot_", "Grand Theft Auto V"),
    # Dishonored
    (r"(?i)^dishonored \d", "Dishonored"),
    # Generic FF tronqué
    (r"(?i)^dows_edition_screenshot_", "Final Fantasy XV"),
    (r"(?i)^nant_kingdom_screenshot_", "Ni No Kuni II"),
    (r"(?i)^mplete_edition_screenshot_", "Nioh 2"),
    # ANIME (à isoler)
    (r"(?i)^demon_slayer", "_Anime"),
    (r"(?i)^kimetsu", "_Anime"),
]

# Compile une seule fois
PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(p), name) for p, name in RAW_PATTERNS
]


def classify(name: str) -> str | None:
    """Retourne le nom du jeu ou None si pas de match haute-confiance."""
    # Normalise NFC pour gérer Ragnarök / Démo / héritage en NFD macOS
    n = unicodedata.normalize("NFC", name)
    for rx, game in PATTERNS:
        if rx.match(n):
            return game
    return None


def move_one(args: tuple[str, str]) -> tuple[str, str] | None:
    """Worker: déplace un fichier. Retourne (game, name) si bougé, sinon None."""
    name, game = args
    src = SRC / name
    dst_dir = DEST / game
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / name
        if dst.exists():
            # collision : ajoute un suffixe numérique
            stem = dst.stem
            suf = dst.suffix
            i = 1
            while True:
                cand = dst_dir / f"{stem}__dup{i}{suf}"
                if not cand.exists():
                    dst = cand
                    break
                i += 1
        os.rename(src, dst)
        return (game, name)
    except FileNotFoundError:
        return None
    except OSError as e:
        sys.stderr.write(f"ERR {name}: {e}\n")
        return None


def main() -> int:
    if not SRC.is_dir():
        print(f"Source absente: {SRC}", file=sys.stderr)
        return 1
    DEST.mkdir(parents=True, exist_ok=True)

    dry_run = "--apply" not in sys.argv

    # Scan source (rapide, single thread, os.scandir)
    print("Scan source…")
    t0 = time.time()
    names: list[str] = []
    with os.scandir(SRC) as it:
        for e in it:
            if e.is_file(follow_symlinks=False):
                names.append(e.name)
    print(f"{len(names)} fichiers en {time.time()-t0:.1f}s")

    # Classify (single thread, regex pures, suffisamment rapide)
    print("Classification…")
    t0 = time.time()
    matched: list[tuple[str, str]] = []  # (name, game)
    for n in names:
        g = classify(n)
        if g:
            matched.append((n, g))
    print(f"{len(matched)} matchs / {len(names)} fichiers en {time.time()-t0:.1f}s")

    counts = Counter(g for _, g in matched)
    print("\nRépartition par jeu (top 30):")
    for g, c in counts.most_common(30):
        print(f"  {c:6d}  {g}")

    if dry_run:
        print("\n=== DRY RUN ===")
        print(f"Aperçu des {DRY_RUN_N} premiers mappings :")
        for nm, gm in matched[:DRY_RUN_N]:
            print(f"  {gm:40s} ← {nm}")
        print("\nPour APPLIQUER les moves : relancer avec --apply")
        return 0

    # Apply : pool 16
    print(f"\nMoves en parallèle (Pool 16) sur {len(matched)} fichiers…")
    t0 = time.time()
    moved = Counter()
    with ProcessPoolExecutor(max_workers=16) as ex:
        for res in ex.map(move_one, matched, chunksize=200):
            if res is not None:
                moved[res[0]] += 1
    dt = time.time() - t0
    print(f"Moves terminés en {dt:.1f}s ({sum(moved.values())} fichiers)")

    # Rapport
    lines = [
        f"SortByGame report — {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Source       : {SRC}",
        f"Destination  : {DEST}",
        f"Total source : {len(names)}",
        f"Matchs       : {len(matched)}",
        f"Déplacés     : {sum(moved.values())}",
        f"Non triés    : {len(names) - sum(moved.values())}",
        f"Durée moves  : {dt:.1f}s",
        "",
        "Par jeu :",
    ]
    for g, c in moved.most_common():
        lines.append(f"  {c:6d}  {g}")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nRapport écrit : {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
