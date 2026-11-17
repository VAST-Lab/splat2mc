#!/usr/bin/env python3
"""
Interactive & CLI Builder for Pack 02: `bedrock/02_SplatMesh_Pack` (TSDF Splat-to-Mesh Bake).

Replaces the legacy Quad-Sphere shrinkwrap (`bedrock/_archive/mesh_converted_from_splat`) with
multi-view 3DGS RGB-D TSDF + space carving -> Marching Cubes -> single-component QEM decimation,
connected-chart UV unwrapping, and depth-verified multi-view texture baking.
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from export_splat_mesh import bake_splat_to_lowpoly
from mesh_bake_utils import render_mesh_icon, write_bedrock_polymesh_geo, write_binary_fbx
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


# ==============================================================================
# Pack 02 Quality & Specification Settings
# ==============================================================================
MESH_SETTINGS = {
    "source_dir": "source",
    "fbx_out_dir": "source/FBX_Output",
    "pack_dir": "bedrock/02_SplatMesh_Pack",
    "bp_name": "SplatMesh_BP",
    "rp_name": "SplatMesh_RP",
    "default_tiers": ["med"],  # Default to 14k-tri Medium TSDF bake per PLY gem
    "target_size": 24.0,
    "smooth_iters": 1,
    "grid_res": 216,
    "n_views": 42,
    "view_res": 384,
    "icon_res": 128,
}

MESH_TIERS = [
    {
        "key": "low",
        "label": "Low: 6k tris",
        "tris": 6000,
        "atlas_res": 512,
        "description": "Low (6,000 tris @ 512px multi-view bake)",
        "is_primary": False,
    },
    {
        "key": "med",
        "label": "Med: 14k tris",
        "tris": 14000,
        "atlas_res": 1024,
        "description": "Medium (14,000 tris @ 1024px multi-view bake, recommended)",
        "is_primary": True,
    },
    {
        "key": "high",
        "label": "High: 24k tris",
        "tris": 24000,
        "atlas_res": 2048,
        "description": "High (24,000 tris @ 2048px multi-view bake, high polycount)",
        "is_primary": False,
    },
]


def build_mesh_pack(
    repo_root: Path,
    source_dir: Path | None = None,
    tiers: list[dict] | None = None,
    include_high_poly: bool = False,
    bake_normals: bool = False,
    smooth_iters: int = MESH_SETTINGS["smooth_iters"],
    save_fbx: bool = False,
    fbx_out_dir: Path | None = None,
) -> list[dict]:
    t0 = time.time()
    src = source_dir if source_dir is not None else (repo_root / MESH_SETTINGS["source_dir"])
    fbx_export_dir = fbx_out_dir if fbx_out_dir is not None else (repo_root / MESH_SETTINGS["fbx_out_dir"])

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
        f"({len(ply_files)} PLYs x {len(active_tiers)} tier(s): {[t['key'] for t in active_tiers]}, "
        f"normals={'on' if bake_normals else 'off'}) ==="
    )

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
            label = display_name if single_tier else f"{display_name} [{tier['label']}]"
            variants.append(
                {
                    "idx": idx,
                    "slug": slug_base if single_tier else f"{tier['key']}_{slug_base}",
                    "label": label,
                    "ply_path": ply_path,
                    "tris": tier["tris"],
                    "atlas_res": tier["atlas_res"],
                    "is_primary": True if single_tier else tier["is_primary"],
                }
            )
            idx += 1

    by_ply: dict[Path, list[dict]] = {}
    for v in variants:
        by_ply.setdefault(v["ply_path"], []).append(v)

    for ply_path, ply_variants in by_ply.items():
        cached_high = None
        for v in ply_variants:
            i = v["idx"]
            vt0 = time.time()
            low_mesh, low_uv, low_fuv, diff_img, nrm_img, cached_high = bake_splat_to_lowpoly(
                ply_path,
                target_tris=v["tris"],
                atlas_res=v["atlas_res"],
                smooth_iters=smooth_iters,
                n_views=MESH_SETTINGS["n_views"],
                view_res=MESH_SETTINGS["view_res"],
                grid_res=MESH_SETTINGS["grid_res"],
                bake_normals=bake_normals,
                cached_high=cached_high,
            )

            tex_out = rp_dir / "textures" / "entity" / f"poly_gem_tex_{i}.png"
            tex_out.parent.mkdir(parents=True, exist_ok=True)
            diff_img.save(tex_out, optimize=True)

            if bake_normals and nrm_img is not None:
                nrm_out = rp_dir / "textures" / "entity" / f"poly_gem_tex_{i}_normal.png"
                nrm_img.save(nrm_out, optimize=True)
                write_json(
                    rp_dir / "textures" / "entity" / f"poly_gem_tex_{i}.texture_set.json",
                    {
                        "format_version": "1.16.100",
                        "minecraft:texture_set": {
                            "color": f"poly_gem_tex_{i}",
                            "normal": f"poly_gem_tex_{i}_normal",
                        },
                    },
                )

            write_bedrock_polymesh_geo(
                low_mesh,
                low_uv,
                low_fuv,
                output_geo=rp_dir / "models" / "entity" / f"poly_gem_{i}.geo.json",
                geometry_id=f"geometry.poly_gem_{i}",
                atlas_res=v["atlas_res"],
                target_size=MESH_SETTINGS["target_size"],
            )

            if save_fbx:
                out_stem = (
                    f"{ply_path.stem}_splat"
                    if single_tier
                    else f"{ply_path.stem}_splat_{v['tris'] // 1000}k"
                )
                write_binary_fbx(
                    low_mesh,
                    low_uv,
                    low_fuv,
                    diff_img,
                    fbx_export_dir / f"{out_stem}.fbx",
                    normal_img=nrm_img,
                )

            icon_img = render_mesh_icon(
                low_mesh, low_uv, low_fuv, diff_img, icon_res=MESH_SETTINGS["icon_res"]
            )
            icon_out = rp_dir / "textures" / "items" / f"poly_gem_{i}.png"
            icon_out.parent.mkdir(parents=True, exist_ok=True)
            icon_img.save(icon_out, optimize=True)

            print(
                f"    ✓ Variant {i} ({v['label']}): {len(low_mesh.vertices):,} verts, "
                f"{len(low_mesh.faces):,} tris @ {v['atlas_res']}px in {time.time() - vt0:.2f}s"
            )

    src_icon = repo_root / "bedrock" / "01_SplatImposter_Pack" / "SplatImposter_RP" / "pack_icon.png"
    if src_icon.exists():
        for dst in (bp_dir / "pack_icon.png", rp_dir / "pack_icon.png"):
            if not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(src_icon, dst)

    tier_desc = ", ".join(f"{t['tris']} tris @ {t['atlas_res']}px" for t in active_tiers)
    bp_uuid = "b2024991-61e0-49f1-9c2a-592837164501"
    rp_uuid = "a2024991-61e0-49f1-9c2a-592837164502"

    write_json(
        bp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "02 Splat High-to-Low Mesh Pack BP",
                "description": f"Tier 2: TSDF High-to-Low 3DGS PolyMesh + Connected UV Bake ({tier_desc})",
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

    rp_manifest = {
        "format_version": 2,
        "header": {
            "name": "02 Splat High-to-Low Mesh Pack RP",
            "description": f"Tier 2: TSDF High-to-Low 3DGS PolyMesh + Connected UV Bake ({tier_desc})",
            "uuid": rp_uuid,
            "version": [1, 0, 0],
            "min_engine_version": [1, 21, 0],
        },
        "modules": [
            {
                "description": "TSDF high-to-low poly_mesh models and multi-view textures",
                "type": "resources",
                "uuid": "a2024991-61e0-49f1-9c2a-592837164505",
                "version": [1, 0, 0],
            }
        ],
        "dependencies": [{"uuid": bp_uuid, "version": [1, 0, 0]}],
    }
    if bake_normals:
        rp_manifest["capabilities"] = ["pbr"]
    write_json(rp_dir / "manifest.json", rp_manifest)

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
            pack_title="02_SplatMesh_Pack (TSDF High-to-Low 3D Mesh)",
            pack_subtitle=f"TSDF Splat Mesh + Multi-View Connected UV Bake ({tier_desc})",
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

    tex_map = {"default": f"textures/entity/poly_gem_tex_{primary_variant}"}
    geo_map = {"default": f"geometry.poly_gem_{primary_variant}"}
    for v in variants:
        i = v["idx"]
        tex_map[f"gem_{i}"] = f"textures/entity/poly_gem_tex_{i}"
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
            "resource_pack_name": "02 Splat High-to-Low Mesh Pack RP",
            "texture_name": "atlas.items",
            "texture_data": item_tex_data,
        },
    )

    lang_lines = ["entity.custom:poly_gem.name=Splat High-to-Low 3D Mesh Gem"]
    for v in variants:
        lang_lines.append(f"item.custom:poly_gem_item_{v['idx']}.name=✦ {v['label']}")
    lang_lines.append("item.custom:polymesh_menu.name=✦ PolyMesh Gem Catalog")
    (rp_dir / "texts").mkdir(parents=True, exist_ok=True)
    (rp_dir / "texts" / "en_US.lang").write_text("\n".join(lang_lines) + "\n", encoding="utf-8")
    write_install_cmd(pack_dir, bp_name, rp_name, "02 Splat High-to-Low Mesh Pack")

    print(f"✓ Built 02_SplatMesh_Pack ({len(variants)} variants) in {time.time() - t0:.2f}s")
    return variants


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Pack 02: 02_SplatMesh_Pack from source/*.ply")
    parser.add_argument("--source-dir", type=Path, default=None, help="Directory containing .ply files (default: source/)")
    parser.add_argument("--tiers", type=str, default=None, help="Comma-separated tiers: low, med, high, or all")
    parser.add_argument("--bake-normals", action="store_true", help="Also bake tangent-space normal maps (off by default)")
    parser.add_argument("--smooth-iters", type=int, default=MESH_SETTINGS["smooth_iters"], help="Taubin smoothing passes (default: 1)")
    parser.add_argument("--save-fbx", action="store_true", help="Also save reduced .fbx + _diffuse.png to source/FBX_Output/")
    parser.add_argument("--fbx-out-dir", type=Path, default=None, help="Output folder for --save-fbx (default: source/FBX_Output/)")
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive mode (use default tier: med)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    save_fbx = args.save_fbx
    if args.tiers is not None:
        selected_tiers = resolve_tier_selection(MESH_TIERS, args.tiers, MESH_SETTINGS["default_tiers"])
    elif args.yes:
        selected_tiers = resolve_tier_selection(MESH_TIERS, "", MESH_SETTINGS["default_tiers"])
    else:
        selected_tiers = prompt_quality_tiers("02_SplatMesh_Pack", MESH_TIERS, MESH_SETTINGS["default_tiers"])
        if not save_fbx:
            try:
                ans = input("Save reduced .fbx + _diffuse.png copies to source/FBX_Output/? [y/N]: ").strip().lower()
            except EOFError:
                ans = ""
            save_fbx = ans in ("y", "yes")

    build_mesh_pack(
        repo_root,
        source_dir=args.source_dir,
        tiers=selected_tiers,
        bake_normals=args.bake_normals,
        smooth_iters=args.smooth_iters,
        save_fbx=save_fbx,
        fbx_out_dir=args.fbx_out_dir,
    )


if __name__ == "__main__":
    main()
