"""Validation suite for the 3-tier splat2mc Bedrock Add-On packs."""

import json
from pathlib import Path
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_pack_01_imposter_quality_tiers_and_angle_pinning():
    pack_dir = REPO_ROOT / "bedrock" / "01_Imposter_Pack"
    bp_dir = pack_dir / "Imposter_BP"
    rp_dir = pack_dir / "Imposter_RP"

    bp_man = json.loads((bp_dir / "manifest.json").read_text(encoding="utf-8"))
    rp_man = json.loads((rp_dir / "manifest.json").read_text(encoding="utf-8"))
    assert bp_man["header"]["uuid"] == rp_man["dependencies"][0]["uuid"]

    expected_sizes = [
        (1920, 480),  # 0: Low (12x3 @ 160px)
        (2304, 720),  # 1: Med (16x5 @ 144px)
        (3072, 896),  # 2: High (24x7 @ 128px)
        (1920, 480),  # 3: Low (12x3 @ 160px)
        (2304, 720),  # 4: Med (16x5 @ 144px)
        (3072, 896),  # 5: High (24x7 @ 128px)
    ]

    for i, exp_size in enumerate(expected_sizes):
        atlas_path = rp_dir / "textures" / "entity" / f"spritesheet_{i}.png"
        icon_path = rp_dir / "textures" / "items" / f"gem_{i}.png"
        assert atlas_path.exists()
        assert icon_path.exists()
        with Image.open(atlas_path) as img:
            assert img.size == exp_size, f"Variant {i} atlas size mismatch: {img.size} != {exp_size}"

    # Verify angle-pinned Molang rotation (`v.snapped_pitch`, `v.snapped_yaw`) and double-sided UV plane
    anim = json.loads((rp_dir / "animations" / "imposter.animation.json").read_text(encoding="utf-8"))
    rot = anim["animations"]["animation.imposter.align_to_baked_frame"]["bones"]["body"]["rotation"]
    assert rot == ["v.snapped_pitch", "v.snapped_yaw", 0]

    geo = json.loads((rp_dir / "models" / "entity" / "imposter.geo.json").read_text(encoding="utf-8"))
    cube_uv = geo["minecraft:geometry"][0]["bones"][1]["cubes"][0]["uv"]
    assert "north" in cube_uv and "south" in cube_uv

    # Verify startup help banner in main.js
    main_js = (bp_dir / "scripts" / "main.js").read_text(encoding="utf-8")
    assert "printPackHelp" in main_js
    assert 'const CMD_PREFIX = "imposter";' in main_js
    assert "/scriptevent ${CMD_PREFIX}:spawn" in main_js


def test_pack_02_polymesh_shrinkwrap_options():
    pack_dir = REPO_ROOT / "bedrock" / "02_Poly_Mesh_Pack"
    bp_dir = pack_dir / "PolyMesh_BP"
    rp_dir = pack_dir / "PolyMesh_RP"

    bp_man = json.loads((bp_dir / "manifest.json").read_text(encoding="utf-8"))
    rp_man = json.loads((rp_dir / "manifest.json").read_text(encoding="utf-8"))
    assert bp_man["header"]["uuid"] == rp_man["dependencies"][0]["uuid"]

    expected_tris = [1280, 5120, 320, 1280, 5120, 320]
    for i, exp_t in enumerate(expected_tris):
        geo_path = rp_dir / "models" / "entity" / f"poly_gem_{i}.geo.json"
        assert geo_path.exists()
        geo = json.loads(geo_path.read_text(encoding="utf-8"))["minecraft:geometry"][0]
        pm = geo["bones"][1]["poly_mesh"]
        assert len(pm["polys"]) == exp_t, f"Variant {i} expected {exp_t} tris, got {len(pm['polys'])}"
        for u, v in pm["uvs"]:
            assert 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0

    main_js = (bp_dir / "scripts" / "main.js").read_text(encoding="utf-8")
    assert "printPackHelp" in main_js
    assert 'const CMD_PREFIX = "polymesh";' in main_js
    assert "/scriptevent ${CMD_PREFIX}:spawn" in main_js


def test_pack_03_full_gs_pack():
    pack_dir = REPO_ROOT / "bedrock" / "03_Full_GS_Pack"
    bp_dir = pack_dir / "SplatRenderer_BP"
    rp_dir = pack_dir / "SplatRenderer_RP"

    bp_man = json.loads((bp_dir / "manifest.json").read_text(encoding="utf-8"))
    rp_man = json.loads((rp_dir / "manifest.json").read_text(encoding="utf-8"))
    assert bp_man["header"]["uuid"] == rp_man["dependencies"][0]["uuid"]

    for i in range(6):
        geo_path = rp_dir / "models" / "entity" / f"splat_cloud_{i}.geo.json"
        tex_path = rp_dir / "textures" / "entity" / f"splat_cloud_{i}.png"
        assert geo_path.exists()
        assert tex_path.exists()
        geo = json.loads(geo_path.read_text(encoding="utf-8"))["minecraft:geometry"][0]
        bones = {b["name"]: b for b in geo["bones"]}
        assert "core_mesh" in bones and "splat_cloud" in bones

    main_js = (bp_dir / "scripts" / "main.js").read_text(encoding="utf-8")
    assert "printPackHelp" in main_js
    assert 'const CMD_PREFIX = "splat";' in main_js
    assert "/scriptevent ${CMD_PREFIX}:spawn" in main_js
    assert "/scriptevent sample:run" in main_js
