# splat2mc

Convert 3D Gaussian Splats (`.ply`) into Minecraft Bedrock Edition 3-Tier Add-Ons (Angle-Pinned Spherical Imposters, Shrinkwrap `poly_mesh` 3D Models, and Full Oriented Splat Clouds) and Java Edition particle datapacks.

![demo](docs/demo.gif)

## Bedrock Edition 3-Tier Suite (`bedrock/`)

The `bedrock/` directory is organized into **three separate Add-On packs** that scale upward in complexity and representation fidelity. Each pack prints its available spawn commands in chat automatically when you join the world, spawns **one gem at a time** in front of you, and supports **Crouch + Interact** to pick up any gem and **Interact** on a block to place it down:

### 1. `bedrock/01_Imposter_Pack` — Angle-Pinned Spherical Imposter (3 Quality Tiers)
* **Angle-Pinned Frame Alignment:** Locks the double-sided imposter plane (`north` + un-mirrored `south` UV faces) strictly to the discrete baked camera angle (`v.snapped_yaw`, `v.snapped_pitch`) so walking around the gem has zero smooth-tracking jumpiness and zero backface UV flipping.
* **3 Angular Quality Presets Included (Citrine & Amethyst):**
  * **Low (`12×3` = 36 angles):** `/scriptevent imposter:spawn 0` (Citrine) | `3` (Amethyst)
  * **Medium (`16×5` = 80 angles):** `/scriptevent imposter:spawn 1` (Citrine) | `4` (Amethyst)
  * **High (`24×7` = 168 angles):** `/scriptevent imposter:spawn 2` (Citrine) | `5` (Amethyst)

### 2. `bedrock/02_Poly_Mesh_Pack` — Shrinkwrap Surface Map + 6-View Orthographic Bake (3 Mesh Styles)
* **Shrinkwrap Surface Conforming (`tools/export_mesh.py`):** Conforms a subdivided icosphere onto the outer opacity hull of the 3DGS splat cloud with Laplacian smoothing—producing a 100% manifold, non-self-intersecting 3D crystal mesh.
* **Continuous 6-View Studio Projection:** Renders 6 orthographic 3DGS studio views (Front, Back, Right, Left, Top, Bottom) with Euclidean distance-transform edge dilation and projects each triangle continuously onto its best-matching view (eliminating per-triangle UV seams).
* **3 Shrinkwrap Mesh Presets Included (Citrine & Amethyst):**
  * **Smooth Shrinkwrap (`1,280` tris):** `/scriptevent polymesh:spawn 0` (Citrine) | `3` (Amethyst)
  * **Detailed Shrinkwrap (`5,120` tris):** `/scriptevent polymesh:spawn 1` (Citrine) | `4` (Amethyst)
  * **Faceted Crystal (`320` tris):** `/scriptevent polymesh:spawn 2` (Citrine) | `5` (Amethyst)

### 3. `bedrock/03_Full_GS_Pack` — Full Oriented Gaussian Splat Cloud & Particle Stream
* **Static Splat Cloud Entities (`tools/export_geo_splat.py`):** Pairs a shrunk inner Shrinkwrap depth occluder (`core_mesh`) with 800, 1,500, or 2,400 tangent-aligned elliptical Gaussian splat quads packed into a single `poly_mesh` bone (`splat_cloud`).
* **3 Splat Density Presets Included (Citrine & Amethyst):**
  * **800 Surface Splats:** `/scriptevent splat:spawn 0` (Citrine) | `3` (Amethyst)
  * **1,500 Surface Splats:** `/scriptevent splat:spawn 1` (Citrine) | `4` (Amethyst)
  * **2,400 Surface Splats:** `/scriptevent splat:spawn 2` (Citrine) | `5` (Amethyst)
* **Oriented Particle Streamer (`tools/export_oriented.py`):** Look at any block and run `/scriptevent sample:run` to stream 5,000 double-sided oriented Gaussian splat particles.

---

## Rebuilding the Packs from CLI

```bash
# Install dependencies
uv sync

# Build all 3 Bedrock packs (01_Imposter_Pack, 02_Poly_Mesh_Pack, 03_Full_GS_Pack)
uv run splat2mc build-gallery

# Or build a specific pack:
uv run splat2mc build-gallery --pack 01
uv run splat2mc build-gallery --pack 02
uv run splat2mc build-gallery --pack 03
```

## License

MIT
