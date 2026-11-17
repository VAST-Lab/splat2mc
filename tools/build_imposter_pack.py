#!/usr/bin/env python3
"""
Interactive & CLI Builder for Pack 01: `bedrock/01_SplatImposter_Pack` (Angle-Pinned Spherical Imposter).

Run directly (`python tools/build_imposter_pack.py` or `build_imposter_pack.bat`) to pick quality
tiers interactively (defaults to Medium only), or pass `--tiers` / `-y` for scripted runs.
"""
import argparse
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from pack_utils import (
    discover_source_plys,
    format_ply_display_name,
    generate_pack_script_js,
    prompt_quality_tiers,
    reset_pack_directory,
    resolve_tier_selection,
    write_catalog_item_json,
    write_install_cmd,
    write_json,
    write_variant_entity_behavior_json,
)
from render_imposter import GaussianScene, render_imposter_atlas


# ==============================================================================
# Pack 01 Quality & Specification Settings
# ==============================================================================
IMPOSTER_SETTINGS = {
    "source_dir": "source",
    "pack_dir": "bedrock/01_SplatImposter_Pack",
    "bp_name": "SplatImposter_BP",
    "rp_name": "SplatImposter_RP",
    "default_tiers": ["med"],  # Default to 1 quality level per gem (Medium)
    "icon_res": 128,
}

QUALITY_TIERS = [
    {
        "key": "low",
        "label": "Low",
        "description": "Low (12x5 = 60 Angles @ 256px, fastest & smallest)",
        "cols": 12,
        "rows": 5,
        "res": 256,
        "pitch_min": -60.0,
        "pitch_max": 60.0,
        "is_primary": False,
    },
    {
        "key": "med",
        "label": "Medium",
        "description": "Medium (32x19 = 608 Angles @ 256px, recommended)",
        "cols": 32,
        "rows": 19,
        "res": 256,
        "pitch_min": -72.0,
        "pitch_max": 72.0,
        "is_primary": True,
    },
    {
        "key": "high",
        "label": "High",
        "description": "High (32x13 = 416 Angles @ 512px, large file size)",
        "cols": 32,
        "rows": 13,
        "res": 512,
        "pitch_min": -72.0,
        "pitch_max": 72.0,
        "is_primary": False,
    },
]


def _molang_tier_switch(tiers: list[dict], field: str) -> str:
    """Build a nested Molang ternary expression mapping `v.tier` to `tier[field]`."""
    expr = f"{float(tiers[-1][field]):.1f}"
    for idx in range(len(tiers) - 2, -1, -1):
        val = f"{float(tiers[idx][field]):.1f}"
        expr = f"(v.tier == {idx}) ? {val} : {expr}"
    return expr


