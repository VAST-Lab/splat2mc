#!/usr/bin/env python3
"""
Shared High-to-Low Poly Mesh Processing, UV Unwrapping, Texture Baking Helpers,
Bedrock `poly_mesh` Exporter (`MCPE-186053`-safe), and Binary FBX v7400 Writer.

Used by both Pack 02 (`export_splat_mesh.py`) and Pack 04 (`export_fbx_mesh.py`).
"""
import json
import math
import struct
import zlib
from pathlib import Path

import fast_simplification
import numba
import numpy as np
import trimesh
from PIL import Image
from scipy.ndimage import distance_transform_edt
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components


# ==============================================================================
# 1. Connected-Component Cleaning & High-to-Low Decimation
# ==============================================================================
def keep_largest_component_arrays(
    verts: np.ndarray,
    tris: np.ndarray,
    tri_uvs: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None]:
    """Keep only the largest connected triangle component and strip any floating vertices."""
    edges_r = np.concatenate([tris[:, 0], tris[:, 1], tris[:, 2]])
    edges_c = np.concatenate([tris[:, 1], tris[:, 2], tris[:, 0]])
    adj = csr_matrix(
        (np.ones(len(edges_r), dtype=np.int8), (edges_r, edges_c)),
        shape=(len(verts), len(verts)),
    )
    n_comp, labels = connected_components(adj, directed=False)
    if n_comp > 1:
        counts = np.bincount(labels)
        main_lbl = int(np.argmax(counts))
        keep_f = labels[tris[:, 0]] == main_lbl
        tris = tris[keep_f]
        if tri_uvs is not None:
            tri_uvs = tri_uvs[keep_f]

    used, inv = np.unique(tris, return_inverse=True)
    if len(used) < len(verts) or n_comp > 1:
        verts = verts[used]
        tris = inv.reshape(-1, 3).astype(np.int32)
    return verts, tris, tri_uvs


def decimate_and_clean_mesh(
    hi_verts: np.ndarray,
    hi_tris: np.ndarray,
    target_tris: int = 16000,
    smooth_iters: int = 1,
) -> trimesh.Trimesh:
    """
    Clean a high-poly mesh to its single main enclosed object, decimate via QEM,
    strip any post-decimation fragments/floating vertices, and optionally apply
    gentle volume-preserving Taubin smoothing.
    """
    v_clean, f_clean, _ = keep_largest_component_arrays(hi_verts, hi_tris)
    if len(f_clean) > target_tris:
        v_low, f_low = fast_simplification.simplify(
            v_clean, f_clean, target_count=int(target_tris), agg=7
        )
    else:
        v_low, f_low = v_clean, f_clean

    mesh = trimesh.Trimesh(vertices=v_low, faces=f_low, process=True)
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.remove_unreferenced_vertices()

    parts = mesh.split(only_watertight=False)
    if len(parts) > 1:
        mesh = max(parts, key=lambda m: len(m.faces))
    mesh.remove_unreferenced_vertices()

    if mesh.is_watertight and mesh.volume < 0:
        mesh.invert()
    else:
        trimesh.repair.fix_normals(mesh)

    if smooth_iters > 0:
        trimesh.smoothing.filter_taubin(mesh, lamb=0.40, nu=-0.42, iterations=smooth_iters)
        mesh.update_faces(mesh.nondegenerate_faces())
        mesh.remove_unreferenced_vertices()

    return mesh


