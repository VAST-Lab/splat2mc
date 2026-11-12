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
    center = size / 2.0
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Create coordinate grid
    y, x = np.ogrid[:size, :size]

    # Calculate distance from center for each pixel center
    dist_from_center = np.sqrt((x - center + 0.5) ** 2 + (y - center + 0.5) ** 2)

    # Gaussian alpha falloff (sigma = size / 4.0 standard 3DGS ratio)
    sigma = size / 4.0
    alpha = np.exp(-0.5 * (dist_from_center / sigma) ** 2)

    # Scale alpha to 0-255 uint8
    alpha = np.clip(alpha * 255, 0, 255).astype(np.uint8)

    # Pure white RGB channels (tinting handled dynamically by particle emitter)
    r = np.full((size, size), 255, dtype=np.uint8)
    g = np.full((size, size), 255, dtype=np.uint8)
    b = np.full((size, size), 255, dtype=np.uint8)

    rgba = np.dstack((r, g, b, alpha))
    img = Image.fromarray(rgba, "RGBA")
    img.save(output_path)
    print(f"✓ Generated {output_path} ({size}x{size})")


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
