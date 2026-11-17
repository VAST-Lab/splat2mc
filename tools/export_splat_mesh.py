#!/usr/bin/env python3
"""
High-to-Low Poly Splat-to-Mesh Extractor & Multi-View Texture Baker for Minecraft Bedrock.

Replaces the legacy Quad-Sphere shrinkwrap (`export_mesh.py`) with a true 3D surface
reconstruction & high-to-low bake pipeline directly from 3DGS `.ply` files:
1. Renders multi-view orthographic RGB + Alpha + Median-Depth (`T <= 0.5`) maps around
   a Fibonacci sphere using the Numba elliptical 3DGS rasterizer (`render_imposter.py`).
2. Integrates all views into a 3D Truncated Signed Distance Field (TSDF) with visual-hull
   free-space carving, preserving concavities, spires, flat bases, and tunnels without
   hollow internal bubbles.
3. Extracts a high-poly surface mesh via Marching Cubes (`skimage.measure.marching_cubes`),
   keeps the single largest enclosed component, QEM-decimates + Taubin-smooths to the
   target Bedrock polygon budget, unwraps overlap-free connected UV charts, and bakes a
   depth-tested multi-view diffuse atlas (with optional normal map and `.fbx` export).
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import math
import os
import time
from pathlib import Path

import numba
import numpy as np
from PIL import Image
from scipy.spatial import cKDTree
from skimage.measure import marching_cubes
import trimesh

from mesh_bake_utils import (
    decimate_and_clean_mesh,
    dilate_atlas,
    rasterize_low_uv_chart,
    render_mesh_icon,
    unwrap_connected_charts,
    write_bedrock_polymesh_geo,
    write_binary_fbx,
)
from render_imposter import GaussianScene, apply_color_grade, eval_sh_color_ortho


# ==============================================================================
# 1. Numba Multi-View RGB + Alpha + Median-Depth Ortho Rasterizer
# ==============================================================================
@numba.njit(fastmath=True, nogil=True)
def rasterize_splats_ortho_rgbd(
    H: int,
    W: int,
    u_px: np.ndarray,
    v_px: np.ndarray,
    z_arr: np.ndarray,
    cov_xx: np.ndarray,
    cov_xy: np.ndarray,
    cov_yy: np.ndarray,
    r_arr: np.ndarray,
    g_arr: np.ndarray,
    b_arr: np.ndarray,
    op_arr: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Front-to-back orthographic 3DGS rasterizer computing:
    - `img`   (H, W, 3): premultiplied RGB
    - `alpha` (H, W)   : accumulated opacity (1 - T)
    - `z_hit` (H, W)   : median surface depth where T crosses 0.5 (weighted mean fallback)
    """
    img = np.zeros((H, W, 3), dtype=np.float32)
    T = np.ones((H, W), dtype=np.float32)
    z_hit = np.zeros((H, W), dtype=np.float32)
    z_wsum = np.zeros((H, W), dtype=np.float32)
    w_sum = np.zeros((H, W), dtype=np.float32)
    has_median = np.zeros((H, W), dtype=np.uint8)
    n = len(u_px)

    for idx in range(n):
        cxx = cov_xx[idx] + 0.3
        cxy = cov_xy[idx]
        cyy = cov_yy[idx] + 0.3
        det = cxx * cyy - cxy * cxy
        if det <= 1e-6:
            continue
        inv_det = 1.0 / det
        conic_a = cyy * inv_det
        conic_b = -cxy * inv_det
        conic_c = cxx * inv_det

        mid = 0.5 * (cxx + cyy)
        disc = max(0.0, mid * mid - det)
        lambda_max = mid + math.sqrt(disc)
        rad = min(96.0, math.ceil(3.0 * math.sqrt(lambda_max)))

        cx = u_px[idx]
        cy = v_px[idx]
        x0 = max(0, int(math.floor(cx - rad)))
        x1 = min(W - 1, int(math.ceil(cx + rad)))
        y0 = max(0, int(math.floor(cy - rad)))
        y1 = min(H - 1, int(math.ceil(cy + rad)))
        if x0 > x1 or y0 > y1:
            continue

        cr = r_arr[idx]
        cg = g_arr[idx]
        cb = b_arr[idx]
        op = op_arr[idx]
        pz = z_arr[idx]

        for py in range(y0, y1 + 1):
            dy = py - cy
            for px in range(x0, x1 + 1):
                t_val = T[py, px]
                if t_val < 0.005:
                    continue
                dx = px - cx
                power = -0.5 * (conic_a * dx * dx + 2.0 * conic_b * dx * dy + conic_c * dy * dy)
                if power > 0.0 or power < -4.5:
                    continue
                alpha = min(0.99, op * math.exp(power))
                if alpha < 0.004:
                    continue
                w = alpha * t_val
                img[py, px, 0] += w * cr
                img[py, px, 1] += w * cg
                img[py, px, 2] += w * cb
                z_wsum[py, px] += w * pz
                w_sum[py, px] += w
                t_new = t_val * (1.0 - alpha)
                if has_median[py, px] == 0 and t_new <= 0.5:
                    has_median[py, px] = 1
                    z_hit[py, px] = pz
                T[py, px] = t_new

    for py in range(H):
        for px in range(W):
            if has_median[py, px] == 0 and w_sum[py, px] > 1e-4:
                z_hit[py, px] = z_wsum[py, px] / w_sum[py, px]

    return img, 1.0 - T, z_hit


