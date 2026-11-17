#!/usr/bin/env python3
"""
Interactive & CLI Pack Builder Wizard for the Bedrock 3DGS Gem Packs.

- Interactive mode (default when run with no flags or via `build_packs.bat`):
  1. Asks which pack(s) to rebuild (default: 1 & 2 — Imposter + Mesh, leaving Pack 03 untouched).
  2. For each selected pack, asks which quality level(s) to include (default: medium quality only).
  3. Clears each selected pack folder and rebuilds it cleanly from `source/*.ply`.
- Non-interactive mode: pass `--pack ...` and `-y` / `--tiers ...`.
"""
import argparse
import re
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from build_imposter_pack import IMPOSTER_SETTINGS, QUALITY_TIERS, build_imposter_pack
from build_mesh_pack import MESH_SETTINGS, MESH_TIERS, build_mesh_pack
from build_splat_pack import SPLAT_SETTINGS, SPLAT_TIERS, build_splat_pack
from pack_utils import prompt_quality_tiers, resolve_tier_selection


DEFAULT_PACK_IDS = ["01", "02"]


def resolve_pack_selection(selection: str) -> list[str]:
    raw = selection.strip().lower()
    if not raw or raw in ("a", "active"):
        return list(DEFAULT_PACK_IDS)
    if raw == "all":
        return ["01", "02", "03"]

    alias = {
        "1": "01",
        "01": "01",
        "imposter": "01",
        "2": "02",
        "02": "02",
        "mesh": "02",
        "polymesh": "02",
        "3": "03",
        "03": "03",
        "splat": "03",
    }
    chosen: list[str] = []
    for tok in re.split(r"[\s,]+", raw):
        if not tok:
            continue
        if tok not in alias:
            raise ValueError(f"Unknown pack choice '{tok}'")
        pid = alias[tok]
        if pid not in chosen:
            chosen.append(pid)
    if not chosen:
        raise ValueError("No valid packs selected")
    return chosen


def prompt_pack_selection() -> list[str]:
    print("=== splat2mc Bedrock Pack Builder ===")
    print("Which pack(s) do you want to rebuild?")
    print("  [1] 01_SplatImposter_Pack  (Spherical Imposter)")
    print("  [2] 02_SplatMesh_Pack      (Shrinkwrap 3D Mesh)")
    print("  [3] 03_SplatRenderer_Pack  (Full Gaussian Splat Cloud)")
    print("  [A] Active packs (1 & 2)   [Default]")
    print("  [ALL] All 3 packs (1, 2, 3)")
    while True:
        try:
            ans = input("Enter choice (e.g. 1, 2, 1,2, A) [Default: 1,2]: ")
        except EOFError:
            ans = ""
        try:
            selected = resolve_pack_selection(ans)
            print(f"  -> Selected packs: {selected}")
            return selected
        except ValueError as exc:
            print(f"  ! {exc}. Please try again.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Interactive & CLI Bedrock 3DGS Pack Builder")
    parser.add_argument(
        "--pack",
        type=str,
        default=None,
        help="Pack(s) to build: active (1,2), all (1,2,3), or comma-separated 1,2,3",
    )
    parser.add_argument("--source-dir", type=Path, default=None, help="Source .ply directory (default: source/)")
    parser.add_argument("--tiers", type=str, default=None, help="Override quality tiers (e.g. med, smooth, all)")
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive mode (use default medium quality)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent

    if args.pack is not None:
        selected_packs = resolve_pack_selection(args.pack)
    elif args.yes:
        selected_packs = list(DEFAULT_PACK_IDS)
    else:
        selected_packs = prompt_pack_selection()

    pack_configs = {
        "01": ("01_SplatImposter_Pack", QUALITY_TIERS, IMPOSTER_SETTINGS["default_tiers"]),
        "02": ("02_SplatMesh_Pack", MESH_TIERS, MESH_SETTINGS["default_tiers"]),
        "03": ("03_SplatRenderer_Pack", SPLAT_TIERS, SPLAT_SETTINGS["default_tiers"]),
    }

    chosen_tiers: dict[str, list[dict]] = {}
    for pid in selected_packs:
        name, tier_defs, default_keys = pack_configs[pid]
        if args.tiers is not None:
            chosen_tiers[pid] = resolve_tier_selection(tier_defs, args.tiers, default_keys)
        elif args.yes:
            chosen_tiers[pid] = resolve_tier_selection(tier_defs, "", default_keys)
        else:
            chosen_tiers[pid] = prompt_quality_tiers(name, tier_defs, default_keys)

    t0 = time.time()
    for pid in selected_packs:
        if pid == "01":
            build_imposter_pack(repo_root, source_dir=args.source_dir, tiers=chosen_tiers[pid])
        elif pid == "02":
            build_mesh_pack(repo_root, source_dir=args.source_dir, tiers=chosen_tiers[pid])
        elif pid == "03":
            build_splat_pack(repo_root, source_dir=args.source_dir, tiers=chosen_tiers[pid])

    print(f"\n✓ Finished building selected packs ({', '.join(selected_packs)}) in {time.time() - t0:.2f}s!")


if __name__ == "__main__":
    main()