def build_imposter_pack(
    repo_root: Path,
    source_dir: Path | None = None,
    tiers: list[dict] | None = None,
    include_high_quality: bool = False,
) -> list[dict]:
    t0 = time.time()
    src = source_dir if source_dir is not None else (repo_root / IMPOSTER_SETTINGS["source_dir"])

    if tiers is not None:
        active_tiers = list(tiers)
    elif include_high_quality:
        active_tiers = list(QUALITY_TIERS)
    else:
        active_tiers = resolve_tier_selection(
            QUALITY_TIERS, "", IMPOSTER_SETTINGS["default_tiers"]
        )

    ply_files = discover_source_plys(
        src,
        fallback_files=[repo_root / "Example1.ply", repo_root / "Example2.ply"],
    )

    pack_dir = repo_root / IMPOSTER_SETTINGS["pack_dir"]
    bp_name = IMPOSTER_SETTINGS["bp_name"]
    rp_name = IMPOSTER_SETTINGS["rp_name"]
    bp_dir = pack_dir / bp_name
    rp_dir = pack_dir / rp_name

    print(
        f"\n=== Building Pack 01: 01_SplatImposter_Pack "
        f"({len(ply_files)} PLYs x {len(active_tiers)} tier(s): {[t['key'] for t in active_tiers]}) ==="
    )

    # Clear existing pack folder completely before regenerating
    reset_pack_directory(
        pack_dir,
        preserve_relpaths=[f"{bp_name}/pack_icon.png", f"{rp_name}/pack_icon.png"],
    )

    single_tier = len(active_tiers) == 1
    variants: list[dict] = []
    idx = 0
    for ply_path in ply_files:
        display_name, slug_base = format_ply_display_name(ply_path)
        for tier in active_tiers:
            angles = tier["cols"] * tier["rows"]
            label = (
                display_name
                if single_tier
                else f"{display_name} [{tier['label']}: {tier['cols']}x{tier['rows']} = {angles} Angles, {tier['res']}px]"
            )
            variants.append(
                {
                    "idx": idx,
                    "slug": slug_base if single_tier else f"{tier['key']}_{slug_base}",
                    "label": label,
                    "ply_path": ply_path,
                    "cols": tier["cols"],
                    "rows": tier["rows"],
                    "res": tier["res"],
                    "pitch_min": tier["pitch_min"],
                    "pitch_max": tier["pitch_max"],
                    "is_primary": True if single_tier else tier["is_primary"],
                }
            )
            idx += 1

    by_ply: dict[Path, list[dict]] = {}
    for v in variants:
        by_ply.setdefault(v["ply_path"], []).append(v)

    for ply_path, ply_variants in by_ply.items():
        scene = GaussianScene(ply_path)
        for v in ply_variants:
            i = v["idx"]
            render_imposter_atlas(
                scene,
                output_atlas=rp_dir / "textures" / "entity" / f"spritesheet_{i}.png",
                output_icon=rp_dir / "textures" / "items" / f"gem_{i}.png",
                cols=v["cols"],
                rows=v["rows"],
                tile_res=v["res"],
                pitch_min=v["pitch_min"],
                pitch_max=v["pitch_max"],
            )

    tier_desc = ", ".join(
        f"{t['label']} ({t['cols']}x{t['rows']} {t['res']}px)" for t in active_tiers
    )

    write_json(
        bp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "01 Spherical Imposter Pack BP",
                "description": f"Tier 1: Angle-pinned spherical 3DGS imposter ({tier_desc})",
                "uuid": "4f183cf9-8b92-491c-b26a-8b839ef74911",
                "version": [1, 2, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "Imposter entities and items",
                    "type": "data",
                    "uuid": "c3539bc7-e314-469b-b89e-3d0d34a41f6e",
                    "version": [1, 2, 0],
                },
                {
                    "description": "Spawn commands and pickup/placement logic",
                    "type": "script",
                    "language": "javascript",
                    "uuid": "94a1f63e-52c1-443d-98ea-6217a67c52f1",
                    "version": [1, 2, 0],
                    "entry": "scripts/main.js",
                },
            ],
            "dependencies": [
                {"module_name": "@minecraft/server", "version": "1.14.0"},
                {"module_name": "@minecraft/server-ui", "version": "1.3.0"},
                {"uuid": "a793a382-f5c7-43cf-87b6-c564175b9de3", "version": [1, 2, 0]},
            ],
        },
    )

    write_json(
        rp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "01 Spherical Imposter Pack RP",
                "description": f"Tier 1: Angle-pinned spherical 3DGS imposter visuals ({tier_desc})",
                "uuid": "a793a382-f5c7-43cf-87b6-c564175b9de3",
                "version": [1, 2, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "Imposter geometry, animations, and textures",
                    "type": "resources",
                    "uuid": "7876a47a-c114-44b4-82a9-0d3ee787e915",
                    "version": [1, 2, 0],
                }
            ],
            "dependencies": [
                {"uuid": "4f183cf9-8b92-491c-b26a-8b839ef74911", "version": [1, 2, 0]}
            ],
        },
    )

    primary_variant = next((v["idx"] for v in variants if v["is_primary"]), 0)
    write_variant_entity_behavior_json(
        bp_dir / "entities" / "imposter.json",
        identifier="custom:imposter",
        variant_count=len(variants),
        default_variant=primary_variant,
        collision_width=0.9,
        collision_height=1.1,
    )

    for v in variants:
        i = v["idx"]
        write_json(
            bp_dir / "items" / f"gem_item_{i}.json",
            {
                "format_version": "1.20.50",
                "minecraft:item": {
                    "description": {"identifier": f"custom:gem_item_{i}", "menu_category": {"category": "items"}},
                    "components": {
                        "minecraft:max_stack_size": 16,
                        "minecraft:icon": {"texture": f"gem_{i}"},
                        "minecraft:display_name": {"value": f"✦ {v['label']}"},
                    },
                },
            },
        )
    write_catalog_item_json(bp_dir, "imposter_menu", f"gem_{primary_variant}", "Imposter Gem Catalog")

    (bp_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (bp_dir / "scripts" / "main.js").write_text(
        generate_pack_script_js(
            pack_title="01_SplatImposter_Pack (Spherical Imposter)",
            pack_subtitle=f"Angle-Pinned 3DGS Imposter ({tier_desc})",
            cmd_prefix="imposter",
            entity_id="custom:imposter",
            item_prefix="custom:gem_item_",
            icon_prefix="textures/items/gem_",
            menu_item_id="custom:imposter_menu",
            catalog_name="Imposter Gem Catalog",
            variants=variants,
            include_sample_runner=False,
        ),
        encoding="utf-8",
    )

    write_json(
        rp_dir / "materials" / "entity.material",
        {
            "materials": {
                "version": "1.0.0",
                "imposter_alphablend:entity_alphablend": {
                    "+defines": ["USE_UV_ANIM"],
                    "-defines": ["ENABLE_FOG"],
                },
                "imposter_alphatest:entity_alphatest": {
                    "+defines": ["USE_UV_ANIM"],
                    "-defines": ["ENABLE_FOG"],
                },
            }
        },
    )

    write_json(
        rp_dir / "models" / "entity" / "imposter.geo.json",
        {
            "format_version": "1.12.0",
            "minecraft:geometry": [
                {
                    "description": {
                        "identifier": "geometry.imposter",
                        "texture_width": 16,
                        "texture_height": 16,
                        "visible_bounds_width": 4,
                        "visible_bounds_height": 4,
                        "visible_bounds_offset": [0, 1, 0],
                    },
                    "bones": [
                        {"name": "root", "pivot": [0, 0, 0]},
                        {
                            "name": "body",
                            "parent": "root",
                            "pivot": [0, 12, 0],
                            "cubes": [
                                {
                                    "origin": [-12, 0, 0],
                                    "size": [24, 24, 0],
                                    "uv": {
                                        "north": {"uv": [0, 0], "uv_size": [16, 16]},
                                        "south": {"uv": [16, 0], "uv_size": [-16, 16]},
                                    },
                                }
                            ],
                        },
                    ],
                }
            ],
        },
    )

    write_json(
        rp_dir / "animations" / "imposter.animation.json",
        {
            "format_version": "1.8.0",
            "animations": {
                "animation.imposter.align_to_baked_frame": {
                    "loop": True,
                    "bones": {"body": {"rotation": ["v.snapped_pitch", "v.snapped_yaw", 0]}},
                },
                "animation.imposter.hand_pose": {
                    "loop": True,
                    "bones": {"root_item": {"rotation": [0, 0, 0], "position": [0, 2, 0], "scale": 0.75}},
                },
            },
        },
    )

    textures_map = {"default": f"textures/entity/spritesheet_{primary_variant}"}
    for v in variants:
        i = v["idx"]
        textures_map[f"gem_{i}"] = f"textures/entity/spritesheet_{i}"

    write_json(
        rp_dir / "render_controllers" / "imposter.render_controllers.json",
        {
            "format_version": "1.8.0",
            "render_controllers": {
                "controller.render.imposter": {
                    "arrays": {
                        "textures": {"Array.gem_sheets": [f"Texture.gem_{v['idx']}" for v in variants]}
                    },
                    "geometry": "Geometry.default",
                    "materials": [{"*": "Material.default"}],
                    "textures": ["Array.gem_sheets[query.variant]"],
                    "uv_anim": {
                        "offset": ["v.col / v.cols", "v.row / v.rows"],
                        "scale": ["1.0 / v.cols", "1.0 / v.rows"],
                    },
                }
            },
        },
    )

    write_json(
        rp_dir / "textures" / "item_texture.json",
        {
            "resource_pack_name": "Spherical Imposter Gallery RP",
            "texture_name": "atlas.items",
            "texture_data": {f"gem_{v['idx']}": {"textures": f"textures/items/gem_{v['idx']}"} for v in variants},
        },
    )

    init_tier = next((t for t in active_tiers if t["is_primary"]), active_tiers[0])
    write_json(
        rp_dir / "entity" / "imposter.entity.json",
        {
            "format_version": "1.10.0",
            "minecraft:client_entity": {
                "description": {
                    "identifier": "custom:imposter",
                    "materials": {"default": "imposter_alphablend"},
                    "textures": textures_map,
                    "geometry": {"default": "geometry.imposter"},
                    "animations": {"align_to_baked_frame": "animation.imposter.align_to_baked_frame"},
                    "scripts": {
                        "initialize": [
                            f"v.cols = {float(init_tier['cols']):.1f};",
                            f"v.rows = {float(init_tier['rows']):.1f};",
                            f"v.pitch_min = {float(init_tier['pitch_min']):.1f};",
                            f"v.pitch_max = {float(init_tier['pitch_max']):.1f};",
                        ],
                        "pre_animation": [
                            f"v.tier = math.mod(query.variant, {len(active_tiers)});",
                            f"v.cols = {_molang_tier_switch(active_tiers, 'cols')};",
                            f"v.rows = {_molang_tier_switch(active_tiers, 'rows')};",
                            f"v.pitch_min = {_molang_tier_switch(active_tiers, 'pitch_min')};",
                            f"v.pitch_max = {_molang_tier_switch(active_tiers, 'pitch_max')};",
                            "v.yaw_step = 360.0 / v.cols;",
                            "v.rel_cam_yaw = query.rotation_to_camera(1) - query.body_y_rotation;",
                            "v.raw_yaw = -v.rel_cam_yaw;",
                            "v.norm_yaw = math.mod(math.mod(v.raw_yaw + (v.yaw_step * 0.5), 360.0) + 360.0, 360.0);",
                            "v.col = math.clamp(math.floor(v.norm_yaw / v.yaw_step), 0, v.cols - 1);",
                            "v.snapped_yaw = -(v.col * v.yaw_step);",
                            "v.raw_pitch = math.clamp(-query.rotation_to_camera(0), v.pitch_min, v.pitch_max);",
                            "v.pitch_step = (v.pitch_max - v.pitch_min) / math.max(1, v.rows - 1);",
                            "v.row = math.clamp(math.round((v.pitch_max - v.raw_pitch) / v.pitch_step), 0, v.rows - 1);",
                            "v.snapped_pitch = -(v.pitch_max - (v.row * v.pitch_step));",
                        ],
                        "animate": ["align_to_baked_frame"],
                    },
                    "render_controllers": ["controller.render.imposter"],
                }
            },
        },
    )

    lang_lines = ["entity.custom:imposter.name=Gaussian Splat Imposter Gem"]
    for v in variants:
        lang_lines.append(f"item.custom:gem_item_{v['idx']}.name=✦ {v['label']}")
    lang_lines.append("item.custom:imposter_menu.name=✦ Imposter Gem Catalog")
    (rp_dir / "texts").mkdir(parents=True, exist_ok=True)
    (rp_dir / "texts" / "en_US.lang").write_text("\n".join(lang_lines) + "\n", encoding="utf-8")
    write_install_cmd(pack_dir, bp_name, rp_name, "01 Spherical Imposter Pack")

    print(f"✓ Built 01_SplatImposter_Pack ({len(variants)} variants) in {time.time() - t0:.2f}s")
    return variants


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Pack 01: 01_SplatImposter_Pack from source/*.ply")
    parser.add_argument("--source-dir", type=Path, default=None, help="Directory containing .ply files (default: source/)")
    parser.add_argument("--tiers", type=str, default=None, help="Comma-separated tiers: low, med, high, or all")
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive mode (use default tier: med)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    if args.tiers is not None:
        selected_tiers = resolve_tier_selection(QUALITY_TIERS, args.tiers, IMPOSTER_SETTINGS["default_tiers"])
    elif args.yes:
        selected_tiers = resolve_tier_selection(QUALITY_TIERS, "", IMPOSTER_SETTINGS["default_tiers"])
    else:
        selected_tiers = prompt_quality_tiers("01_SplatImposter_Pack", QUALITY_TIERS, IMPOSTER_SETTINGS["default_tiers"])

    build_imposter_pack(repo_root, source_dir=args.source_dir, tiers=selected_tiers)


if __name__ == "__main__":
    main()