def _fibonacci_sphere_views(n_views: int = 42) -> list[tuple[float, float]]:
    """Generate evenly distributed `(yaw_deg, pitch_deg)` viewpoints over the full sphere."""
    views: list[tuple[float, float]] = []
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(n_views):
        # Strictly inside (-1, 1) so pitch never hits exact +-90 gimbal singularity
        y = 1.0 - (2.0 * i + 1.0) / n_views
        y = max(-0.996, min(0.996, y))
        pitch_deg = math.degrees(math.asin(y))
        yaw_deg = math.degrees((golden_angle * i) % (2.0 * math.pi))
        views.append((yaw_deg, pitch_deg))
    return views


def _render_single_rgbd_view(
    scene: GaussianScene,
    yaw_deg: float,
    pitch_deg: float,
    res: int,
    fill_ratio: float,
    hue_shift_deg: float,
) -> dict:
    yaw_rad = math.radians(yaw_deg)
    pitch_rad = math.radians(pitch_deg)

    cam_x = math.sin(yaw_rad) * math.cos(pitch_rad)
    cam_y = math.sin(pitch_rad)
    cam_z = math.cos(yaw_rad) * math.cos(pitch_rad)

    right_x = math.cos(yaw_rad)
    right_y = 0.0
    right_z = -math.sin(yaw_rad)

    up_x = -math.sin(yaw_rad) * math.sin(pitch_rad)
    up_y = math.cos(pitch_rad)
    up_z = -math.cos(yaw_rad) * math.sin(pitch_rad)

    depth = -(scene.x * cam_x + scene.y * cam_y + scene.z * cam_z)
    order = np.argsort(depth)

    px_per_unit = (res * fill_ratio) / scene.span
    xs, ys, zs = scene.x[order], scene.y[order], scene.z[order]
    z_sorted = depth[order].astype(np.float32)

    u_px = ((xs * right_x + ys * right_y + zs * right_z) * px_per_unit + res * 0.5).astype(np.float32)
    v_px = (-(xs * up_x + ys * up_y + zs * up_z) * px_per_unit + res * 0.5).astype(np.float32)

    r00, r01, r02 = scene.R00[order], scene.R01[order], scene.R02[order]
    r10, r11, r12 = scene.R10[order], scene.R11[order], scene.R12[order]
    r20, r21, r22 = scene.R20[order], scene.R21[order], scene.R22[order]

    u0 = right_x * r00 + right_y * r10 + right_z * r20
    u1 = right_x * r01 + right_y * r11 + right_z * r21
    u2 = right_x * r02 + right_y * r12 + right_z * r22

    v0 = -(up_x * r00 + up_y * r10 + up_z * r20)
    v1 = -(up_x * r01 + up_y * r11 + up_z * r21)
    v2 = -(up_x * r02 + up_y * r12 + up_z * r22)

    s2x = (scene.sx[order] * px_per_unit) ** 2
    s2y = (scene.sy[order] * px_per_unit) ** 2
    s2z = (scene.sz[order] * px_per_unit) ** 2

    cov_xx = (u0 * u0 * s2x + u1 * u1 * s2y + u2 * u2 * s2z).astype(np.float32)
    cov_xy = (u0 * v0 * s2x + u1 * v1 * s2y + u2 * v2 * s2z).astype(np.float32)
    cov_yy = (v0 * v0 * s2x + v1 * v1 * s2y + v2 * v2 * s2z).astype(np.float32)

    y_sign = -1.0 if scene.flip_y else 1.0
    # Soften high-frequency SH view glints (0.50x) and avoid oversaturated color grading
    # so baked textures preserve clean, scan-faithful colors without black/white speckling.
    f_rest_soft = scene.f_rest[order] * np.float32(0.50) if scene.has_rest else scene.f_rest[order]
    r_sh, g_sh, b_sh = eval_sh_color_ortho(
        float(-cam_x),
        float(-cam_y * y_sign),
        float(cam_z),
        scene.f_dc[order],
        f_rest_soft,
        scene.has_rest,
    )
    if abs(hue_shift_deg) > 1e-3:
        r_sh, g_sh, b_sh = apply_color_grade(
            r_sh, g_sh, b_sh, hue_shift_deg=hue_shift_deg, saturation_boost=1.0, brightness_boost=1.0
        )

    rgb_pre, alpha, z_hit = rasterize_splats_ortho_rgbd(
        res, res, u_px, v_px, z_sorted, cov_xx, cov_xy, cov_yy,
        r_sh, g_sh, b_sh, scene.op[order],
    )
    # Only un-premultiply where accumulated opacity is strong; avoid division blowup at rims
    rgb = np.where(alpha[..., None] > 0.25, rgb_pre / np.maximum(alpha[..., None], 0.25), rgb_pre)
    rgb = np.clip(rgb, 0.0, 1.0).astype(np.float32)

    return {
        "cam": np.array([cam_x, cam_y, cam_z], dtype=np.float32),
        "right": np.array([right_x, right_y, right_z], dtype=np.float32),
        "up": np.array([up_x, up_y, up_z], dtype=np.float32),
        "px_per_unit": np.float32(px_per_unit),
        "rgb": rgb,
        "alpha": alpha.astype(np.float32),
        "z_hit": z_hit.astype(np.float32),
    }


