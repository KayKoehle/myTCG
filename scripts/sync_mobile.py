"""Sync shared sources into the Android app tree.

Masters live in the repo; the copies inside mobile-apk/ are generated:

    src/server/engine/                  -> mobile-apk/android/app/src/main/python/engine/
    src/server/webapp/                  -> mobile-apk/www/            (assets/ left untouched)
    tables/all_cards.csv + finished decks -> mobile-apk/android/app/src/main/python/tables/
    decklists/*.csv                     -> mobile-apk/android/app/src/main/python/decklists/

Run after changing the engine, the webapp, or any card data:

    python scripts/sync_mobile.py          # copy
    python scripts/sync_mobile.py --check  # verify only (used by tests/CI)
"""
from __future__ import annotations

import filecmp
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MOBILE_PY = REPO / "mobile-apk" / "android" / "app" / "src" / "main" / "python"
MOBILE_WWW = REPO / "mobile-apk" / "www"

FINISHED_DECK_TABLES = [
    "tables/religion/mesopotamia/Epic_of_Gilgamesh.csv",
    "tables/religion/mesopotamia/Inannas_Descent_into_the_Underworld.csv",
    "tables/religion/mesopotamia/The_Flood.csv",
    "tables/religion/greek/siege_of_troy.csv",
]


def _iter_file_pairs() -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []

    engine_src = REPO / "src" / "server" / "engine"
    for path in sorted(engine_src.rglob("*.py")):
        rel = path.relative_to(engine_src)
        pairs.append((path, MOBILE_PY / "engine" / rel))

    webapp_src = REPO / "src" / "server" / "webapp"
    for path in sorted(webapp_src.rglob("*")):
        if path.is_file():
            rel = path.relative_to(webapp_src)
            pairs.append((path, MOBILE_WWW / rel))

    # Exported neural policy weights (optional until a checkpoint is exported).
    weights = REPO / "src" / "server" / "model" / "policy_weights.json"
    if weights.exists():
        pairs.append((weights, MOBILE_PY / "model" / "policy_weights.json"))

    pairs.append((REPO / "tables" / "all_cards.csv", MOBILE_PY / "tables" / "all_cards.csv"))
    for rel_str in FINISHED_DECK_TABLES:
        pairs.append((REPO / rel_str, MOBILE_PY / Path(rel_str)))

    for path in sorted((REPO / "decklists").glob("*.csv")):
        pairs.append((path, MOBILE_PY / "decklists" / path.name))

    return pairs


def sync(check_only: bool) -> int:
    stale: list[Path] = []
    for src, dst in _iter_file_pairs():
        if dst.exists() and filecmp.cmp(src, dst, shallow=False):
            continue
        stale.append(dst)
        if not check_only:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # Remove engine modules that no longer exist in the master.
    engine_src = REPO / "src" / "server" / "engine"
    mobile_engine = MOBILE_PY / "engine"
    if mobile_engine.exists():
        for path in sorted(mobile_engine.rglob("*.py")):
            rel = path.relative_to(mobile_engine)
            if not (engine_src / rel).exists():
                stale.append(path)
                if not check_only:
                    path.unlink()

    if check_only:
        if stale:
            print("Mobile tree is out of sync. Run: python scripts/sync_mobile.py")
            for path in stale:
                print(f"  stale: {path.relative_to(REPO)}")
            return 1
        print("Mobile tree is in sync.")
        return 0

    print(f"Synced {len(stale)} file(s)." if stale else "Nothing to sync.")
    return 0


if __name__ == "__main__":
    sys.exit(sync(check_only="--check" in sys.argv))
