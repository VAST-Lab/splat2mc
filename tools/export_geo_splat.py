#!/usr/bin/env python3
"""
Bake 3D Gaussian Splats (.ply) directly into a static Minecraft Bedrock .geo.json model
and an accompanying high-resolution RGBA Gaussian texture atlas.
Generates an inset depth-writing core shell (core_*) for all surface voxels so
back-side splats are hardware-occluded from every 360-degree angle, paired with
smooth feathered Gaussian surface splats (s_*) with zero square-edge clipping.
"""
import json
import math
import argparse
from pathlib import Path
import numpy as np
from PIL import Image
from plyfile import PlyData


def rotation_matrix_to_bedrock_euler(R_plane: np.ndarray) -> tuple[float, float, float]:
    """
    Convert a 3x3 column-major basis matrix [axis_u, axis_v, normal] in Minecraft space
    into Bedrock bone Euler angles [rx, ry, rz] (in degrees).
    """
    sy = math.sqrt(R_plane[0, 0] ** 2 + R_plane[1, 0] ** 2)
    singular = sy < 1e-6
    if not singular:
        rx = math.atan2(R_plane[2, 1], R_plane[2, 2])
        ry = math.atan2(-R_plane[2, 0], sy)
        rz = math.atan2(R_plane[1, 0], R_plane[0, 0])
    else:
        rx = math.atan2(-R_plane[1, 2], R_plane[1, 1])
        ry = math.atan2(-R_plane[2, 0], sy)
        rz = 0.0
    return -math.degrees(rx), -math.degrees(ry), math.degrees(rz)


def compute_local_inward_vectors(pts: np.ndarray, radius: float) -> np.ndarray:
    """Compute unit inward direction toward local occupied neighborhood center-of-mass."""
    n = len(pts)
    inward = np.zeros_like(pts)
    r2 = radius * radius
    chunk = 512
    for start in range(0, n, chunk):
        end = min(start + chunk, n)
        diff = pts[None, :, :] - pts[start:end, None, :]
        d2 = np.sum(diff * diff, axis=-1)
        mask = d2 < r2
        counts = np.maximum(mask.sum(axis=1, keepdims=True), 1)
        mean_vec = (mask[:, :, None] * diff).sum(axis=1) / counts
        norms = np.linalg.norm(mean_vec, axis=1, keepdims=True)
        valid_dir = norms[:, 0] > (0.04 * radius)
        inward[start:end][valid_dir] = mean_vec[valid_dir] / norms[valid_dir]
    return inward


def build_splat_bone(
    name: str,
    px_k: float,
    py_k: float,
    pz_k: float,
    w_size: float,
    h_size: float,
    rot_x: float,
    rot_y: float,
    rot_z: float,
    u: int,
    v_uv: int,
    tile_px: int,
) -> dict:
    """Create a single-quad Bedrock bone (rendered double-sided via DisableCulling)."""
    return {
        "name": name,
        "parent": "root",
        "pivot": [round(px_k, 3), round(py_k, 3), round(pz_k, 3)],
        "rotation": [round(rot_x, 2), round(rot_y, 2), round(rot_z, 2)],
        "cubes": [
            {
                "origin": [round(px_k - w_size / 2, 3), round(py_k - h_size / 2, 3), round(pz_k, 3)],
                "size": [round(w_size, 3), round(h_size, 3), 0],
                "uv": {
                    "north": {"uv": [u, v_uv], "uv_size": [tile_px, tile_px]},
                },
            }
        ],
    }


