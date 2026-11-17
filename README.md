# splat2mc

Convert 3D Gaussian Splats (`.ply`) into Minecraft Bedrock Edition Add-On packs (Angle-Pinned Spherical Imposters, Quad-Sphere Shrinkwrap 3D Meshes, and Full Oriented Splat Clouds) and Java Edition particle datapacks.

## Bedrock Edition Packs (`bedrock/`)

Place your `.ply` Gaussian Splat files in `source/` and build any combination of the packs below. Every pack automatically gives players a **Gem Catalog** item on join—right-click/use it (or run `/scriptevent <prefix>:menu`) to open the native Bedrock button menu with 128×128 gem previews, **Interact** on a block to place, and **Crouch + Interact** to pick up.

### 1. `bedrock/01_SplatImposter_Pack` — Angle-Pinned Spherical Imposter
* **Angle-Pinned Frame Alignment:** Locks the double-sided imposter plane (`north` + un-mirrored `south` UV faces) strictly to the discrete baked camera angle (`v.snapped_yaw`, `v.snapped_pitch`) so walking around a gem has zero smooth-tracking jumpiness and zero backface UV flipping.
* **Configurable Quality Tiers:**
  * **Low (`low`):** `12×5` = 60 angles, pitch `±60°` @ `256px`
  * **Medium (`med`, default):** `32×19` = 608 angles, pitch `±72°` @ `256px`
  * **High (`high`):** `32×13` = 416 angles, pitch `±72°` @ `512px`
* **In-Game Commands:** `/scriptevent imposter:menu`, `:items`, `:spawn <0..N-1|slug>`, `:clear`, `:help`

### 2. `bedrock/02_SplatMesh_Pack` — High-to-Low TSDF Splat-to-Mesh Bake
* **Multi-View TSDF & Marching Cubes (`tools/export_splat_mesh.py`):** Renders 42 Fibonacci-sphere 3DGS RGB-D views (using median depth `T <= 0.5`), fuses them into a `216³` TSDF volume with visual-hull free-space carving (preserving concavities, spires, and tunnels without internal hollow shells), and extracts a clean single-component low-poly shell via QEM decimation + Taubin smoothing. (The prior Quad-Sphere shrinkwrap pack is archived at `bedrock/_archive/mesh_converted_from_splat/`.)
* **Connected-Chart UV & Occlusion-Tested Bake:** Unwraps overlap-free connected UV charts and bakes visibility-tested multi-view 3DGS colors with distance-transform padding (plus optional `--bake-normals` and `--save-fbx` to `source/FBX_Output/`).
* **Configurable Bake Tiers:**
  * **Low (`low`):** `6,000` tris @ `1024px` baked diffuse
  * **Medium (`med`, default):** `14,000` tris @ `2048px` baked diffuse
  * **High (`high`):** `24,000` tris @ `4096px` baked diffuse
* **In-Game Commands:** `/scriptevent polymesh:menu`, `:items`, `:spawn <0..N-1|slug>`, `:clear`, `:help`

### 3. `bedrock/03_SplatRenderer_Pack` — Full Oriented Splat Cloud & Particle Stream *(Legacy Reference)*
* **Static Splat Cloud Entities (`tools/export_geo_splat.py`):** Pairs inset depth-writing core quads (`core_*`) with quaternion-oriented, anisotropic feathered Gaussian surface splats (`s_*`) at `800`, `1500` (default), or `2500` splat planes.
* **Oriented Particle Streamer (`tools/export_oriented.py`):** Look at a block and click **Toggle Particle Stream** in the catalog menu (or run `/scriptevent sample:run`) to stream 5,000 double-sided oriented Gaussian splat particles.
* **In-Game Commands:** `/scriptevent splat:menu`, `:items`, `:spawn <0..N-1|slug>`, `:clear`, `:help`, `sample:run`

### 4. `bedrock/04_FBXMesh_Pack` — High-to-Low Poly FBX Bake *(Optional)*
* **High-Poly Scan Decimation & Clean (`tools/export_fbx_mesh.py`):** Reads binary FBX photogrammetry scans from `source/FBX/*.fbx` + `*_diffuse.png`, converts Z-up scans to Y-up Minecraft coordinates, keeps the largest connected component (no floating triangles/vertices), performs QEM decimation + gentle Taubin smoothing, and emits 4-vertex degenerate quads (`MCPE-186053` safe).
* **UV Unwrap & High-to-Low Texture Bake:** Automatically unwraps connected UV charts with distance-transform padding and bakes high-res diffuse textures (plus optional `--bake-normals` PBR tangent-space normal maps, off by default).
* **Configurable Bake Tiers:**
  * **Low (`low`):** `6,000` tris @ `512px` baked diffuse
  * **Medium (`med`, default):** `16,000` tris @ `1024px` baked diffuse
  * **High (`high`):** `28,000` tris @ `2048px` baked diffuse
* **In-Game Commands:** `/scriptevent fbxmesh:menu`, `:items`, `:spawn <0..N-1|slug>`, `:clear`, `:help`

---

## Building & Installing Packs

### Windows One-Click Interactive Builder
Double-click **`update_pack_gems.bat`** (or `build_gems_to_mc.bat` if present) in the repo root. It will:
1. Ask which pack(s) to rebuild (defaults to **1 & 2** — `01_SplatImposter_Pack` and `02_SplatMesh_Pack`, leaving `03_SplatRenderer_Pack` and `04_FBXMesh_Pack` untouched unless selected).
2. Ask which quality level(s) to include for each pack (defaults to **Medium** quality).
3. Rebuild pack assets from `source/*.ply` (and `source/FBX/*.fbx` for Pack 04) while preserving any existing install scripts (`install.cmd`, `*.bat`, `*.ps1`, `*.sh`) in the pack folders.

To install all built packs into Minecraft Bedrock on Windows, double-click **`install_gems_to_mc.bat`** (or `install.cmd` inside a specific pack's folder).

### Command Line (`uv`)
```bash
# Install dependencies
uv sync

# Interactive wizard (same as update_pack_gems.bat)
uv run python tools/build_all_packs.py

# Non-interactive defaults (Packs 01 & 02 at Medium quality)
uv run python tools/build_all_packs.py -y

# Build Pack 04 (FBX High-to-Low Bake) explicitly and export reduced .fbx + .png copies
uv run python tools/build_all_packs.py --pack 4 -y --save-fbx

# Build a single pack directly with custom tiers
uv run python tools/build_imposter_pack.py --tiers low,med
uv run python tools/build_mesh_pack.py --tiers med --save-fbx
uv run python tools/build_splat_pack.py --tiers 1500
uv run python tools/build_fbx_pack.py --tiers med --save-fbx
```

### Imposter Preview Lab
Open `imposter_demo.html` in a browser to inspect imposter spritesheets and UV angle transitions interactively.

## License

MIT
