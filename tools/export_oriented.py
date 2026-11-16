#!/usr/bin/env python3
"""
Export oriented 3D Gaussian Splats (.ply) into Bedrock TypeScript/JavaScript format.

Fixes the jagged shard artifacts from earlier versions by computing smooth outward surface
normals from a Gaussian density field gradient so that:
- Inset depth-writing core particles (flag=1) are strictly pushed inside the true volume interior.
- Outer smooth Gaussian surface splats (flag=0) are aligned tangent to the smoothed surface
  with clamped aspect ratios and KNN-smoothed SH colors.
"""
import argparse
import math
from pathlib import Path
import numpy as np
from plyfile import PlyData
from scipy.ndimage import gaussian_filter


def compute_smooth_outward_normals(pts: np.ndarray, weights: np.ndarray, grid_res: int = 64) -> np.ndarray:
    """Compute smooth outward-pointing surface normals from the negative gradient of a 3D Gaussian density grid."""
    pad = 4
    mins = np.min(pts, axis=0)
    maxs = np.max(pts, axis=0)
    span = float(np.max(maxs - mins)) or 1.0
    step = span / (grid_res - 2 * pad)
    origin = mins - pad * step

    gx = np.clip(((pts[:, 0] - origin[0]) / step).astype(np.int32), 1, grid_res - 2)
    gy = np.clip(((pts[:, 1] - origin[1]) / step).astype(np.int32), 1, grid_res - 2)
    gz = np.clip(((pts[:, 2] - origin[2]) / step).astype(np.int32), 1, grid_res - 2)

    density = np.zeros((grid_res, grid_res, grid_res), dtype=np.float32)
    np.add.at(density, (gx, gy, gz), weights)
    smooth = gaussian_filter(density, sigma=2.2)

    # Outward normal is -grad(density)
    grad_x, grad_y, grad_z = np.gradient(-smooth)
    nx = grad_x[gx, gy, gz]
    ny = grad_y[gx, gy, gz]
    nz = grad_z[gx, gy, gz]
    vecs = np.column_stack([nx, ny, nz])
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)

    # Fallback to radial outward vector from centroid where gradient is near zero
    centroid = np.mean(pts, axis=0, keepdims=True)
    radial = pts - centroid
    r_norms = np.linalg.norm(radial, axis=1, keepdims=True)
    radial_unit = radial / np.maximum(r_norms, 1e-6)

    valid = norms[:, 0] > 1e-5
    outward = np.where(valid[:, None], vecs / np.maximum(norms, 1e-6), radial_unit)
    return outward.astype(np.float64)


