#!/usr/bin/env python3
import sys
import struct
import argparse
from pathlib import Path
try:
    from .converter import load_ply, normalize_splats
except ImportError:
    from converter import load_ply, normalize_splats

def export_mod(ply_file: Path, output_file: Path, size: float = 10.0, flip_y: bool = False):
    print(f"Loading {ply_file}...")
    splats = load_ply(ply_file)
    
    print(f"Normalizing to {size} blocks (Flip Y: {flip_y})...")
    # Utilizing your existing normalize_splats logic
    splats = normalize_splats(splats, target_size=size, flip_y=flip_y)
    
    print(f"Baking {len(splats)} splats to binary...")
    with open(output_file, 'wb') as f:
        for s in splats:
            # Struct packing: 8 Little-Endian floats (x, y, z, r, g, b, opacity, scale)
            data = struct.pack('<8f', s.x, s.y, s.z, s.r, s.g, s.b, s.opacity, s.scale)
            f.write(data)
            
    print(f"✓ Saved to {output_file}")
    print("Place this file in your Minecraft test client's 'run' directory and use /splat load")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export PLY to MCGS binary for Fabric Mod.")
    parser.add_argument("ply_file", type=Path, help="Input .ply file")
    parser.add_argument("-o", "--output", type=Path, default=Path("scene.mcgs"), help="Output .mcgs file")
    parser.add_argument("-s", "--size", type=float, default=10.0, help="Target size in blocks")
    parser.add_argument("--flip-y", action="store_true", help="Flip Y axis")
    
    args = parser.parse_args()
    
    if not args.ply_file.exists():
        print(f"Error: File {args.ply_file} not found.")
        sys.exit(1)
        
    export_mod(args.ply_file, args.output, args.size, args.flip_y)