# splat2mc

Convert 3D Gaussian Splats to Minecraft particle datapacks and Bedrock Edition Add-Ons.

![demo](docs/demo.gif)

## What is this?

3D Gaussian Splatting (3DGS) is a technique for reconstructing 3D scenes from photos. This repository provides pipelines to render these reconstructions in Minecraft:
1. **Java Edition Datapack CLI:** Converts PLY splat clouds into `.mcfunction` particle commands.
2. **Bedrock Path A — Zero-Latency Spherical Imposter (`01_Imposter_Pack`):** 100% client-side Molang angle-pinned spritesheet imposter with orthographic spherical Blender baking.
3. **Bedrock Path B — Staggered Oriented Particle & Static Geometry Splat Renderer (`02_Oriented_Splat_Pack`):** Single-sided `DisableCulling` oriented splat particles streamed in tick-staggered batches, plus a zero-particle `.geo.json` static mesh exporter.

## Installation

```bash
# Clone
git clone https://github.com/jackgladowsky/splat2mc.git
cd splat2mc

# Install with uv
uv sync

# Or with pip
pip install -e .
```

---

## Bedrock Edition Suite

The `bedrock/` directory contains two production-ready Add-On packs for Minecraft Bedrock Edition (latest version):

### Path A: Zero-Latency Spherical Imposter (`bedrock/01_Imposter_Pack/`)
* **Client-Side Molang:** Rotations and UV frame offsets compute at full display framerate via Molang `query.rotation_to_camera`. Zero server ticks or property synchronization needed.
* **Angle-Pinned Alignment:** The imposter plane locks strictly to the discrete baked angles (`v.snapped_yaw`, `v.snapped_pitch`), eliminating perspective warping and swimming artifacts between frame switches.
* **Centered Pivot Spine:** The 16×16 geometry plane pivots around `[0, 8, 0]` with origin `[-8, 0, 0]`.
* **In-Game Usage:**
  ```text
  /summon custom:imposter
  ```

### Path B: Staggered Oriented Splat Particle & Static Geometry (`bedrock/02_Oriented_Splat_Pack/`)
* **Single-Emission Oriented Particles:** Uses `splat_double_sided` material with `DisableCulling` and `DisableDepthWrite` (`materials/particles.material`), halving particle emitter calls and doubling active splat limits.
* **Seamless Tick-Staggered Streaming:** Spreads 100-tick (5.0s) lifetime particles evenly across every tick via `ScriptBox.js`, eliminating single-tick particle lag spikes.
* **Static `.geo.json` Exporter (`tools/export_geo_splat.py`):** Bakes PLY splats into a static Bedrock Entity Geometry (`geometry.splat_cloud`) with an accompanying RGBA palette atlas.
* **In-Game Usage:**
  * For particle stream: Look at the ground block and run:
    ```text
    /scriptevent sample:run
    ```
  * For static geometry cloud:
    ```text
    /summon custom:splat_cloud
    ```

### Installing Add-Ons to Minecraft Bedrock
Run the `install.cmd` script inside either pack folder, or copy the `_BP` folder to `development_behavior_packs` and `_RP` folder to `development_resource_packs`.

---

## Standalone Python Tools (`tools/`)

All Python generators and exporters are consolidated in `tools/`:

| Script | Description | Example Command |
|---|---|---|
| `export_oriented.py` | Exports PLY into oriented particle data array (`SplatData.js`) | `uv run python tools/export_oriented.py scene.ply -n 5000` |
| `export_geo_splat.py` | Bakes PLY into static Bedrock `.geo.json` + texture atlas | `uv run python tools/export_geo_splat.py scene.ply -n 1024` |
| `make_texture.py` | Generates Gaussian falloff 64×64 RGBA particle texture | `uv run python tools/make_texture.py` |
| `make_spritesheet.py` | Combines rendered PNG frames into an MxN imposter atlas | `uv run python tools/make_spritesheet.py -i spritesheet -r 3` |
| `blender_renderer.py` | Blender add-on for orthographic spherical frame baking | Install as add-on in Blender 3.0+ |
| `export_bedrock.py` | Full standalone Bedrock Add-On exporter | `uv run python tools/export_bedrock.py scene.ply` |

---

## Java Edition Datapack CLI

### Convert a single PLY file

```bash
splat2mc convert scene.ply -o ./datapacks

# Options:
#   -n, --max-particles  Maximum particles (default: 5000)
#   -s, --size          Target size in blocks (default: 10)
#   --min-opacity       Minimum opacity threshold (default: 0.1)
```

### Batch convert

```bash
splat2mc batch ./splats -o ./datapacks
```

### Get info about a PLY file

```bash
splat2mc info scene.ply
```

### In Minecraft (Java Edition)

1. Copy the generated datapack to your world's `datapacks/` folder
2. Run `/reload`
3. Run `/function splats:<name>`

The particles spawn relative to your position.

## Getting PLY files

You can get 3DGS PLY files from:
- [Luma AI](https://lumalabs.ai/) - Scan with your phone
- [Polycam](https://poly.cam/) - Another scanning app
- Train your own with [gaussian-splatting](https://github.com/graphdeco-inria/gaussian-splatting)

## How it works

1. **Parse PLY** - Extract position, color (from spherical harmonics), opacity, and scale
2. **Normalize** - Scale and center to fit in Minecraft
3. **Downsample** - Keep the most opaque splats to stay under particle limits
4. **Generate mcfunction** - Output `/particle` commands for each splat
5. **Package** - Create a ready-to-use datapack

## Limitations

- Minecraft particles are spherical, not ellipsoidal like true Gaussians
- Limited to ~5000 particles before performance issues
- No rotation/anisotropy - we just use average scale
- Particles fade quickly - they're meant to be re-triggered

## License

MIT