def voxelize_points(
    px: np.ndarray,
    py: np.ndarray,
    pz: np.ndarray,
    r: np.ndarray,
    g: np.ndarray,
    b: np.ndarray,
    opacity: np.ndarray,
    chroma: np.ndarray,
    target_size: float,
    budget: int,
):
    """Downsample valid splats into spatial voxels up to budget."""
    lo_res, hi_res = 6.0, 140.0
    for _ in range(18):
        mid_res = (lo_res + hi_res) * 0.5
        vsize = target_size / mid_res
        vx = np.floor((px + target_size * 4.0) / vsize).astype(np.int64)
        vy = np.floor((py + target_size * 4.0) / vsize).astype(np.int64)
        vz = np.floor((pz + target_size * 4.0) / vsize).astype(np.int64)
        keys = (vx << 40) | (vy << 20) | vz
        if len(np.unique(keys)) < budget:
            lo_res = mid_res
        else:
            hi_res = mid_res

    vsize = target_size / ((lo_res + hi_res) * 0.5)
    vx = np.floor((px + target_size * 4.0) / vsize).astype(np.int64)
    vy = np.floor((py + target_size * 4.0) / vsize).astype(np.int64)
    vz = np.floor((pz + target_size * 4.0) / vsize).astype(np.int64)
    keys = (vx << 40) | (vy << 20) | vz
    uniq_keys, first_idx, inv = np.unique(keys, return_index=True, return_inverse=True)

    w = opacity * (1.0 + 0.8 * np.clip(chroma, 0.0, 0.4))
    w_sum = np.bincount(inv, weights=w)
    op_sum = np.bincount(inv, weights=opacity)

    vx_mean = np.bincount(inv, weights=px * w) / w_sum
    vy_mean = np.bincount(inv, weights=py * w) / w_sum
    vz_mean = np.bincount(inv, weights=pz * w) / w_sum
    vr = np.bincount(inv, weights=r * w) / w_sum
    vg = np.bincount(inv, weights=g * w) / w_sum
    vb = np.bincount(inv, weights=b * w) / w_sum

    lum = 0.299 * vr + 0.587 * vg + 0.114 * vb
    vr = np.clip((lum + (vr - lum) * 1.18) * 1.06, 0.0, 1.0)
    vg = np.clip((lum + (vg - lum) * 1.18) * 1.06, 0.0, 1.0)
    vb = np.clip((lum + (vb - lum) * 1.18) * 1.06, 0.0, 1.0)
    v_op = np.clip(0.75 + 0.20 * (1.0 - np.exp(-op_sum)), 0.75, 0.95)

    keep = np.arange(len(uniq_keys))
    if len(keep) > budget:
        keep = keep[np.argsort(op_sum[keep])[::-1][:budget]]

    return (
        vsize,
        first_idx[keep],
        np.column_stack([vx_mean[keep], vy_mean[keep], vz_mean[keep]]),
        vr[keep],
        vg[keep],
        vb[keep],
        v_op[keep],
    )


