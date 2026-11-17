#!/usr/bin/env python3
"""
Interactive & CLI Builder for Pack 03: `bedrock/03_SplatRenderer_Pack` (Oriented Gaussian Splat Cloud).

Run directly (`python tools/build_splat_pack.py` or `build_splat_pack.bat`) to pick splat counts
interactively (defaults to 1500/Medium only), or pass `--tiers` / `-y` for scripted runs.
"""
import argparse
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from export_geo_splat import export_geo_splat
from export_oriented import export_oriented_bedrock
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
from render_imposter import GaussianScene


# ==============================================================================
# Pack 03 Quality & Specification Settings
# ==============================================================================
SPLAT_SETTINGS = {
    "source_dir": "source",
    "pack_dir": "bedrock/03_SplatRenderer_Pack",
    "bp_name": "SplatRenderer_BP",
    "rp_name": "SplatRenderer_RP",
    "default_tiers": ["1500"],  # Default to 1 quality level per gem (Medium / 1500 splats)
    "target_size": 48.0,
    "particle_max": 5000,
    "particle_size": 5.0,
    "icon_res": 128,
}

SPLAT_TIERS = [
    {
        "key": "800",
        "splats": 800,
        "label": "800 Splats",
        "description": "Low (800 splat planes)",
        "is_primary": False,
    },
    {
        "key": "1500",
        "splats": 1500,
        "label": "1500 Splats",
        "description": "Medium (1,500 splat planes, recommended)",
        "is_primary": True,
    },
    {
        "key": "2500",
        "splats": 2500,
        "label": "2500 Splats",
        "description": "High (2,500 splat planes)",
        "is_primary": False,
    },
]


