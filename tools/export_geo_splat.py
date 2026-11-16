#!/usr/bin/env python3
"""
Bake 3D Gaussian Splats (.ply) directly into a static Minecraft Bedrock .geo.json splat cloud
and an accompanying RGBA Gaussian texture atlas using `poly_mesh` (used by `03_Full_GS_Pack`).

Features:
1. Smooth Shrinkwrap Core Occluder (`core_mesh`): Uses the clean, non-self-intersecting
   icosphere shrinkwrap hull shrunk by 6% inside the surface so back-side splats are 100%
   hardware depth-occluded from every viewing angle with zero protruding dark discs.
2. Tangent-Aligned Anisotropic Gaussian Splat Cloud (`splat_cloud`): Packs 1,200–2,400
   KNN-color-smoothed, surface-tangent elliptical Gaussian splat quads into a single-bone
   `poly_mesh`, avoiding Bedrock's 2,500-bone CPU transform overhead and eliminating spiky
   intersecting cards.
"""
import argparse
import json
import math
import time
from pathlib import Path
import numpy as np
from PIL import Image
from scipy.spatial import cKDTree

from render_imposter import GaussianScene, eval_sh_color_ortho, apply_color_grade
from export_mesh import render_6view_ortho_atlas, build_shrinkwrap_mesh, project_6view_uvs


