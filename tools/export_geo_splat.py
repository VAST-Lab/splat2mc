#!/usr/bin/env python3
"""
Bake 3D Gaussian Splats (.ply) directly into a static Minecraft Bedrock .geo.json model
and an accompanying RGBA texture atlas. Zero particles or scripts required at runtime.
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


def export_geo_splat(
    ply_file: Path,
    output_geo: Path,
    output_tex: Path,
    max_splats: int = 1024,
    target_size: float = 48.0,
    flip_y: bool = True,
):
    plydata = PlyData.read(str(ply_file))
    v = plydata["vertex"]
    x, y, z = np.array(v["x"]), np.array(v["y"]), np.array(v["z"])

    C0 = 0.28209479177387814
    if "f_dc_0" in v.data.dtype.names:
        r = np.clip(0.5 + np.array(v["f_dc_0"]) * C0, 0, 1)
        g = np.clip(0.5 + np.array(v["f_dc_1"]) * C0, 0, 1)
        b = np.clip(0.5 + np.array(v["f_dc_2"]) * C0, 0, 1)
    else:
        r = np.array(v["red"]) / 255.0
        g = np.array(v["green"]) / 255.0
        b = np.array(v["blue"]) / 255.0

    # sRGB gamma correction
    r, g, b = np.power(r, 1.0 / 2.2), np.power(g, 1.0 / 2.2), np.power(b, 1.0 / 2.2)

    opacity = (
        1.0 / (1.0 + np.exp(-np.array(v["opacity"])))
        if "opacity" in v.data.dtype.names
        else np.ones_like(x)
    )
    valid = np.where(opacity > 0.1)[0]
    indices = valid[np.argsort(opacity[valid])[::-1][:max_splats]]

    # Sort back-to-front from center for cleaner alpha blending order in bone list
    cx_raw, cy_raw, cz_raw = np.mean(x[indices]), np.mean(y[indices]), np.mean(z[indices])
    dist_sq = (x[indices] - cx_raw) ** 2 + (y[indices] - cy_raw) ** 2 + (z[indices] - cz_raw) ** 2
    indices = indices[np.argsort(dist_sq)[::-1]]

    sx, sy, sz = np.exp(v["scale_0"]), np.exp(v["scale_1"]), np.exp(v["scale_2"])
    rw, rx, ry, rz = v["rot_0"], v["rot_1"], v["rot_2"], v["rot_3"]
    norms = np.sqrt(rw**2 + rx**2 + ry**2 + rz**2)
    rw, rx, ry, rz = rw / norms, rx / norms, ry / norms, rz / norms

    min_x, max_x = np.percentile(x[indices], 2), np.percentile(x[indices], 98)
    min_y, max_y = np.percentile(y[indices], 2), np.percentile(y[indices], 98)
    min_z, max_z = np.percentile(z[indices], 2), np.percentile(z[indices], 98)
    scale_factor = target_size / max(max_x - min_x or 1, max_y - min_y or 1, max_z - min_z or 1)
    cx, cy, cz = (min_x + max_x) / 2, (min_y + max_y) / 2, (min_z + max_z) / 2

    # Build Texture Atlas: each splat gets an 8x8 Gaussian soft-circle tile
    grid_dim = math.ceil(math.sqrt(len(indices)))
    tile_px = 8
    atlas_px = grid_dim * tile_px
    atlas = Image.new("RGBA", (atlas_px, atlas_px), (0, 0, 0, 0))

    yy, xx = np.ogrid[:tile_px, :tile_px]
    center = (tile_px - 1) / 2.0
    gauss_mask = np.exp(-0.5 * ((xx - center) ** 2 + (yy - center) ** 2) / ((tile_px / 3.2) ** 2))

    bones = [{"name": "root", "pivot": [0, 0, 0]}]
    y_sign = -1.0 if flip_y else 1.0

    for idx_pos, i in enumerate(indices):
        col = idx_pos % grid_dim
        row = idx_pos // grid_dim
        u, v_uv = col * tile_px, row * tile_px

        tile_rgba = np.zeros((tile_px, tile_px, 4), dtype=np.uint8)
        tile_rgba[..., 0] = int(r[i] * 255)
        tile_rgba[..., 1] = int(g[i] * 255)
        tile_rgba[..., 2] = int(b[i] * 255)
        tile_rgba[..., 3] = np.clip(gauss_mask * opacity[i] * 255, 0, 255).astype(np.uint8)
        atlas.paste(Image.fromarray(tile_rgba, "RGBA"), (u, v_uv))

        w_q, i_x, i_y, i_z = rw[i], rx[i], ry[i], rz[i]
        R = np.array([
            [1 - 2 * (i_y**2 + i_z**2), 2 * (i_x * i_y - w_q * i_z), 2 * (i_x * i_z + w_q * i_y)],
            [2 * (i_x * i_y + w_q * i_z), 1 - 2 * (i_x**2 + i_z**2), 2 * (i_y * i_z - w_q * i_x)],
            [2 * (i_x * i_z - w_q * i_y), 2 * (i_y * i_z + w_q * i_x), 1 - 2 * (i_x**2 + i_y**2)],
        ])
        if flip_y:
            R[1, :] = -R[1, :]
            R[2, :] = -R[2, :]

        scales = np.array([sx[i], sy[i], sz[i]]) * scale_factor
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

        px = float((x[i] - cx) * scale_factor)
        py = float(y_sign * (y[i] - cy) * scale_factor + (target_size * 0.5))
        pz = float(y_sign * (z[i] - cz) * scale_factor)

        w_size = float(np.clip(scales[axes[0]] * 3.0, 0.5, 16.0))
        h_size = float(np.clip(scales[axes[1]] * 3.0, 0.5, 16.0))

        bones.append({
            "name": f"s_{idx_pos}",
            "parent": "root",
            "pivot": [round(px, 3), round(py, 3), round(pz, 3)],
            "rotation": [round(rot_x, 2), round(rot_y, 2), round(rot_z, 2)],
            "cubes": [
                {
                    "origin": [round(px - w_size / 2, 3), round(py - h_size / 2, 3), round(pz, 3)],
                    "size": [round(w_size, 3), round(h_size, 3), 0],
                    "uv": {
                        "north": {"uv": [u, v_uv], "uv_size": [tile_px, tile_px]},
                        "south": {"uv": [u + tile_px, v_uv], "uv_size": [-tile_px, tile_px]},
                    },
                }
            ],
        })

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
    print(f"✓ Exported {len(indices)} double-sided static 3D splat planes to {output_geo} and {output_tex}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bake PLY splats into a static Bedrock .geo.json model.")
    parser.add_argument("ply_file", type=Path, help="Input .ply file")
    parser.add_argument("-n", "--max-splats", type=int, default=1024, help="Number of splat planes (default: 1024)")
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