def build_splat_pack(
    repo_root: Path,
    source_dir: Path | None = None,
    tiers: list[dict] | None = None,
    fallback_plys: list[Path] | None = None,
) -> list[dict]:
    t0 = time.time()
    src = source_dir if source_dir is not None else (repo_root / SPLAT_SETTINGS["source_dir"])
    active_tiers = (
        list(tiers)
        if tiers is not None
        else resolve_tier_selection(SPLAT_TIERS, "", SPLAT_SETTINGS["default_tiers"])
    )
    ply_files = discover_source_plys(
        src,
        fallback_files=fallback_plys or [repo_root / "Example1.ply", repo_root / "Example2.ply"],
    )

    pack_dir = repo_root / SPLAT_SETTINGS["pack_dir"]
    bp_name = SPLAT_SETTINGS["bp_name"]
    rp_name = SPLAT_SETTINGS["rp_name"]
    bp_dir = pack_dir / bp_name
    rp_dir = pack_dir / rp_name

    print(
        f"\n=== Building Pack 03: 03_SplatRenderer_Pack "
        f"({len(ply_files)} PLYs x {len(active_tiers)} tier(s): {[t['key'] for t in active_tiers]}) ==="
    )

    # Clear existing pack folder while preserving particle stream scripts & shaders
    reset_pack_directory(
        pack_dir,
        preserve_relpaths=[
            f"{bp_name}/pack_icon.png",
            f"{rp_name}/pack_icon.png",
            f"{bp_name}/scripts/SampleManager.js",
            f"{bp_name}/scripts/ScriptBox.js",
            f"{bp_name}/scripts/SplatData.js",
            f"{rp_name}/materials/entity.material",
            f"{rp_name}/materials/particles.material",
            f"{rp_name}/particles/splat.particle.json",
            f"{rp_name}/particles/splat_core.particle.json",
            f"{rp_name}/textures/particles/splat_tex.png",
        ],
    )

    single_tier = len(active_tiers) == 1
    variants: list[dict] = []
    idx = 0
    for ply_path in ply_files:
        display_name, slug_base = format_ply_display_name(ply_path)
        for tier in active_tiers:
            label = display_name if single_tier else f"{display_name} Splat Cloud [{tier['label']}]"
            variants.append(
                {
                    "idx": idx,
                    "slug": slug_base if single_tier else f"{slug_base}_{tier['key']}",
                    "label": label,
                    "ply_path": ply_path,
                    "splats": tier["splats"],
                    "is_primary": True if single_tier else tier["is_primary"],
                }
            )
            idx += 1

    by_ply: dict[Path, list[dict]] = {}
    for v in variants:
        by_ply.setdefault(v["ply_path"], []).append(v)

    for ply_path, ply_variants in by_ply.items():
        scene = GaussianScene(ply_path)
        icon_img = scene.render_view(
            yaw_deg=-15.0,
            pitch_deg=18.0,
            tile_res=SPLAT_SETTINGS["icon_res"],
            fill_ratio=0.84,
        )
        for v in ply_variants:
            i = v["idx"]
            export_geo_splat(
                scene,
                output_geo=rp_dir / "models" / "entity" / f"splat_cloud_{i}.geo.json",
                output_tex=rp_dir / "textures" / "entity" / f"splat_cloud_{i}.png",
                geometry_id=f"geometry.splat_cloud_{i}",
                max_splats=v["splats"],
                target_size=SPLAT_SETTINGS["target_size"],
            )
            icon_out = rp_dir / "textures" / "items" / f"splat_gem_{i}.png"
            icon_out.parent.mkdir(parents=True, exist_ok=True)
            icon_img.save(icon_out, optimize=True)

    if not (bp_dir / "scripts" / "SplatData.js").exists():
        export_oriented_bedrock(
            ply_files[-1],
            bp_dir / "scripts" / "SplatData.js",
            max_particles=SPLAT_SETTINGS["particle_max"],
            target_size=SPLAT_SETTINGS["particle_size"],
        )

    write_json(
        bp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "03 Full Gaussian Splat Pack BP",
                "description": "Tier 3: Full Oriented 3D Gaussian Splat Cloud entities & Particle Streamer",
                "uuid": "c204272a-f9df-4bb0-bd6a-d42d790854f6",
                "version": [1, 2, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "Entity definitions for full splat clouds",
                    "type": "data",
                    "uuid": "6ef19c0a-4a51-4e78-9d8c-3b27821b9d14",
                    "version": [1, 2, 0],
                },
                {
                    "description": "Script resources for oriented splat streaming & spawning",
                    "type": "script",
                    "language": "javascript",
                    "uuid": "280f1f87-c5a5-4756-b33c-0976e55d0ad3",
                    "version": [1, 2, 0],
                    "entry": "scripts/main.js",
                },
            ],
            "dependencies": [
                {"module_name": "@minecraft/server", "version": "1.14.0"},
                {"module_name": "@minecraft/server-ui", "version": "1.3.0"},
                {"uuid": "78ec6229-ce0d-4d97-9bf3-8fe3584a86ea", "version": [1, 2, 0]},
            ],
        },
    )

    write_json(
        rp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "03 Full Gaussian Splat Pack RP",
                "description": "Tier 3: Full Oriented 3D Gaussian Splat Cloud visuals and double-sided particles",
                "uuid": "78ec6229-ce0d-4d97-9bf3-8fe3584a86ea",
                "version": [1, 2, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "Custom splat particles, textures, and geometries",
                    "type": "resources",
                    "uuid": "ebdde273-3e6a-4dc4-9337-2698f9767496",
                    "version": [1, 2, 0],
                }
            ],
            "dependencies": [
                {"uuid": "c204272a-f9df-4bb0-bd6a-d42d790854f6", "version": [1, 2, 0]}
            ],
        },
    )

    primary_variant = next((v["idx"] for v in variants if v["is_primary"]), 0)
    write_variant_entity_behavior_json(
        bp_dir / "entities" / "splat_cloud.json",
        identifier="custom:splat_cloud",
        variant_count=len(variants),
        default_variant=primary_variant,
        collision_width=0.9,
        collision_height=1.2,
    )

    for v in variants:
        i = v["idx"]
        write_json(
            bp_dir / "items" / f"splat_gem_item_{i}.json",
            {
                "format_version": "1.20.50",
                "minecraft:item": {
                    "description": {"identifier": f"custom:splat_gem_item_{i}", "menu_category": {"category": "items"}},
                    "components": {
                        "minecraft:max_stack_size": 16,
                        "minecraft:icon": {"texture": f"splat_gem_{i}"},
                        "minecraft:display_name": {"value": f"✦ {v['label']}"},
                    },
                },
            },
        )
    write_catalog_item_json(bp_dir, "splat_menu", f"splat_gem_{primary_variant}", "Splat Gem Catalog")

    (bp_dir / "scripts" / "main.js").write_text(
        generate_pack_script_js(
            pack_title="03_SplatRenderer_Pack (Full Gaussian Splat Renderer)",
            pack_subtitle="Full Oriented Splat Cloud + Particle Stream",
            cmd_prefix="splat",
            entity_id="custom:splat_cloud",
            item_prefix="custom:splat_gem_item_",
            icon_prefix="textures/items/splat_gem_",
            menu_item_id="custom:splat_menu",
            catalog_name="Splat Gem Catalog",
            variants=variants,
            include_sample_runner=True,
        ),
        encoding="utf-8",
    )

    write_json(
        rp_dir / "render_controllers" / "splat_cloud.render_controllers.json",
        {
            "format_version": "1.8.0",
            "render_controllers": {
                "controller.render.splat_cloud": {
                    "arrays": {
                        "geometries": {"Array.gem_geos": [f"Geometry.gem_{i}" for i in range(len(variants))]},
                        "textures": {"Array.gem_texs": [f"Texture.gem_{i}" for i in range(len(variants))]},
                    },
                    "geometry": "Array.gem_geos[query.variant]",
                    "materials": [{"*": "Material.default"}, {"core_*": "Material.core"}],
                    "textures": ["Array.gem_texs[query.variant]"],
                },
                "controller.render.splat_cloud_attachable": {
                    "geometry": "Geometry.default",
                    "materials": [{"*": "Material.default"}, {"core_*": "Material.core"}],
                    "textures": ["Texture.default"],
                },
            },
        },
    )

    tex_map = {"default": f"textures/entity/splat_cloud_{primary_variant}"}
    geo_map = {"default": f"geometry.splat_cloud_{primary_variant}"}
    for v in variants:
        i = v["idx"]
        tex_map[f"gem_{i}"] = f"textures/entity/splat_cloud_{i}"
        geo_map[f"gem_{i}"] = f"geometry.splat_cloud_{i}"

    write_json(
        rp_dir / "entity" / "splat_cloud.entity.json",
        {
            "format_version": "1.10.0",
            "minecraft:client_entity": {
                "description": {
                    "identifier": "custom:splat_cloud",
                    "materials": {"default": "splat_cloud_blend", "core": "splat_cloud_core"},
                    "textures": tex_map,
                    "geometry": geo_map,
                    "render_controllers": ["controller.render.splat_cloud"],
                }
            },
        },
    )

    write_json(
        rp_dir / "animations" / "splat_cloud.animation.json",
        {
            "format_version": "1.8.0",
            "animations": {
                "animation.splat_cloud.held_and_dropped": {
                    "loop": True,
                    "bones": {"root": {"scale": 0.42, "position": [0, 3, 0]}},
                }
            },
        },
    )

    item_tex_data = {f"splat_gem_{v['idx']}": {"textures": f"textures/items/splat_gem_{v['idx']}"} for v in variants}
    write_json(
        rp_dir / "textures" / "item_texture.json",
        {
            "resource_pack_name": "03 Full Gaussian Splat Pack RP",
            "texture_name": "atlas.items",
            "texture_data": item_tex_data,
        },
    )

    lang_lines = ["entity.custom:splat_cloud.name=Full Gaussian Splat Cloud Gem"]
    for v in variants:
        lang_lines.append(f"item.custom:splat_gem_item_{v['idx']}.name=✦ {v['label']}")
    lang_lines.append("item.custom:splat_menu.name=✦ Splat Gem Catalog")
    (rp_dir / "texts").mkdir(parents=True, exist_ok=True)
    (rp_dir / "texts" / "en_US.lang").write_text("\n".join(lang_lines) + "\n", encoding="utf-8")
    write_install_cmd(pack_dir, bp_name, rp_name, "03 Full Gaussian Splat Pack")

    print(f"✓ Built 03_SplatRenderer_Pack ({len(variants)} variants) in {time.time() - t0:.2f}s")
    return variants


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Pack 03: 03_SplatRenderer_Pack")
    parser.add_argument("--source-dir", type=Path, default=None, help="Directory containing .ply files (default: source/)")
    parser.add_argument("--tiers", type=str, default=None, help="Comma-separated tiers: 800, 1500, 2500, or all")
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive mode (use default tier: 1500)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    if args.tiers is not None:
        selected_tiers = resolve_tier_selection(SPLAT_TIERS, args.tiers, SPLAT_SETTINGS["default_tiers"])
    elif args.yes:
        selected_tiers = resolve_tier_selection(SPLAT_TIERS, "", SPLAT_SETTINGS["default_tiers"])
    else:
        selected_tiers = prompt_quality_tiers("03_SplatRenderer_Pack", SPLAT_TIERS, SPLAT_SETTINGS["default_tiers"])

    build_splat_pack(repo_root, source_dir=args.source_dir, tiers=selected_tiers)


if __name__ == "__main__":
    main()
