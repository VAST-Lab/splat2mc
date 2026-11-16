#!/usr/bin/env python3
"""
Headless 3D Gaussian Splatting (3DGS) Spherical Imposter Renderer & Icon Generator.

Replaces the manual Blender rendering workflow with a fast, Numba-accelerated true
elliptical 3DGS alpha-compositing rasterizer (supporting degree-3 Spherical Harmonics,
full 3D covariance projection, and optional gallery color palette transformations).

Outputs:
1. A complete MxN spherical imposter atlas (`spritesheet.png`) ready for Minecraft Bedrock.
2. A crisp 128x128 item thumbnail icon (`icon.png`) for held/inventory items.
"""
import argparse
import math
import time
from pathlib import Path
import numpy as np
import numba
from PIL import Image
from plyfile import PlyData


@numba.njit(fastmath=True)
def eval_sh_color_ortho(
    dx: float,
    dy: float,
    dz: float,
    f_dc: np.ndarray,
    f_rest: np.ndarray,
    has_rest: bool,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Evaluate degree-3 Spherical Harmonics color in native PLY space for view dir (dx, dy, dz)."""
    C0 = 0.28209479177387814
    C1 = 0.4886025119029199
    C2_0 = 1.0925484305920792
    C2_1 = -1.0925484305920792
    C2_2 = 0.31539156525252005
    C2_3 = -1.0925484305920792
    C2_4 = 0.5462742152960396
    C3_0 = -0.5900435899266435
    C3_1 = 2.890611442640554
    C3_2 = -0.4570457994644658
    C3_3 = 0.3731763325901154
    C3_4 = -0.4570457994644658
    C3_5 = 1.445305721320277
    C3_6 = -0.5900435899266435

    n = len(f_dc)
    r_out = np.empty(n, dtype=np.float32)
    g_out = np.empty(n, dtype=np.float32)
    b_out = np.empty(n, dtype=np.float32)

    xx, yy, zz = dx * dx, dy * dy, dz * dz
    xy, yz, xz = dx * dy, dy * dz, dx * dz

    b1 = -C1 * dy
    b2 = C1 * dz
    b3 = -C1 * dx
    b4 = C2_0 * xy
    b5 = C2_1 * yz
    b6 = C2_2 * (2.0 * zz - xx - yy)
    b7 = C2_3 * xz
    b8 = C2_4 * (xx - yy)
    b9 = C3_0 * dy * (3.0 * xx - yy)
    b10 = C3_1 * xy * dz
    b11 = C3_2 * dy * (4.0 * zz - xx - yy)
    b12 = C3_3 * dz * (2.0 * zz - 3.0 * xx - 3.0 * yy)
    b13 = C3_4 * dx * (4.0 * zz - xx - yy)
    b14 = C3_5 * dz * (xx - yy)
    b15 = C3_6 * dx * (xx - 3.0 * yy)

    for i in range(n):
        cr = 0.5 + C0 * f_dc[i, 0]
        cg = 0.5 + C0 * f_dc[i, 1]
        cb = 0.5 + C0 * f_dc[i, 2]
        if has_rest:
            fr = f_rest[i]
            cr += (
                b1 * fr[0] + b2 * fr[1] + b3 * fr[2]
                + b4 * fr[3] + b5 * fr[4] + b6 * fr[5] + b7 * fr[6] + b8 * fr[7]
                + b9 * fr[8] + b10 * fr[9] + b11 * fr[10] + b12 * fr[11]
                + b13 * fr[12] + b14 * fr[13] + b15 * fr[14]
            )
            cg += (
                b1 * fr[15] + b2 * fr[16] + b3 * fr[17]
                + b4 * fr[18] + b5 * fr[19] + b6 * fr[20] + b7 * fr[21] + b8 * fr[22]
                + b9 * fr[23] + b10 * fr[24] + b11 * fr[25] + b12 * fr[26]
                + b13 * fr[27] + b14 * fr[28] + b15 * fr[29]
            )
            cb += (
                b1 * fr[30] + b2 * fr[31] + b3 * fr[32]
                + b4 * fr[33] + b5 * fr[34] + b6 * fr[35] + b7 * fr[36] + b8 * fr[37]
                + b9 * fr[38] + b10 * fr[39] + b11 * fr[40] + b12 * fr[41]
                + b13 * fr[42] + b14 * fr[43] + b15 * fr[44]
            )
        r_out[i] = min(1.0, max(0.0, cr))
        g_out[i] = min(1.0, max(0.0, cg))
        b_out[i] = min(1.0, max(0.0, cb))
    return r_out, g_out, b_out


@numba.njit(fastmath=True)
def rasterize_splats_ortho(
    H: int,
    W: int,
    u_px: np.ndarray,
    v_px: np.ndarray,
    cov_xx: np.ndarray,
    cov_xy: np.ndarray,
    cov_yy: np.ndarray,
    r_arr: np.ndarray,
    g_arr: np.ndarray,
    b_arr: np.ndarray,
    op_arr: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Front-to-back orthographic 2D Gaussian splat rasterizer with early transmittance termination."""
    img = np.zeros((H, W, 3), dtype=np.float32)
    T = np.ones((H, W), dtype=np.float32)
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
        rad = min(64.0, math.ceil(3.0 * math.sqrt(lambda_max)))

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

        for py in range(y0, y1 + 1):
            dy = py - cy
            for px in range(x0, x1 + 1):
                t_val = T[py, px]
                if t_val < 0.003:
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
                T[py, px] = t_val * (1.0 - alpha)
    return img, 1.0 - T


def apply_color_grade(
    r: np.ndarray,
    g: np.ndarray,
    b: np.ndarray,
    hue_shift_deg: float = 0.0,
    saturation_boost: float = 1.15,
    brightness_boost: float = 1.05,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Apply optional hue rotation, saturation boost, and brightness lift."""
    if abs(hue_shift_deg) > 1e-3:
        angle = math.radians(hue_shift_deg)
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        sqrt1_3 = 1.0 / math.sqrt(3.0)
        # Rodrigues rotation around grey axis (1, 1, 1) / sqrt(3)
        m00 = cos_a + (1.0 - cos_a) / 3.0
        m01 = (1.0 - cos_a) / 3.0 - sqrt1_3 * sin_a
        m02 = (1.0 - cos_a) / 3.0 + sqrt1_3 * sin_a
        m10 = (1.0 - cos_a) / 3.0 + sqrt1_3 * sin_a
        m11 = cos_a + (1.0 - cos_a) / 3.0
        m12 = (1.0 - cos_a) / 3.0 - sqrt1_3 * sin_a
        m20 = (1.0 - cos_a) / 3.0 - sqrt1_3 * sin_a
        m21 = (1.0 - cos_a) / 3.0 + sqrt1_3 * sin_a
        m22 = cos_a + (1.0 - cos_a) / 3.0
        nr = r * m00 + g * m01 + b * m02
        ng = r * m10 + g * m11 + b * m12
        nb = r * m20 + g * m21 + b * m22
        r, g, b = nr, ng, nb

    lum = 0.299 * r + 0.587 * g + 0.114 * b
    r = np.clip((lum + (r - lum) * saturation_boost) * brightness_boost, 0.0, 1.0)
    g = np.clip((lum + (g - lum) * saturation_boost) * brightness_boost, 0.0, 1.0)
    b = np.clip((lum + (b - lum) * saturation_boost) * brightness_boost, 0.0, 1.0)
    return r.astype(np.float32), g.astype(np.float32), b.astype(np.float32)


class GaussianScene:
    """Loaded and pre-filtered 3D Gaussian Splat scene ready for multi-angle rendering."""

    def __init__(self, ply_file: Path, max_splats: int = 350000, flip_y: bool = True):
        t0 = time.time()
        plydata = PlyData.read(str(ply_file))
        v = plydata["vertex"]

        raw_x = np.array(v["x"], dtype=np.float32)
        raw_y = np.array(v["y"], dtype=np.float32)
        raw_z = np.array(v["z"], dtype=np.float32)

        op = (
            1.0 / (1.0 + np.exp(-np.array(v["opacity"], dtype=np.float32)))
            if "opacity" in v.data.dtype.names
            else np.ones_like(raw_x)
        )
        sx = np.exp(np.array(v["scale_0"], dtype=np.float32))
        sy = np.exp(np.array(v["scale_1"], dtype=np.float32))
        sz = np.exp(np.array(v["scale_2"], dtype=np.float32))
        s_max = np.maximum(np.maximum(sx, sy), sz)

        # Filter out low-opacity background fog and huge background floaters
        scale_cap = np.percentile(s_max, 92)
        mask = np.where((op > 0.025) & (s_max <= scale_cap))[0]
        if len(mask) > max_splats:
            # Keep highest opacity splats
            top_k = np.argsort(op[mask])[::-1][:max_splats]
            mask = mask[top_k]

        self.flip_y = flip_y
        y_sign = -1.0 if flip_y else 1.0
        x = raw_x[mask]
        y = y_sign * raw_y[mask]
        z = -raw_z[mask]

        # Robustly trim spatial outliers (beyond 1st..99th percentile box of opaque core)
        core_sel = op[mask] > np.percentile(op[mask], 50)
        x_lo, x_hi = np.percentile(x[core_sel], [1, 99])
        y_lo, y_hi = np.percentile(y[core_sel], [1, 99])
        z_lo, z_hi = np.percentile(z[core_sel], [1, 99])
        pad_x = (x_hi - x_lo) * 0.15
        pad_y = (y_hi - y_lo) * 0.15
        pad_z = (z_hi - z_lo) * 0.15
        in_box = (
            (x >= x_lo - pad_x) & (x <= x_hi + pad_x)
            & (y >= y_lo - pad_y) & (y <= y_hi + pad_y)
            & (z >= z_lo - pad_z) & (z <= z_hi + pad_z)
        )
        mask = mask[in_box]
        x, y, z = x[in_box], y[in_box], z[in_box]

        # Center using core percentiles
        cx = 0.5 * (np.percentile(x, 3) + np.percentile(x, 97))
        cy = 0.5 * (np.percentile(y, 3) + np.percentile(y, 97))
        cz = 0.5 * (np.percentile(z, 3) + np.percentile(z, 97))
        self.x = (x - cx).astype(np.float32)
        self.y = (y - cy).astype(np.float32)
        self.z = (z - cz).astype(np.float32)

        span_x = float(np.percentile(self.x, 98) - np.percentile(self.x, 2))
        span_y = float(np.percentile(self.y, 98) - np.percentile(self.y, 2))
        span_z = float(np.percentile(self.z, 98) - np.percentile(self.z, 2))
        self.span = max(span_x, span_y, span_z, 1e-4)

        self.op = op[mask].astype(np.float32)
        self.sx = sx[mask].astype(np.float32)
        self.sy = sy[mask].astype(np.float32)
        self.sz = sz[mask].astype(np.float32)

        rw = np.array(v["rot_0"], dtype=np.float32)[mask]
        rx = np.array(v["rot_1"], dtype=np.float32)[mask]
        ry = np.array(v["rot_2"], dtype=np.float32)[mask]
        rz = np.array(v["rot_3"], dtype=np.float32)[mask]
        norm = np.sqrt(rw * rw + rx * rx + ry * ry + rz * rz)
        norm = np.where(norm == 0, 1.0, norm)
        rw, rx, ry, rz = rw / norm, rx / norm, ry / norm, rz / norm

        # Rotation matrix rows in Minecraft-aligned space (X right, Y up, Z front)
        self.R00 = (1.0 - 2.0 * (ry * ry + rz * rz)).astype(np.float32)
        self.R01 = (2.0 * (rx * ry - rw * rz)).astype(np.float32)
        self.R02 = (2.0 * (rx * rz + rw * ry)).astype(np.float32)

        self.R10 = (y_sign * 2.0 * (rx * ry + rw * rz)).astype(np.float32)
        self.R11 = (y_sign * (1.0 - 2.0 * (rx * rx + rz * rz))).astype(np.float32)
        self.R12 = (y_sign * 2.0 * (ry * rz - rw * rx)).astype(np.float32)

        self.R20 = (-2.0 * (rx * rz - rw * ry)).astype(np.float32)
        self.R21 = (-2.0 * (ry * rz + rw * rx)).astype(np.float32)
        self.R22 = (-(1.0 - 2.0 * (rx * rx + ry * ry))).astype(np.float32)

        # Spherical Harmonics coefficients
        self.f_dc = np.column_stack([
            np.array(v["f_dc_0"], dtype=np.float32)[mask],
            np.array(v["f_dc_1"], dtype=np.float32)[mask],
            np.array(v["f_dc_2"], dtype=np.float32)[mask],
        ])
        self.has_rest = "f_rest_0" in v.data.dtype.names and "f_rest_44" in v.data.dtype.names
        if self.has_rest:
            rest_cols = [np.array(v[f"f_rest_{i}"], dtype=np.float32)[mask] for i in range(45)]
            self.f_rest = np.column_stack(rest_cols)
        else:
            self.f_rest = np.zeros((len(mask), 45), dtype=np.float32)

        print(
            f"Loaded {ply_file.name}: {len(self.x):,} active splats "
            f"(span={self.span:.2f}, SH_deg={'3' if self.has_rest else '0'}) in {time.time() - t0:.2f}s"
        )

    def render_view(
        self,
        yaw_deg: float,
        pitch_deg: float,
        tile_res: int = 256,
        fill_ratio: float = 0.80,
        hue_shift_deg: float = 0.0,
    ) -> Image.Image:
        """
        Render an orthographic view from spherical angle (yaw_deg, pitch_deg).
        Convention matches Minecraft Bedrock entity space:
        - yaw_deg = 0: Camera at +Z looking toward origin (front face)
        - yaw_deg = 90: Camera at +X looking toward origin (right side)
        - pitch_deg > 0: Camera elevated above Y=0 looking down at origin
        """
        yaw_rad = math.radians(yaw_deg)
        pitch_rad = math.radians(pitch_deg)

        # Camera position unit vector (from origin toward camera)
        cam_x = math.sin(yaw_rad) * math.cos(pitch_rad)
        cam_y = math.sin(pitch_rad)
        cam_z = math.cos(yaw_rad) * math.cos(pitch_rad)

        # Camera basis vectors in Minecraft world space:
        # forward = from camera to origin = (-cam_x, -cam_y, -cam_z)
        # right = horizontal perpendicular so image +U is screen right
        right_x = math.cos(yaw_rad)
        right_y = 0.0
        right_z = -math.sin(yaw_rad)

        # up = cross(right, forward) = (-sin(yaw)*sin(pitch), cos(pitch), -cos(yaw)*sin(pitch))
        up_x = -math.sin(yaw_rad) * math.sin(pitch_rad)
        up_y = math.cos(pitch_rad)
        up_z = -math.cos(yaw_rad) * math.sin(pitch_rad)

        # Depth along ray from camera to origin: smaller depth = closer to camera
        depth = -(self.x * cam_x + self.y * cam_y + self.z * cam_z)
        order = np.argsort(depth)

        px_per_unit = (tile_res * fill_ratio) / self.span

        xs = self.x[order]
        ys = self.y[order]
        zs = self.z[order]

        u_px = ((xs * right_x + ys * right_y + zs * right_z) * px_per_unit + tile_res * 0.5).astype(np.float32)
        v_px = (-(xs * up_x + ys * up_y + zs * up_z) * px_per_unit + tile_res * 0.5).astype(np.float32)

        # Project 3D rotation matrix onto screen (right, -up) axes
        r00, r01, r02 = self.R00[order], self.R01[order], self.R02[order]
        r10, r11, r12 = self.R10[order], self.R11[order], self.R12[order]
        r20, r21, r22 = self.R20[order], self.R21[order], self.R22[order]

        # Screen U row = right . R
        u0 = right_x * r00 + right_y * r10 + right_z * r20
        u1 = right_x * r01 + right_y * r11 + right_z * r21
        u2 = right_x * r02 + right_y * r12 + right_z * r22

        # Screen V row = -up . R
        v0 = -(up_x * r00 + up_y * r10 + up_z * r20)
        v1 = -(up_x * r01 + up_y * r11 + up_z * r21)
        v2 = -(up_x * r02 + up_y * r12 + up_z * r22)

        s2x = (self.sx[order] * px_per_unit) ** 2
        s2y = (self.sy[order] * px_per_unit) ** 2
        s2z = (self.sz[order] * px_per_unit) ** 2

        cov_xx = (u0 * u0 * s2x + u1 * u1 * s2y + u2 * u2 * s2z).astype(np.float32)
        cov_xy = (u0 * v0 * s2x + u1 * v1 * s2y + u2 * v2 * s2z).astype(np.float32)
        cov_yy = (v0 * v0 * s2x + v1 * v1 * s2y + v2 * v2 * s2z).astype(np.float32)

        # Evaluate view-dependent SH color in native PLY direction (from camera to splat)
        y_sign = -1.0 if self.flip_y else 1.0
        dir_ply_x = float(-cam_x)
        dir_ply_y = float(-cam_y * y_sign)
        dir_ply_z = float(cam_z)

        r_sh, g_sh, b_sh = eval_sh_color_ortho(
            dir_ply_x,
            dir_ply_y,
            dir_ply_z,
            self.f_dc[order],
            self.f_rest[order],
            self.has_rest,
        )
        r_sh, g_sh, b_sh = apply_color_grade(r_sh, g_sh, b_sh, hue_shift_deg=hue_shift_deg)

        rgb, alpha = rasterize_splats_ortho(
            tile_res,
            tile_res,
            u_px,
            v_px,
            cov_xx,
            cov_xy,
            cov_yy,
            r_sh,
            g_sh,
            b_sh,
            self.op[order],
        )

        # Un-premultiply RGB so alpha-blended edges in Minecraft have zero dark fringes
        safe_a = np.maximum(alpha[..., None], 1e-5)
        rgb_unpremult = np.clip(rgb / safe_a, 0.0, 1.0)
        # Clean out ultra-faint floaters (< 2% alpha)
        clean_alpha = np.where(alpha < 0.02, 0.0, np.clip((alpha - 0.02) / 0.98, 0.0, 1.0))

        rgba = np.dstack([
            (rgb_unpremult[..., 0] * 255.0).astype(np.uint8),
            (rgb_unpremult[..., 1] * 255.0).astype(np.uint8),
            (rgb_unpremult[..., 2] * 255.0).astype(np.uint8),
            (clean_alpha * 255.0).astype(np.uint8),
        ])
        return Image.fromarray(rgba, "RGBA")


def render_imposter_atlas(
    scene: GaussianScene,
    output_atlas: Path,
    output_icon: Path | None = None,
    cols: int = 16,
    rows: int = 5,
    tile_res: int = 192,
    pitch_min: float = -45.0,
    pitch_max: float = 45.0,
    hue_shift_deg: float = 0.0,
) -> None:
    """Render full MxN spherical imposter atlas and optional item thumbnail icon."""
    t0 = time.time()
    atlas = Image.new("RGBA", (cols * tile_res, rows * tile_res), (0, 0, 0, 0))
    yaw_step = 360.0 / cols
    pitch_step = (pitch_max - pitch_min) / max(1, rows - 1)

    for r_idx in range(rows):
        pitch_deg = pitch_max - r_idx * pitch_step
        for c_idx in range(cols):
            yaw_deg = c_idx * yaw_step
            frame = scene.render_view(
                yaw_deg=yaw_deg,
                pitch_deg=pitch_deg,
                tile_res=tile_res,
                hue_shift_deg=hue_shift_deg,
            )
            atlas.paste(frame, (c_idx * tile_res, r_idx * tile_res))

    output_atlas.parent.mkdir(parents=True, exist_ok=True)
    atlas.save(output_atlas, optimize=True)
    print(
        f"✓ Saved {cols}x{rows} spherical imposter atlas ({cols * tile_res}x{rows * tile_res} px) "
        f"to {output_atlas} in {time.time() - t0:.2f}s"
    )

    if output_icon is not None:
        # Render a crisp 128x128 3/4 view thumbnail for inventory item icon
        icon_img = scene.render_view(
            yaw_deg=-15.0,
            pitch_deg=18.0,
            tile_res=128,
            fill_ratio=0.84,
            hue_shift_deg=hue_shift_deg,
        )
        output_icon.parent.mkdir(parents=True, exist_ok=True)
        icon_img.save(output_icon, optimize=True)
        print(f"✓ Saved 128x128 item thumbnail icon to {output_icon}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Headless 3DGS Spherical Imposter Atlas & Item Icon Renderer (no Blender required)."
    )
    parser.add_argument("ply_file", type=Path, help="Input 3DGS .ply file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("./bedrock/01_Imposter_Pack/Imposter_RP/textures/entity/spritesheet.png"),
        help="Output spritesheet PNG path",
    )
    parser.add_argument(
        "--icon-out",
        type=Path,
        default=None,
        help="Optional output path for 128x128 item thumbnail icon PNG",
    )
    parser.add_argument("-c", "--cols", type=int, default=16, help="Number of yaw columns (default: 16)")
    parser.add_argument("-r", "--rows", type=int, default=5, help="Number of pitch rows (default: 5)")
    parser.add_argument("--res", type=int, default=192, help="Per-tile resolution in pixels (default: 192)")
    parser.add_argument("--pitch-min", type=float, default=-45.0, help="Minimum pitch angle in degrees")
    parser.add_argument("--pitch-max", type=float, default=45.0, help="Maximum pitch angle in degrees")
    parser.add_argument("--hue-shift", type=float, default=0.0, help="Optional hue shift in degrees")
    parser.add_argument("--no-flip-y", action="store_true", help="Do not flip PLY Y axis")

    args = parser.parse_args()
    gs_scene = GaussianScene(args.ply_file, flip_y=not args.no_flip_y)
    render_imposter_atlas(
        gs_scene,
        output_atlas=args.output,
        output_icon=args.icon_out,
        cols=args.cols,
        rows=args.rows,
        tile_res=args.res,
        pitch_min=args.pitch_min,
        pitch_max=args.pitch_max,
        hue_shift_deg=args.hue_shift,
    )