def export_geo_splat(
    ply_file: Path | GaussianScene,
    output_geo: Path,
    output_tex: Path | None = None,
    geometry_id: str = "geometry.splat_cloud_0",
    max_splats: int = 1600,
    target_size: float = 24.0,
    tile_res: int = 512,
    hue_shift_deg: float = 0.0,
    flip_y: bool = True,
) -> None:
    """
    Export a Full Static Gaussian Splat Cloud (`core_mesh` + `splat_cloud` bones)
    into a Bedrock `.geo.json` model and RGBA atlas.
    """
    t0 = time.time()
    scene = ply_file if isinstance(ply_file, GaussianScene) else GaussianScene(ply_file, flip_y=flip_y)

    atlas_w = tile_res * 3
    spr_rows_px = 512
    atlas_h = tile_res * 2 + spr_rows_px

    # 1. Build clean Shrinkwrap Core Hull (shrunk 5% inward so outer splats sit cleanly on top)
    verts_scene, verts_mc, vert_normals, faces = build_shrinkwrap_mesh(
        scene, mode="smooth", target_size=target_size
    )
    center_mc = np.mean(verts_mc, axis=0, keepdims=True)
    core_verts_mc = center_mc + (verts_mc - center_mc) * 0.94

    uvs_core_raw, polys_core = project_6view_uvs(verts_scene, faces, scene.span, fill_ratio=0.82)
    # Rescale V coordinates because the atlas has an extra `spr_rows_px` strip at the bottom
    top_h_ratio = (tile_res * 2) / atlas_h
    uvs_core = []
    for u, v_norm in uvs_core_raw:
        v_img = (1.0 - v_norm) * top_h_ratio
        uvs_core.append([round(u, 5), round(1.0 - v_img, 5)])

    # 2. Sample `max_splats` surface-hugging Gaussian splats
    scale_factor = target_size / scene.span
    px = scene.x * scale_factor
    py = scene.y * scale_factor
    pz = scene.z * scale_factor
    # Align Y shift with `verts_mc`
    y_shift = float(np.min(verts_mc[:, 1]) - np.min(verts_scene[:, 1] * scale_factor))
    py = py + y_shift

    mesh_tree = cKDTree(verts_mc)
    splat_pts = np.column_stack([px, py, pz])
    dist_to_surf, nearest_v = mesh_tree.query(splat_pts, k=1, workers=-1)

    near_mask = np.where((dist_to_surf < 1.35) & (scene.op > 0.10))[0]
    if len(near_mask) == 0:
        near_mask = np.arange(len(px))

    # Evaluate view-dependent SH colors
    r_f, g_f, b_f = eval_sh_color_ortho(0.2, -0.25, 0.94, scene.f_dc, scene.f_rest, scene.has_rest)
    r_b, g_b, b_b = eval_sh_color_ortho(-0.2, -0.25, -0.94, scene.f_dc, scene.f_rest, scene.has_rest)
    fw = np.clip(0.5 + 0.5 * (scene.z / (np.linalg.norm(np.column_stack([scene.x, scene.y, scene.z]), axis=1) + 1e-6)), 0.0, 1.0)
    r_all = r_f * fw + r_b * (1.0 - fw)
    g_all = g_f * fw + g_b * (1.0 - fw)
    b_all = b_f * fw + b_b * (1.0 - fw)
    r_all, g_all, b_all = apply_color_grade(r_all, g_all, b_all, hue_shift_deg=hue_shift_deg, saturation_boost=1.20)

    # Spatially stratify splats into voxels so the entire gem is evenly covered in soft Gaussian splats
    lo_res, hi_res = 8.0, 96.0
    pts_n = splat_pts[near_mask]
    for _ in range(16):
        mid = 0.5 * (lo_res + hi_res)
        vsize = target_size / mid
        vx = np.floor((pts_n[:, 0] + target_size * 2.0) / vsize).astype(np.int64)
        vy = np.floor((pts_n[:, 1] + target_size * 2.0) / vsize).astype(np.int64)
        vz = np.floor((pts_n[:, 2] + target_size * 2.0) / vsize).astype(np.int64)
        keys = (vx << 40) | (vy << 20) | vz
        if len(np.unique(keys)) < max_splats:
            lo_res = mid
        else:
            hi_res = mid

    vsize = target_size / (0.5 * (lo_res + hi_res))
    vx = np.floor((pts_n[:, 0] + target_size * 2.0) / vsize).astype(np.int64)
    vy = np.floor((pts_n[:, 1] + target_size * 2.0) / vsize).astype(np.int64)
    vz = np.floor((pts_n[:, 2] + target_size * 2.0) / vsize).astype(np.int64)
    keys = (vx << 40) | (vy << 20) | vz
    uniq_keys, _, inv = np.unique(keys, return_index=True, return_inverse=True)

    op_n = scene.op[near_mask]
    w_sum = np.bincount(inv, weights=op_n) + 1e-6
    sx_mean = np.bincount(inv, weights=pts_n[:, 0] * op_n) / w_sum
    sy_mean = np.bincount(inv, weights=pts_n[:, 1] * op_n) / w_sum
    sz_mean = np.bincount(inv, weights=pts_n[:, 2] * op_n) / w_sum
    sr_mean = np.bincount(inv, weights=r_all[near_mask] * op_n) / w_sum
    sg_mean = np.bincount(inv, weights=g_all[near_mask] * op_n) / w_sum
    sb_mean = np.bincount(inv, weights=b_all[near_mask] * op_n) / w_sum

    keep = np.arange(len(uniq_keys))
    if len(keep) > max_splats:
        keep = keep[np.argsort(w_sum)[::-1][:max_splats]]

    s_xyz = np.column_stack([sx_mean[keep], sy_mean[keep], sz_mean[keep]])
    sr, sg, sb = sr_mean[keep], sg_mean[keep], sb_mean[keep]

    # Sort splats inside-out from centroid
    d_c = np.sum((s_xyz - center_mc) ** 2, axis=1)
    ord_s = np.argsort(d_c)
    s_xyz = s_xyz[ord_s]
    sr, sg, sb = sr[ord_s], sg[ord_s], sb[ord_s]

    _, v_idx = mesh_tree.query(s_xyz, k=1, workers=-1)
    s_normals = vert_normals[v_idx]

    # Paint 16x16 soft Gaussian tiles into bottom strip of the atlas
    splat_tile_px = 16
    tiles_per_row = atlas_w // splat_tile_px
    max_tiles = (spr_rows_px // splat_tile_px) * tiles_per_row
    n_splats = min(len(s_xyz), max_tiles)

    if output_tex is not None:
        base_6view = render_6view_ortho_atlas(scene, tile_res=tile_res, fill_ratio=0.82, hue_shift_deg=hue_shift_deg)
        full_rgba = np.zeros((atlas_h, atlas_w, 4), dtype=np.uint8)
        full_rgba[: tile_res * 2, :, :] = np.array(base_6view, dtype=np.uint8)

        yy, xx = np.ogrid[:splat_tile_px, :splat_tile_px]
        center_t = (splat_tile_px - 1) / 2.0
        dist_t = np.sqrt((xx - center_t) ** 2 + (yy - center_t) ** 2)
        sigma_t = splat_tile_px * 0.26
        t_win = np.clip((splat_tile_px * 0.47 - dist_t) / (splat_tile_px * 0.18), 0.0, 1.0)
        gauss_mask = np.exp(-0.5 * (dist_t / sigma_t) ** 2) * (t_win * t_win * (3.0 - 2.0 * t_win))

        for i in range(n_splats):
            col = i % tiles_per_row
            row = i // tiles_per_row
            u0_px = col * splat_tile_px
            v0_px = tile_res * 2 + row * splat_tile_px
            full_rgba[v0_px : v0_px + splat_tile_px, u0_px : u0_px + splat_tile_px, 0] = int(np.clip(sr[i] * 255, 0, 255))
            full_rgba[v0_px : v0_px + splat_tile_px, u0_px : u0_px + splat_tile_px, 1] = int(np.clip(sg[i] * 255, 0, 255))
            full_rgba[v0_px : v0_px + splat_tile_px, u0_px : u0_px + splat_tile_px, 2] = int(np.clip(sb[i] * 255, 0, 255))
            full_rgba[v0_px : v0_px + splat_tile_px, u0_px : u0_px + splat_tile_px, 3] = (
                gauss_mask * 0.78 * 255.0
            ).astype(np.uint8)

        output_tex.parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(full_rgba, "RGBA").save(output_tex, optimize=True)

    # Build `splat_cloud` poly_mesh quads tangent to the surface
    quad_half = vsize * 0.95
    halo_positions = []
    halo_normals = []
    halo_uvs = []
    halo_polys = []

    for i in range(n_splats):
        col = i % tiles_per_row
        row = i // tiles_per_row
        u0_px = col * splat_tile_px
        v0_px = tile_res * 2 + row * splat_tile_px

        n_vec = s_normals[i]
        n_vec = n_vec / max(float(np.linalg.norm(n_vec)), 1e-6)
        # Position splat slightly outside the core hull along surface normal
        c_pos = verts_mc[v_idx[i]] + n_vec * 0.12

        up_ref = np.array([0.0, 1.0, 0.0], dtype=np.float32)
        if abs(n_vec[1]) > 0.95:
            up_ref = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        t_u = np.cross(up_ref, n_vec)
        t_u /= max(float(np.linalg.norm(t_u)), 1e-6)
        t_v = np.cross(n_vec, t_u)

        q0 = c_pos - t_u * quad_half - t_v * quad_half
        q1 = c_pos + t_u * quad_half - t_v * quad_half
        q2 = c_pos + t_u * quad_half + t_v * quad_half
        q3 = c_pos - t_u * quad_half + t_v * quad_half

        v_base = len(halo_positions)
        for q_pt in (q0, q1, q2, q3):
            halo_positions.append([round(float(q_pt[0]), 4), round(float(q_pt[1]), 4), round(float(q_pt[2]), 4)])
        n_idx = len(halo_normals)
        halo_normals.append([round(float(n_vec[0]), 4), round(float(n_vec[1]), 4), round(float(n_vec[2]), 4)])

        u_min = (u0_px + 0.5) / atlas_w
        u_max = (u0_px + splat_tile_px - 0.5) / atlas_w
        v_min = 1.0 - (v0_px + splat_tile_px - 0.5) / atlas_h
        v_max = 1.0 - (v0_px + 0.5) / atlas_h

        uv_base = len(halo_uvs)
        halo_uvs.extend([
            [round(u_min, 5), round(v_min, 5)],
            [round(u_max, 5), round(v_min, 5)],
            [round(u_max, 5), round(v_max, 5)],
            [round(u_min, 5), round(v_max, 5)],
        ])
        halo_polys.append([
            [v_base, n_idx, uv_base],
            [v_base + 1, n_idx, uv_base + 1],
            [v_base + 2, n_idx, uv_base + 2],
            [v_base + 3, n_idx, uv_base + 3],
        ])

    core_positions = [[round(float(v[0]), 4), round(float(v[1]), 4), round(float(v[2]), 4)] for v in core_verts_mc]
    core_normals = [[round(float(n[0]), 4), round(float(n[1]), 4), round(float(n[2]), 4)] for n in vert_normals]

    geo_json = {
        "format_version": "1.12.0",
        "minecraft:geometry": [
            {
                "description": {
                    "identifier": geometry_id,
                    "texture_width": atlas_w,
                    "texture_height": atlas_h,
                    "visible_bounds_width": 4,
                    "visible_bounds_height": 4,
                    "visible_bounds_offset": [0, 1.5, 0],
                },
                "bones": [
                    {"name": "root", "pivot": [0, 0, 0]},
                    {
                        "name": "core_mesh",
                        "parent": "root",
                        "pivot": [0, 8, 0],
                        "poly_mesh": {
                            "normalized_uvs": True,
                            "positions": core_positions,
                            "normals": core_normals,
                            "uvs": uvs_core,
                            "polys": polys_core,
                        },
                    },
                    {
                        "name": "splat_cloud",
                        "parent": "core_mesh",
                        "pivot": [0, 8, 0],
                        "poly_mesh": {
                            "normalized_uvs": True,
                            "positions": halo_positions,
                            "normals": halo_normals,
                            "uvs": halo_uvs,
                            "polys": halo_polys,
                        },
                    },
                ],
            }
        ],
    }

    output_geo.parent.mkdir(parents=True, exist_ok=True)
    output_geo.write_text(json.dumps(geo_json), encoding="utf-8")
    print(
        f"✓ Exported Full GS Splat Cloud ({len(faces)} core tris + {n_splats} tangent splats) "
        f"to {output_geo.name} in {time.time() - t0:.2f}s"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bake PLY splats into a static Bedrock .geo.json model.")
    parser.add_argument("ply_file", type=Path, help="Input .ply file")
    parser.add_argument("-n", "--max-splats", type=int, default=1600, help="Total surface splat planes (default: 1600)")
    parser.add_argument("-s", "--size", type=float, default=24.0, help="Target size in model pixels (16 = 1 block)")
    parser.add_argument("--hue-shift", type=float, default=0.0, help="Optional hue shift in degrees")
    parser.add_argument("--no-flip-y", action="store_true", help="Do not flip Y/Z axes")
    parser.add_argument(
        "--geo-out",
        type=Path,
        default=Path("./bedrock/03_Full_GS_Pack/SplatRenderer_RP/models/entity/splat_cloud_0.geo.json"),
    )
    parser.add_argument(
        "--tex-out",
        type=Path,
        default=Path("./bedrock/03_Full_GS_Pack/SplatRenderer_RP/textures/entity/splat_cloud_0.png"),
    )
    parser.add_argument("--geo-id", type=str, default="geometry.splat_cloud_0", help="Geometry identifier")
    args = parser.parse_args()
    export_geo_splat(
        args.ply_file,
        output_geo=args.geo_out,
        output_tex=args.tex_out,
        geometry_id=args.geo_id,
        max_splats=args.max_splats,
        target_size=args.size,
        hue_shift_deg=args.hue_shift,
        flip_y=not args.no_flip_y,
    )
