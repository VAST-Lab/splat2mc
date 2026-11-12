#!/usr/bin/env python3
import sys
import json
import uuid
import argparse
from pathlib import Path
import numpy as np
from plyfile import PlyData
from dataclasses import dataclass

@dataclass
class GaussianSplat:
    """A single Gaussian splat with position, color, opacity, and scale."""
    x: float
    y: float
    z: float
    r: float  # 0-1
    g: float  # 0-1
    b: float  # 0-1
    opacity: float  # 0-1
    scale: float  # average scale

def load_ply(path: Path) -> list[GaussianSplat]:
    """Load Gaussian splats from a PLY file.
    
    3DGS PLY files typically contain:
    - x, y, z: position
    - f_dc_0, f_dc_1, f_dc_2: spherical harmonics DC component (color)
    - opacity: opacity (logit space, needs sigmoid)
    - scale_0, scale_1, scale_2: log scale
    - rot_0, rot_1, rot_2, rot_3: rotation quaternion
    """
    plydata = PlyData.read(str(path))
    vertex = plydata['vertex']
    
    # Extract positions
    x = np.array(vertex['x'])
    y = np.array(vertex['y'])
    z = np.array(vertex['z'])
    
    # Extract colors from spherical harmonics DC component
    # SH coefficients need conversion: color = 0.5 + SH_DC * C0
    # where C0 = 0.28209479177387814
    C0 = 0.28209479177387814
    
    if 'f_dc_0' in vertex.data.dtype.names:
        # 3DGS format with spherical harmonics
        r = 0.5 + np.array(vertex['f_dc_0']) * C0
        g = 0.5 + np.array(vertex['f_dc_1']) * C0
        b = 0.5 + np.array(vertex['f_dc_2']) * C0
    elif 'red' in vertex.data.dtype.names:
        # Standard PLY with RGB
        r = np.array(vertex['red']) / 255.0
        g = np.array(vertex['green']) / 255.0
        b = np.array(vertex['blue']) / 255.0
    else:
        # Fallback: white
        r = np.ones_like(x)
        g = np.ones_like(x)
        b = np.ones_like(x)
    
    # Clamp colors to valid range
    r = np.clip(r, 0, 1)
    g = np.clip(g, 0, 1)
    b = np.clip(b, 0, 1)
    
    # Extract opacity (stored as logit, apply sigmoid)
    if 'opacity' in vertex.data.dtype.names:
        opacity_logit = np.array(vertex['opacity'])
        opacity = 1 / (1 + np.exp(-opacity_logit))
    else:
        opacity = np.ones_like(x)
    
    # Extract scale (stored as log, take exp and average)
    if 'scale_0' in vertex.data.dtype.names:
        scale_0 = np.exp(np.array(vertex['scale_0']))
        scale_1 = np.exp(np.array(vertex['scale_1']))
        scale_2 = np.exp(np.array(vertex['scale_2']))
        scale = (scale_0 + scale_1 + scale_2) / 3
    else:
        scale = np.ones_like(x) * 0.01
    
    # Build splat list
    splats = []
    for i in range(len(x)):
        splats.append(GaussianSplat(
            x=float(x[i]),
            y=float(y[i]),
            z=float(z[i]),
            r=float(r[i]),
            g=float(g[i]),
            b=float(b[i]),
            opacity=float(opacity[i]),
            scale=float(scale[i]),
        ))
    
    return splats

def normalize_splats(
    splats: list[GaussianSplat],
    target_size: float = 10.0,
    center: bool = True,
    flip_y: bool = False,
) -> list[GaussianSplat]:
    """Normalize splat positions to fit in a reasonable Minecraft space."""
    if not splats:
        return splats
    
    # Flip the up vector if requested
    if flip_y:
        for s in splats:
            s.y = -s.y
            
    # Get bounds
    xs = [s.x for s in splats]
    ys = [s.y for s in splats]
    zs = [s.z for s in splats]
    
    # 3DGS often has "floaters" (stray particles far from the center). 
    # Using strict min/max includes these floaters and shrinks the main object.
    # We use the 2nd and 98th percentiles to calculate bounds based on the dense core.
    min_x, max_x = np.percentile(xs, 2), np.percentile(xs, 98)
    min_y, max_y = np.percentile(ys, 2), np.percentile(ys, 98)
    min_z, max_z = np.percentile(zs, 2), np.percentile(zs, 98)
    
    # Calculate scale factor based on the trimmed range
    range_x = max_x - min_x or 1
    range_y = max_y - min_y or 1
    range_z = max_z - min_z or 1
    max_range = max(range_x, range_y, range_z)
    scale_factor = target_size / max_range
    
    # Calculate center offset based on the dense core
    if center:
        center_x = (min_x + max_x) / 2
        center_y = (min_y + max_y) / 2
        center_z = (min_z + max_z) / 2
    else:
        center_x = center_y = center_z = 0
    
    # Normalize
    normalized = []
    for s in splats:
        normalized.append(GaussianSplat(
            x=(s.x - center_x) * scale_factor,
            y=(s.y - center_y) * scale_factor,
            z=(s.z - center_z) * scale_factor,
            r=s.r,
            g=s.g,
            b=s.b,
            opacity=s.opacity,
            scale=s.scale * scale_factor,
        ))
    
    return normalized