def export_oriented_bedrock(
    ply_file: Path,
    output_ts: Path,
    max_particles: int = 5000,
    target_size: float = 5.0,
    flip_y: bool = True,
):
    print(f"Loading {ply_file}...")
    plydata = PlyData.read(str(ply_file))
    vertex = plydata["vertex"]

    x = np.array(vertex["x"], dtype=np.float64)
    y = np.array(vertex["y"], dtype=np.float64)
    z = np.array(vertex["z"], dtype=np.float64)

    C0 = 0.28209479177387814
    if "f_dc_0" in vertex.data.dtype.names:
        r = 0.5 + np.array(vertex["f_dc_0"], dtype=np.float64) * C0
        g = 0.5 + np.array(vertex["f_dc_1"], dtype=np.float64) * C0
        b = 0.5 + np.array(vertex["f_dc_2"], dtype=np.float64) * C0
    else:
        r = np.array(vertex["red"], dtype=np.float64) / 255.0
        g = np.array(vertex["green"], dtype=np.float64) / 255.0
        b = np.array(vertex["blue"], dtype=np.float64) / 255.0

    r, g, b = np.clip(r, 0.0, 1.0), np.clip(g, 0.0, 1.0), np.clip(b, 0.0, 1.0)

    opacity = (
        1.0 / (1.0 + np.exp(-np.array(vertex["opacity"], dtype=np.float64)))
        if "opacity" in vertex.data.dtype.names
        else np.ones_like(x)
    )
    sx = np.exp(np.array(vertex["scale_0"], dtype=np.float64))
    sy = np.exp(np.array(vertex["scale_1"], dtype=np.float64))
    sz = np.exp(np.array(vertex["scale_2"], dtype=np.float64))
    s_max = np.maximum(np.maximum(sx, sy), sz)

    scale_cap = np.percentile(s_max, 85)
    valid = np.where((opacity > 0.08) & (s_max <= scale_cap))[0]
    if len(valid) == 0:
        valid = np.arange(len(x))

    chroma_valid = np.maximum(np.maximum(r[valid], g[valid]), b[valid]) - np.minimum(
        np.minimum(r[valid], g[valid]), b[valid]
    )
    importance = opacity[valid] * (1.0 + 0.8 * chroma_valid)
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

    core_budget = max(64, int(max_particles * 0.25))
    surf_budget = max(128, max_particles - core_budget)

    rv, gv, bv, opv = r[valid], g[valid], b[valid], opacity[valid]
    vs_c, f_c, c_pts, cr, cg, cb, cop = voxelize_points(
        px, py, pz, rv, gv, bv, opv, chroma_valid, target_size, core_budget
    )
    vs_s, f_s, s_pts, sr, sg, sb, sop = voxelize_points(
        px, py, pz, rv, gv, bv, opv, chroma_valid, target_size, surf_budget
    )

    y_shift = -min(float(np.min(c_pts[:, 1])), float(np.min(s_pts[:, 1]))) + 0.15
    c_pts[:, 1] += y_shift
    s_pts[:, 1] += y_shift

    # Compute smooth outward normals from 3D Gaussian density gradient so core discs never poke outward
    c_outward = compute_smooth_outward_normals(c_pts, cop)
    s_outward = compute_smooth_outward_normals(s_pts, sop)
    c_pts = c_pts - c_outward * (0.75 * vs_c)
    s_pts = s_pts + s_outward * (0.08 * vs_s)

    print(
        f"Baking {len(c_pts)} depth-core + {len(s_pts)} tangent-aligned surface splats "
        f"(from {len(x)} raw PLY splats, vs_c={vs_c:.3f}, vs_s={vs_s:.3f})..."
    )

    def format_entry(pt, normal_vec, r_val, g_val, b_val, op_val, orig_idx, base_scale, is_core: int):
        nx, ny, nz = float(normal_vec[0]), float(normal_vec[1]), float(normal_vec[2])
        scales = np.sort([sx[orig_idx], sy[orig_idx], sz[orig_idx]]) * scale_factor

        if is_core:
            size_x = size_y = base_scale
            roll = 0.0
        else:
            aspect = float(np.clip(scales[2] / max(scales[1], 1e-8), 0.75, 1.32))
            size_x = base_scale * math.sqrt(aspect)
            size_y = base_scale / math.sqrt(aspect)
            roll = float((orig_idx * 37) % 180 - 90)

        return (
            f"  [{pt[0]:.3f}, {pt[1]:.3f}, {pt[2]:.3f}, "
            f"{r_val:.3f}, {g_val:.3f}, {b_val:.3f}, "
            f"{size_x:.3f}, {size_y:.3f}, "
            f"{nx:.3f}, {ny:.3f}, {nz:.3f}, {roll:.3f}, {op_val:.3f}, {is_core}],\n"
        )

    output_ts.parent.mkdir(parents=True, exist_ok=True)
    with open(output_ts, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by splat2mc\nexport const splatData = [\n")
        for i in range(len(c_pts)):
            f.write(
                format_entry(
                    c_pts[i], c_outward[i], cr[i], cg[i], cb[i], cop[i], valid[f_c[i]], vs_c * 0.55, is_core=1
                )
            )
        for i in range(len(s_pts)):
            f.write(
                format_entry(
                    s_pts[i], s_outward[i], sr[i], sg[i], sb[i], sop[i], valid[f_s[i]], vs_s * 0.72, is_core=0
                )
            )
        f.write("];\n")
    print(f"✓ Saved to {output_ts}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export oriented 3DGS to Bedrock.")
    parser.add_argument("ply_file", type=Path, help="Input .ply file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("./bedrock/02_Oriented_Splat_Pack/SplatRenderer_BP/scripts/SplatData.js"),
        help="Output JavaScript file path",
    )
    parser.add_argument(
        "-n",
        "--max-particles",
        type=int,
        default=5000,
        help="Max base particles (rendered double-sided via DisableCulling material)",
    )
    parser.add_argument("-s", "--size", type=float, default=5.0, help="Target bounding box size in blocks")
    parser.add_argument("--no-flip-y", action="store_true", help="Do not flip Y axis")
    args = parser.parse_args()

    export_oriented_bedrock(
        args.ply_file,
        args.output,
        max_particles=args.max_particles,
        target_size=args.size,
        flip_y=not args.no_flip_y,
    )
