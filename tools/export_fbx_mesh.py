#!/usr/bin/env python3
"""
FBX High-to-Low Poly Mesh Decimator & Texture Baker for Minecraft Bedrock.

Reads binary Kaydara FBX photogrammetry scans (`gem_files/FBX/*.fbx` + `*_diffuse.png`),
cleans & decimates them to a single enclosed, floating-part-free low-poly mesh,
generates non-overlapping connected-chart UVs, bakes the 8K diffuse map (and
optional tangent-space normal map) onto the low-poly UVs, and exports a Bedrock
`poly_mesh` `.geo.json` using 4-vertex degenerate quads (`[v0, v1, v2, v2]`) to
avoid Bedrock's `MCPE-186053` missing-triangle bug.
"""
import argparse
import math
import struct
import time
import zlib
from pathlib import Path

import numba
import numpy as np
import trimesh
from PIL import Image
from scipy.spatial import cKDTree

from mesh_bake_utils import (
    decimate_and_clean_mesh,
    dilate_atlas,
    keep_largest_component_arrays,
    rasterize_low_uv_chart,
    render_mesh_icon,
    unwrap_connected_charts,
    write_bedrock_polymesh_geo,
    write_binary_fbx,
)

# Backwards-compatible aliases
decimate_and_clean_fbx = decimate_and_clean_mesh
render_fbx_icon = render_mesh_icon
write_fbx_bedrock_geo = write_bedrock_polymesh_geo


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


def load_fbx_mesh(
    fbx_path: Path,
    convert_zup: bool = True,
) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray | None]:
    """
    Load the primary mesh from a binary Kaydara FBX file.
    By default (`convert_zup=True`) converts Z-up scan coordinates to Y-up `(x, z, -y)`.
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

    if convert_zup:
        verts_out = np.column_stack([verts[:, 0], verts[:, 2], -verts[:, 1]]).astype(np.float32)
    else:
        verts_out = verts.astype(np.float32)
    tris, tri_uvs = _decode_fbx_polys_and_uvs(pvi, uv_coords, uv_indices, uv_mapping, uv_ref)
    return verts_out, tris, uv_coords, tri_uvs


# ==============================================================================
# 2. High-to-Low Texture & Optional Normal Baker (From High-Poly FBX Scan)
# ==============================================================================
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
    """Project each low-poly texel onto candidate high-poly triangles and sample diffuse + normal."""
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
            if hnx * tex_norm[idx, 0] + hny * tex_norm[idx, 1] + hnz * tex_norm[idx, 2] < 0.0:
                hnx, hny, hnz = -hnx, -hny, -hnz
            tnx = hnx * tex_tan[idx, 0] + hny * tex_tan[idx, 1] + hnz * tex_tan[idx, 2]
            tny = hnx * tex_bitan[idx, 0] + hny * tex_bitan[idx, 1] + hnz * tex_bitan[idx, 2]
            tnz = hnx * tex_norm[idx, 0] + hny * tex_norm[idx, 1] + hnz * tex_norm[idx, 2]
            out_nrm[idx, 0] = min(255, max(0, int((tnx * 0.5 + 0.5) * 255.0 + 0.5)))
            out_nrm[idx, 1] = min(255, max(0, int((tny * 0.5 + 0.5) * 255.0 + 0.5)))
            out_nrm[idx, 2] = min(255, max(0, int((max(0.0, tnz) * 0.5 + 0.5) * 255.0 + 0.5)))

    return out_rgb, out_nrm


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

    pys, pxs, t_pos, t_norm, t_tan, t_bitan = rasterize_low_uv_chart(
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
    diff_img = dilate_atlas(diff_atlas, mask)

    nrm_img = None
    if bake_normals:
        nrm_atlas = np.zeros((atlas_res, atlas_res, 4), dtype=np.uint8)
        nrm_atlas[pys, pxs, :3] = baked_nrm
        nrm_img = dilate_atlas(nrm_atlas, mask)

    return diff_img, nrm_img


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
    parser.add_argument("--fbx-out", type=Path, default=None, help="Optional output reduced .fbx path (+_diffuse.png)")
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
    if args.fbx_out is not None:
        write_binary_fbx(low_mesh, low_uv, low_fuv, diff_img, args.fbx_out, normal_img=nrm_img)
    print(f"✓ Baked {args.fbx_file.name} -> {len(low_mesh.faces):,} tris @ {args.res}px in {time.time() - t0:.2f}s")
