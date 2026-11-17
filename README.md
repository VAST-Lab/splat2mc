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

### 2. `bedrock/02_SplatMesh_Pack` — Quad-Sphere Shrinkwrap Mesh + 6-View Studio Bake
* **Watertight Quad-Sphere Shrinkwrap (`tools/export_mesh.py`):** Conforms a 6-patch Quad-Sphere onto the outer opacity hull of the 3DGS cloud using pure 4-vertex quads (avoiding Bedrock's `MCPE-186053` missing-triangle bug).
* **Continuous 6-View Studio Projection:** Renders 6 orthographic 3DGS studio views (Front, Back, Right, Left, Top, Bottom) with Euclidean distance-transform edge dilation so UV borders never bleed background black.
* **Configurable Mesh Styles:**
  * **Faceted (`faceted`):** `294` quads (`588` tris) with flat crystal normals
  * **Smooth (`smooth`, default):** `1,536` quads (`3,072` tris) with smooth normals
  * **Detailed (`detailed`):** `4,704` quads (`9,408` tris) high-poly hull
* **In-Game Commands:** `/scriptevent polymesh:menu`, `:items`, `:spawn <0..N-1|slug>`, `:clear`, `:help`

### 3. `bedrock/03_SplatRenderer_Pack` — Full Oriented Splat Cloud & Particle Stream *(Legacy Reference)*
* **Static Splat Cloud Entities (`tools/export_geo_splat.py`):** Pairs inset depth-writing core quads (`core_*`) with quaternion-oriented, anisotropic feathered Gaussian surface splats (`s_*`) at `800`, `1500` (default), or `2500` splat planes.
* **Oriented Particle Streamer (`tools/export_oriented.py`):** Look at a block and click **Toggle Particle Stream** in the catalog menu (or run `/scriptevent sample:run`) to stream 5,000 double-sided oriented Gaussian splat particles.
* **In-Game Commands:** `/scriptevent splat:menu`, `:items`, `:spawn <0..N-1|slug>`, `:clear`, `:help`, `sample:run`

---

## Building & Installing Packs

### Windows One-Click Interactive Builder
Double-click **`build_gems_to_mc.bat`** in the repo root. It will:
1. Ask which pack(s) to rebuild (defaults to **1 & 2** — `01_SplatImposter_Pack` and `02_SplatMesh_Pack`, leaving `03_SplatRenderer_Pack` untouched).
2. Ask which quality level(s) to include for each pack (defaults to **Medium** quality).
3. Clear the target pack folder and rebuild everything from `source/*.ply`.

To install a built pack into Minecraft Bedrock on Windows, double-click `install.cmd` inside that pack's folder (e.g., `bedrock/01_SplatImposter_Pack/install.cmd`).

### Command Line (`uv`)
```bash
# Install dependencies
uv sync

# Interactive wizard (same as build_gems_to_mc.bat)
uv run python tools/build_all_packs.py

# Non-interactive defaults (Packs 01 & 02 at Medium quality)
uv run python tools/build_all_packs.py -y

# Build a single pack directly with custom tiers
uv run python tools/build_imposter_pack.py --tiers low,med
uv run python tools/build_mesh_pack.py --tiers smooth,faceted
uv run python tools/build_splat_pack.py --tiers 1500

# Run validation tests
uv run pytest -v
```

### Imposter Preview Lab
Open `imposter_demo.html` in a browser to inspect imposter spritesheets and UV angle transitions interactively.

## License

MIT
