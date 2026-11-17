#!/usr/bin/env python3
"""
FBX High-to-Low Poly Mesh Decimator & Texture Baker for Minecraft Bedrock.

Reads binary Kaydara FBX photogrammetry scans (`source/FBX/*.fbx` + `*_diffuse.png`),
cleans & decimates them to a single enclosed, floating-part-free low-poly mesh,
generates non-overlapping connected-chart UVs, bakes the 8K diffuse map (and
optional tangent-space normal map) onto the low-poly UVs, and exports a Bedrock
`poly_mesh` `.geo.json` using 4-vertex degenerate quads (`[v0, v1, v2, v2]`) to
avoid Bedrock's `MCPE-186053` missing-triangle bug.
"""
import argparse
import json
import math
import struct
import time
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
from scipy.spatial import cKDTree


# ==============================================================================
# 1. Pure-Python Kaydara FBX Binary Loader
# ==============================================================================
def _parse_fbx_props(f, nprops: int) -> list:
    props = []
    for _ in range(nprops):
        t = f.read(1).decode("ascii")
        if t in ("Y", "C", "I", "F", "D", "L"):
            fmt = {"Y": "<h", "C": "<?", "I": "<i", "F": "<f", "D": "<d", "L": "<q"}[t]
            props.append(struct.unpack(fmt, f.read(struct.calcsize(fmt)))[0])
        elif t in ("S", "R"):
            ln = struct.unpack("<I", f.read(4))[0]
            b = f.read(ln)
            props.append(b.decode("utf-8", "replace") if t == "S" else b)
        elif t in ("f", "d", "l", "i", "b", "c"):
            _, enc, comp_len = struct.unpack("<III", f.read(12))
            raw = f.read(comp_len)
            if enc == 1:
                raw = zlib.decompress(raw)
            dt = {
                "f": np.float32,
                "d": np.float64,
                "l": np.int64,
                "i": np.int32,
                "b": np.uint8,
                "c": np.uint8,
            }[t]
            props.append(np.frombuffer(raw, dtype=dt))
        else:
            raise ValueError(f"Unsupported FBX property type {t!r}")
    return props


def _read_fbx_node(f, is_64: bool):
    hdr_sz = 25 if is_64 else 13
    hdr = f.read(hdr_sz)
    if len(hdr) < hdr_sz:
        return None
    end_off, nprops, plen, nlen = struct.unpack("<QQQB" if is_64 else "<IIIB", hdr)
    if end_off == 0:
        return None
    name = f.read(nlen).decode("utf-8", "replace")
    ps = f.tell()
    props = _parse_fbx_props(f, nprops)
    f.seek(ps + plen)
    children = []
    if f.tell() < end_off:
        while f.tell() < end_off - hdr_sz:
            ch = _read_fbx_node(f, is_64)
            if ch is None:
                break
            children.append(ch)
    f.seek(end_off)
    return (name, props, children)


def _decode_fbx_polys_and_uvs(
    pvi: np.ndarray,
    uv_coords: np.ndarray | None,
    uv_indices: np.ndarray | None,
    uv_mapping: str,
    uv_ref: str,
) -> tuple[np.ndarray, np.ndarray | None]:
    """Decode `PolygonVertexIndex` into `(tris, tri_uv_indices)` supporting tris or general polys."""
    # Fast vectorized path for pure triangle meshes (every 3rd index < 0)
    if len(pvi) % 3 == 0 and np.all(pvi[2::3] < 0) and np.all(pvi[0::3] >= 0) and np.all(pvi[1::3] >= 0):
        tris = pvi.reshape(-1, 3).copy()
        tris[:, 2] = ~tris[:, 2]
        tris = tris.astype(np.int32)
        if uv_coords is None:
            return tris, None
        if uv_mapping == "ByPolygonVertex":
            if uv_ref == "IndexToDirect" and uv_indices is not None:
                return tris, uv_indices.reshape(-1, 3).astype(np.int32)
            return tris, np.arange(len(pvi), dtype=np.int32).reshape(-1, 3)
        if uv_mapping == "ByVertice":
            if uv_ref == "IndexToDirect" and uv_indices is not None:
                return tris, uv_indices[tris].astype(np.int32)
            return tris, tris.copy()

    # General polygon fallback (quads / n-gons)
    tris_list = []
    tri_uv_list = []
    poly = []
    poly_uvs = []
    for k, raw_idx in enumerate(pvi):
        is_end = raw_idx < 0
        v_idx = int(~raw_idx if is_end else raw_idx)
        poly.append(v_idx)
        if uv_coords is not None:
            if uv_mapping == "ByPolygonVertex":
                u_idx = int(uv_indices[k]) if (uv_ref == "IndexToDirect" and uv_indices is not None) else k
            else:
                u_idx = int(uv_indices[v_idx]) if (uv_ref == "IndexToDirect" and uv_indices is not None) else v_idx
            poly_uvs.append(u_idx)
        if is_end:
            for j in range(1, len(poly) - 1):
                tris_list.append((poly[0], poly[j], poly[j + 1]))
                if uv_coords is not None:
                    tri_uv_list.append((poly_uvs[0], poly_uvs[j], poly_uvs[j + 1]))
            poly.clear()
            poly_uvs.clear()
    tris = np.array(tris_list, dtype=np.int32)
    tri_uvs = np.array(tri_uv_list, dtype=np.int32) if uv_coords is not None else None
    return tris, tri_uvs