# ==============================================================================
# 2. Overlap-Free Connected-Chart UV Unwrapping & Shelf Packing
# ==============================================================================
def _ortho_basis_for_normal(n: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    n = n / max(float(np.linalg.norm(n)), 1e-8)
    ref = np.array([0.0, 1.0, 0.0]) if abs(n[1]) < 0.9 else np.array([1.0, 0.0, 0.0])
    t = np.cross(ref, n)
    t /= max(float(np.linalg.norm(t)), 1e-8)
    b = np.cross(n, t)
    return n, t, b


def _split_chart_if_overlapping(
    face_ids: np.ndarray,
    verts: np.ndarray,
    faces: np.ndarray,
    face_normals: np.ndarray,
    depth_limit: int = 4,
) -> list[tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """
    Ensure every face in a chart projects with positive 2D area and zero self-overlap.
    Returns a list of `(chart_face_ids, u_axis, v_axis)`.
    """
    mean_n = np.mean(face_normals[face_ids], axis=0)
    n_len = float(np.linalg.norm(mean_n))
    if n_len < 1e-6:
        mean_n = face_normals[face_ids[0]]
    n_dir, u_ax, v_ax = _ortho_basis_for_normal(mean_n)

    dots = face_normals[face_ids] @ n_dir
    valid_mask = dots > 0.20
    if not np.all(valid_mask):
        good = face_ids[valid_mask]
        bad = face_ids[~valid_mask]
        out = []
        if len(good) > 0:
            out.extend(_split_chart_if_overlapping(good, verts, faces, face_normals, depth_limit))
        if len(bad) > 0:
            if len(bad) == len(face_ids):
                for fid in bad:
                    _, tu, tv = _ortho_basis_for_normal(face_normals[fid])
                    out.append((np.array([fid], dtype=np.int32), tu, tv))
            else:
                out.extend(_split_chart_if_overlapping(bad, verts, faces, face_normals, depth_limit))
        return out

    if len(face_ids) <= 1 or depth_limit <= 0:
        return [(face_ids, u_ax, v_ax)]

    tri_v = verts[faces[face_ids]]
    uv2 = np.stack([tri_v @ u_ax, tri_v @ v_ax], axis=-1)
    e1 = uv2[:, 1] - uv2[:, 0]
    e2 = uv2[:, 2] - uv2[:, 0]
    areas2 = e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0]
    if np.any(areas2 <= 1e-10):
        pos_f = face_ids[areas2 > 1e-10]
        neg_f = face_ids[areas2 <= 1e-10]
        out = []
        if len(pos_f) > 0:
            out.extend(_split_chart_if_overlapping(pos_f, verts, faces, face_normals, depth_limit - 1))
        for fid in neg_f:
            _, tu, tv = _ortho_basis_for_normal(face_normals[fid])
            out.append((np.array([fid], dtype=np.int32), tu, tv))
        return out

    mins = uv2.reshape(-1, 2).min(axis=0)
    maxs = uv2.reshape(-1, 2).max(axis=0)
    span = np.maximum(maxs - mins, 1e-6)
    cents_2d = uv2.mean(axis=1)
    gx = np.clip(((cents_2d[:, 0] - mins[0]) / span[0] * 48).astype(np.int32), 0, 47)
    gy = np.clip(((cents_2d[:, 1] - mins[1]) / span[1] * 48).astype(np.int32), 0, 47)
    cell_ids = gy * 48 + gx
    depths = tri_v.mean(axis=1) @ n_dir
    has_overlap = False
    order = np.argsort(cell_ids)
    sorted_cells = cell_ids[order]
    sorted_depths = depths[order]
    thresh = float(np.max(span)) * 0.15
    i = 0
    n_c = len(sorted_cells)
    while i < n_c:
        j = i + 1
        while j < n_c and sorted_cells[j] == sorted_cells[i]:
            j += 1
        if j - i >= 2:
            d_slice = sorted_depths[i:j]
            if float(d_slice.max() - d_slice.min()) > thresh:
                has_overlap = True
                break
        i = j

    if not has_overlap:
        return [(face_ids, u_ax, v_ax)]

    med_d = float(np.median(depths))
    front = face_ids[depths >= med_d]
    back = face_ids[depths < med_d]
    if len(front) == 0 or len(back) == 0:
        half = len(face_ids) // 2
        front, back = face_ids[:half], face_ids[half:]
    return (
        _split_chart_if_overlapping(front, verts, faces, face_normals, depth_limit - 1)
        + _split_chart_if_overlapping(back, verts, faces, face_normals, depth_limit - 1)
    )


def unwrap_connected_charts(
    mesh: trimesh.Trimesh,
    atlas_res: int = 1024,
    pad_px: int = 4,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Build overlap-free connected planar UV charts and shelf-pack them into `[0, 1]^2`.
    Returns `(uv_coords [N_uv, 2], face_uv_idx [N_f, 3])` with bottom-up `[0, 1]` UVs.
    """
    verts = mesh.vertices.astype(np.float32)
    faces = mesh.faces.astype(np.int32)
    fn = mesh.face_normals.astype(np.float32)
    n_faces = len(faces)

    abs_n = np.abs(fn)
    dom_axis = np.argmax(abs_n, axis=1)
    dom_sign = np.where(fn[np.arange(n_faces), dom_axis] >= 0, 0, 1)
    dom_dir = dom_axis * 2 + dom_sign

    adj_pairs = mesh.face_adjacency
    valid_adj = (dom_dir[adj_pairs[:, 0]] == dom_dir[adj_pairs[:, 1]]) & (
        np.sum(fn[adj_pairs[:, 0]] * fn[adj_pairs[:, 1]], axis=1) > 0.40
    )
    vp = adj_pairs[valid_adj]
    f_adj = csr_matrix(
        (
            np.ones(len(vp) * 2, dtype=np.int8),
            (np.concatenate([vp[:, 0], vp[:, 1]]), np.concatenate([vp[:, 1], vp[:, 0]])),
        ),
        shape=(n_faces, n_faces),
    )
    _, f_labels = connected_components(f_adj, directed=False)

    groups: dict[int, list[int]] = {}
    for fid, lbl in enumerate(f_labels):
        groups.setdefault(int(lbl), []).append(fid)

    raw_charts = []
    for fids in groups.values():
        f_arr = np.array(fids, dtype=np.int32)
        raw_charts.extend(_split_chart_if_overlapping(f_arr, verts, faces, fn))

    chart_data = []
    total_area_2d = 0.0
    for f_arr, u_ax, v_ax in raw_charts:
        used_v, inv_f = np.unique(faces[f_arr], return_inverse=True)
        pts = verts[used_v]
        u_vals = pts @ u_ax
        v_vals = pts @ v_ax
        u_min, u_max = float(u_vals.min()), float(u_vals.max())
        v_min, v_max = float(v_vals.min()), float(v_vals.max())
        w = max(u_max - u_min, 1e-4)
        h = max(v_max - v_min, 1e-4)
        if h > w:
            u_ax, v_ax = v_ax, -u_ax
            u_vals, v_vals = v_vals, -u_vals
            u_min, u_max = float(u_vals.min()), float(u_vals.max())
            v_min, v_max = float(v_vals.min()), float(v_vals.max())
            w, h = h, w
        total_area_2d += w * h
        chart_data.append({
            "f_arr": f_arr,
            "inv_f": inv_f.reshape(-1, 3),
            "u_local": (u_vals - u_min).astype(np.float32),
            "v_local": (v_vals - v_min).astype(np.float32),
            "w": w,
            "h": h,
        })

    chart_data.sort(key=lambda c: c["h"], reverse=True)

    lo_scale = 0.1
    hi_scale = (atlas_res * 0.95) / math.sqrt(max(total_area_2d, 1e-6)) * 1.6

    def _pack_at_scale(scale: float):
        x_cur = pad_px
        y_cur = pad_px
        row_h = 0
        placements = []
        for ch in chart_data:
            pw = max(2, int(math.ceil(ch["w"] * scale)))
            ph = max(2, int(math.ceil(ch["h"] * scale)))
            if pw + 2 * pad_px > atlas_res:
                return None
            if x_cur + pw + pad_px > atlas_res:
                x_cur = pad_px
                y_cur += row_h + pad_px
                row_h = 0
            if y_cur + ph + pad_px > atlas_res:
                return None
            placements.append((x_cur, y_cur, pw, ph))
            x_cur += pw + pad_px
            row_h = max(row_h, ph)
        return placements

    best_scale = lo_scale
    best_placements = None
    for _ in range(28):
        mid = 0.5 * (lo_scale + hi_scale)
        res = _pack_at_scale(mid)
        if res is not None:
            best_scale = mid
            best_placements = res
            lo_scale = mid
        else:
            hi_scale = mid

    if best_placements is None:
        best_placements = _pack_at_scale(lo_scale)
        if best_placements is None:
            raise RuntimeError("Failed to pack UV charts into atlas")

    uv_list = []
    face_uv_idx = np.zeros((n_faces, 3), dtype=np.int32)
    uv_offset = 0
    for ch, (px0, py0, _, _) in zip(chart_data, best_placements):
        u_px = px0 + ch["u_local"] * best_scale
        v_px = py0 + ch["v_local"] * best_scale
        u_norm = u_px / float(atlas_res)
        v_norm = 1.0 - (v_px / float(atlas_res))
        uv_list.append(np.column_stack([u_norm, v_norm]))
        face_uv_idx[ch["f_arr"]] = ch["inv_f"] + uv_offset
        uv_offset += len(u_norm)

    uv_coords = np.vstack(uv_list).astype(np.float32)
    return uv_coords, face_uv_idx


# ==============================================================================
# 3. UV Chart Texel Rasterizer & Euclidean Dilation
# ==============================================================================
@numba.njit(fastmath=True, nogil=True)
def rasterize_low_uv_chart(
    atlas_res: int,
    low_verts: np.ndarray,
    low_norms: np.ndarray,
    low_faces: np.ndarray,
    low_uvs: np.ndarray,
    low_face_uvs: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Rasterize low-poly triangles in UV space and return covered texel attributes."""
    n_faces = len(low_faces)
    max_texels = atlas_res * atlas_res
    out_py = np.empty(max_texels, dtype=np.int32)
    out_px = np.empty(max_texels, dtype=np.int32)
    out_pos = np.empty((max_texels, 3), dtype=np.float32)
    out_norm = np.empty((max_texels, 3), dtype=np.float32)
    out_tan = np.empty((max_texels, 3), dtype=np.float32)
    out_bitan = np.empty((max_texels, 3), dtype=np.float32)
    visited = np.zeros((atlas_res, atlas_res), dtype=np.uint8)
    count = 0

    for f_i in range(n_faces):
        i0, i1, i2 = low_faces[f_i, 0], low_faces[f_i, 1], low_faces[f_i, 2]
        u0_i, u1_i, u2_i = low_face_uvs[f_i, 0], low_face_uvs[f_i, 1], low_face_uvs[f_i, 2]

        p0 = low_verts[i0]
        p1 = low_verts[i1]
        p2 = low_verts[i2]
        n0 = low_norms[i0]
        n1 = low_norms[i1]
        n2 = low_norms[i2]

        ax = low_uvs[u0_i, 0] * atlas_res
        ay = (1.0 - low_uvs[u0_i, 1]) * atlas_res
        bx = low_uvs[u1_i, 0] * atlas_res
        by = (1.0 - low_uvs[u1_i, 1]) * atlas_res
        cx = low_uvs[u2_i, 0] * atlas_res
        cy = (1.0 - low_uvs[u2_i, 1]) * atlas_res

        denom = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy)
        if abs(denom) < 1e-8:
            continue
        inv_denom = 1.0 / denom

        du1 = low_uvs[u1_i, 0] - low_uvs[u0_i, 0]
        dv1 = low_uvs[u1_i, 1] - low_uvs[u0_i, 1]
        du2 = low_uvs[u2_i, 0] - low_uvs[u0_i, 0]
        dv2 = low_uvs[u2_i, 1] - low_uvs[u0_i, 1]
        uv_det = du1 * dv2 - du2 * dv1
        if abs(uv_det) > 1e-10:
            inv_uv = 1.0 / uv_det
            tx = ((p1[0] - p0[0]) * dv2 - (p2[0] - p0[0]) * dv1) * inv_uv
            ty = ((p1[1] - p0[1]) * dv2 - (p2[1] - p0[1]) * dv1) * inv_uv
            tz = ((p1[2] - p0[2]) * dv2 - (p2[2] - p0[2]) * dv1) * inv_uv
            tlen = math.sqrt(tx * tx + ty * ty + tz * tz) + 1e-8
            tx /= tlen
            ty /= tlen
            tz /= tlen
        else:
            tx, ty, tz = 1.0, 0.0, 0.0

        min_x = max(0, int(math.floor(min(ax, min(bx, cx)) - 1.0)))
        max_x = min(atlas_res - 1, int(math.ceil(max(ax, max(bx, cx)) + 1.0)))
        min_y = max(0, int(math.floor(min(ay, min(by, cy)) - 1.0)))
        max_y = min(atlas_res - 1, int(math.ceil(max(ay, max(by, cy)) + 1.0)))

        for py in range(min_y, max_y + 1):
            py_c = py + 0.5
            for px in range(min_x, max_x + 1):
                if visited[py, px]:
                    continue
                px_c = px + 0.5
                w0 = ((by - cy) * (px_c - cx) + (cx - bx) * (py_c - cy)) * inv_denom
                w1 = ((cy - ay) * (px_c - cx) + (ax - cx) * (py_c - cy)) * inv_denom
                w2 = 1.0 - w0 - w1
                if w0 < -0.02 or w1 < -0.02 or w2 < -0.02:
                    continue
                cw0 = max(0.0, w0)
                cw1 = max(0.0, w1)
                cw2 = max(0.0, w2)
                csum = cw0 + cw1 + cw2
                cw0 /= csum
                cw1 /= csum
                cw2 /= csum

                visited[py, px] = 1
                out_py[count] = py
                out_px[count] = px
                out_pos[count, 0] = cw0 * p0[0] + cw1 * p1[0] + cw2 * p2[0]
                out_pos[count, 1] = cw0 * p0[1] + cw1 * p1[1] + cw2 * p2[1]
                out_pos[count, 2] = cw0 * p0[2] + cw1 * p1[2] + cw2 * p2[2]

                nx = cw0 * n0[0] + cw1 * n1[0] + cw2 * n2[0]
                ny = cw0 * n0[1] + cw1 * n1[1] + cw2 * n2[1]
                nz = cw0 * n0[2] + cw1 * n1[2] + cw2 * n2[2]
                nl = math.sqrt(nx * nx + ny * ny + nz * nz) + 1e-8
                nx /= nl
                ny /= nl
                nz /= nl
                out_norm[count, 0] = nx
                out_norm[count, 1] = ny
                out_norm[count, 2] = nz

                ndott = nx * tx + ny * ty + nz * tz
                otx = tx - ndott * nx
                oty = ty - ndott * ny
                otz = tz - ndott * nz
                otl = math.sqrt(otx * otx + oty * oty + otz * otz) + 1e-8
                otx /= otl
                oty /= otl
                otz /= otl
                out_tan[count, 0] = otx
                out_tan[count, 1] = oty
                out_tan[count, 2] = otz
                out_bitan[count, 0] = ny * otz - nz * oty
                out_bitan[count, 1] = nz * otx - nx * otz
                out_bitan[count, 2] = nx * oty - ny * otx
                count += 1

    return (
        out_py[:count],
        out_px[:count],
        out_pos[:count],
        out_norm[:count],
        out_tan[:count],
        out_bitan[:count],
    )


def dilate_atlas(arr: np.ndarray, mask: np.ndarray) -> Image.Image:
    """Fill uncovered gutter pixels via Euclidean nearest-neighbor dilation and set alpha=255."""
    if np.any(mask) and not np.all(mask):
        _, nearest_idx = distance_transform_edt(~mask, return_indices=True)
        for c in range(3):
            arr[..., c] = arr[nearest_idx[0], nearest_idx[1], c]
    arr[..., 3] = 255
    return Image.fromarray(arr, "RGBA")


# ==============================================================================
# 4. Fast Software 3/4 Item Icon Renderer
# ==============================================================================
@numba.njit(fastmath=True, nogil=True)
def _rasterize_mesh_icon(
    res: int,
    verts: np.ndarray,
    faces: np.ndarray,
    uvs: np.ndarray,
    face_uvs: np.ndarray,
    tex_rgb: np.ndarray,
) -> np.ndarray:
    """Orthographic Z-buffered 3/4 thumbnail rasterizer for item icons."""
    rgba = np.zeros((res, res, 4), dtype=np.uint8)
    zbuf = np.full((res, res), -1e30, dtype=np.float32)
    th, tw = tex_rgb.shape[0], tex_rgb.shape[1]

    yr, pr = math.radians(-25.0), math.radians(20.0)
    cx, cy, cz = math.sin(yr) * math.cos(pr), math.sin(pr), math.cos(yr) * math.cos(pr)
    rx, ry, rz = math.cos(yr), 0.0, -math.sin(yr)
    ux, uy, uz = -math.sin(yr) * math.sin(pr), math.cos(pr), -math.cos(yr) * math.sin(pr)

    u_proj = verts[:, 0] * rx + verts[:, 1] * ry + verts[:, 2] * rz
    v_proj = -(verts[:, 0] * ux + verts[:, 1] * uy + verts[:, 2] * uz)
    d_proj = verts[:, 0] * cx + verts[:, 1] * cy + verts[:, 2] * cz
    span = max(float(u_proj.max() - u_proj.min()), float(v_proj.max() - v_proj.min()), 1e-4)
    uc = 0.5 * float(u_proj.max() + u_proj.min())
    vc = 0.5 * float(v_proj.max() + v_proj.min())
    scale = (res * 0.84) / span

    sx = (u_proj - uc) * scale + res * 0.5
    sy = (v_proj - vc) * scale + res * 0.5

    for f_i in range(len(faces)):
        i0, i1, i2 = faces[f_i, 0], faces[f_i, 1], faces[f_i, 2]
        ax, ay, az = sx[i0], sy[i0], d_proj[i0]
        bx, by, bz = sx[i1], sy[i1], d_proj[i1]
        cx_p, cy_p, cz_p = sx[i2], sy[i2], d_proj[i2]

        denom = (by - cy_p) * (ax - cx_p) + (cx_p - bx) * (ay - cy_p)
        if abs(denom) < 1e-6:
            continue
        inv_d = 1.0 / denom

        u0_i, u1_i, u2_i = face_uvs[f_i, 0], face_uvs[f_i, 1], face_uvs[f_i, 2]
        x0 = max(0, int(math.floor(min(ax, min(bx, cx_p)))))
        x1 = min(res - 1, int(math.ceil(max(ax, max(bx, cx_p)))))
        y0 = max(0, int(math.floor(min(ay, min(by, cy_p)))))
        y1 = min(res - 1, int(math.ceil(max(ay, max(by, cy_p)))))

        for py in range(y0, y1 + 1):
            for px in range(x0, x1 + 1):
                w0 = ((by - cy_p) * (px + 0.5 - cx_p) + (cx_p - bx) * (py + 0.5 - cy_p)) * inv_d
                w1 = ((cy_p - ay) * (px + 0.5 - cx_p) + (ax - cx_p) * (py + 0.5 - cy_p)) * inv_d
                w2 = 1.0 - w0 - w1
                if w0 < 0.0 or w1 < 0.0 or w2 < 0.0:
                    continue
                depth = w0 * az + w1 * bz + w2 * cz_p
                if depth <= zbuf[py, px]:
                    continue
                zbuf[py, px] = depth
                u = w0 * uvs[u0_i, 0] + w1 * uvs[u1_i, 0] + w2 * uvs[u2_i, 0]
                v = w0 * uvs[u0_i, 1] + w1 * uvs[u1_i, 1] + w2 * uvs[u2_i, 1]
                tx = min(tw - 1, max(0, int(u * (tw - 1))))
                ty = min(th - 1, max(0, int((1.0 - v) * (th - 1))))
                rgba[py, px, 0] = tex_rgb[ty, tx, 0]
                rgba[py, px, 1] = tex_rgb[ty, tx, 1]
                rgba[py, px, 2] = tex_rgb[ty, tx, 2]
                rgba[py, px, 3] = 255
    return rgba


def render_mesh_icon(
    mesh: trimesh.Trimesh,
    uvs: np.ndarray,
    face_uvs: np.ndarray,
    diffuse_img: Image.Image,
    icon_res: int = 128,
) -> Image.Image:
    """Render a supersampled 3/4 item thumbnail directly from the baked low-poly mesh."""
    tex_rgb = np.asarray(diffuse_img.convert("RGB"), dtype=np.uint8)
    hi = _rasterize_mesh_icon(
        icon_res * 2,
        mesh.vertices.astype(np.float32),
        mesh.faces.astype(np.int32),
        uvs,
        face_uvs,
        tex_rgb,
    )
    return Image.fromarray(hi, "RGBA").resize((icon_res, icon_res), Image.Resampling.LANCZOS)


# ==============================================================================
# 5. Bedrock `poly_mesh` Exporter (`MCPE-186053` Safe 4-Vertex Degenerate Quads)
# ==============================================================================
def write_bedrock_polymesh_geo(
    mesh: trimesh.Trimesh,
    uvs: np.ndarray,
    face_uvs: np.ndarray,
    output_geo: Path,
    geometry_id: str,
    atlas_res: int = 1024,
    target_size: float = 24.0,
) -> None:
    """Export cleaned low-poly mesh to Bedrock `poly_mesh` using 4-vertex degenerate quads."""
    verts = mesh.vertices.astype(np.float32).copy()
    v_min = verts.min(axis=0)
    v_max = verts.max(axis=0)
    center_xz = 0.5 * (v_min + v_max)
    verts[:, 0] -= center_xz[0]
    verts[:, 2] -= center_xz[2]
    span = max(float((v_max - v_min).max()), 1e-5)
    scale = target_size / span
    verts *= scale
    verts[:, 1] -= float(verts[:, 1].min()) - 0.5

    normals = mesh.vertex_normals.astype(np.float32)
    mesh_positions = [[round(float(v[0]), 4), round(float(v[1]), 4), round(float(v[2]), 4)] for v in verts]
    mesh_normals = [[round(float(n[0]), 4), round(float(n[1]), 4), round(float(n[2]), 4)] for n in normals]
    mesh_uvs = [[round(float(uv[0]), 5), round(float(uv[1]), 5)] for uv in uvs]

    faces = mesh.faces
    polys = [
        [
            [int(i0), int(i0), int(u0)],
            [int(i1), int(i1), int(u1)],
            [int(i2), int(i2), int(u2)],
            [int(i2), int(i2), int(u2)],
        ]
        for (i0, i1, i2), (u0, u1, u2) in zip(faces, face_uvs)
    ]

    geo_json = {
        "format_version": "1.12.0",
        "minecraft:geometry": [
            {
                "description": {
                    "identifier": geometry_id,
                    "texture_width": atlas_res,
                    "texture_height": atlas_res,
                    "visible_bounds_width": 4,
                    "visible_bounds_height": 4,
                    "visible_bounds_offset": [0, 1.5, 0],
                },
                "bones": [
                    {"name": "root", "pivot": [0, 0, 0]},
                    {
                        "name": "gem_mesh",
                        "parent": "root",
                        "pivot": [0, 8, 0],
                        "poly_mesh": {
                            "normalized_uvs": True,
                            "positions": mesh_positions,
                            "normals": mesh_normals,
                            "uvs": mesh_uvs,
                            "polys": polys,
                        },
                    },
                ],
            }
        ],
    }
    output_geo.parent.mkdir(parents=True, exist_ok=True)
    output_geo.write_text(json.dumps(geo_json), encoding="utf-8")


# ==============================================================================
# 6. Pure-Python Kaydara Binary FBX v7400 Writer (`.fbx` + `_diffuse.png`)
# ==============================================================================
def _fbx_prop(ptype: str, val) -> bytes:
    if ptype == "Y":
        return b"Y" + struct.pack("<h", int(val))
    if ptype == "I":
        return b"I" + struct.pack("<i", int(val))
    if ptype == "L":
        return b"L" + struct.pack("<q", int(val))
    if ptype == "F":
        return b"F" + struct.pack("<f", float(val))
    if ptype == "D":
        return b"D" + struct.pack("<d", float(val))
    if ptype == "S":
        raw = val.encode("utf-8") if isinstance(val, str) else val
        return b"S" + struct.pack("<I", len(raw)) + raw
    if ptype == "R":
        return b"R" + struct.pack("<I", len(val)) + val
    if ptype in ("d", "i", "f"):
        dt = {"d": np.float64, "i": np.int32, "f": np.float32}[ptype]
        arr = np.ascontiguousarray(val, dtype=dt).ravel()
        raw = arr.tobytes()
        comp = zlib.compress(raw, level=6)
        use_comp = len(comp) < len(raw)
        payload = comp if use_comp else raw
        enc = 1 if use_comp else 0
        return ptype.encode("ascii") + struct.pack("<III", len(arr), enc, len(payload)) + payload
    raise ValueError(f"Unsupported FBX property type {ptype!r}")


def _fbx_node(
    name: str,
    props: list[tuple[str, object]] | None = None,
    children: list[bytes] | None = None,
    offset: int = 0,
) -> bytes:
    """Serialize one FBX v7400 node (32-bit offsets) at exact byte position `offset`."""
    name_b = name.encode("utf-8")
    prop_b = b"".join(_fbx_prop(t, v) for t, v in (props or []))
    hdr_len = 13 + len(name_b)

    child_buf = bytearray()
    if children:
        cur_off = offset + hdr_len + len(prop_b)
        for ch_name, ch_props, ch_kids in children:
            ch_bytes = _fbx_node(ch_name, ch_props, ch_kids, offset=cur_off)
            child_buf.extend(ch_bytes)
            cur_off += len(ch_bytes)
        # Every non-empty nested block ends with a 13-byte null sentinel
        child_buf.extend(b"\x00" * 13)

    end_off = offset + hdr_len + len(prop_b) + len(child_buf)
    header = struct.pack("<IIIB", end_off, len(props or []), len(prop_b), len(name_b)) + name_b
    return header + prop_b + bytes(child_buf)


def _fbx_p70_int(name: str, val: int):
    return ("P", [("S", name), ("S", "int"), ("S", "Integer"), ("S", ""), ("I", val)], None)


def _fbx_p70_enum(name: str, val: int):
    return ("P", [("S", name), ("S", "enum"), ("S", ""), ("S", ""), ("I", val)], None)


def _fbx_p70_float(name: str, val: float):
    return ("P", [("S", name), ("S", "float"), ("S", ""), ("S", "A"), ("D", val)], None)


def _fbx_p70_color(name: str, r: float, g: float, b: float):
    return ("P", [("S", name), ("S", "Color"), ("S", ""), ("S", "A"), ("D", r), ("D", g), ("D", b)], None)


def _fbx_p70_vec3(name: str, label: str, x: float, y: float, z: float):
    return ("P", [("S", name), ("S", label), ("S", ""), ("S", "A"), ("D", x), ("D", y), ("D", z)], None)


def write_binary_fbx(
    mesh: trimesh.Trimesh,
    uvs: np.ndarray,
    face_uvs: np.ndarray,
    diffuse_img: Image.Image,
    output_fbx: Path,
    normal_img: Image.Image | None = None,
    target_extent: float | None = 2.0,
) -> tuple[Path, Path]:
    """
    Export cleaned low-poly mesh, UVs, and paired diffuse texture to Kaydara Binary FBX v7400.

    Saves `<stem>.fbx` and `<stem>_diffuse.png` in the same directory (plus `<stem>_normal.png`
    if `normal_img` is provided). Coordinates are centered on XZ resting at Y=0 in standard
    right-handed Y-up FBX space (`UpAxis=1`, `FrontAxis=2`, `CoordAxis=0`).
    Returns `(fbx_path, diffuse_path)`.
    """
    output_fbx = Path(output_fbx)
    output_fbx.parent.mkdir(parents=True, exist_ok=True)
    stem = output_fbx.stem
    diffuse_path = output_fbx.with_name(f"{stem}_diffuse.png")
    diffuse_img.convert("RGBA").save(diffuse_path, optimize=True)
    if normal_img is not None:
        normal_path = output_fbx.with_name(f"{stem}_normal.png")
        normal_img.convert("RGBA").save(normal_path, optimize=True)

    verts = mesh.vertices.astype(np.float64).copy()
    v_min = verts.min(axis=0)
    v_max = verts.max(axis=0)
    center_xz = 0.5 * (v_min + v_max)
    verts[:, 0] -= center_xz[0]
    verts[:, 2] -= center_xz[2]
    if target_extent is not None:
        span = max(float((v_max - v_min).max()), 1e-5)
        verts *= target_extent / span
    verts[:, 1] -= float(verts[:, 1].min())

    normals = mesh.vertex_normals.astype(np.float64)
    faces = mesh.faces.astype(np.int32)
    pvi = faces.copy()
    pvi[:, 2] = ~pvi[:, 2]
    fuv_flat = face_uvs.astype(np.int32).ravel()

    # Deterministic 64-bit object IDs
    doc_id = 1000001
    geo_id = 1000101
    model_id = 1000201
    mat_id = 1000301
    tex_id = 1000401
    video_id = 1000501

    tex_rel = diffuse_path.name
    mat_name = f"{stem}_Material"

    top_nodes = [
        (
            "FBXHeaderExtension",
            [],
            [
                ("FBXHeaderVersion", [("I", 1003)], None),
                ("FBXVersion", [("I", 7400)], None),
                ("EncryptionType", [("I", 0)], None),
                (
                    "CreationTimeStamp",
                    [],
                    [
                        ("Version", [("I", 1000)], None),
                        ("Year", [("I", 2026)], None),
                        ("Month", [("I", 1)], None),
                        ("Day", [("I", 1)], None),
                        ("Hour", [("I", 0)], None),
                        ("Minute", [("I", 0)], None),
                        ("Second", [("I", 0)], None),
                        ("Millisecond", [("I", 0)], None),
                    ],
                ),
                ("Creator", [("S", "splat2mc FBX Exporter")], None),
            ],
        ),
        ("FileId", [("R", b"splat2mc_fbx_file_id")], None),
        ("CreationTime", [("S", "2026-01-01 00:00:00:000")], None),
        ("Creator", [("S", "splat2mc FBX Exporter")], None),
        (
            "GlobalSettings",
            [],
            [
                ("Version", [("I", 1000)], None),
                (
                    "Properties70",
                    [],
                    [
                        _fbx_p70_int("UpAxis", 1),
                        _fbx_p70_int("UpAxisSign", 1),
                        _fbx_p70_int("FrontAxis", 2),
                        _fbx_p70_int("FrontAxisSign", 1),
                        _fbx_p70_int("CoordAxis", 0),
                        _fbx_p70_int("CoordAxisSign", 1),
                        _fbx_p70_int("OriginalUpAxis", 1),
                        _fbx_p70_int("OriginalUpAxisSign", 1),
                        ("P", [("S", "UnitScaleFactor"), ("S", "double"), ("S", "Number"), ("S", ""), ("D", 100.0)], None),
                    ],
                ),
            ],
        ),
        (
            "Documents",
            [],
            [
                ("Count", [("I", 1)], None),
                (
                    "Document",
                    [("L", doc_id), ("S", "Scene"), ("S", "Scene")],
                    [
                        ("Properties70", [], [("P", [("S", "SourceObject"), ("S", "object"), ("S", ""), ("S", "")], None)]),
                        ("RootNode", [("L", 0)], None),
                    ],
                ),
            ],
        ),
        ("References", [], None),
        (
            "Definitions",
            [],
            [
                ("Version", [("I", 100)], None),
                ("Count", [("I", 6)], None),
                ("ObjectType", [("S", "GlobalSettings")], [("Count", [("I", 1)], None)]),
                ("ObjectType", [("S", "Model")], [("Count", [("I", 1)], None)]),
                ("ObjectType", [("S", "Geometry")], [("Count", [("I", 1)], None)]),
                ("ObjectType", [("S", "Material")], [("Count", [("I", 1)], None)]),
                ("ObjectType", [("S", "Texture")], [("Count", [("I", 1)], None)]),
                ("ObjectType", [("S", "Video")], [("Count", [("I", 1)], None)]),
            ],
        ),
        (
            "Objects",
            [],
            [
                (
                    "Geometry",
                    [("L", geo_id), ("S", f"{stem}\x00\x01Geometry"), ("S", "Mesh")],
                    [
                        ("Vertices", [("d", verts)], None),
                        ("PolygonVertexIndex", [("i", pvi)], None),
                        ("GeometryVersion", [("I", 124)], None),
                        (
                            "LayerElementNormal",
                            [("I", 0)],
                            [
                                ("Version", [("I", 102)], None),
                                ("Name", [("S", "")], None),
                                ("MappingInformationType", [("S", "ByVertice")], None),
                                ("ReferenceInformationType", [("S", "Direct")], None),
                                ("Normals", [("d", normals)], None),
                            ],
                        ),
                        (
                            "LayerElementUV",
                            [("I", 0)],
                            [
                                ("Version", [("I", 101)], None),
                                ("Name", [("S", "UVMap")], None),
                                ("MappingInformationType", [("S", "ByPolygonVertex")], None),
                                ("ReferenceInformationType", [("S", "IndexToDirect")], None),
                                ("UV", [("d", uvs.astype(np.float64))], None),
                                ("UVIndex", [("i", fuv_flat)], None),
                            ],
                        ),
                        (
                            "LayerElementMaterial",
                            [("I", 0)],
                            [
                                ("Version", [("I", 101)], None),
                                ("Name", [("S", "")], None),
                                ("MappingInformationType", [("S", "AllSame")], None),
                                ("ReferenceInformationType", [("S", "IndexToDirect")], None),
                                ("Materials", [("i", np.array([0], dtype=np.int32))], None),
                            ],
                        ),
                        (
                            "Layer",
                            [("I", 0)],
                            [
                                ("Version", [("I", 100)], None),
                                ("LayerElement", [], [("Type", [("S", "LayerElementNormal")], None), ("TypedIndex", [("I", 0)], None)]),
                                ("LayerElement", [], [("Type", [("S", "LayerElementUV")], None), ("TypedIndex", [("I", 0)], None)]),
                                ("LayerElement", [], [("Type", [("S", "LayerElementMaterial")], None), ("TypedIndex", [("I", 0)], None)]),
                            ],
                        ),
                    ],
                ),
                (
                    "Model",
                    [("L", model_id), ("S", f"{stem}\x00\x01Model"), ("S", "Mesh")],
                    [
                        ("Version", [("I", 232)], None),
                        (
                            "Properties70",
                            [],
                            [
                                _fbx_p70_vec3("Lcl Translation", "Lcl Translation", 0.0, 0.0, 0.0),
                                _fbx_p70_vec3("Lcl Rotation", "Lcl Rotation", 0.0, 0.0, 0.0),
                                _fbx_p70_vec3("Lcl Scaling", "Lcl Scaling", 1.0, 1.0, 1.0),
                            ],
                        ),
                        ("Shading", [("I", 1)], None),
                        ("Culling", [("S", "CullingOff")], None),
                    ],
                ),
                (
                    "Material",
                    [("L", mat_id), ("S", f"{mat_name}\x00\x01Material"), ("S", "")],
                    [
                        ("Version", [("I", 102)], None),
                        ("ShadingModel", [("S", "phong")], None),
                        ("MultiLayer", [("I", 0)], None),
                        (
                            "Properties70",
                            [],
                            [
                                _fbx_p70_color("DiffuseColor", 1.0, 1.0, 1.0),
                                _fbx_p70_color("SpecularColor", 0.05, 0.05, 0.05),
                                _fbx_p70_float("ShininessExponent", 8.0),
                            ],
                        ),
                    ],
                ),
                (
                    "Video",
                    [("L", video_id), ("S", f"{tex_rel}\x00\x01Video"), ("S", "Clip")],
                    [
                        ("Type", [("S", "Clip")], None),
                        ("Properties70", [], None),
                        ("UseMipMap", [("I", 0)], None),
                        ("Filename", [("S", tex_rel)], None),
                        ("RelativeFilename", [("S", tex_rel)], None),
                    ],
                ),
                (
                    "Texture",
                    [("L", tex_id), ("S", f"{tex_rel}\x00\x01Texture"), ("S", "")],
                    [
                        ("Type", [("S", "TextureVideoClip")], None),
                        ("Version", [("I", 202)], None),
                        ("TextureName", [("S", f"{tex_rel}\x00\x01Texture")], None),
                        (
                            "Properties70",
                            [],
                            [
                                _fbx_p70_enum("CurrentTextureBlendMode", 0),
                                ("P", [("S", "UVSet"), ("S", "KString"), ("S", ""), ("S", ""), ("S", "UVMap")], None),
                                _fbx_p70_int("UseMaterial", 1),
                            ],
                        ),
                        ("Media", [("S", f"{tex_rel}\x00\x01Video")], None),
                        ("FileName", [("S", tex_rel)], None),
                        ("RelativeFilename", [("S", tex_rel)], None),
                        ("ModelUVTranslation", [("D", 0.0), ("D", 0.0)], None),
                        ("ModelUVScaling", [("D", 1.0), ("D", 1.0)], None),
                        ("Texture_Alpha_Source", [("S", "None")], None),
                        ("Cropping", [("I", 0), ("I", 0), ("I", 0), ("I", 0)], None),
                    ],
                ),
            ],
        ),
        (
            "Connections",
            [],
            [
                ("C", [("S", "OO"), ("L", model_id), ("L", 0)], None),
                ("C", [("S", "OO"), ("L", geo_id), ("L", model_id)], None),
                ("C", [("S", "OO"), ("L", mat_id), ("L", model_id)], None),
                ("C", [("S", "OP"), ("L", tex_id), ("L", mat_id), ("S", "DiffuseColor")], None),
                ("C", [("S", "OO"), ("L", video_id), ("L", tex_id)], None),
            ],
        ),
        ("Takes", [], [("Current", [("S", "")], None)]),
    ]

    buf = bytearray()
    # 27-byte Kaydara Binary FBX v7400 header
    buf.extend(b"Kaydara FBX Binary  \x00\x1a\x00")
    buf.extend(struct.pack("<I", 7400))

    for n_name, n_props, n_kids in top_nodes:
        buf.extend(_fbx_node(n_name, n_props, n_kids, offset=len(buf)))

    # Top-level 13-byte null terminator
    buf.extend(b"\x00" * 13)

    # Standard Kaydara footer (footer_id + 16-byte alignment pad + version + 120 zeros + magic)
    buf.extend(b"\xfa\xbc\xa8\x09\xd2\xc9\xd7\x65\xb0\x70\xfa\x81\x1a\xfb\x2b\x7f")
    pad_len = ((-len(buf)) % 16) or 16
    buf.extend(b"\x00" * pad_len)
    buf.extend(struct.pack("<II", 0, 7400))
    buf.extend(b"\x00" * 116)
    buf.extend(bytes.fromhex("f85a8c6adef5d97eece90ce3758f290b"))

    output_fbx.write_bytes(bytes(buf))
    return output_fbx, diffuse_path
