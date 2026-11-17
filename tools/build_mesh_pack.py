#!/usr/bin/env python3
"""
Interactive & CLI Builder for Pack 02: `bedrock/02_SplatMesh_Pack` (Quad-Sphere Shrinkwrap Mesh).

Run directly (`python tools/build_mesh_pack.py` or `build_mesh_pack.bat`) to pick quality
tiers interactively (defaults to Smooth/Medium only), or pass `--tiers` / `-y` for scripted runs.
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from export_mesh import export_shrinkwrap_polymesh, render_6view_ortho_atlas
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
# Pack 02 Quality & Specification Settings
# ==============================================================================
MESH_SETTINGS = {
    "source_dir": "source",
    "pack_dir": "bedrock/02_SplatMesh_Pack",
    "bp_name": "SplatMesh_BP",
    "rp_name": "SplatMesh_RP",
    "default_tiers": ["smooth"],  # Default to 1 quality level per gem (Medium / Smooth)
    "target_size": 24.0,
    "tile_res": 512,
    "fill_ratio": 0.82,
    "icon_res": 128,
}

MESH_TIERS = [
    {
        "key": "faceted",
        "mode": "faceted",
        "label": "Faceted Crystal: 294 quads",
        "description": "Low / Faceted Crystal (294 quads / 588 tris)",
        "is_primary": False,
    },
    {
        "key": "smooth",
        "mode": "smooth",
        "label": "Smooth Shrinkwrap: 1536 quads",
        "description": "Medium / Smooth Shrinkwrap (1,536 quads / 3,072 tris, recommended)",
        "is_primary": True,
    },
    {
        "key": "detailed",
        "mode": "detailed",
        "label": "Detailed Shrinkwrap: 4704 quads",
        "description": "High / Detailed Shrinkwrap (4,704 quads / 9,408 tris, high polycount)",
        "is_primary": False,
    },
]


def build_mesh_pack(
    repo_root: Path,
    source_dir: Path | None = None,
    tiers: list[dict] | None = None,
    include_high_poly: bool = False,
) -> list[dict]:
    t0 = time.time()
    src = source_dir if source_dir is not None else (repo_root / MESH_SETTINGS["source_dir"])

    if tiers is not None:
        active_tiers = list(tiers)
    elif include_high_poly:
        active_tiers = list(MESH_TIERS)
    else:
        active_tiers = resolve_tier_selection(
            MESH_TIERS, "", MESH_SETTINGS["default_tiers"]
        )

    ply_files = discover_source_plys(
        src,
        fallback_files=[repo_root / "Example1.ply", repo_root / "Example2.ply"],
    )

    pack_dir = repo_root / MESH_SETTINGS["pack_dir"]
    bp_name = MESH_SETTINGS["bp_name"]
    rp_name = MESH_SETTINGS["rp_name"]
    bp_dir = pack_dir / bp_name
    rp_dir = pack_dir / rp_name

    print(
        f"\n=== Building Pack 02: 02_SplatMesh_Pack "
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
    for tex_idx, ply_path in enumerate(ply_files):
        display_name, slug_base = format_ply_display_name(ply_path)
        for tier in active_tiers:
            label = display_name if single_tier else f"{display_name} [{tier['label']}]"
            variants.append(
                {
                    "idx": idx,
                    "tex_idx": tex_idx,
                    "slug": slug_base if single_tier else f"{tier['key']}_{slug_base}",
                    "label": label,
                    "ply_path": ply_path,
                    "mode": tier["mode"],
                    "is_primary": True if single_tier else tier["is_primary"],
                }
            )
            idx += 1

    by_tex: dict[int, list[dict]] = {}
    for v in variants:
        by_tex.setdefault(v["tex_idx"], []).append(v)

    for tex_idx, tex_variants in by_tex.items():
        ply_path = tex_variants[0]["ply_path"]
        scene = GaussianScene(ply_path)

        tex_out = rp_dir / "textures" / "entity" / f"poly_gem_tex_{tex_idx}.png"
        tex_out.parent.mkdir(parents=True, exist_ok=True)
        atlas_img = render_6view_ortho_atlas(
            scene,
            tile_res=MESH_SETTINGS["tile_res"],
            fill_ratio=MESH_SETTINGS["fill_ratio"],
        )
        atlas_img.save(tex_out, optimize=True)

        icon_img = scene.render_view(
            yaw_deg=-15.0,
            pitch_deg=18.0,
            tile_res=MESH_SETTINGS["icon_res"],
            fill_ratio=0.84,
        )

        for v in tex_variants:
            i = v["idx"]
            export_shrinkwrap_polymesh(
                scene,
                output_geo=rp_dir / "models" / "entity" / f"poly_gem_{i}.geo.json",
                output_tex=None,
                geometry_id=f"geometry.poly_gem_{i}",
                mode=v["mode"],
                target_size=MESH_SETTINGS["target_size"],
                tile_res=MESH_SETTINGS["tile_res"],
            )
            icon_out = rp_dir / "textures" / "items" / f"poly_gem_{i}.png"
            icon_out.parent.mkdir(parents=True, exist_ok=True)
            icon_img.save(icon_out, optimize=True)

    src_icon = repo_root / "bedrock" / "01_SplatImposter_Pack" / "SplatImposter_RP" / "pack_icon.png"
    if src_icon.exists():
        for dst in (bp_dir / "pack_icon.png", rp_dir / "pack_icon.png"):
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src_icon, dst)

    tier_desc = ", ".join(t["mode"].title() for t in active_tiers)
    bp_uuid = "b2024991-61e0-49f1-9c2a-592837164501"
    rp_uuid = "a2024991-61e0-49f1-9c2a-592837164502"

    write_json(
        bp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "02 Shrinkwrap PolyMesh Pack BP",
                "description": f"Tier 2: Quad-Sphere Shrinkwrap 3DGS mesh + 6-view studio bake ({tier_desc})",
                "uuid": bp_uuid,
                "version": [1, 0, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "PolyMesh entities and items",
                    "type": "data",
                    "uuid": "b2024991-61e0-49f1-9c2a-592837164503",
                    "version": [1, 0, 0],
                },
                {
                    "description": "Spawn commands and pickup/placement logic",
                    "type": "script",
                    "language": "javascript",
                    "uuid": "b2024991-61e0-49f1-9c2a-592837164504",
                    "version": [1, 0, 0],
                    "entry": "scripts/main.js",
                },
            ],
            "dependencies": [
                {"module_name": "@minecraft/server", "version": "1.14.0"},
                {"module_name": "@minecraft/server-ui", "version": "1.3.0"},
                {"uuid": rp_uuid, "version": [1, 0, 0]},
            ],
        },
    )

    write_json(
        rp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "02 Shrinkwrap PolyMesh Pack RP",
                "description": f"Tier 2: Quad-Sphere Shrinkwrap 3DGS mesh + 6-view studio bake ({tier_desc})",
                "uuid": rp_uuid,
                "version": [1, 0, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "Shrinkwrap poly_mesh models and 6-view textures",
                    "type": "resources",
                    "uuid": "a2024991-61e0-49f1-9c2a-592837164505",
                    "version": [1, 0, 0],
                }
            ],
            "dependencies": [{"uuid": bp_uuid, "version": [1, 0, 0]}],
        },
    )

    primary_variant = next((v["idx"] for v in variants if v["is_primary"]), 0)
    write_variant_entity_behavior_json(
        bp_dir / "entities" / "poly_gem.json",
        identifier="custom:poly_gem",
        variant_count=len(variants),
        default_variant=primary_variant,
        collision_width=0.9,
        collision_height=1.2,
    )

    for v in variants:
        i = v["idx"]
        write_json(
            bp_dir / "items" / f"poly_gem_item_{i}.json",
            {
                "format_version": "1.20.50",
                "minecraft:item": {
                    "description": {"identifier": f"custom:poly_gem_item_{i}", "menu_category": {"category": "items"}},
                    "components": {
                        "minecraft:max_stack_size": 16,
                        "minecraft:icon": {"texture": f"poly_gem_{i}"},
                        "minecraft:display_name": {"value": f"✦ {v['label']}"},
                    },
                },
            },
        )
    write_catalog_item_json(bp_dir, "polymesh_menu", f"poly_gem_{primary_variant}", "PolyMesh Gem Catalog")

    (bp_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (bp_dir / "scripts" / "main.js").write_text(
        generate_pack_script_js(
            pack_title="02_SplatMesh_Pack (Shrinkwrap 3D Mesh)",
            pack_subtitle=f"Clean Shrinkwrap Surface Mesh + 6-View Studio Bake ({tier_desc})",
            cmd_prefix="polymesh",
            entity_id="custom:poly_gem",
            item_prefix="custom:poly_gem_item_",
            icon_prefix="textures/items/poly_gem_",
            menu_item_id="custom:polymesh_menu",
            catalog_name="PolyMesh Gem Catalog",
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
                "poly_gem_opaque:entity_alphatest": {
                    "-defines": ["ENABLE_FOG"],
                    "+states": ["DisableCulling"],
                },
            }
        },
    )

    write_json(
        rp_dir / "render_controllers" / "poly_gem.render_controllers.json",
        {
            "format_version": "1.8.0",
            "render_controllers": {
                "controller.render.poly_gem": {
                    "arrays": {
                        "geometries": {"Array.gem_geos": [f"Geometry.gem_{i}" for i in range(len(variants))]},
                        "textures": {"Array.gem_texs": [f"Texture.gem_{i}" for i in range(len(variants))]},
                    },
                    "geometry": "Array.gem_geos[query.variant]",
                    "materials": [{"*": "Material.default"}],
                    "textures": ["Array.gem_texs[query.variant]"],
                },
                "controller.render.poly_gem_attachable": {
                    "geometry": "Geometry.default",
                    "materials": [{"*": "Material.default"}],
                    "textures": ["Texture.default"],
                },
            },
        },
    )

    tex_map = {"default": f"textures/entity/poly_gem_tex_{variants[primary_variant]['tex_idx']}"}
    geo_map = {"default": f"geometry.poly_gem_{primary_variant}"}
    for v in variants:
        i = v["idx"]
        t_i = v["tex_idx"]
        tex_map[f"gem_{i}"] = f"textures/entity/poly_gem_tex_{t_i}"
        geo_map[f"gem_{i}"] = f"geometry.poly_gem_{i}"

    write_json(
        rp_dir / "entity" / "poly_gem.entity.json",
        {
            "format_version": "1.10.0",
            "minecraft:client_entity": {
                "description": {
                    "identifier": "custom:poly_gem",
                    "materials": {"default": "poly_gem_opaque"},
                    "textures": tex_map,
                    "geometry": geo_map,
                    "render_controllers": ["controller.render.poly_gem"],
                }
            },
        },
    )

    write_json(
        rp_dir / "animations" / "poly_gem.animation.json",
        {
            "format_version": "1.8.0",
            "animations": {
                "animation.poly_gem.held_and_dropped": {
                    "loop": True,
                    "bones": {"root": {"scale": 0.45, "position": [0, 3, 0]}},
                }
            },
        },
    )

    item_tex_data = {f"poly_gem_{v['idx']}": {"textures": f"textures/items/poly_gem_{v['idx']}"} for v in variants}
    write_json(
        rp_dir / "textures" / "item_texture.json",
        {
            "resource_pack_name": "02 Shrinkwrap PolyMesh Pack RP",
            "texture_name": "atlas.items",
            "texture_data": item_tex_data,
        },
    )

    lang_lines = ["entity.custom:poly_gem.name=Shrinkwrap 3D Mesh Gem"]
    for v in variants:
        lang_lines.append(f"item.custom:poly_gem_item_{v['idx']}.name=✦ {v['label']}")
    lang_lines.append("item.custom:polymesh_menu.name=✦ PolyMesh Gem Catalog")
    (rp_dir / "texts").mkdir(parents=True, exist_ok=True)
    (rp_dir / "texts" / "en_US.lang").write_text("\n".join(lang_lines) + "\n", encoding="utf-8")
    write_install_cmd(pack_dir, bp_name, rp_name, "02 Shrinkwrap PolyMesh Pack")

    print(f"✓ Built 02_SplatMesh_Pack ({len(variants)} variants) in {time.time() - t0:.2f}s")
    return variants


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Pack 02: 02_SplatMesh_Pack from source/*.ply")
    parser.add_argument("--source-dir", type=Path, default=None, help="Directory containing .ply files (default: source/)")
    parser.add_argument("--tiers", type=str, default=None, help="Comma-separated tiers: faceted, smooth, detailed, or all")
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive mode (use default tier: smooth)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    if args.tiers is not None:
        selected_tiers = resolve_tier_selection(MESH_TIERS, args.tiers, MESH_SETTINGS["default_tiers"])
    elif args.yes:
        selected_tiers = resolve_tier_selection(MESH_TIERS, "", MESH_SETTINGS["default_tiers"])
    else:
        selected_tiers = prompt_quality_tiers("02_SplatMesh_Pack", MESH_TIERS, MESH_SETTINGS["default_tiers"])

    build_mesh_pack(repo_root, source_dir=args.source_dir, tiers=selected_tiers)


if __name__ == "__main__":
    main()
