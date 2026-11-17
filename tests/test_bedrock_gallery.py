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

    assert not (rp_dir / "textures" / "entity" / "spritesheet.png").exists()

    ply_count = len(list((REPO_ROOT / "source").glob("*.ply"))) or 2
    expected_sizes = [(3072, 1280), (8192, 4864)] * ply_count

    for i, exp_size in enumerate(expected_sizes):
        atlas_path = rp_dir / "textures" / "entity" / f"spritesheet_{i}.png"
        icon_path = rp_dir / "textures" / "items" / f"gem_{i}.png"
        assert atlas_path.exists()
        assert icon_path.exists()
        with Image.open(atlas_path) as img:
            assert img.size == exp_size, f"Variant {i} atlas size mismatch: {img.size} != {exp_size}"

    assert not (rp_dir / "textures" / "entity" / f"spritesheet_{len(expected_sizes)}.png").exists()

    entity_rp = json.loads((rp_dir / "entity" / "imposter.entity.json").read_text(encoding="utf-8"))
    assert "v.tier = math.mod(query.variant, 2);" in entity_rp["minecraft:client_entity"]["description"]["scripts"]["pre_animation"]

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

    ply_count = len(list((REPO_ROOT / "source").glob("*.ply"))) or 2
    expected_quads = [1536, 294] * ply_count
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

    assert not (rp_dir / "models" / "entity" / f"poly_gem_{len(expected_quads)}.geo.json").exists()

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


def test_interactive_pack_and_tier_selectors(tmp_path):
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from build_all_packs import resolve_pack_selection
    from build_imposter_pack import QUALITY_TIERS, _molang_tier_switch
    from build_mesh_pack import MESH_TIERS
    from build_splat_pack import SPLAT_TIERS
    from pack_utils import reset_pack_directory, resolve_tier_selection

    # Pack selector defaults to 01 + 02; supports explicit combinations and 'all'
    assert resolve_pack_selection("") == ["01", "02"]
    assert resolve_pack_selection("2,3") == ["02", "03"]
    assert resolve_pack_selection("all") == ["01", "02", "03"]

    # Default quality tier for each pack is single medium tier
    imp_default = resolve_tier_selection(QUALITY_TIERS, "", ["med"])
    assert [t["key"] for t in imp_default] == ["med"]
    assert _molang_tier_switch(imp_default, "cols") == "32.0"

    mesh_default = resolve_tier_selection(MESH_TIERS, "", ["smooth"])
    assert [t["key"] for t in mesh_default] == ["smooth"]

    splat_default = resolve_tier_selection(SPLAT_TIERS, "", ["1500"])
    assert [t["key"] for t in splat_default] == ["1500"]

    assert len(resolve_tier_selection(QUALITY_TIERS, "1,2", ["med"])) == 2
    assert len(resolve_tier_selection(MESH_TIERS, "all", ["smooth"])) == 3

    # reset_pack_directory wipes stale files while preserving pack_icon.png
    pack = tmp_path / "pack"
    (pack / "BP").mkdir(parents=True)
    (pack / "BP" / "pack_icon.png").write_bytes(b"ICON")
    (pack / "BP" / "stale.json").write_text("old", encoding="utf-8")
    reset_pack_directory(pack, preserve_relpaths=["BP/pack_icon.png"])
    assert (pack / "BP" / "pack_icon.png").read_bytes() == b"ICON"
    assert not (pack / "BP" / "stale.json").exists()