def render_multiview_rgbd(
    scene: GaussianScene,
    n_views: int = 42,
    view_res: int = 384,
    fill_ratio: float = 0.84,
    hue_shift_deg: float = 0.0,
) -> list[dict]:
    """Render `n_views` uniformly distributed orthographic RGB+Alpha+Depth views in parallel."""
    angles = _fibonacci_sphere_views(n_views)
    # Warm up Numba JIT kernels on view 0 before spawning threads
    first = _render_single_rgbd_view(scene, angles[0][0], angles[0][1], view_res, fill_ratio, hue_shift_deg)
    views = [first]
    if len(angles) > 1:
        max_workers = min(os.cpu_count() or 4, 8)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [
                pool.submit(_render_single_rgbd_view, scene, yaw, pitch, view_res, fill_ratio, hue_shift_deg)
                for yaw, pitch in angles[1:]
            ]
            views.extend(f.result() for f in futs)
    return views


# ==============================================================================
# 2. Multi-View TSDF Fusion + Space Carving & Marching Cubes Surface Extraction
# ==============================================================================
@numba.njit(parallel=True, fastmath=True, nogil=True)
def _integrate_tsdf_views_kernel(
    grid_res: int,
    origin: float,
    vox_sz: float,
    trunc: float,
    cams: np.ndarray,
    rights: np.ndarray,
    ups: np.ndarray,
    px_scales: np.ndarray,
    alphas: np.ndarray,
    zhits: np.ndarray,
) -> np.ndarray:
    """
    Integrate N orthographic Alpha + Median-Depth views into a 3D TSDF volume.
    Convention: `tsdf > 0` = outside / empty air, `tsdf < 0` = inside crystal.
    - Background rays (`alpha < 0.35`) carve empty air (`+1.0`).
    - Surface bands (`signed_dist in [-trunc, +trunc]`) fuse weighted TSDF values.
    - Deep occluded interiors (`signed_dist < -trunc` with `alpha >= 0.45`) keep an inside bias (`-1.0`)
      so closed cores never generate hollow interior bubbles.
    """
    n_views, H, W = alphas.shape
    tsdf = np.empty((grid_res, grid_res, grid_res), dtype=np.float32)

    for ix in numba.prange(grid_res):
        px = origin + ix * vox_sz
        for iy in range(grid_res):
            py = origin + iy * vox_sz
            for iz in range(grid_res):
                pz = origin + iz * vox_sz

                # Slight inside prior so completely occluded solid interior stays negative (-1)
                w_acc = 0.02
                v_acc = -0.02

                for v in range(n_views):
                    sc = px_scales[v]
                    fu = (px * rights[v, 0] + py * rights[v, 1] + pz * rights[v, 2]) * sc + W * 0.5
                    fv = -(px * ups[v, 0] + py * ups[v, 1] + pz * ups[v, 2]) * sc + H * 0.5
                    iu = int(fu + 0.5)
                    iv = int(fv + 0.5)
                    if iu < 0 or iu >= W or iv < 0 or iv >= H:
                        w_acc += 0.35
                        v_acc += 0.35
                        continue

                    a = alphas[v, iv, iu]
                    if a < 0.18:
                        # Free-space ray carving keeps real tunnels and pockets open
                        w_acc += 0.45
                        v_acc += 0.45
                        continue

                    if a >= 0.25:
                        z_p = -(px * cams[v, 0] + py * cams[v, 1] + pz * cams[v, 2])
                        d = zhits[v, iv, iu] - z_p  # >0 in front of surface, <0 behind
                        if d >= -trunc:
                            sdf_n = min(1.0, d / trunc)
                            w = 1.0 if d <= trunc else 0.5
                            w_acc += w
                            v_acc += w * sdf_n
                        else:
                            # Deep behind visible surface: reinforce solid interior softly
                            w_acc += 0.05
                            v_acc -= 0.05

                tsdf[ix, iy, iz] = v_acc / w_acc
    return tsdf


