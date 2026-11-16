#!/usr/bin/env python3
"""
Quad-Sphere Shrinkwrap Surface Map & 6-View Orthographic High-to-Low 3DGS Baker for Minecraft Bedrock.

Uses a watertight 6-patch Quad-Sphere (`make_quad_sphere`) where every face in `poly_mesh["polys"]`
is a true 4-vertex quad `[v0, v1, v2, v3]`. This completely avoids Bedrock's `MCPE-186053` bug
(where 3-vertex triangles in `poly_mesh` lose every other face and stretch shards across the interior).

Supports 3 Mesh Styles (`--mode`):
- `smooth`   (16x16x6 = 1,536 quads / 3,072 tris, smooth normals)
- `detailed` (28x28x6 = 4,704 quads / 9,408 tris, high-detail crystal contour)
- `faceted`  ( 7x7x6  =   294 quads /   588 tris, flat per-quad normals for a crystal look)
"""
import argparse
import json
import math
import time
from pathlib import Path
import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt
from scipy.spatial import cKDTree

from render_imposter import GaussianScene


ORTHO_6_VIEWS = [
    (0.0, 0.0),     # 0: Front (+Z)
    (180.0, 0.0),   # 1: Back (-Z)
    (90.0, 0.0),    # 2: Right (+X)
    (-90.0, 0.0),   # 3: Left (-X)
    (0.0, 90.0),    # 4: Top (+Y)
    (0.0, -90.0),   # 5: Bottom (-Y)
]


def render_6view_ortho_atlas(
    scene: GaussianScene,
    tile_res: int = 512,
    fill_ratio: float = 0.82,
    hue_shift_deg: float = 0.0,
) -> Image.Image:
    """
    Render 6 orthographic 3DGS views (Front, Back, Right, Left, Top, Bottom) into a 3x2 atlas,
    using Euclidean distance-transform edge dilation so UV borders never sample background black.
    """
    atlas = np.zeros((tile_res * 2, tile_res * 3, 4), dtype=np.uint8)
    for idx, (yaw, pitch) in enumerate(ORTHO_6_VIEWS):
        img = scene.render_view(
            yaw_deg=yaw,
            pitch_deg=pitch,
            tile_res=tile_res,
            fill_ratio=fill_ratio,
            hue_shift_deg=hue_shift_deg,
        )
        arr = np.array(img, dtype=np.uint8)
        mask = arr[..., 3] > 28
        if np.any(mask):
            _, nearest_idx = distance_transform_edt(~mask, return_indices=True)
            for c in range(3):
                arr[..., c] = arr[nearest_idx[0], nearest_idx[1], c]
        arr[..., 3] = 255
        col, row = idx % 3, idx // 3
        atlas[row * tile_res : (row + 1) * tile_res, col * tile_res : (col + 1) * tile_res] = arr
    return Image.fromarray(atlas, "RGBA")


def make_quad_sphere(n: int) -> tuple[np.ndarray, np.ndarray, list[set[int]]]:
    """
    Build a watertight unit Quad-Sphere with 6 * n * n outward-wound 4-vertex quads.
    Uses tangent-angle cube-to-sphere projection for uniform quad area across the sphere.
    Returns (u_dirs, quads, vertex_neighbors).
    """
    t = np.tan(np.linspace(-1.0, 1.0, n + 1) * (math.pi / 4.0))
    raw_verts = []
    raw_quads = []

    for face in range(6):
        base = len(raw_verts)
        for r in range(n + 1):
            for c in range(n + 1):
                u, v = float(t[c]), float(-t[r])
                if face == 0:
                    vec = (u, v, 1.0)
                elif face == 1:
                    vec = (-u, v, -1.0)
                elif face == 2:
                    vec = (1.0, v, -u)
                elif face == 3:
                    vec = (-1.0, v, u)
                elif face == 4:
                    vec = (u, 1.0, -v)
                else:
                    vec = (u, -1.0, v)
                norm = math.sqrt(vec[0] * vec[0] + vec[1] * vec[1] + vec[2] * vec[2])
                raw_verts.append((vec[0] / norm, vec[1] / norm, vec[2] / norm))

        for r in range(n):
            for c in range(n):
                i00 = base + r * (n + 1) + c
                i10 = base + (r + 1) * (n + 1) + c
                i11 = base + (r + 1) * (n + 1) + (c + 1)
                i01 = base + r * (n + 1) + (c + 1)
                raw_quads.append((i00, i10, i11, i01))

    raw_verts_arr = np.array(raw_verts, dtype=np.float64)
    rounded = np.round(raw_verts_arr * 1e6).astype(np.int64)
    _, uniq_idx, inv = np.unique(rounded, axis=0, return_index=True, return_inverse=True)

    u_dirs = raw_verts_arr[uniq_idx].astype(np.float32)
    quads = inv[np.array(raw_quads, dtype=np.int32)]

    adj: list[set[int]] = [set() for _ in range(len(u_dirs))]
    for q0, q1, q2, q3 in quads:
        adj[q0].update((q1, q3))
        adj[q1].update((q0, q2))
        adj[q2].update((q1, q3))
        adj[q3].update((q2, q0))

    return u_dirs, quads, adj


