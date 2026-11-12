#!/usr/bin/env python3
"""
Generate Gaussian falloff splat texture (splat_tex.png) for Bedrock particle rendering.
"""
import argparse
from pathlib import Path
import numpy as np
from PIL import Image


def generate_splat_texture(
    size: int = 64,
    output_file: Path = Path("./bedrock/02_Oriented_Splat_Pack/SplatRenderer_RP/textures/particles/splat_tex.png"),
):
    center = (size - 1) / 2.0
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    y, x = np.ogrid[:size, :size]
    dist_from_center = np.sqrt((x - center) ** 2 + (y - center) ** 2)

    # Smooth Gaussian core multiplied by a circular smoothstep taper so alpha reaches
    # strictly 0 before the quad perimeter (eliminating visible square quad edges).
    sigma = size * 0.25
    t_win = np.clip((size * 0.48 - dist_from_center) / (size * 0.18), 0.0, 1.0)
    smooth_window = t_win * t_win * (3.0 - 2.0 * t_win)
    alpha = np.exp(-0.5 * (dist_from_center / sigma) ** 2) * smooth_window

    alpha_u8 = np.clip(alpha * 255, 0, 255).astype(np.uint8)

    # Pure white RGB channels (tinting handled dynamically by particle emitter)
    white = np.full((size, size), 255, dtype=np.uint8)
    rgba = np.dstack((white, white, white, alpha_u8))
    Image.fromarray(rgba, "RGBA").save(output_path)
    print(f"✓ Generated {output_path} ({size}x{size}, edge alpha={int(alpha_u8[0, size // 2])})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate Gaussian falloff splat texture.")
    parser.add_argument(
        "-s", "--size", type=int, default=64, help="Texture width/height in pixels (default: 64)"
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("./bedrock/02_Oriented_Splat_Pack/SplatRenderer_RP/textures/particles/splat_tex.png"),
        help="Output PNG path",
    )
    args = parser.parse_args()
    generate_splat_texture(size=args.size, output_file=args.output)