def downsample_splats(
    splats: list[GaussianSplat],
    max_count: int = 5000,
    method: str = "opacity",
) -> list[GaussianSplat]:
    """Downsample splats to fit Minecraft particle limits.
    
    Args:
        splats: List of splats
        max_count: Maximum number of splats to keep
        method: "opacity" (keep most opaque) or "random"
    """
    if len(splats) <= max_count:
        return splats
    
    if method == "opacity":
        # Sort by opacity descending, keep top N
        sorted_splats = sorted(splats, key=lambda s: s.opacity, reverse=True)
        return sorted_splats[:max_count]
    else:
        # Random sample
        indices = np.random.choice(len(splats), max_count, replace=False)
        return [splats[i] for i in indices]

def create_manifest(pack_type: str, name: str, description: str, dep_uuid: str = None) -> dict:
    """Generate a valid Bedrock manifest.json with unique UUIDs."""
    header_uuid = str(uuid.uuid4())
    module_uuid = str(uuid.uuid4())
    
    manifest = {
        "format_version": 2,
        "header": {
            "name": name,
            "description": description,
            "uuid": header_uuid,
            "version": [1, 0, 0],
            "min_engine_version": [1, 20, 40]
        },
        "modules": [
            {
                "type": "data" if pack_type == "behavior" else "resources",
                "uuid": module_uuid,
                "version": [1, 0, 0]
            }
        ]
    }

    # Behavior packs need to link to the Resource pack and request the Script API
    if pack_type == "behavior":
        manifest["modules"][0]["type"] = "script"
        manifest["modules"][0]["language"] = "javascript"
        manifest["modules"][0]["entry"] = "scripts/main.js"
        
        manifest["dependencies"] = [
            {
                "module_name": "@minecraft/server",
                "version": "1.11.0"
            }
        ]
        if dep_uuid:
            manifest["dependencies"].append({
                "uuid": dep_uuid,
                "version": [1, 0, 0]
            })

    return manifest, header_uuid