def build_shrinkwrap_mesh(
    scene: GaussianScene,
    mode: str = "smooth",
    target_size: float = 24.0,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Conform a watertight Quad-Sphere onto the 3DGS splat cloud (Shrinkwrap surface map).
    Filters out oversized background/filler Gaussians so the hull tightly follows crystal geometry.
    Returns (verts_scene, verts_mc, vert_normals, quads).
    """
    if mode == "detailed":
        grid_n = 28       # 4,704 quads (9,408 tris)
        smooth_iters = 1
        cone_radius = 0.09
        percentile_q = 86
    elif mode == "faceted":
        grid_n = 7        # 294 quads (588 tris)
        smooth_iters = 1
        cone_radius = 0.16
        percentile_q = 85
    else:  # "smooth"
        grid_n = 16       # 1,536 quads (3,072 tris)
        smooth_iters = 2
        cone_radius = 0.12
        percentile_q = 86

    # Filter out oversized filler Gaussians (> 85th percentile scale) so hull hugs true crystal shape
    s_max = np.maximum(np.maximum(scene.sx, scene.sy), scene.sz)
    scale_cap = np.percentile(s_max, 85)
    solid = (scene.op > 0.10) & (s_max <= scale_cap)
    if np.sum(solid) < 64:
        solid = scene.op > 0.08

    pts = np.column_stack([scene.x[solid], scene.y[solid], scene.z[solid]])
    pt_r = np.linalg.norm(pts, axis=1) + 1e-6
    pt_dirs = pts / pt_r[:, None]
    tree = cKDTree(pt_dirs)

    u_dirs, quads, adj = make_quad_sphere(grid_n)

    radii = np.zeros(len(u_dirs), dtype=np.float32)
    for i, u in enumerate(u_dirs):
        idx = tree.query_ball_point(u, r=cone_radius)
        if len(idx) > 6:
            radii[i] = np.percentile(pt_r[idx], percentile_q)
        else:
            _, nn = tree.query(u, k=18)
            radii[i] = np.percentile(pt_r[nn], percentile_q - 5)

    for _ in range(smooth_iters):
        new_r = radii.copy()
        for i, nbrs in enumerate(adj):
            if nbrs:
                nbr_list = list(nbrs)
                new_r[i] = 0.58 * radii[i] + 0.42 * float(np.mean(radii[nbr_list]))
        radii = new_r

    verts_scene = (u_dirs * radii[:, None]).astype(np.float32)
    scale_factor = target_size / scene.span
    verts_mc = (verts_scene * scale_factor).astype(np.float32)
    # Shift Y so bottom of mesh sits cleanly on ground/pedestal (min Y = 0.5 model units)
    verts_mc[:, 1] -= float(np.min(verts_mc[:, 1])) - 0.5

    # Compute outward quad normals & accumulate smooth vertex normals
    p0 = verts_mc[quads[:, 0]]
    p1 = verts_mc[quads[:, 1]]
    p2 = verts_mc[quads[:, 2]]
    p3 = verts_mc[quads[:, 3]]
    fn = np.cross(p1 - p0, p2 - p0) + np.cross(p2 - p0, p3 - p0)
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-8)

    vert_normals = np.zeros_like(verts_mc)
    for corner in range(4):
        np.add.at(vert_normals, quads[:, corner], fn)
    vert_normals /= np.maximum(np.linalg.norm(vert_normals, axis=1, keepdims=True), 1e-8)

    return verts_scene, verts_mc, vert_normals.astype(np.float32), quads


def project_6view_uvs(
    verts_scene: np.ndarray,
    quads: np.ndarray,
    span: float,
    fill_ratio: float = 0.82,
    flat_normals: np.ndarray | None = None,
) -> tuple[list[list[float]], list[list[list[int]]]]:
    """
    Project each 4-vertex quad onto its best-matching orthographic studio view.
    Emits 4-vertex quads `[[v0, n0, uv0], [v1, n1, uv1], [v2, n2, uv2], [v3, n3, uv3]]`
    so Bedrock's `poly_mesh` quad parser renders 100% of faces without gaps.
    """
    v_normals, v_rights, v_ups = [], [], []
    for yaw, pitch in ORTHO_6_VIEWS:
        yr, pr = math.radians(yaw), math.radians(pitch)
        cx, cy, cz = math.sin(yr) * math.cos(pr), math.sin(pr), math.cos(yr) * math.cos(pr)
        rx, ry, rz = math.cos(yr), 0.0, -math.sin(yr)
        ux, uy, uz = -math.sin(yr) * math.sin(pr), math.cos(pr), -math.cos(yr) * math.sin(pr)
        v_normals.append(np.array([cx, cy, cz], dtype=np.float32))
        v_rights.append(np.array([rx, ry, rz], dtype=np.float32))
        v_ups.append(np.array([ux, uy, uz], dtype=np.float32))
    v_normals_mat = np.stack(v_normals)

    p0 = verts_scene[quads[:, 0]]
    p1 = verts_scene[quads[:, 1]]
    p2 = verts_scene[quads[:, 2]]
    p3 = verts_scene[quads[:, 3]]
    fn = np.cross(p1 - p0, p2 - p0) + np.cross(p2 - p0, p3 - p0)
    fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-8)

    # Bias slightly toward horizontal views (0..3) over top/bottom (4..5) for seamless side facets
    scores = fn @ v_normals_mat.T
    scores[:, 4:] *= 0.92
    best_view = np.argmax(scores, axis=1)

    uvs = []
    polys = []
    scale = fill_ratio / span
    for q_idx, (i0, i1, i2, i3) in enumerate(quads):
        vw = int(best_view[q_idx])
        col, row = vw % 3, vw // 3
        right, up = v_rights[vw], v_ups[vw]
        uv_base = len(uvs)
        for vi in (i0, i1, i2, i3):
            pt = verts_scene[vi]
            u_local = float(np.clip(np.dot(pt, right) * scale + 0.5, 0.02, 0.98))
            v_local = float(np.clip(-np.dot(pt, up) * scale + 0.5, 0.02, 0.98))
            u_glob = (col + u_local) / 3.0
            v_glob_img = (row + v_local) / 2.0
            uvs.append([round(u_glob, 5), round(1.0 - v_glob_img, 5)])

        if flat_normals is not None:
            n0 = n1 = n2 = n3 = int(q_idx)
        else:
            n0, n1, n2, n3 = int(i0), int(i1), int(i2), int(i3)

        polys.append([
            [int(i0), n0, uv_base],
            [int(i1), n1, uv_base + 1],
            [int(i2), n2, uv_base + 2],
            [int(i3), n3, uv_base + 3],
        ])
    return uvs, polys


def export_shrinkwrap_polymesh(
    ply_file: Path | GaussianScene,
    output_geo: Path,
    output_tex: Path | None = None,
    geometry_id: str = "geometry.poly_mesh_gem",
    mode: str = "smooth",
    target_size: float = 24.0,
    tile_res: int = 512,
    hue_shift_deg: float = 0.0,
    flip_y: bool = True,
) -> None:
    """Export a watertight Quad-Sphere Shrinkwrap `poly_mesh` Bedrock geometry and its 6-view studio atlas."""
    t0 = time.time()
    scene = ply_file if isinstance(ply_file, GaussianScene) else GaussianScene(ply_file, flip_y=flip_y)

    if output_tex is not None:
        atlas_img = render_6view_ortho_atlas(
            scene, tile_res=tile_res, fill_ratio=0.82, hue_shift_deg=hue_shift_deg
        )
        output_tex.parent.mkdir(parents=True, exist_ok=True)
        atlas_img.save(output_tex, optimize=True)

    verts_scene, verts_mc, vert_normals, quads = build_shrinkwrap_mesh(
        scene, mode=mode, target_size=target_size
    )

    if mode == "faceted":
        p0 = verts_mc[quads[:, 0]]
        p1 = verts_mc[quads[:, 1]]
        p2 = verts_mc[quads[:, 2]]
        p3 = verts_mc[quads[:, 3]]
        fn = np.cross(p1 - p0, p2 - p0) + np.cross(p2 - p0, p3 - p0)
        fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-8)
        uvs, polys = project_6view_uvs(verts_scene, quads, scene.span, fill_ratio=0.82, flat_normals=fn)
        mesh_normals = [[round(float(n[0]), 4), round(float(n[1]), 4), round(float(n[2]), 4)] for n in fn]
    else:
        uvs, polys = project_6view_uvs(verts_scene, quads, scene.span, fill_ratio=0.82)
        mesh_normals = [[round(float(n[0]), 4), round(float(n[1]), 4), round(float(n[2]), 4)] for n in vert_normals]

    mesh_positions = [[round(float(v[0]), 4), round(float(v[1]), 4), round(float(v[2]), 4)] for v in verts_mc]

    geo_json = {
        "format_version": "1.12.0",
        "minecraft:geometry": [
            {
                "description": {
                    "identifier": geometry_id,
                    "texture_width": tile_res * 3,
                    "texture_height": tile_res * 2,
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
                            "uvs": uvs,
                            "polys": polys,
                        },
                    },
                ],
            }
        ],
    }

    output_geo.parent.mkdir(parents=True, exist_ok=True)
    output_geo.write_text(json.dumps(geo_json), encoding="utf-8")
    print(
        f"✓ Exported Quad-Sphere PolyMesh [{mode}] ({len(verts_mc)} verts, {len(quads)} quads / {len(quads)*2} tris) "
        f"to {output_geo.name} in {time.time() - t0:.2f}s"
    )


# Keep backwards-compatible alias
export_high_to_low_polymesh = export_shrinkwrap_polymesh


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Quad-Sphere Shrinkwrap Surface Map & 6-View Orthographic 3DGS Baker for Minecraft Bedrock."
    )
    parser.add_argument("ply_file", type=Path, help="Input 3DGS .ply file")
    parser.add_argument(
        "--geo-out",
        type=Path,
        default=Path("./bedrock/02_SplatMesh_Pack/SplatMesh_RP/models/entity/poly_gem_0.geo.json"),
        help="Output .geo.json path",
    )
    parser.add_argument(
        "--tex-out",
        type=Path,
        default=Path("./bedrock/02_SplatMesh_Pack/SplatMesh_RP/textures/entity/poly_gem_tex_0.png"),
        help="Output RGBA 6-view texture atlas path",
    )
    parser.add_argument("--geo-id", type=str, default="geometry.poly_gem_0", help="Bedrock geometry identifier")
    parser.add_argument(
        "-m",
        "--mode",
        choices=["smooth", "detailed", "faceted"],
        default="smooth",
        help="Shrinkwrap mesh style (smooth=1536q, detailed=4704q, faceted=294q)",
    )
    parser.add_argument("-s", "--size", type=float, default=24.0, help="Target size in Bedrock model units (16 = 1 block)")
    parser.add_argument("--tile-res", type=int, default=512, help="Per-view orthographic resolution (default: 512)")
    parser.add_argument("--hue-shift", type=float, default=0.0, help="Optional hue shift in degrees")
    parser.add_argument("--no-flip-y", action="store_true", help="Do not flip PLY Y axis")

    args = parser.parse_args()
    export_shrinkwrap_polymesh(
        args.ply_file,
        output_geo=args.geo_out,
        output_tex=args.tex_out,
        geometry_id=args.geo_id,
        mode=args.mode,
        target_size=args.size,
        tile_res=args.tile_res,
        hue_shift_deg=args.hue_shift,
        flip_y=not args.no_flip_y,
    )
