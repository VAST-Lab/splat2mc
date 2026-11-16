"""Validation suite for the 3-tier splat2mc Bedrock Add-On packs."""

import json
from pathlib import Path
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parent.parent


def test_pack_01_imposter_quality_tiers_and_angle_pinning():
    pack_dir = REPO_ROOT / "bedrock" / "01_SplatImposter_Pack"
    bp_dir = pack_dir / "SplatImposter_BP"
    rp_dir = pack_dir / "SplatImposter_RP"

    bp_man = json.loads((bp_dir / "manifest.json").read_text(encoding="utf-8"))
    rp_man = json.loads((rp_dir / "manifest.json").read_text(encoding="utf-8"))
    assert bp_man["header"]["uuid"] == rp_man["dependencies"][0]["uuid"]
    assert any(d.get("module_name") == "@minecraft/server-ui" for d in bp_man["dependencies"])
    assert (bp_dir / "items" / "imposter_menu.json").exists()
    assert not (rp_dir / "attachables").exists()

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

    anim = json.loads((rp_dir / "animations" / "imposter.animation.json").read_text(encoding="utf-8"))
    rot = anim["animations"]["animation.imposter.align_to_baked_frame"]["bones"]["body"]["rotation"]
    assert rot == ["v.snapped_pitch", "v.snapped_yaw", 0]

    geo = json.loads((rp_dir / "models" / "entity" / "imposter.geo.json").read_text(encoding="utf-8"))
    cube_uv = geo["minecraft:geometry"][0]["bones"][1]["cubes"][0]["uv"]
    assert "north" in cube_uv and "south" in cube_uv

    main_js = (bp_dir / "scripts" / "main.js").read_text(encoding="utf-8")
    assert "openGemMenu" in main_js
    assert "initPlayer" in main_js
    assert "printPackHelp" in main_js
    assert 'const CMD_PREFIX = "imposter";' in main_js


def test_pack_02_polymesh_shrinkwrap_options():
    pack_dir = REPO_ROOT / "bedrock" / "02_SplatMesh_Pack"
    bp_dir = pack_dir / "SplatMesh_BP"
    rp_dir = pack_dir / "SplatMesh_RP"

    bp_man = json.loads((bp_dir / "manifest.json").read_text(encoding="utf-8"))
    rp_man = json.loads((rp_dir / "manifest.json").read_text(encoding="utf-8"))
    assert bp_man["header"]["uuid"] == rp_man["dependencies"][0]["uuid"]
    assert any(d.get("module_name") == "@minecraft/server-ui" for d in bp_man["dependencies"])
    assert (bp_dir / "items" / "polymesh_menu.json").exists()
    assert not (rp_dir / "attachables").exists()

    expected_quads = [1536, 4704, 294, 1536, 4704, 294]
    for i, exp_q in enumerate(expected_quads):
        geo_path = rp_dir / "models" / "entity" / f"poly_gem_{i}.geo.json"
        assert geo_path.exists()
        geo = json.loads(geo_path.read_text(encoding="utf-8"))["minecraft:geometry"][0]
        assert geo["bones"][1]["name"] == "gem_mesh"
        pm = geo["bones"][1]["poly_mesh"]
        assert len(pm["polys"]) == exp_q, f"Variant {i} expected {exp_q} quads, got {len(pm['polys'])}"
        # Every polygon must be a 4-vertex quad (prevents MCPE-186053 missing triangles in Bedrock)
        assert all(len(poly) == 4 for poly in pm["polys"])
        for u, v in pm["uvs"]:
            assert 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0

    main_js = (bp_dir / "scripts" / "main.js").read_text(encoding="utf-8")
    assert "openGemMenu" in main_js
    assert "initPlayer" in main_js
    assert 'const CMD_PREFIX = "polymesh";' in main_js


def test_pack_03_full_gs_pack():
    pack_dir = REPO_ROOT / "bedrock" / "03_SplatRenderer_Pack"
    bp_dir = pack_dir / "SplatRenderer_BP"
    rp_dir = pack_dir / "SplatRenderer_RP"

    bp_man = json.loads((bp_dir / "manifest.json").read_text(encoding="utf-8"))
    rp_man = json.loads((rp_dir / "manifest.json").read_text(encoding="utf-8"))
    assert bp_man["header"]["uuid"] == rp_man["dependencies"][0]["uuid"]
    assert any(d.get("module_name") == "@minecraft/server-ui" for d in bp_man["dependencies"])
    assert (bp_dir / "items" / "splat_menu.json").exists()
    assert not (rp_dir / "attachables").exists()

    for i in range(6):
        geo_path = rp_dir / "models" / "entity" / f"splat_cloud_{i}.geo.json"
        tex_path = rp_dir / "textures" / "entity" / f"splat_cloud_{i}.png"
        assert geo_path.exists()
        assert tex_path.exists()
        geo = json.loads(geo_path.read_text(encoding="utf-8"))["minecraft:geometry"][0]
        bones = {b["name"]: b for b in geo["bones"]}
        assert "core_0" in bones and "s_0" in bones

    main_js = (bp_dir / "scripts" / "main.js").read_text(encoding="utf-8")
    assert "openGemMenu" in main_js
    assert "initPlayer" in main_js
    assert 'const CMD_PREFIX = "splat";' in main_js
    assert "/scriptevent sample:run" in main_js