def extract_splat_highpoly_mesh(
    views: list[dict],
    span: float,
    fill_ratio: float = 0.84,
    grid_res: int = 216,
) -> tuple[np.ndarray, np.ndarray]:
    """Fuse multi-view RGB-D renders into a TSDF volume and extract high-poly tris via Marching Cubes."""
    half_world = (span / fill_ratio) * 0.52
    vox_sz = (2.0 * half_world) / float(grid_res)
    origin = -half_world + 0.5 * vox_sz
    trunc = 2.8 * vox_sz

    cams = np.stack([v["cam"] for v in views]).astype(np.float32)
    rights = np.stack([v["right"] for v in views]).astype(np.float32)
    ups = np.stack([v["up"] for v in views]).astype(np.float32)
    px_scales = np.array([v["px_per_unit"] for v in views], dtype=np.float32)
    alphas = np.stack([v["alpha"] for v in views]).astype(np.float32)
    zhits = np.stack([v["z_hit"] for v in views]).astype(np.float32)

    tsdf = _integrate_tsdf_views_kernel(
        grid_res, origin, vox_sz, trunc, cams, rights, ups, px_scales, alphas, zhits
    )

    verts, faces, _, _ = marching_cubes(tsdf, level=0.0, spacing=(vox_sz, vox_sz, vox_sz))
    verts = verts.astype(np.float32) + origin
    faces = faces.astype(np.int32)
    # Ensure outward winding (positive signed volume) directly at extraction time
    signed_vol = float(np.sum(np.cross(verts[faces[:, 1]] - verts[faces[:, 0]], verts[faces[:, 2]] - verts[faces[:, 0]]) * verts[faces[:, 0]]))
    if signed_vol < 0.0:
        faces = faces[:, [0, 2, 1]].astype(np.int32)
    return verts, faces


