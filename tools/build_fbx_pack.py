#!/usr/bin/env python3
"""
Interactive & CLI Builder for Pack 04: `bedrock/04_FBXMesh_Pack` (FBX High-to-Low Poly Bake).

Processes `gem_files/FBX/*.fbx` + `*_diffuse.png` scans into cleaned, single-component
low-poly Bedrock `poly_mesh` entities with tightly packed baked diffuse atlases
(and optional `--bake-normals` tangent-space PBR texture sets).
"""
import argparse
import shutil
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image
from scipy.spatial import cKDTree

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from export_fbx_mesh import (
    bake_high_to_low_textures,
    decimate_and_clean_fbx,
    find_matching_diffuse,
    load_fbx_mesh,
    render_fbx_icon,
    unwrap_connected_charts,
    write_binary_fbx,
    write_fbx_bedrock_geo,
)
from pack_utils import (
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
# Pack 04 Quality & Specification Settings
# ==============================================================================
FBX_SETTINGS = {
    "source_dir": "gem_files/FBX",
    "fbx_out_dir": "gem_files/FBX_Output",
    "pack_dir": "bedrock/04_FBXMesh_Pack",
    "bp_name": "FBXMesh_BP",
    "rp_name": "FBXMesh_RP",
    "default_tiers": ["med"],  # Default to 16k-tri Medium bake per FBX gem
    "target_size": 24.0,
    "smooth_iters": 1,
    "icon_res": 128,
}

FBX_TIERS = [
    {
        "key": "low",
        "label": "Low: 6k tris",
        "tris": 6000,
        "atlas_res": 512,
        "description": "Low (6,000 tris @ 512px baked diffuse)",
        "is_primary": False,
    },
    {
        "key": "med",
        "label": "Med: 16k tris",
        "tris": 16000,
        "atlas_res": 1024,
        "description": "Medium (16,000 tris @ 1024px baked diffuse, recommended)",
        "is_primary": True,
    },
    {
        "key": "high",
        "label": "High: 28k tris",
        "tris": 28000,
        "atlas_res": 2048,
        "description": "High (28,000 tris @ 2048px baked diffuse, high polycount)",
        "is_primary": False,
    },
]


def discover_source_fbxs(source_dir: Path) -> list[Path]:
    if source_dir.exists():
        found = sorted(source_dir.glob("*.fbx"), key=lambda p: p.name.lower())
        if found:
            return found
    raise FileNotFoundError(f"No .fbx files found in {source_dir}")


def build_fbx_pack(
    repo_root: Path,
    source_dir: Path | None = None,
    tiers: list[dict] | None = None,
    bake_normals: bool = False,
    smooth_iters: int = FBX_SETTINGS["smooth_iters"],
    save_fbx: bool = False,
    fbx_out_dir: Path | None = None,
) -> list[dict]:
    t0 = time.time()
    src = source_dir if source_dir is not None else (repo_root / FBX_SETTINGS["source_dir"])
    fbx_export_dir = fbx_out_dir if fbx_out_dir is not None else (repo_root / FBX_SETTINGS["fbx_out_dir"])
    active_tiers = (
        list(tiers)
        if tiers is not None
        else resolve_tier_selection(FBX_TIERS, "", FBX_SETTINGS["default_tiers"])
    )
    fbx_files = discover_source_fbxs(src)

    pack_dir = repo_root / FBX_SETTINGS["pack_dir"]
    bp_name = FBX_SETTINGS["bp_name"]
    rp_name = FBX_SETTINGS["rp_name"]
    bp_dir = pack_dir / bp_name
    rp_dir = pack_dir / rp_name

    print(
        f"\n=== Building Pack 04: 04_FBXMesh_Pack "
        f"({len(fbx_files)} FBXs x {len(active_tiers)} tier(s): {[t['key'] for t in active_tiers]}, "
        f"normals={'on' if bake_normals else 'off'}) ==="
    )

    reset_pack_directory(
        pack_dir,
        preserve_relpaths=[f"{bp_name}/pack_icon.png", f"{rp_name}/pack_icon.png"],
    )

    single_tier = len(active_tiers) == 1
    variants: list[dict] = []
    idx = 0
    for fbx_path in fbx_files:
        display_name, slug_base = format_ply_display_name(fbx_path)
        for tier in active_tiers:
            label = display_name if single_tier else f"{display_name} [{tier['label']}]"
            variants.append(
                {
                    "idx": idx,
                    "slug": slug_base if single_tier else f"{tier['key']}_{slug_base}",
                    "label": label,
                    "fbx_path": fbx_path,
                    "tris": tier["tris"],
                    "atlas_res": tier["atlas_res"],
                    "is_primary": True if single_tier else tier["is_primary"],
                }
            )
            idx += 1

    by_fbx: dict[Path, list[dict]] = {}
    for v in variants:
        by_fbx.setdefault(v["fbx_path"], []).append(v)

    for fbx_path, fbx_variants in by_fbx.items():
        g_t0 = time.time()
        diff_path = find_matching_diffuse(fbx_path)
        hi_v, hi_f, hi_uv, hi_fuv = load_fbx_mesh(fbx_path)
        if hi_uv is None or hi_fuv is None:
            raise ValueError(f"FBX mesh is missing UV data: {fbx_path}")

        with Image.open(diff_path) as im:
            diffuse_rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)

        hi_centroids = (
            hi_v[hi_f[:, 0]] + hi_v[hi_f[:, 1]] + hi_v[hi_f[:, 2]]
        ) * (1.0 / 3.0)
        hi_tree = cKDTree(hi_centroids, leafsize=32)
        print(
            f"  Loaded {fbx_path.name} ({len(hi_v):,} verts, {len(hi_f):,} tris) + "
            f"{diff_path.name} in {time.time() - g_t0:.2f}s"
        )

        for v in fbx_variants:
            i = v["idx"]
            vt0 = time.time()
            low_mesh = decimate_and_clean_fbx(
                hi_v, hi_f, target_tris=v["tris"], smooth_iters=smooth_iters
            )
            low_uv, low_fuv = unwrap_connected_charts(low_mesh, atlas_res=v["atlas_res"])
            diff_img, nrm_img = bake_high_to_low_textures(
                hi_v,
                hi_f,
                hi_uv,
                hi_fuv,
                diff_path,
                low_mesh,
                low_uv,
                low_fuv,
                atlas_res=v["atlas_res"],
                bake_normals=bake_normals,
                hi_tree=hi_tree,
                diffuse_rgb=diffuse_rgb,
            )

            tex_path = rp_dir / "textures" / "entity" / f"fbx_gem_tex_{i}.png"
            tex_path.parent.mkdir(parents=True, exist_ok=True)
            diff_img.save(tex_path, optimize=True)

            if bake_normals and nrm_img is not None:
                nrm_path = rp_dir / "textures" / "entity" / f"fbx_gem_tex_{i}_normal.png"
                nrm_img.save(nrm_path, optimize=True)
                write_json(
                    rp_dir / "textures" / "entity" / f"fbx_gem_tex_{i}.texture_set.json",
                    {
                        "format_version": "1.16.100",
                        "minecraft:texture_set": {
                            "color": f"fbx_gem_tex_{i}",
                            "normal": f"fbx_gem_tex_{i}_normal",
                        },
                    },
                )

            write_fbx_bedrock_geo(
                low_mesh,
                low_uv,
                low_fuv,
                output_geo=rp_dir / "models" / "entity" / f"fbx_gem_{i}.geo.json",
                geometry_id=f"geometry.fbx_gem_{i}",
                atlas_res=v["atlas_res"],
                target_size=FBX_SETTINGS["target_size"],
            )

            if save_fbx:
                out_stem = fbx_path.stem if single_tier else f"{fbx_path.stem}_{v['tris'] // 1000}k"
                write_binary_fbx(
                    low_mesh,
                    low_uv,
                    low_fuv,
                    diff_img,
                    fbx_export_dir / f"{out_stem}.fbx",
                    normal_img=nrm_img,
                )

            icon_img = render_fbx_icon(
                low_mesh, low_uv, low_fuv, diff_img, icon_res=FBX_SETTINGS["icon_res"]
            )
            icon_out = rp_dir / "textures" / "items" / f"fbx_gem_{i}.png"
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
    bp_uuid = "d4044991-61e0-49f1-9c2a-592837164501"
    rp_uuid = "e4044991-61e0-49f1-9c2a-592837164502"

    write_json(
        bp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "04 FBX High-to-Low Mesh Pack BP",
                "description": f"Tier 4: Photogrammetry FBX High-to-Low Poly Bake ({tier_desc})",
                "uuid": bp_uuid,
                "version": [1, 0, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "FBX baked mesh entities and items",
                    "type": "data",
                    "uuid": "d4044991-61e0-49f1-9c2a-592837164503",
                    "version": [1, 0, 0],
                },
                {
                    "description": "Spawn commands and pickup/placement logic",
                    "type": "script",
                    "language": "javascript",
                    "uuid": "d4044991-61e0-49f1-9c2a-592837164504",
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
            "name": "04 FBX High-to-Low Mesh Pack RP",
            "description": f"Tier 4: Photogrammetry FBX High-to-Low Poly Bake ({tier_desc})",
            "uuid": rp_uuid,
            "version": [1, 0, 0],
            "min_engine_version": [1, 21, 0],
        },
        "modules": [
            {
                "description": "Baked low-poly FBX meshes and textures",
                "type": "resources",
                "uuid": "e4044991-61e0-49f1-9c2a-592837164505",
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
        bp_dir / "entities" / "fbx_gem.json",
        identifier="custom:fbx_gem",
        variant_count=len(variants),
        default_variant=primary_variant,
        collision_width=0.9,
        collision_height=1.2,
    )

    for v in variants:
        i = v["idx"]
        write_json(
            bp_dir / "items" / f"fbx_gem_item_{i}.json",
            {
                "format_version": "1.20.50",
                "minecraft:item": {
                    "description": {"identifier": f"custom:fbx_gem_item_{i}", "menu_category": {"category": "items"}},
                    "components": {
                        "minecraft:max_stack_size": 16,
                        "minecraft:icon": {"texture": f"fbx_gem_{i}"},
                        "minecraft:display_name": {"value": f"✦ {v['label']}"},
                    },
                },
            },
        )
    write_catalog_item_json(bp_dir, "fbxmesh_menu", f"fbx_gem_{primary_variant}", "FBX Mesh Gem Catalog")

    (bp_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (bp_dir / "scripts" / "main.js").write_text(
        generate_pack_script_js(
            pack_title="04_FBXMesh_Pack (FBX High-to-Low Bake)",
            pack_subtitle=f"Decimated Single-Component FBX Mesh + Baked Diffuse ({tier_desc})",
            cmd_prefix="fbxmesh",
            entity_id="custom:fbx_gem",
            item_prefix="custom:fbx_gem_item_",
            icon_prefix="textures/items/fbx_gem_",
            menu_item_id="custom:fbxmesh_menu",
            catalog_name="FBX Mesh Gem Catalog",
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
                "fbx_gem_opaque:entity_alphatest": {
                    "-defines": ["ENABLE_FOG"],
                    "+states": ["DisableCulling"],
                },
            }
        },
    )

    write_json(
        rp_dir / "render_controllers" / "fbx_gem.render_controllers.json",
        {
            "format_version": "1.8.0",
            "render_controllers": {
                "controller.render.fbx_gem": {
                    "arrays": {
                        "geometries": {"Array.gem_geos": [f"Geometry.gem_{i}" for i in range(len(variants))]},
                        "textures": {"Array.gem_texs": [f"Texture.gem_{i}" for i in range(len(variants))]},
                    },
                    "geometry": "Array.gem_geos[query.variant]",
                    "materials": [{"*": "Material.default"}],
                    "textures": ["Array.gem_texs[query.variant]"],
                },
                "controller.render.fbx_gem_attachable": {
                    "geometry": "Geometry.default",
                    "materials": [{"*": "Material.default"}],
                    "textures": ["Texture.default"],
                },
            },
        },
    )

    tex_map = {"default": f"textures/entity/fbx_gem_tex_{primary_variant}"}
    geo_map = {"default": f"geometry.fbx_gem_{primary_variant}"}
    for v in variants:
        i = v["idx"]
        tex_map[f"gem_{i}"] = f"textures/entity/fbx_gem_tex_{i}"
        geo_map[f"gem_{i}"] = f"geometry.fbx_gem_{i}"

    write_json(
        rp_dir / "entity" / "fbx_gem.entity.json",
        {
            "format_version": "1.10.0",
            "minecraft:client_entity": {
                "description": {
                    "identifier": "custom:fbx_gem",
                    "materials": {"default": "fbx_gem_opaque"},
                    "textures": tex_map,
                    "geometry": geo_map,
                    "render_controllers": ["controller.render.fbx_gem"],
                }
            },
        },
    )

    write_json(
        rp_dir / "animations" / "fbx_gem.animation.json",
        {
            "format_version": "1.8.0",
            "animations": {
                "animation.fbx_gem.held_and_dropped": {
                    "loop": True,
                    "bones": {"root": {"scale": 0.45, "position": [0, 3, 0]}},
                }
            },
        },
    )

    item_tex_data = {f"fbx_gem_{v['idx']}": {"textures": f"textures/items/fbx_gem_{v['idx']}"} for v in variants}
    write_json(
        rp_dir / "textures" / "item_texture.json",
        {
            "resource_pack_name": "04 FBX High-to-Low Mesh Pack RP",
            "texture_name": "atlas.items",
            "texture_data": item_tex_data,
        },
    )

    lang_lines = ["entity.custom:fbx_gem.name=FBX High-to-Low Gem"]
    for v in variants:
        lang_lines.append(f"item.custom:fbx_gem_item_{v['idx']}.name=✦ {v['label']}")
    lang_lines.append("item.custom:fbxmesh_menu.name=✦ FBX Mesh Gem Catalog")
    (rp_dir / "texts").mkdir(parents=True, exist_ok=True)
    (rp_dir / "texts" / "en_US.lang").write_text("\n".join(lang_lines) + "\n", encoding="utf-8")
    write_install_cmd(pack_dir, bp_name, rp_name, "04 FBX High-to-Low Mesh Pack")

    print(f"✓ Built 04_FBXMesh_Pack ({len(variants)} variants) in {time.time() - t0:.2f}s")
    return variants


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Pack 04: 04_FBXMesh_Pack from gem_files/FBX/*.fbx")
    parser.add_argument("--source-dir", type=Path, default=None, help="Directory containing .fbx files (default: gem_files/FBX/)")
    parser.add_argument("--tiers", type=str, default=None, help="Comma-separated tiers: low, med, high, or all")
    parser.add_argument("--bake-normals", action="store_true", help="Bake tangent-space normal maps + PBR texture_set.json")
    parser.add_argument("--smooth-iters", type=int, default=FBX_SETTINGS["smooth_iters"], help="Taubin smoothing iterations (default: 1)")
    parser.add_argument("--save-fbx", action="store_true", help="Also save reduced .fbx + _diffuse.png to gem_files/FBX_Output/")
    parser.add_argument("--fbx-out-dir", type=Path, default=None, help="Output folder for --save-fbx (default: gem_files/FBX_Output/)")
    parser.add_argument("-y", "--yes", action="store_true", help="Non-interactive mode (use default tier: med)")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    save_fbx = args.save_fbx
    if args.tiers is not None:
        selected_tiers = resolve_tier_selection(FBX_TIERS, args.tiers, FBX_SETTINGS["default_tiers"])
    elif args.yes:
        selected_tiers = resolve_tier_selection(FBX_TIERS, "", FBX_SETTINGS["default_tiers"])
    else:
        selected_tiers = prompt_quality_tiers("04_FBXMesh_Pack", FBX_TIERS, FBX_SETTINGS["default_tiers"])
        if not save_fbx:
            try:
                ans = input("Save reduced .fbx + _diffuse.png copies to gem_files/FBX_Output/? [y/N]: ").strip().lower()
            except EOFError:
                ans = ""
            save_fbx = ans in ("y", "yes")

    build_fbx_pack(
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