def load_fbx_mesh(fbx_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None]:
    """
    Load the primary mesh from a binary Kaydara FBX file and convert Z-up scan space
    to right-handed Y-up Minecraft space `(x, z, -y)`.
    Returns `(verts, tris, uv_coords, tri_uv_indices)`.
    """
    with open(fbx_path, "rb") as f:
        magic = f.read(23)
        if not magic.startswith(b"Kaydara FBX Binary"):
            raise ValueError(f"Unsupported FBX format (expected binary FBX): {fbx_path}")
        ver = struct.unpack("<I", f.read(4))[0]
        is_64 = ver >= 7500

        verts = None
        pvi = None
        uv_coords = None
        uv_indices = None
        uv_mapping = "ByPolygonVertex"
        uv_ref = "Direct"
        lcl_scale = np.array([1.0, 1.0, 1.0], dtype=np.float64)
        lcl_rot = np.array([0.0, 0.0, 0.0], dtype=np.float64)
        lcl_trans = np.array([0.0, 0.0, 0.0], dtype=np.float64)

        while True:
            node = _read_fbx_node(f, is_64)
            if node is None:
                break
            if node[0] == "Objects":
                for obj in node[2]:
                    if obj[0] == "Geometry" and verts is None:
                        for ch in obj[2]:
                            if ch[0] == "Vertices":
                                verts = ch[1][0].reshape(-1, 3).astype(np.float64)
                            elif ch[0] == "PolygonVertexIndex":
                                pvi = ch[1][0]
                            elif ch[0] == "LayerElementUV" and uv_coords is None:
                                for uvc in ch[2]:
                                    if uvc[0] == "MappingInformationType":
                                        uv_mapping = str(uvc[1][0])
                                    elif uvc[0] == "ReferenceInformationType":
                                        uv_ref = str(uvc[1][0])
                                    elif uvc[0] == "UV":
                                        uv_coords = uvc[1][0].reshape(-1, 2).astype(np.float32)
                                    elif uvc[0] == "UVIndex":
                                        uv_indices = uvc[1][0]
                    elif obj[0] == "Model":
                        for ch in obj[2]:
                            if ch[0] == "Properties70":
                                for pr in ch[2]:
                                    if pr[1]:
                                        pname = str(pr[1][0])
                                        if pname == "Lcl Translation":
                                            lcl_trans = np.array(pr[1][4:7], dtype=np.float64)
                                        elif pname == "Lcl Rotation":
                                            lcl_rot = np.array(pr[1][4:7], dtype=np.float64)
                                        elif pname == "Lcl Scaling":
                                            lcl_scale = np.array(pr[1][4:7], dtype=np.float64)

    if verts is None or pvi is None:
        raise ValueError(f"No mesh geometry found in {fbx_path}")

    # Apply Model local TRS if non-identity
    verts = verts * lcl_scale[None, :]
    if np.any(np.abs(lcl_rot) > 1e-6):
        rx, ry, rz = np.radians(lcl_rot)
        cx, sx = math.cos(rx), math.sin(rx)
        cy, sy = math.cos(ry), math.sin(ry)
        cz, sz = math.cos(rz), math.sin(rz)
        Rx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
        Ry = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
        Rz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
        verts = verts @ (Rz @ Ry @ Rx).T
    verts = verts + lcl_trans[None, :]

    # Convert Z-up photogrammetry scan space -> Y-up right-handed Bedrock space: (x, y, z) -> (x, z, -y)
    verts_yup = np.column_stack([verts[:, 0], verts[:, 2], -verts[:, 1]]).astype(np.float32)
    tris, tri_uvs = _decode_fbx_polys_and_uvs(pvi, uv_coords, uv_indices, uv_mapping, uv_ref)
    return verts_yup, tris, uv_coords, tri_uvs


