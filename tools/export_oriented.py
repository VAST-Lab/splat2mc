#!/usr/bin/env python3
"""
Export oriented 3D Gaussian Splats (.ply) into Bedrock TypeScript/JavaScript format.
Emits an inset depth-writing core particle shell (flag=1) plus outer smooth
Gaussian surface splats (flag=0) so back-side particles never bleed through front
surfaces regardless of viewing angle.
"""
import argparse
import math
from pathlib import Path
import numpy as np
from plyfile import PlyData


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

    w = opacity * (1.0 + 1.5 * chroma)
    w_sum = np.bincount(inv, weights=w)
    op_sum = np.bincount(inv, weights=opacity)

    vx_mean = np.bincount(inv, weights=px * w) / w_sum
    vy_mean = np.bincount(inv, weights=py * w) / w_sum
    vz_mean = np.bincount(inv, weights=pz * w) / w_sum
    vr = np.bincount(inv, weights=r * w) / w_sum
    vg = np.bincount(inv, weights=g * w) / w_sum
    vb = np.bincount(inv, weights=b * w) / w_sum

    lum = 0.299 * vr + 0.587 * vg + 0.114 * vb
    vr = np.clip((lum + (vr - lum) * 1.30) * 1.12, 0.0, 1.0)
    vg = np.clip((lum + (vg - lum) * 1.30) * 1.12, 0.0, 1.0)
    vb = np.clip((lum + (vb - lum) * 1.30) * 1.12, 0.0, 1.0)
    v_op = np.clip(0.82 + 0.16 * (1.0 - np.exp(-op_sum)), 0.82, 0.98)

    keep = np.arange(len(uniq_keys))
    if len(keep) > budget:
        keep = keep[np.argsort(op_sum[keep])[::-1][:budget]]

    return vsize, first_idx[keep], np.column_stack([vx_mean[keep], vy_mean[keep], vz_mean[keep]]), vr[keep], vg[keep], vb[keep], v_op[keep]


def export_oriented_bedrock(
    ply_file: Path,
    output_ts: Path,
    max_particles: int = 5000,
    target_size: float = 10.0,
    flip_y: bool = True,
):
    print(f"Loading {ply_file}...")
    plydata = PlyData.read(str(ply_file))
    vertex = plydata["vertex"]

    x = np.array(vertex["x"], dtype=np.float64)
    y = np.array(vertex["y"], dtype=np.float64)
    z = np.array(vertex["z"], dtype=np.float64)

    # 1. Extract Colors in native 3DGS sRGB space (0.5 + C0 * f_dc is already sRGB)
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

    rw = np.array(vertex["rot_0"], dtype=np.float64)
    rx = np.array(vertex["rot_1"], dtype=np.float64)
    ry = np.array(vertex["rot_2"], dtype=np.float64)
    rz = np.array(vertex["rot_3"], dtype=np.float64)
    norms = np.sqrt(rw**2 + rx**2 + ry**2 + rz**2)
    norms = np.where(norms == 0, 1.0, norms)
    rw, rx, ry, rz = rw / norms, rx / norms, ry / norms, rz / norms

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

    # Allocate ~25% budget to inset depth-writing core particles, ~75% to outer surface splats
    core_budget = max(64, int(max_particles * 0.25))
    surf_budget = max(128, max_particles - core_budget)

    rv, gv, bv, opv = r[valid], g[valid], b[valid], opacity[valid]
    vs_c, f_c, c_pts, cr, cg, cb, cop = voxelize_points(px, py, pz, rv, gv, bv, opv, chroma_valid, target_size, core_budget)
    vs_s, f_s, s_pts, sr, sg, sb, sop = voxelize_points(px, py, pz, rv, gv, bv, opv, chroma_valid, target_size, surf_budget)

    # Shift Y cleanly above ground block (min Y = 0.2)
    y_shift = -min(float(np.min(c_pts[:, 1])), float(np.min(s_pts[:, 1]))) + 0.2
    c_pts[:, 1] += y_shift
    s_pts[:, 1] += y_shift

    # Inset core particles strictly inside the rock wall; nudge surface slightly outward
    c_inward = compute_local_inward_vectors(c_pts, radius=2.4 * vs_c)
    s_inward = compute_local_inward_vectors(s_pts, radius=2.4 * vs_s)
    c_pts = c_pts + c_inward * (0.60 * vs_c)
    s_pts = s_pts - s_inward * (0.18 * vs_s)

    print(
        f"Baking {len(c_pts)} depth-core + {len(s_pts)} surface oriented splats "
        f"(from {len(x)} raw PLY splats, vs_c={vs_c:.3f}, vs_s={vs_s:.3f})..."
    )

    def format_entry(pt, r_val, g_val, b_val, op_val, orig_idx, base_scale, is_core: int):
        w_q, i_x, i_y, i_z = rw[orig_idx], rx[orig_idx], ry[orig_idx], rz[orig_idx]
        R = np.array([
            [1 - 2 * (i_y**2 + i_z**2), 2 * (i_x * i_y - w_q * i_z), 2 * (i_x * i_z + w_q * i_y)],
            [2 * (i_x * i_y + w_q * i_z), 1 - 2 * (i_x**2 + i_z**2), 2 * (i_y * i_z - w_q * i_x)],
            [2 * (i_x * i_z - w_q * i_y), 2 * (i_y * i_z + w_q * i_x), 1 - 2 * (i_x**2 + i_y**2)],
        ])
        R[1, :] = y_sign * R[1, :]
        R[2, :] = -R[2, :]

        scales = np.array([sx[orig_idx], sy[orig_idx], sz[orig_idx]]) * scale_factor
        min_idx = int(np.argmin(scales))
        nx, ny, nz = R[:, min_idx]
        axes = [0, 1, 2]
        axes.remove(min_idx)
        axis_u = R[:, axes[0]]

        if is_core:
            size_x = size_y = base_scale
        else:
            aspect = float(np.clip(scales[axes[0]] / max(scales[axes[1]], 1e-8), 0.58, 1.72))
            size_x = base_scale * math.sqrt(aspect)
            size_y = base_scale / math.sqrt(aspect)

        world_y = np.array([0.0, 1.0, 0.0])
        if abs(ny) > 0.999:
            v_right = np.array([1.0, 0.0, 0.0])
        else:
            v_right = np.cross(world_y, [nx, ny, nz])
            v_right = v_right / np.linalg.norm(v_right)
        v_up = np.cross([nx, ny, nz], v_right)
        roll = -np.degrees(np.arctan2(np.dot(axis_u, v_up), np.dot(axis_u, v_right)))

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
            f.write(format_entry(c_pts[i], cr[i], cg[i], cb[i], cop[i], valid[f_c[i]], vs_c * 0.62, is_core=1))
        for i in range(len(s_pts)):
            f.write(format_entry(s_pts[i], sr[i], sg[i], sb[i], sop[i], valid[f_s[i]], vs_s * 0.85, is_core=0))
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
    parser.add_argument("-s", "--size", type=float, default=10.0, help="Target bounding box size in blocks")
    parser.add_argument("--no-flip-y", action="store_true", help="Do not flip Y axis")
    args = parser.parse_args()

    export_oriented_bedrock(
        args.ply_file,
        args.output,
        max_particles=args.max_particles,
        target_size=args.size,
        flip_y=not args.no_flip_y,
    )