def export_bedrock_addon(ply_file: Path, output_dir: Path, max_particles: int = 50000, size: float = 10.0, flip_y: bool = False):
    print(f"Loading {ply_file}...")
    splats = load_ply(ply_file)
    
    print(f"Normalizing to {size} blocks (Flip Y: {flip_y})...")
    splats = normalize_splats(splats, target_size=size, flip_y=flip_y)

    if len(splats) > max_particles:
        print(f"Downsampling from {len(splats)} to {max_particles} particles...")
        splats = downsample_splats(splats, max_count=max_particles)

    # 1. Setup Directories
    bp_dir = output_dir / "Splat_BP"
    rp_dir = output_dir / "Splat_RP"
    
    (bp_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (rp_dir / "particles").mkdir(parents=True, exist_ok=True)

    # 2. Generate Manifests
    rp_manifest, rp_uuid = create_manifest("resource", "Splat RP", "Resource pack for 3D splats")
    bp_manifest, _ = create_manifest("behavior", "Splat BP", "Behavior pack for 3D splats", dep_uuid=rp_uuid)

    with open(rp_dir / "manifest.json", "w") as f:
        json.dump(rp_manifest, f, indent=4)
    with open(bp_dir / "manifest.json", "w") as f:
        json.dump(bp_manifest, f, indent=4)

    # 3. Create Custom Particle Definition (Resource Pack)
    particle_json = {
        "format_version": "1.10.0",
        "particle_effect": {
            "description": {
                "identifier": "vastlab:splat",
                "basic_render_parameters": {
                    "material": "particles_alpha",
                    "texture": "textures/particle/particles"
                }
            },
            "components": {
                "minecraft:emitter_rate_instant": { "num_particles": 1 },
                "minecraft:emitter_lifetime_once": { "active_time": 1.0 },
                "minecraft:particle_lifetime_expression": { "max_lifetime": 1.0 },
                "minecraft:particle_appearance_billboard": {
                    "size": ["variable.scale", "variable.scale"],
                    "facing_camera_mode": "lookat_xyz",
                    "uv": {
                        "texture_width": 128,
                        "texture_height": 128,
                        "uv": [0, 8],
                        "uv_size": [8, 8]
                    }
                },
                "minecraft:particle_appearance_tinting": {
                    "color": ["variable.color_r", "variable.color_g", "variable.color_b", 1.0]
                }
            }
        }
    }
    with open(rp_dir / "particles" / "splat.particle.json", "w") as f:
        json.dump(particle_json, f, indent=4)

    # 4. Bake Splat Data into a JavaScript Module (Behavior Pack)
    print(f"Baking {len(splats)} splats into JavaScript...")
    with open(bp_dir / "scripts" / "splat_data.js", "w", encoding="utf-8") as f:
        f.write("// Auto-generated by splat2mc\n")
        f.write("export const splatData = [\n")
        for s in splats:
            r, g, b = max(0, min(1, s.r)), max(0, min(1, s.g)), max(0, min(1, s.b))
            scale = max(0.01, min(2.0, s.scale * 10))
            f.write(f"  [{s.x:.3f},{s.y:.3f},{s.z:.3f},{r:.3f},{g:.3f},{b:.3f},{scale:.3f}],\n")
        f.write("];\n")
    
    print(f"Baking {len(splats)} splats into TypeScript...")
    with open("SplatData.ts", "w", encoding="utf-8") as f:
        f.write("// Auto-generated by splat2mc\n")
        f.write("export const splatData: number[][] = [\n")
        for s in splats:
            r, g, b = max(0, min(1, s.r)), max(0, min(1, s.g)), max(0, min(1, s.b))
            scale = max(0.01, min(2.0, s.scale * 10))
            f.write(f"  [{s.x:.3f}, {s.y:.3f}, {s.z:.3f}, {r:.3f}, {g:.3f}, {b:.3f}, {scale:.3f}],\n")
        f.write("];\n")

    # 5. Create Main JavaScript Logic (Behavior Pack)
    main_js = """import { world, system, MolangVariableMap } from "@minecraft/server";
import { splatData } from "./splat_data.js";

let activeSplats = [];

// Listen for chat commands to spawn or clear the splat
world.beforeEvents.chatSend.subscribe((event) => {
    if (event.message === "!splat") {
        event.cancel = true;
        activeSplats.push({
            x: event.sender.location.x,
            y: event.sender.location.y,
            z: event.sender.location.z,
            dimension: event.sender.dimension
        });
        event.sender.sendMessage("\\xA7a[splat2mc] Spawning splat! Type !clearsplat to remove.");
    }
    if (event.message === "!clearsplat") {
        event.cancel = true;
        activeSplats = [];
        event.sender.sendMessage("\\xA7e[splat2mc] Cleared splats.");
    }
});

// Run loop every 20 ticks (1 second) to respawn particles
system.runInterval(() => {
    for (const anchor of activeSplats) {
        for (const s of splatData) {
            const vars = new MolangVariableMap();
            vars.setFloat("variable.color_r", s[3]);
            vars.setFloat("variable.color_g", s[4]);
            vars.setFloat("variable.color_b", s[5]);
            vars.setFloat("variable.scale", s[6]);

            anchor.dimension.spawnParticle("vastlab:splat", {
                x: anchor.x + s[0],
                y: anchor.y + s[1],
                z: anchor.z + s[2]
            }, vars);
        }
    }
}, 20);
"""
    with open(bp_dir / "scripts" / "main.js", "w", encoding="utf-8") as f:
        f.write(main_js)

    print(f"\nBedrock Add-On created at: {output_dir}")
    print("  1. Copy 'Splat_BP' to your com.mojang/development_behavior_packs folder")
    print("  2. Copy 'Splat_RP' to your com.mojang/development_resource_packs folder")
    print("  3. Create a world, enable 'Beta APIs' under Experiments, and attach both packs.")
    print("  4. Type !splat in the chat!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export PLY to a Minecraft Bedrock Add-On.")
    parser.add_argument("ply_file", type=Path, help="Input .ply file")
    parser.add_argument("-o", "--output", type=Path, default=Path("./Bedrock_Splat"), help="Output directory")
    parser.add_argument("-n", "--max-particles", type=int, default=50000, help="Maximum number of particles")
    parser.add_argument("-s", "--size", type=float, default=10.0, help="Target size in blocks")
    parser.add_argument("--flip-y", action="store_true", help="Flip Y axis")
    
    args = parser.parse_args()
    
    if not args.ply_file.exists():
        print(f"Error: File {args.ply_file} not found.")
        sys.exit(1)
        
    export_bedrock_addon(args.ply_file, args.output, args.max_particles, args.size, args.flip_y)