# ==============================================================================
# 2. Connected-Component Cleaning & High-to-Low Decimation
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


def decimate_and_clean_fbx(
    hi_verts: np.ndarray,
    hi_tris: np.ndarray,
    target_tris: int = 16000,
    smooth_iters: int = 1,
) -> trimesh.Trimesh:
    """
    Clean high-poly scan to its single main enclosed object, decimate via QEM,
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
# 3. Overlap-Free Connected-Chart UV Unwrapping & Shelf Packing
# ==============================================================================
_BASIS_AXES = [
    (np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, -1.0]), np.array([0.0, 1.0, 0.0])),   # +X
    (np.array([-1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0]), np.array([0.0, 1.0, 0.0])),   # -X
    (np.array([0.0, 1.0, 0.0]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, -1.0])),   # +Y
    (np.array([0.0, -1.0, 0.0]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 0.0, 1.0])),   # -Y
    (np.array([0.0, 0.0, 1.0]), np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])),    # +Z
    (np.array([0.0, 0.0, -1.0]), np.array([-1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])),  # -Z
]


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

    # Peel off any faces that curl past orthogonal (> ~78 deg from mean normal)
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

    # Fast grid overlap probe across face centroids/triangles in 2D chart projection
    tri_v = verts[faces[face_ids]]
    uv2 = np.stack([tri_v @ u_ax, tri_v @ v_ax], axis=-1)  # (K, 3, 2)
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

    # Check if two distinct layers overlap at the same 2D (u, v) using depth-sorted centroid bins
    mins = uv2.reshape(-1, 2).min(axis=0)
    maxs = uv2.reshape(-1, 2).max(axis=0)
    span = np.maximum(maxs - mins, 1e-6)
    cents_2d = uv2.mean(axis=1)
    gx = np.clip(((cents_2d[:, 0] - mins[0]) / span[0] * 48).astype(np.int32), 0, 47)
    gy = np.clip(((cents_2d[:, 1] - mins[1]) / span[1] * 48).astype(np.int32), 0, 47)
    cell_ids = gy * 48 + gx
    depths = tri_v.mean(axis=1) @ n_dir
    # If same 2D bin has centroids separated in depth by > 15% of span, split by median depth
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

    # Dominant cube-axis per face (0..5)
    abs_n = np.abs(fn)
    dom_axis = np.argmax(abs_n, axis=1)
    dom_sign = np.where(fn[np.arange(n_faces), dom_axis] >= 0, 0, 1)
    dom_dir = dom_axis * 2 + dom_sign

    # Connected face charts sharing dominant direction & smooth normal dot
    adj_pairs = mesh.face_adjacency
    valid_adj = (dom_dir[adj_pairs[:, 0]] == dom_dir[adj_pairs[:, 1]]) & (
        np.sum(fn[adj_pairs[:, 0]] * fn[adj_pairs[:, 1]], axis=1) > 0.40
    )
    vp = adj_pairs[valid_adj]
    f_adj = csr_matrix(
        (np.ones(len(vp) * 2, dtype=np.int8), (np.concatenate([vp[:, 0], vp[:, 1]]), np.concatenate([vp[:, 1], vp[:, 0]]))),
        shape=(n_faces, n_faces),
    )
    n_charts, f_labels = connected_components(f_adj, directed=False)

    groups: dict[int, list[int]] = {}
    for fid, lbl in enumerate(f_labels):
        groups.setdefault(int(lbl), []).append(fid)

    raw_charts = []
    for fids in groups.values():
        f_arr = np.array(fids, dtype=np.int32)
        raw_charts.extend(_split_chart_if_overlapping(f_arr, verts, faces, fn))

    # Compute 2D bounding boxes in world units for each chart
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
        # Rotate portrait charts if taller than wide for tighter shelf packing
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

    # Sort tallest first for compact shelf packing
    chart_data.sort(key=lambda c: c["h"], reverse=True)

    # Binary search uniform world-to-pixel scale so all charts fit cleanly inside atlas_res
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
        # Convert image-top-down v_px to standard UV bottom-up [0, 1]
        v_norm = 1.0 - (v_px / float(atlas_res))
        uv_list.append(np.column_stack([u_norm, v_norm]))
        face_uv_idx[ch["f_arr"]] = ch["inv_f"] + uv_offset
        uv_offset += len(u_norm)

    uv_coords = np.vstack(uv_list).astype(np.float32)
    return uv_coords, face_uv_idx


# ==============================================================================
# 4. Numba High-to-Low Texture & Optional Normal Baker
# ==============================================================================
@numba.njit(fastmath=True, nogil=True)
def _rasterize_low_uv_chart(
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

        # UV in pixel coords (top-down py)
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

        # Per-triangle tangent/bitangent for optional normal baking
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
                # Slight -0.02 tolerance captures boundary texels cleanly before dilation
                if w0 < -0.02 or w1 < -0.02 or w2 < -0.02:
                    continue
                # Clamp weights onto triangle
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

                # Gram-Schmidt orthogonalize tangent & compute bitangent
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


@numba.njit(fastmath=True, nogil=True)
def _closest_bary_on_tri(
    px: float, py: float, pz: float,
    ax: float, ay: float, az: float,
    bx: float, by: float, bz: float,
    cx: float, cy: float, cz: float,
) -> tuple[float, float, float, float]:
    """Return `(dist2, w0, w1, w2)` from point P to triangle ABC (Ericson algorithm)."""
    abx, aby, abz = bx - ax, by - ay, bz - az
    acx, acy, acz = cx - ax, cy - ay, cz - az
    apx, apy, apz = px - ax, py - ay, pz - az

    d1 = abx * apx + aby * apy + abz * apz
    d2 = acx * apx + acy * apy + acz * apz
    if d1 <= 0.0 and d2 <= 0.0:
        dx, dy, dz = px - ax, py - ay, pz - az
        return dx * dx + dy * dy + dz * dz, 1.0, 0.0, 0.0

    bpx, bpy, bpz = px - bx, py - by, pz - bz
    d3 = abx * bpx + aby * bpy + abz * bpz
    d4 = acx * bpx + acy * bpy + acz * bpz
    if d3 >= 0.0 and d4 <= d3:
        dx, dy, dz = px - bx, py - by, pz - bz
        return dx * dx + dy * dy + dz * dz, 0.0, 1.0, 0.0

    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3)
        qx, qy, qz = ax + v * abx, ay + v * aby, az + v * abz
        dx, dy, dz = px - qx, py - qy, pz - qz
        return dx * dx + dy * dy + dz * dz, 1.0 - v, v, 0.0

    cpx, cpy, cpz = px - cx, py - cy, pz - cz
    d5 = abx * cpx + aby * cpy + abz * cpz
    d6 = acx * cpx + acy * cpy + acz * cpz
    if d6 >= 0.0 and d5 <= d6:
        dx, dy, dz = px - cx, py - cy, pz - cz
        return dx * dx + dy * dy + dz * dz, 0.0, 0.0, 1.0

    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6)
        qx, qy, qz = ax + w * acx, ay + w * acy, az + w * acz
        dx, dy, dz = px - qx, py - qy, pz - qz
        return dx * dx + dy * dy + dz * dz, 1.0 - w, 0.0, w

    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        qx, qy, qz = bx + w * (cx - bx), by + w * (cy - by), bz + w * (cz - bz)
        dx, dy, dz = px - qx, py - qy, pz - qz
        return dx * dx + dy * dy + dz * dz, 0.0, 1.0 - w, w

    denom = 1.0 / (va + vb + vc)
    v = vb * denom
    w = vc * denom
    u = 1.0 - v - w
    qx = u * ax + v * bx + w * cx
    qy = u * ay + v * by + w * cy
    qz = u * az + v * bz + w * cz
    dx, dy, dz = px - qx, py - qy, pz - qz
    return dx * dx + dy * dy + dz * dz, u, v, w


@numba.njit(parallel=True, fastmath=True, nogil=True)
def _bake_texels_kernel(
    tex_pos: np.ndarray,
    tex_norm: np.ndarray,
    tex_tan: np.ndarray,
    tex_bitan: np.ndarray,
    cand_tris: np.ndarray,
    hi_verts: np.ndarray,
    hi_tris: np.ndarray,
    hi_uvs: np.ndarray,
    hi_tri_uvs: np.ndarray,
    diffuse_rgb: np.ndarray,
    bake_normals: bool,
) -> tuple[np.ndarray, np.ndarray]:
    """Project each low-poly texel onto candidate high-poly triangles and sample 8K diffuse + normal."""
    m = len(tex_pos)
    k = cand_tris.shape[1]
    img_h, img_w = diffuse_rgb.shape[0], diffuse_rgb.shape[1]
    out_rgb = np.empty((m, 3), dtype=np.uint8)
    out_nrm = np.empty((m, 3), dtype=np.uint8)

    for idx in numba.prange(m):
        px, py, pz = tex_pos[idx, 0], tex_pos[idx, 1], tex_pos[idx, 2]
        best_d2 = 1e30
        best_tri = cand_tris[idx, 0]
        bw0, bw1, bw2 = 1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0

        for j in range(k):
            t_id = cand_tris[idx, j]
            i0, i1, i2 = hi_tris[t_id, 0], hi_tris[t_id, 1], hi_tris[t_id, 2]
            d2, w0, w1, w2 = _closest_bary_on_tri(
                px, py, pz,
                hi_verts[i0, 0], hi_verts[i0, 1], hi_verts[i0, 2],
                hi_verts[i1, 0], hi_verts[i1, 1], hi_verts[i1, 2],
                hi_verts[i2, 0], hi_verts[i2, 1], hi_verts[i2, 2],
            )
            if d2 < best_d2:
                best_d2 = d2
                best_tri = t_id
                bw0, bw1, bw2 = w0, w1, w2

        u0_i = hi_tri_uvs[best_tri, 0]
        u1_i = hi_tri_uvs[best_tri, 1]
        u2_i = hi_tri_uvs[best_tri, 2]
        u = bw0 * hi_uvs[u0_i, 0] + bw1 * hi_uvs[u1_i, 0] + bw2 * hi_uvs[u2_i, 0]
        v = bw0 * hi_uvs[u0_i, 1] + bw1 * hi_uvs[u1_i, 1] + bw2 * hi_uvs[u2_i, 1]

        # Wrap UVs into [0, 1) and bilinearly sample diffuse_rgb (top-down row = (1 - v))
        u_w = u - math.floor(u)
        v_w = v - math.floor(v)
        fx = u_w * (img_w - 1)
        fy = (1.0 - v_w) * (img_h - 1)
        x0 = min(img_w - 1, max(0, int(math.floor(fx))))
        y0 = min(img_h - 1, max(0, int(math.floor(fy))))
        x1 = min(img_w - 1, x0 + 1)
        y1 = min(img_h - 1, y0 + 1)
        dx = fx - x0
        dy = fy - y0
        for c in range(3):
            c00 = float(diffuse_rgb[y0, x0, c])
            c10 = float(diffuse_rgb[y0, x1, c])
            c01 = float(diffuse_rgb[y1, x0, c])
            c11 = float(diffuse_rgb[y1, x1, c])
            val = (c00 * (1.0 - dx) + c10 * dx) * (1.0 - dy) + (c01 * (1.0 - dx) + c11 * dx) * dy
            out_rgb[idx, c] = min(255, max(0, int(val + 0.5)))

        if bake_normals:
            i0, i1, i2 = hi_tris[best_tri, 0], hi_tris[best_tri, 1], hi_tris[best_tri, 2]
            e1x = hi_verts[i1, 0] - hi_verts[i0, 0]
            e1y = hi_verts[i1, 1] - hi_verts[i0, 1]
            e1z = hi_verts[i1, 2] - hi_verts[i0, 2]
            e2x = hi_verts[i2, 0] - hi_verts[i0, 0]
            e2y = hi_verts[i2, 1] - hi_verts[i0, 1]
            e2z = hi_verts[i2, 2] - hi_verts[i0, 2]
            hnx = e1y * e2z - e1z * e2y
            hny = e1z * e2x - e1x * e2z
            hnz = e1x * e2y - e1y * e2x
            hl = math.sqrt(hnx * hnx + hny * hny + hnz * hnz) + 1e-8
            hnx /= hl
            hny /= hl
            hnz /= hl
            # Flip if winding disagrees with low-poly smooth normal
            if hnx * tex_norm[idx, 0] + hny * tex_norm[idx, 1] + hnz * tex_norm[idx, 2] < 0.0:
                hnx, hny, hnz = -hnx, -hny, -hnz
            tnx = hnx * tex_tan[idx, 0] + hny * tex_tan[idx, 1] + hnz * tex_tan[idx, 2]
            tny = hnx * tex_bitan[idx, 0] + hny * tex_bitan[idx, 1] + hnz * tex_bitan[idx, 2]
            tnz = hnx * tex_norm[idx, 0] + hny * tex_norm[idx, 1] + hnz * tex_norm[idx, 2]
            out_nrm[idx, 0] = min(255, max(0, int((tnx * 0.5 + 0.5) * 255.0 + 0.5)))
            out_nrm[idx, 1] = min(255, max(0, int((tny * 0.5 + 0.5) * 255.0 + 0.5)))
            out_nrm[idx, 2] = min(255, max(0, int((max(0.0, tnz) * 0.5 + 0.5) * 255.0 + 0.5)))

    return out_rgb, out_nrm


def _dilate_atlas(arr: np.ndarray, mask: np.ndarray) -> Image.Image:
    """Fill uncovered gutter pixels via Euclidean nearest-neighbor dilation and set alpha=255."""
    if np.any(mask) and not np.all(mask):
        _, nearest_idx = distance_transform_edt(~mask, return_indices=True)
        for c in range(3):
            arr[..., c] = arr[nearest_idx[0], nearest_idx[1], c]
    arr[..., 3] = 255
    return Image.fromarray(arr, "RGBA")


def bake_high_to_low_textures(
    hi_verts: np.ndarray,
    hi_tris: np.ndarray,
    hi_uvs: np.ndarray,
    hi_tri_uvs: np.ndarray,
    diffuse_path: Path,
    low_mesh: trimesh.Trimesh,
    low_uvs: np.ndarray,
    low_face_uvs: np.ndarray,
    atlas_res: int = 1024,
    bake_normals: bool = False,
    hi_tree: cKDTree | None = None,
    diffuse_rgb: np.ndarray | None = None,
) -> tuple[Image.Image, Image.Image | None]:
    """Bake high-poly diffuse (and optional tangent-space normal map) onto low-poly UVs."""
    if diffuse_rgb is None:
        with Image.open(diffuse_path) as im:
            diffuse_rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)

    if hi_tree is None:
        hi_centroids = (
            hi_verts[hi_tris[:, 0]] + hi_verts[hi_tris[:, 1]] + hi_verts[hi_tris[:, 2]]
        ) * (1.0 / 3.0)
        hi_tree = cKDTree(hi_centroids, leafsize=32)

    low_verts = low_mesh.vertices.astype(np.float32)
    low_norms = low_mesh.vertex_normals.astype(np.float32)
    low_faces = low_mesh.faces.astype(np.int32)

    pys, pxs, t_pos, t_norm, t_tan, t_bitan = _rasterize_low_uv_chart(
        atlas_res, low_verts, low_norms, low_faces, low_uvs, low_face_uvs
    )

    _, cand_tris = hi_tree.query(t_pos, k=12, workers=-1)
    cand_tris = np.asarray(cand_tris, dtype=np.int32)

    baked_rgb, baked_nrm = _bake_texels_kernel(
        t_pos,
        t_norm,
        t_tan,
        t_bitan,
        cand_tris,
        hi_verts,
        hi_tris,
        hi_uvs,
        hi_tri_uvs,
        diffuse_rgb,
        bake_normals,
    )

    diff_atlas = np.zeros((atlas_res, atlas_res, 4), dtype=np.uint8)
    mask = np.zeros((atlas_res, atlas_res), dtype=bool)
    diff_atlas[pys, pxs, :3] = baked_rgb
    mask[pys, pxs] = True
    diff_img = _dilate_atlas(diff_atlas, mask)

    nrm_img = None
    if bake_normals:
        nrm_atlas = np.zeros((atlas_res, atlas_res, 4), dtype=np.uint8)
        nrm_atlas[pys, pxs, :3] = baked_nrm
        nrm_img = _dilate_atlas(nrm_atlas, mask)

    return diff_img, nrm_img


# ==============================================================================
# 5. Fast Software 3/4 Item Icon Renderer (from baked low-poly mesh)
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

    # Camera at yaw=-25°, pitch=20°
    yr, pr = math.radians(-25.0), math.radians(20.0)
    cx, cy, cz = math.sin(yr) * math.cos(pr), math.sin(pr), math.cos(yr) * math.cos(pr)
    rx, ry, rz = math.cos(yr), 0.0, -math.sin(yr)
    ux, uy, uz = -math.sin(yr) * math.sin(pr), math.cos(pr), -math.cos(yr) * math.sin(pr)

    # Compute screen bounds
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
        if denom >= -1e-6:
            # Back-face or degenerate in screen (Y-down screen flips sign)
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


def render_fbx_icon(
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
# 6. Bedrock `poly_mesh` Exporter (`MCPE-186053` Safe 4-Vertex Degenerate Quads)
# ==============================================================================
def write_fbx_bedrock_geo(
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
    # Rest bottom cleanly at y = 0.5 Bedrock units
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


def find_matching_diffuse(fbx_path: Path) -> Path:
    """Locate the matching `*_diffuse.png` texture alongside `fbx_path`."""
    candidates = [
        fbx_path.with_name(f"{fbx_path.stem}_diffuse.png"),
        fbx_path.with_name(f"{fbx_path.stem}.png"),
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError(f"No matching *_diffuse.png found for {fbx_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="FBX High-to-Low Poly Decimator & Texture Baker for Minecraft Bedrock."
    )
    parser.add_argument("fbx_file", type=Path, help="Input binary .fbx file")
    parser.add_argument("--diffuse", type=Path, default=None, help="Input diffuse PNG (auto-detected if omitted)")
    parser.add_argument("--geo-out", type=Path, required=True, help="Output Bedrock .geo.json path")
    parser.add_argument("--tex-out", type=Path, required=True, help="Output baked diffuse PNG path")
    parser.add_argument("--normal-out", type=Path, default=None, help="Optional output normal map PNG path")
    parser.add_argument("--geo-id", type=str, default="geometry.fbx_gem_0", help="Bedrock geometry identifier")
    parser.add_argument("--tris", type=int, default=16000, help="Target triangle count (default: 16000)")
    parser.add_argument("--res", type=int, default=1024, help="Baked texture resolution (default: 1024)")
    parser.add_argument("--smooth-iters", type=int, default=1, help="Taubin smoothing passes (default: 1)")
    parser.add_argument("--bake-normals", action="store_true", help="Also bake tangent-space normal map")
    args = parser.parse_args()

    t0 = time.time()
    diff_path = args.diffuse or find_matching_diffuse(args.fbx_file)
    hi_v, hi_f, hi_uv, hi_fuv = load_fbx_mesh(args.fbx_file)
    low_mesh = decimate_and_clean_fbx(hi_v, hi_f, target_tris=args.tris, smooth_iters=args.smooth_iters)
    low_uv, low_fuv = unwrap_connected_charts(low_mesh, atlas_res=args.res)
    diff_img, nrm_img = bake_high_to_low_textures(
        hi_v, hi_f, hi_uv, hi_fuv, diff_path, low_mesh, low_uv, low_fuv,
        atlas_res=args.res, bake_normals=args.bake_normals,
    )
    args.tex_out.parent.mkdir(parents=True, exist_ok=True)
    diff_img.save(args.tex_out, optimize=True)
    if args.bake_normals and nrm_img is not None:
        nrm_path = args.normal_out or args.tex_out.with_name(f"{args.tex_out.stem}_normal.png")
        nrm_img.save(nrm_path, optimize=True)
    write_fbx_bedrock_geo(low_mesh, low_uv, low_fuv, args.geo_out, args.geo_id, atlas_res=args.res)
    print(f"✓ Baked {args.fbx_file.name} -> {len(low_mesh.faces):,} tris @ {args.res}px in {time.time() - t0:.2f}s")