# ==============================================================================
# 3. Visibility-Tested Multi-View Texture & Optional Normal Baker
# ==============================================================================
@numba.njit(parallel=True, fastmath=True, nogil=True)
def _bake_multiview_texels_kernel(
    t_pos: np.ndarray,
    t_norm: np.ndarray,
    cams: np.ndarray,
    rights: np.ndarray,
    ups: np.ndarray,
    px_scales: np.ndarray,
    rgbs: np.ndarray,
    alphas: np.ndarray,
    zhits: np.ndarray,
    depth_tol: float,
    fallback_rgb: np.ndarray,
) -> np.ndarray:
    """
    For each low-poly UV texel `(P, N)`, project into all 42 rendered views and blend the
    most perpendicular, depth-verified un-occluded views. Falls back to nearest opaque
    splat color only inside deep cracks occluded from every angle.
    """
    m = len(t_pos)
    n_views, H, W, _ = rgbs.shape
    out_rgb = np.empty((m, 3), dtype=np.uint8)

    for idx in numba.prange(m):
        px, py, pz = t_pos[idx, 0], t_pos[idx, 1], t_pos[idx, 2]
        nx, ny, nz = t_norm[idx, 0], t_norm[idx, 1], t_norm[idx, 2]

        r_sum = 0.0
        g_sum = 0.0
        b_sum = 0.0
        w_sum = 0.0

        best_loose_r = 0.0
        best_loose_g = 0.0
        best_loose_b = 0.0
        best_loose_score = -1.0

        for v in range(n_views):
            cos_a = nx * cams[v, 0] + ny * cams[v, 1] + nz * cams[v, 2]
            if cos_a <= 0.12:
                continue

            sc = px_scales[v]
            fx = (px * rights[v, 0] + py * rights[v, 1] + pz * rights[v, 2]) * sc + W * 0.5
            fy = -(px * ups[v, 0] + py * ups[v, 1] + pz * ups[v, 2]) * sc + H * 0.5
            x0 = int(math.floor(fx))
            y0 = int(math.floor(fy))
            if x0 < 0 or x0 >= W - 1 or y0 < 0 or y0 >= H - 1:
                continue

            a00 = alphas[v, y0, x0]
            a10 = alphas[v, y0, x0 + 1]
            a01 = alphas[v, y0 + 1, x0]
            a11 = alphas[v, y0 + 1, x0 + 1]
            if min(a00, min(a10, min(a01, a11))) < 0.40:
                continue

            # Nearest/2x2 depth visibility check to prevent projecting occluders onto hidden faces
            z_p = -(px * cams[v, 0] + py * cams[v, 1] + pz * cams[v, 2])
            z_hit = zhits[v, int(fy + 0.5), int(fx + 0.5)]
            dz = abs(z_p - z_hit)

            dx = fx - x0
            dy = fy - y0
            w00 = (1.0 - dx) * (1.0 - dy)
            w10 = dx * (1.0 - dy)
            w01 = (1.0 - dx) * dy
            w11 = dx * dy

            cr = (
                rgbs[v, y0, x0, 0] * w00 + rgbs[v, y0, x0 + 1, 0] * w10
                + rgbs[v, y0 + 1, x0, 0] * w01 + rgbs[v, y0 + 1, x0 + 1, 0] * w11
            )
            cg = (
                rgbs[v, y0, x0, 1] * w00 + rgbs[v, y0, x0 + 1, 1] * w10
                + rgbs[v, y0 + 1, x0, 1] * w01 + rgbs[v, y0 + 1, x0 + 1, 1] * w11
            )
            cb = (
                rgbs[v, y0, x0, 2] * w00 + rgbs[v, y0, x0 + 1, 2] * w10
                + rgbs[v, y0 + 1, x0, 2] * w01 + rgbs[v, y0 + 1, x0 + 1, 2] * w11
            )

            if dz <= depth_tol:
                ang_w = cos_a * cos_a * cos_a
                depth_w = math.exp(-0.5 * (dz / (0.5 * depth_tol)) ** 2)
                wt = ang_w * depth_w
                r_sum += cr * wt
                g_sum += cg * wt
                b_sum += cb * wt
                w_sum += wt
            else:
                loose = cos_a / (1.0 + dz / depth_tol)
                if loose > best_loose_score:
                    best_loose_score = loose
                    best_loose_r = cr
                    best_loose_g = cg
                    best_loose_b = cb

        if w_sum > 1e-6:
            rr = r_sum / w_sum
            gg = g_sum / w_sum
            bb = b_sum / w_sum
            out_rgb[idx, 0] = min(255, max(0, int(rr * 255.0 + 0.5)))
            out_rgb[idx, 1] = min(255, max(0, int(gg * 255.0 + 0.5)))
            out_rgb[idx, 2] = min(255, max(0, int(bb * 255.0 + 0.5)))
        elif best_loose_score > 0.35:
            out_rgb[idx, 0] = min(255, max(0, int(best_loose_r * 255.0 + 0.5)))
            out_rgb[idx, 1] = min(255, max(0, int(best_loose_g * 255.0 + 0.5)))
            out_rgb[idx, 2] = min(255, max(0, int(best_loose_b * 255.0 + 0.5)))
        else:
            out_rgb[idx, 0] = fallback_rgb[idx, 0]
            out_rgb[idx, 1] = fallback_rgb[idx, 1]
            out_rgb[idx, 2] = fallback_rgb[idx, 2]

    return out_rgb


