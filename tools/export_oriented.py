#!/usr/bin/env python3
"""
Export oriented 3D Gaussian Splats (.ply) into Bedrock TypeScript/JavaScript format.
Uses 3D surface-voxel downsampling, removes oversized interior filler Gaussians,
and preserves native 3DGS sRGB colors with vibrance enhancement.
"""
import argparse
from pathlib import Path
import numpy as np
from plyfile import PlyData


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

    # 1. Extract Colors in native 3DGS sRGB space (0.5 + C0 * f_dc is already sRGB;
    #    applying an extra 1/2.2 gamma washes out dark/rich colors into pale tan/gray).
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

    # 2. Extract Opacity, Scales, and Quaternions
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

    # 3. Filter out oversized interior/background filler Gaussians (> 85th percentile scale)
    #    which otherwise dominate pure-opacity sorting with giant gray/tan clouds.
    scale_cap = np.percentile(s_max, 85)
    valid = np.where((opacity > 0.08) & (s_max <= scale_cap))[0]
    if len(valid) == 0:
        valid = np.arange(len(x))

    # Sort valid splats by chroma-weighted opacity descending so the first index in each
    # spatial voxel is the sharpest, most color-accurate surface splat.
    chroma_valid = np.maximum(np.maximum(r[valid], g[valid]), b[valid]) - np.minimum(
        np.minimum(r[valid], g[valid]), b[valid]
    )
    importance = opacity[valid] * (1.0 + 1.5 * chroma_valid)
    valid = valid[np.argsort(importance)[::-1]]

    # 4. Normalization Bounds
    min_x, max_x = np.percentile(x[valid], 2), np.percentile(x[valid], 98)
    min_y, max_y = np.percentile(y[valid], 2), np.percentile(y[valid], 98)
    min_z, max_z = np.percentile(z[valid], 2), np.percentile(z[valid], 98)
    scale_factor = target_size / max(max_x - min_x or 1.0, max_y - min_y or 1.0, max_z - min_z or 1.0)
    cx, cy, cz = (min_x + max_x) / 2.0, (min_y + max_y) / 2.0, (min_z + max_z) / 2.0

    y_sign = -1.0 if flip_y else 1.0
    px = (x[valid] - cx) * scale_factor
    py = y_sign * (y[valid] - cy) * scale_factor
    pz = -(z[valid] - cz) * scale_factor

    # 5. 3D Voxel-Surface Downsampling to match max_particles budget
    lo_res, hi_res = 8.0, 140.0
    for _ in range(18):
        mid_res = (lo_res + hi_res) * 0.5
        vsize = target_size / mid_res
        vx = np.floor((px + target_size * 4.0) / vsize).astype(np.int64)
        vy = np.floor((py + target_size * 4.0) / vsize).astype(np.int64)
        vz = np.floor((pz + target_size * 4.0) / vsize).astype(np.int64)
        keys = (vx << 40) | (vy << 20) | vz
        # Estimate after interior culling (~95% of occupied voxels are on surface shell)
        if len(np.unique(keys)) * 0.95 < max_particles:
            lo_res = mid_res
        else:
            hi_res = mid_res

    vsize = target_size / ((lo_res + hi_res) * 0.5)
    vx = np.floor((px + target_size * 4.0) / vsize).astype(np.int64)
    vy = np.floor((py + target_size * 4.0) / vsize).astype(np.int64)
    vz = np.floor((pz + target_size * 4.0) / vsize).astype(np.int64)
    keys = (vx << 40) | (vy << 20) | vz
    uniq_keys, first_idx, inv = np.unique(keys, return_index=True, return_inverse=True)

    # Aggregate position, chroma-weighted color, and accumulated opacity per voxel
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

    # Apply vibrance/saturation boost (1.35x) and gentle brightness lift (1.15x)
    lum = 0.299 * vr + 0.587 * vg + 0.114 * vb
    vr = np.clip((lum + (vr - lum) * 1.35) * 1.15, 0.0, 1.0)
    vg = np.clip((lum + (vg - lum) * 1.35) * 1.15, 0.0, 1.0)
    vb = np.clip((lum + (vb - lum) * 1.35) * 1.15, 0.0, 1.0)
    v_op = np.clip(1.0 - np.exp(-op_sum * 1.4), 0.75, 1.0)

    # Cull strictly buried interior voxels (surrounded on all 6 faces)
    ux = vx[first_idx]
    uy = vy[first_idx]
    uz = vz[first_idx]
    coord_set = set(zip(ux.tolist(), uy.tolist(), uz.tolist()))
    surface_mask = np.array(
        [
            not (
                (ix + 1, iy, iz) in coord_set
                and (ix - 1, iy, iz) in coord_set
                and (ix, iy + 1, iz) in coord_set
                and (ix, iy - 1, iz) in coord_set
                and (ix, iy, iz + 1) in coord_set
                and (ix, iy, iz - 1) in coord_set
            )
            for ix, iy, iz in zip(ux.tolist(), uy.tolist(), uz.tolist())
        ],
        dtype=bool,
    )

    keep = np.where(surface_mask)[0]
    if len(keep) > max_particles:
        # Keep highest accumulated opacity voxels if slightly above max_particles
        keep = keep[np.argsort(op_sum[keep])[::-1][:max_particles]]

    # Shift Y so the model base sits cleanly above the ground block (min Y = 0.2)
    y_offset = -np.min(vy_mean[keep]) + 0.2
    vy_mean = vy_mean + y_offset

    # Sort kept voxels inside-out from centroid so outer surface splats render on top
    c_x, c_y, c_z = np.mean(vx_mean[keep]), np.mean(vy_mean[keep]), np.mean(vz_mean[keep])
    dist_sq = (vx_mean[keep] - c_x) ** 2 + (vy_mean[keep] - c_y) ** 2 + (vz_mean[keep] - c_z) ** 2
    keep = keep[np.argsort(dist_sq)]

    # Clamp particle base scales to voxel neighborhood so GLOBAL_SCALE_MULTIPLIER=3.0
    # produces overlapping ~0.35-0.50 block surface discs without huge blurry clouds.
    min_s, max_s = vsize * 0.45, vsize * 0.68

    print(
        f"Baking {len(keep)} surface-voxel oriented splats (from {len(x)} raw PLY splats, vsize={vsize:.3f})..."
    )
    output_ts.parent.mkdir(parents=True, exist_ok=True)
    with open(output_ts, "w", encoding="utf-8") as f:
        f.write("// Auto-generated by splat2mc\nexport const splatData = [\n")

        for k in keep:
            orig_i = valid[first_idx[k]]
            w_q, i_x, i_y, i_z = rw[orig_i], rx[orig_i], ry[orig_i], rz[orig_i]

            # Standard Quaternion to Rotation Matrix
            R = np.array(
                [
                    [1 - 2 * (i_y**2 + i_z**2), 2 * (i_x * i_y - w_q * i_z), 2 * (i_x * i_z + w_q * i_y)],
                    [2 * (i_x * i_y + w_q * i_z), 1 - 2 * (i_x**2 + i_z**2), 2 * (i_y * i_z - w_q * i_x)],
                    [2 * (i_x * i_z - w_q * i_y), 2 * (i_y * i_z + w_q * i_x), 1 - 2 * (i_x**2 + i_y**2)],
                ]
            )

            # Transform to Minecraft coordinate space
            R[1, :] = y_sign * R[1, :]
            R[2, :] = -R[2, :]

            scales = np.array([sx[orig_i], sy[orig_i], sz[orig_i]]) * scale_factor
            min_idx = int(np.argmin(scales))

            # Normal is the thinnest axis
            nx, ny, nz = R[:, min_idx]

            # The other two axes define the splat plane
            axes = [0, 1, 2]
            axes.remove(min_idx)
            axis_u = R[:, axes[0]]

            size_x = float(np.clip(scales[axes[0]] * 2.2, min_s, max_s))
            size_y = float(np.clip(scales[axes[1]] * 2.2, min_s, max_s))

            # Calculate Roll based on Bedrock billboard alignment
            world_y = np.array([0.0, 1.0, 0.0])
            if abs(ny) > 0.999:
                v_right = np.array([1.0, 0.0, 0.0])
            else:
                v_right = np.cross(world_y, [nx, ny, nz])
                v_right = v_right / np.linalg.norm(v_right)

            v_up = np.cross([nx, ny, nz], v_right)

            u_proj_x = np.dot(axis_u, v_right)
            u_proj_y = np.dot(axis_u, v_up)
            roll = -np.degrees(np.arctan2(u_proj_y, u_proj_x))

            f.write(
                f"  [{vx_mean[k]:.3f}, {vy_mean[k]:.3f}, {vz_mean[k]:.3f}, "
                f"{vr[k]:.3f}, {vg[k]:.3f}, {vb[k]:.3f}, "
                f"{size_x:.3f}, {size_y:.3f}, "
                f"{nx:.3f}, {ny:.3f}, {nz:.3f}, {roll:.3f}, {v_op[k]:.3f}],\n"
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