def export_geo_splat(
    ply_file: Path,
    output_geo: Path,
    output_tex: Path,
    max_splats: int = 2500,
    target_size: float = 48.0,
    flip_y: bool = True,
):
    plydata = PlyData.read(str(ply_file))
    v = plydata["vertex"]
    x = np.array(v["x"], dtype=np.float64)
    y = np.array(v["y"], dtype=np.float64)
    z = np.array(v["z"], dtype=np.float64)

    # 1. Extract Colors in native 3DGS sRGB space (no extra 1/2.2 gamma washout)
    C0 = 0.28209479177387814
    if "f_dc_0" in v.data.dtype.names:
        r = np.clip(0.5 + np.array(v["f_dc_0"], dtype=np.float64) * C0, 0.0, 1.0)
        g = np.clip(0.5 + np.array(v["f_dc_1"], dtype=np.float64) * C0, 0.0, 1.0)
        b = np.clip(0.5 + np.array(v["f_dc_2"], dtype=np.float64) * C0, 0.0, 1.0)
    else:
        r = np.array(v["red"], dtype=np.float64) / 255.0
        g = np.array(v["green"], dtype=np.float64) / 255.0
        b = np.array(v["blue"], dtype=np.float64) / 255.0

    opacity = (
        1.0 / (1.0 + np.exp(-np.array(v["opacity"], dtype=np.float64)))
        if "opacity" in v.data.dtype.names
        else np.ones_like(x)
    )
    sx = np.exp(np.array(v["scale_0"], dtype=np.float64))
    sy = np.exp(np.array(v["scale_1"], dtype=np.float64))
    sz = np.exp(np.array(v["scale_2"], dtype=np.float64))
    s_max = np.maximum(np.maximum(sx, sy), sz)

    rw = np.array(v["rot_0"], dtype=np.float64)
    rx = np.array(v["rot_1"], dtype=np.float64)
    ry = np.array(v["rot_2"], dtype=np.float64)
    rz = np.array(v["rot_3"], dtype=np.float64)
    norms = np.sqrt(rw**2 + rx**2 + ry**2 + rz**2)
    norms = np.where(norms == 0, 1.0, norms)
    rw, rx, ry, rz = rw / norms, rx / norms, ry / norms, rz / norms

    # 2. Filter out oversized interior/background filler Gaussians (> 85th percentile scale)
    scale_cap = np.percentile(s_max, 85)
    valid = np.where((opacity > 0.08) & (s_max <= scale_cap))[0]
    if len(valid) == 0:
        valid = np.arange(len(x))

    chroma_valid = np.maximum(np.maximum(r[valid], g[valid]), b[valid]) - np.minimum(
        np.minimum(r[valid], g[valid]), b[valid]
    )
    importance = opacity[valid] * (1.0 + 1.5 * chroma_valid)
    valid = valid[np.argsort(importance)[::-1]]

    min_x, max_x = np.percentile(x[valid], 2), np.percentile(x[valid], 98)
    min_y, max_y = np.percentile(y[valid], 2), np.percentile(y[valid], 98)
    min_z, max_z = np.percentile(z[valid], 2), np.percentile(z[valid], 98)
    scale_factor = target_size / max(max_x - min_x or 1.0, max_y - min_y or 1.0, max_z - min_z or 1.0)
    cx, cy, cz = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0, (min_z + max_z) / 2.0

    y_sign = -1.0 if flip_y else 1.0
    px = (x[valid] - cx) * scale_factor
    py = y_sign * (y[valid] - cy) * scale_factor
    pz = -(z[valid] - cz) * scale_factor

    # 3. Voxelize to half of max_splats so every voxel gets an inset depth-writing core
    #    bone + a smooth outer Gaussian surface bone (preserving 360-deg Z occlusion).
    voxel_budget = max(64, max_splats // 2)
    lo_res, hi_res = 6.0, 96.0
    for _ in range(18):
        mid_res = (lo_res + hi_res) * 0.5
        vsize = target_size / mid_res
        vx = np.floor((px + target_size * 4.0) / vsize).astype(np.int64)
        vy = np.floor((py + target_size * 4.0) / vsize).astype(np.int64)
        vz = np.floor((pz + target_size * 4.0) / vsize).astype(np.int64)
        keys = (vx << 40) | (vy << 20) | vz
        if len(np.unique(keys)) < voxel_budget:
            lo_res = mid_res
        else:
            hi_res = mid_res

    vsize = target_size / ((lo_res + hi_res) * 0.5)
    vx = np.floor((px + target_size * 4.0) / vsize).astype(np.int64)
    vy = np.floor((py + target_size * 4.0) / vsize).astype(np.int64)
    vz = np.floor((pz + target_size * 4.0) / vsize).astype(np.int64)
    keys = (vx << 40) | (vy << 20) | vz
    uniq_keys, first_idx, inv = np.unique(keys, return_index=True, return_inverse=True)

    rv, gv, bv, opv = r[valid], g[valid], b[valid], opacity[valid]
    w = opv * (1.0 + 1.5 * chroma_valid)
    w_sum = np.bincount(inv, weights=w)
    op_sum = np.bincount(inv, weights=opv)

    vx_mean = np.bincount(inv, weights=px * w) / w_sum
    vy_mean = np.bincount(inv, weights=py * w) / w_sum
    vz_mean = np.bincount(inv, weights=pz * w) / w_sum
    vr = np.bincount(inv, weights=rv * w) / w_sum
    vg = np.bincount(inv, weights=gv * w) / w_sum
    vb = np.bincount(inv, weights=bv * w) / w_sum

    # Vibrance boost (1.30x) and gentle brightness lift (1.12x)
    lum = 0.299 * vr + 0.587 * vg + 0.114 * vb
    vr = np.clip((lum + (vr - lum) * 1.30) * 1.12, 0.0, 1.0)
    vg = np.clip((lum + (vg - lum) * 1.30) * 1.12, 0.0, 1.0)
    vb = np.clip((lum + (vb - lum) * 1.30) * 1.12, 0.0, 1.0)
    v_op = np.clip(0.82 + 0.16 * (1.0 - np.exp(-op_sum)), 0.82, 0.98)

    keep = np.arange(len(uniq_keys))
    if len(keep) > voxel_budget:
        keep = keep[np.argsort(op_sum[keep])[::-1][:voxel_budget]]

    # Shift Y so model base sits cleanly on ground (min Y = 1.0 model pixel)
    vy_mean = vy_mean - np.min(vy_mean[keep]) + 1.0

    # Compute local inward vector for each voxel to separate Z-writing core from outer surface
    pts = np.column_stack([vx_mean[keep], vy_mean[keep], vz_mean[keep]])
    inward = compute_local_inward_vectors(pts, radius=2.4 * vsize)
    core_pts = pts + inward * (0.65 * vsize)
    surf_pts = pts - inward * (0.20 * vsize)

    # Sort surface splats inside-out from centroid
    c_x, c_y, c_z = np.mean(surf_pts[:, 0]), np.mean(surf_pts[:, 1]), np.mean(surf_pts[:, 2])
    dist_sq = (surf_pts[:, 0] - c_x) ** 2 + (surf_pts[:, 1] - c_y) ** 2 + (surf_pts[:, 2] - c_z) ** 2
    surf_order = np.argsort(dist_sq)

    # Build 32x32 per-splat RGBA atlas:
    # - Core tiles use a crisp circular disc (for entity_alphatest Z-buffer occlusion)
    # - Surface tiles use a smooth circular Gaussian fading strictly to alpha=0 at border
    n_voxels = len(keep)
    total_tiles = n_voxels * 2
    grid_dim = math.ceil(math.sqrt(total_tiles))
    tile_px = 32
    atlas_px = grid_dim * tile_px
    atlas = Image.new("RGBA", (atlas_px, atlas_px), (0, 0, 0, 0))

    yy, xx = np.ogrid[:tile_px, :tile_px]
    center = (tile_px - 1) / 2.0
    dist = np.sqrt((xx - center) ** 2 + (yy - center) ** 2)
    sigma = tile_px * 0.28
    t_win = np.clip((tile_px * 0.48 - dist) / (tile_px * 0.18), 0.0, 1.0)
    smooth_win = t_win * t_win * (3.0 - 2.0 * t_win)
    gauss_mask = np.exp(-0.5 * (dist / sigma) ** 2) * smooth_win
    core_mask = (dist <= tile_px * 0.42).astype(np.float64)

    bones = [{"name": "root", "pivot": [0, 0, 0]}]
    core_size = vsize * 2.25
    base_surf_s = vsize * 4.0

    def emit_layer(order_idx: np.ndarray, layer_pts: np.ndarray, prefix: str, mask: np.ndarray, is_core: bool, start_idx: int):
        for offset, local_i in enumerate(order_idx):
            k = keep[local_i]
            idx_pos = start_idx + offset
            orig_i = valid[first_idx[k]]
            col = idx_pos % grid_dim
            row = idx_pos // grid_dim
            u, v_uv = col * tile_px, row * tile_px

            tile_rgba = np.zeros((tile_px, tile_px, 4), dtype=np.uint8)
            tile_rgba[..., 0] = int(vr[k] * 255)
            tile_rgba[..., 1] = int(vg[k] * 255)
            tile_rgba[..., 2] = int(vb[k] * 255)
            alpha_scale = 1.0 if is_core else v_op[k]
            tile_rgba[..., 3] = np.clip(mask * alpha_scale * 255, 0, 255).astype(np.uint8)
            atlas.paste(Image.fromarray(tile_rgba, "RGBA"), (u, v_uv))

            w_q, i_x, i_y, i_z = rw[orig_i], rx[orig_i], ry[orig_i], rz[orig_i]
            R = np.array([
                [1 - 2 * (i_y**2 + i_z**2), 2 * (i_x * i_y - w_q * i_z), 2 * (i_x * i_z + w_q * i_y)],
                [2 * (i_x * i_y + w_q * i_z), 1 - 2 * (i_x**2 + i_z**2), 2 * (i_y * i_z - w_q * i_x)],
                [2 * (i_x * i_z - w_q * i_y), 2 * (i_y * i_z + w_q * i_x), 1 - 2 * (i_x**2 + i_y**2)],
            ])
            R[1, :] = y_sign * R[1, :]
            R[2, :] = -R[2, :]

            scales = np.array([sx[orig_i], sy[orig_i], sz[orig_i]]) * scale_factor
            min_idx = int(np.argmin(scales))
            axes = [0, 1, 2]
            axes.remove(min_idx)

            axis_u = R[:, axes[0]]
            axis_v = R[:, axes[1]]
            normal = np.cross(axis_u, axis_v)
            norm_len = np.linalg.norm(normal)
            if norm_len > 1e-6:
                normal = normal / norm_len

            R_plane = np.column_stack((axis_u, axis_v, normal))
            rot_x, rot_y, rot_z = rotation_matrix_to_bedrock_euler(R_plane)

            if is_core:
                w_size = h_size = core_size
            else:
                aspect = float(np.clip(scales[axes[0]] / max(scales[axes[1]], 1e-8), 0.55, 1.8))
                w_size = base_surf_s * math.sqrt(aspect)
                h_size = base_surf_s / math.sqrt(aspect)

            bones.append(
                build_splat_bone(
                    f"{prefix}{offset}",
                    float(layer_pts[local_i, 0]),
                    float(layer_pts[local_i, 1]),
                    float(layer_pts[local_i, 2]),
                    w_size,
                    h_size,
                    rot_x,
                    rot_y,
                    rot_z,
                    u,
                    v_uv,
                    tile_px,
                )
            )

    emit_layer(np.arange(n_voxels), core_pts, "core_", core_mask, is_core=True, start_idx=0)
    emit_layer(surf_order, surf_pts, "s_", gauss_mask, is_core=False, start_idx=n_voxels)

    geo_json = {
        "format_version": "1.12.0",
        "minecraft:geometry": [
            {
                "description": {
                    "identifier": "geometry.splat_cloud",
                    "texture_width": atlas_px,
                    "texture_height": atlas_px,
                    "visible_bounds_width": 8,
                    "visible_bounds_height": 8,
                    "visible_bounds_offset": [0, 2, 0],
                },
                "bones": bones,
            }
        ],
    }

    output_geo.parent.mkdir(parents=True, exist_ok=True)
    output_tex.parent.mkdir(parents=True, exist_ok=True)
    output_geo.write_text(json.dumps(geo_json, indent=2), encoding="utf-8")
    atlas.save(output_tex)
    print(
        f"✓ Exported {n_voxels} inset core + {n_voxels} smooth Gaussian surface planes "
        f"({atlas_px}x{atlas_px} atlas) to {output_geo} and {output_tex}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bake PLY splats into a static Bedrock .geo.json model.")
    parser.add_argument("ply_file", type=Path, help="Input .ply file")
    parser.add_argument("-n", "--max-splats", type=int, default=2500, help="Total splat planes (default: 2500)")
    parser.add_argument("-s", "--size", type=float, default=48.0, help="Target size in model pixels (16 = 1 block)")
    parser.add_argument("--no-flip-y", action="store_true", help="Do not flip Y/Z axes")
    parser.add_argument(
        "--geo-out",
        type=Path,
        default=Path("./bedrock/02_Oriented_Splat_Pack/SplatRenderer_RP/models/entity/splat_cloud.geo.json"),
    )
    parser.add_argument(
        "--tex-out",
        type=Path,
        default=Path("./bedrock/02_Oriented_Splat_Pack/SplatRenderer_RP/textures/entity/splat_cloud.png"),
    )
    args = parser.parse_args()
    export_geo_splat(
        args.ply_file,
        args.geo_out,
        args.tex_out,
        max_splats=args.max_splats,
        target_size=args.size,
        flip_y=not args.no_flip_y,
    )