@numba.njit(parallel=True, fastmath=True, nogil=True)
def _bake_splat_normals_kernel(
    t_norm: np.ndarray,
    t_tan: np.ndarray,
    t_bitan: np.ndarray,
    nn_tri: np.ndarray,
    hi_fnorm: np.ndarray,
) -> np.ndarray:
    """Bake tangent-space normals from nearest high-poly TSDF Marching Cubes triangles."""
    m = len(t_norm)
    out_nrm = np.empty((m, 3), dtype=np.uint8)
    for idx in numba.prange(m):
        ti = nn_tri[idx]
        hnx, hny, hnz = hi_fnorm[ti, 0], hi_fnorm[ti, 1], hi_fnorm[ti, 2]
        if hnx * t_norm[idx, 0] + hny * t_norm[idx, 1] + hnz * t_norm[idx, 2] < 0.0:
            hnx, hny, hnz = -hnx, -hny, -hnz
        tnx = hnx * t_tan[idx, 0] + hny * t_tan[idx, 1] + hnz * t_tan[idx, 2]
        tny = hnx * t_bitan[idx, 0] + hny * t_bitan[idx, 1] + hnz * t_bitan[idx, 2]
        tnz = hnx * t_norm[idx, 0] + hny * t_norm[idx, 1] + hnz * t_norm[idx, 2]
        out_nrm[idx, 0] = min(255, max(0, int((tnx * 0.5 + 0.5) * 255.0 + 0.5)))
        out_nrm[idx, 1] = min(255, max(0, int((tny * 0.5 + 0.5) * 255.0 + 0.5)))
        out_nrm[idx, 2] = min(255, max(0, int((max(0.0, tnz) * 0.5 + 0.5) * 255.0 + 0.5)))
    return out_nrm


def bake_splat_textures(
    scene: GaussianScene,
    views: list[dict],
    hi_verts: np.ndarray,
    hi_tris: np.ndarray,
    low_mesh: trimesh.Trimesh,
    low_uvs: np.ndarray,
    low_face_uvs: np.ndarray,
    atlas_res: int = 1024,
    grid_res: int = 216,
    fill_ratio: float = 0.84,
    bake_normals: bool = False,
    hue_shift_deg: float = 0.0,
) -> tuple[Image.Image, Image.Image | None]:
    """Bake visibility-tested multi-view 3DGS colors (and optional high-poly normals) onto low UVs."""
    low_verts = low_mesh.vertices.astype(np.float32)
    low_norms = low_mesh.vertex_normals.astype(np.float32)
    low_faces = low_mesh.faces.astype(np.int32)

    pys, pxs, t_pos, t_norm, t_tan, t_bitan = rasterize_low_uv_chart(
        atlas_res, low_verts, low_norms, low_faces, low_uvs, low_face_uvs
    )

    # Prepare smooth k=8 inverse-distance weighted DC fallback for any deep occluded crevice
    solid = scene.op > 0.25
    if int(np.sum(solid)) < 32:
        solid = scene.op > 0.05
    splat_pts = np.column_stack([scene.x[solid], scene.y[solid], scene.z[solid]])
    splat_tree = cKDTree(splat_pts, leafsize=32)
    dists, nn_idx = splat_tree.query(t_pos, k=8, workers=-1)
    C0 = 0.28209479177387814
    dc = scene.f_dc[solid]
    fb_r = np.clip(0.5 + C0 * dc[:, 0], 0.0, 1.0)
    fb_g = np.clip(0.5 + C0 * dc[:, 1], 0.0, 1.0)
    fb_b = np.clip(0.5 + C0 * dc[:, 2], 0.0, 1.0)
    if abs(hue_shift_deg) > 1e-3:
        fb_r, fb_g, fb_b = apply_color_grade(
            fb_r, fb_g, fb_b, hue_shift_deg=hue_shift_deg, saturation_boost=1.0, brightness_boost=1.0
        )
    w_k = 1.0 / (dists + 1e-4 * max(scene.span, 1e-3)) ** 2
    w_k /= np.sum(w_k, axis=1, keepdims=True)
    fallback_rgb = np.column_stack([
        (np.sum(fb_r[nn_idx] * w_k, axis=1) * 255.0 + 0.5).astype(np.uint8),
        (np.sum(fb_g[nn_idx] * w_k, axis=1) * 255.0 + 0.5).astype(np.uint8),
        (np.sum(fb_b[nn_idx] * w_k, axis=1) * 255.0 + 0.5).astype(np.uint8),
    ])

    half_world = (scene.span / fill_ratio) * 0.52
    vox_sz = (2.0 * half_world) / float(grid_res)
    depth_tol = max(6.5 * vox_sz, scene.span * 0.04)

    cams = np.stack([v["cam"] for v in views]).astype(np.float32)
    rights = np.stack([v["right"] for v in views]).astype(np.float32)
    ups = np.stack([v["up"] for v in views]).astype(np.float32)
    px_scales = np.array([v["px_per_unit"] for v in views], dtype=np.float32)
    rgbs = np.stack([v["rgb"] for v in views]).astype(np.float32)
    alphas = np.stack([v["alpha"] for v in views]).astype(np.float32)
    zhits = np.stack([v["z_hit"] for v in views]).astype(np.float32)

    baked_rgb = _bake_multiview_texels_kernel(
        t_pos, t_norm, cams, rights, ups, px_scales, rgbs, alphas, zhits, depth_tol, fallback_rgb
    )

    diff_atlas = np.zeros((atlas_res, atlas_res, 4), dtype=np.uint8)
    mask = np.zeros((atlas_res, atlas_res), dtype=bool)
    diff_atlas[pys, pxs, :3] = baked_rgb
    mask[pys, pxs] = True
    diff_img = dilate_atlas(diff_atlas, mask)

    nrm_img = None
    if bake_normals:
        hi_cents = (hi_verts[hi_tris[:, 0]] + hi_verts[hi_tris[:, 1]] + hi_verts[hi_tris[:, 2]]) * (1.0 / 3.0)
        e1 = hi_verts[hi_tris[:, 1]] - hi_verts[hi_tris[:, 0]]
        e2 = hi_verts[hi_tris[:, 2]] - hi_verts[hi_tris[:, 0]]
        hi_fn = np.cross(e1, e2)
        hi_fn /= np.maximum(np.linalg.norm(hi_fn, axis=1, keepdims=True), 1e-8)
        hi_tree = cKDTree(hi_cents, leafsize=32)
        _, nn_tri = hi_tree.query(t_pos, k=1, workers=-1)
        baked_nrm = _bake_splat_normals_kernel(t_norm, t_tan, t_bitan, nn_tri.astype(np.int32), hi_fn.astype(np.float32))
        nrm_atlas = np.zeros((atlas_res, atlas_res, 4), dtype=np.uint8)
        nrm_atlas[pys, pxs, :3] = baked_nrm
        nrm_img = dilate_atlas(nrm_atlas, mask)

    return diff_img, nrm_img


# ==============================================================================
# 4. High-Level Splat-to-Mesh High-to-Low Bake Entry Point
# ==============================================================================
def bake_splat_to_lowpoly(
    ply_file: Path | GaussianScene,
    target_tris: int = 12000,
    atlas_res: int = 1024,
    smooth_iters: int = 1,
    n_views: int = 42,
    view_res: int = 384,
    grid_res: int = 216,
    fill_ratio: float = 0.84,
    hue_shift_deg: float = 0.0,
    bake_normals: bool = False,
    flip_y: bool = True,
    cached_high: tuple | None = None,
) -> tuple[trimesh.Trimesh, np.ndarray, np.ndarray, Image.Image, Image.Image | None, tuple]:
    """
    Reconstruct a high-poly TSDF mesh from a `.ply` Gaussian Splat, decimate + clean + unwrap
    connected UV charts, and bake a seamless multi-view diffuse atlas.

    Returns `(low_mesh, low_uvs, low_face_uvs, diff_img, nrm_img, cached_high)`.
    """
    scene = ply_file if isinstance(ply_file, GaussianScene) else GaussianScene(ply_file, flip_y=flip_y)
    if cached_high is None:
        views = render_multiview_rgbd(
            scene,
            n_views=n_views,
            view_res=view_res,
            fill_ratio=fill_ratio,
            hue_shift_deg=hue_shift_deg,
        )
        hi_verts, hi_tris = extract_splat_highpoly_mesh(
            views, span=scene.span, fill_ratio=fill_ratio, grid_res=grid_res
        )
        cached_high = (scene, views, hi_verts, hi_tris)
    else:
        scene, views, hi_verts, hi_tris = cached_high

    low_mesh = decimate_and_clean_mesh(
        hi_verts, hi_tris, target_tris=target_tris, smooth_iters=smooth_iters
    )
    low_uvs, low_face_uvs = unwrap_connected_charts(low_mesh, atlas_res=atlas_res)
    diff_img, nrm_img = bake_splat_textures(
        scene,
        views,
        hi_verts,
        hi_tris,
        low_mesh,
        low_uvs,
        low_face_uvs,
        atlas_res=atlas_res,
        grid_res=grid_res,
        fill_ratio=fill_ratio,
        bake_normals=bake_normals,
        hue_shift_deg=hue_shift_deg,
    )
    return low_mesh, low_uvs, low_face_uvs, diff_img, nrm_img, cached_high


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="High-to-Low TSDF Splat-to-Mesh Extractor & Texture Baker for Minecraft Bedrock."
    )
    parser.add_argument("ply_file", type=Path, help="Input 3DGS .ply file")
    parser.add_argument("--geo-out", type=Path, required=True, help="Output Bedrock .geo.json path")
    parser.add_argument("--tex-out", type=Path, required=True, help="Output baked diffuse PNG path")
    parser.add_argument("--fbx-out", type=Path, default=None, help="Optional output .fbx path (+_diffuse.png)")
    parser.add_argument("--geo-id", type=str, default="geometry.poly_gem_0", help="Bedrock geometry identifier")
    parser.add_argument("--tris", type=int, default=12000, help="Target triangle count (default: 12000)")
    parser.add_argument("--res", type=int, default=1024, help="Baked texture resolution (default: 1024)")
    parser.add_argument("--smooth-iters", type=int, default=1, help="Taubin smoothing passes (default: 1)")
    parser.add_argument("--grid-res", type=int, default=216, help="TSDF voxel resolution (default: 216)")
    parser.add_argument("--views", type=int, default=42, help="Fibonacci sphere view count (default: 42)")
    parser.add_argument("--view-res", type=int, default=384, help="Per-view render resolution (default: 384)")
    parser.add_argument("--bake-normals", action="store_true", help="Also bake tangent-space normal map")
    parser.add_argument("--no-flip-y", action="store_true", help="Do not flip PLY Y axis")
    args = parser.parse_args()

    t0 = time.time()
    low_mesh, low_uvs, low_face_uvs, diff_img, nrm_img, _ = bake_splat_to_lowpoly(
        args.ply_file,
        target_tris=args.tris,
        atlas_res=args.res,
        smooth_iters=args.smooth_iters,
        n_views=args.views,
        view_res=args.view_res,
        grid_res=args.grid_res,
        bake_normals=args.bake_normals,
        flip_y=not args.no_flip_y,
    )
    args.tex_out.parent.mkdir(parents=True, exist_ok=True)
    diff_img.save(args.tex_out, optimize=True)
    if args.bake_normals and nrm_img is not None:
        nrm_img.save(args.tex_out.with_name(f"{args.tex_out.stem}_normal.png"), optimize=True)
    write_bedrock_polymesh_geo(low_mesh, low_uvs, low_face_uvs, args.geo_out, args.geo_id, atlas_res=args.res)
    if args.fbx_out is not None:
        write_binary_fbx(low_mesh, low_uvs, low_face_uvs, diff_img, args.fbx_out, normal_img=nrm_img)
    print(f"✓ Baked {args.ply_file.name} -> {len(low_mesh.faces):,} tris @ {args.res}px in {time.time() - t0:.2f}s")
