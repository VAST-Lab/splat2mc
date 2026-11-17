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


def _assert_single_component_degenerate_quad_pack(
    rp_dir: Path,
    prefix: str,
    tex_prefix: str,
    variant_count: int,
    max_polys: int,
    expect_normals: bool = False,
) -> None:
    from collections import deque

    for i in range(variant_count):
        geo_path = rp_dir / "models" / "entity" / f"{prefix}_{i}.geo.json"
        tex_path = rp_dir / "textures" / "entity" / f"{tex_prefix}_{i}.png"
        icon_path = rp_dir / "textures" / "items" / f"{prefix}_{i}.png"
        normal_path = rp_dir / "textures" / "entity" / f"{tex_prefix}_{i}_normal.png"
        assert geo_path.exists()
        assert tex_path.exists()
        assert icon_path.exists()
        if not expect_normals:
            assert not normal_path.exists()

        geo = json.loads(geo_path.read_text(encoding="utf-8"))["minecraft:geometry"][0]
        assert geo["bones"][1]["name"] == "gem_mesh"
        pm = geo["bones"][1]["poly_mesh"]
        n_pos = len(pm["positions"])
        n_uv = len(pm["uvs"])
        polys = pm["polys"]
        assert 0 < n_pos < 65536
        assert 0 < n_uv < 65536
        assert 0 < len(polys) <= max_polys
        # Every polygon must be a 4-vertex degenerate quad (prevents MCPE-186053)
        assert all(len(poly) == 4 and poly[2][0] == poly[3][0] for poly in polys)
        for u, v in pm["uvs"]:
            assert -1e-6 <= u <= 1.0 + 1e-6 and -1e-6 <= v <= 1.0 + 1e-6

        # Zero unreferenced vertices & single connected component (no floating tris/verts)
        adj: list[list[int]] = [[] for _ in range(n_pos)]
        used: set[int] = set()
        for poly in polys:
            a, b, c = poly[0][0], poly[1][0], poly[2][0]
            used.update((a, b, c))
            adj[a].extend((b, c))
            adj[b].extend((a, c))
            adj[c].extend((a, b))
        assert len(used) == n_pos
        visited = {0}
        q = deque([0])
        while q:
            cur = q.popleft()
            for nb in adj[cur]:
                if nb not in visited:
                    visited.add(nb)
                    q.append(nb)
        assert len(visited) == n_pos, f"Variant {i} has disconnected mesh pieces ({len(visited)}/{n_pos})"

    assert not (rp_dir / "models" / "entity" / f"{prefix}_{variant_count}.geo.json").exists()


def test_pack_02_splat_high_to_low_mesh():
    archive_dir = REPO_ROOT / "bedrock" / "_archive" / "mesh_converted_from_splat"
    assert (archive_dir / "SplatMesh_BP" / "manifest.json").exists()

    pack_dir = REPO_ROOT / "bedrock" / "02_SplatMesh_Pack"
    bp_dir = pack_dir / "SplatMesh_BP"
    rp_dir = pack_dir / "SplatMesh_RP"

    bp_man = json.loads((bp_dir / "manifest.json").read_text(encoding="utf-8"))
    rp_man = json.loads((rp_dir / "manifest.json").read_text(encoding="utf-8"))
    assert bp_man["header"]["uuid"] == rp_man["dependencies"][0]["uuid"]
    assert any(d.get("module_name") == "@minecraft/server-ui" for d in bp_man["dependencies"])
    assert (bp_dir / "items" / "polymesh_menu.json").exists()
    assert not (rp_dir / "attachables").exists()

    ply_files = sorted((REPO_ROOT / "source").glob("*.ply"), key=lambda p: p.name.lower())
    _assert_single_component_degenerate_quad_pack(
        rp_dir, prefix="poly_gem", tex_prefix="poly_gem_tex", variant_count=len(ply_files), max_polys=14000
    )

    main_js = (bp_dir / "scripts" / "main.js").read_text(encoding="utf-8")
    assert "openGemMenu" in main_js
    assert "initPlayer" in main_js
    assert 'const CMD_PREFIX = "polymesh";' in main_js

    fbx_out = REPO_ROOT / "source" / "FBX_Output"
    for p in ply_files:
        assert (fbx_out / f"{p.stem}_splat.fbx").exists()
        assert (fbx_out / f"{p.stem}_splat_diffuse.png").exists()


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


def test_pack_04_fbx_mesh_pack():
    pack_dir = REPO_ROOT / "bedrock" / "04_FBXMesh_Pack"
    bp_dir = pack_dir / "FBXMesh_BP"
    rp_dir = pack_dir / "FBXMesh_RP"

    bp_man = json.loads((bp_dir / "manifest.json").read_text(encoding="utf-8"))
    rp_man = json.loads((rp_dir / "manifest.json").read_text(encoding="utf-8"))
    assert bp_man["header"]["uuid"] == rp_man["dependencies"][0]["uuid"]
    assert any(d.get("module_name") == "@minecraft/server-ui" for d in bp_man["dependencies"])
    assert (bp_dir / "items" / "fbxmesh_menu.json").exists()
    assert not (rp_dir / "attachables").exists()

    fbx_files = sorted((REPO_ROOT / "source" / "FBX").glob("*.fbx"), key=lambda p: p.name.lower())
    assert len(fbx_files) >= 6
    _assert_single_component_degenerate_quad_pack(
        rp_dir, prefix="fbx_gem", tex_prefix="fbx_gem_tex", variant_count=len(fbx_files), max_polys=16000
    )

    main_js = (bp_dir / "scripts" / "main.js").read_text(encoding="utf-8")
    assert "openGemMenu" in main_js
    assert "initPlayer" in main_js
    assert 'const CMD_PREFIX = "fbxmesh";' in main_js

    fbx_out = REPO_ROOT / "source" / "FBX_Output"
    for p in fbx_files:
        assert (fbx_out / f"{p.stem}.fbx").exists()
        assert (fbx_out / f"{p.stem}_diffuse.png").exists()


def test_interactive_pack_and_tier_selectors(tmp_path):
    import sys

    sys.path.insert(0, str(REPO_ROOT / "tools"))
    from build_all_packs import resolve_pack_selection
    from build_fbx_pack import FBX_TIERS
    from build_imposter_pack import QUALITY_TIERS, _molang_tier_switch
    from build_mesh_pack import MESH_TIERS
    from build_splat_pack import SPLAT_TIERS
    from pack_utils import reset_pack_directory, resolve_tier_selection, write_install_cmd

    # Pack selector defaults to 01 + 02; supports explicit combinations and 'all'
    assert resolve_pack_selection("") == ["01", "02"]
    assert resolve_pack_selection("2,3") == ["02", "03"]
    assert resolve_pack_selection("4") == ["04"]
    assert resolve_pack_selection("fbx") == ["04"]
    assert resolve_pack_selection("all") == ["01", "02", "03", "04"]

    # Default quality tier for each pack is single medium tier
    imp_default = resolve_tier_selection(QUALITY_TIERS, "", ["med"])
    assert [t["key"] for t in imp_default] == ["med"]
    assert _molang_tier_switch(imp_default, "cols") == "32.0"

    mesh_default = resolve_tier_selection(MESH_TIERS, "", ["med"])
    assert [t["key"] for t in mesh_default] == ["med"]

    splat_default = resolve_tier_selection(SPLAT_TIERS, "", ["1500"])
    assert [t["key"] for t in splat_default] == ["1500"]

    fbx_default = resolve_tier_selection(FBX_TIERS, "", ["med"])
    assert [t["key"] for t in fbx_default] == ["med"]

    assert len(resolve_tier_selection(QUALITY_TIERS, "1,2", ["med"])) == 2
    assert len(resolve_tier_selection(MESH_TIERS, "all", ["med"])) == 3
    assert len(resolve_tier_selection(FBX_TIERS, "all", ["med"])) == 3

    # Binary FBX export round-trips cleanly with UVs + paired diffuse PNG
    import numpy as np
    import trimesh
    from PIL import Image
    from export_fbx_mesh import load_fbx_mesh
    from mesh_bake_utils import unwrap_connected_charts, write_binary_fbx

    box = trimesh.creation.icosphere(subdivisions=2, radius=1.0)
    b_uv, b_fuv = unwrap_connected_charts(box, atlas_res=64)
    img = Image.new("RGBA", (64, 64), (10, 180, 220, 255))
    fbx_p, png_p = write_binary_fbx(box, b_uv, b_fuv, img, tmp_path / "gem_test.fbx")
    assert fbx_p.exists() and png_p.exists()
    rv, rf, ruv, rfuv = load_fbx_mesh(fbx_p, convert_zup=False)
    assert len(rv) == len(box.vertices)
    assert len(rf) == len(box.faces)
    assert ruv is not None and len(ruv) == len(b_uv)
    assert rfuv is not None and rfuv.shape == b_fuv.shape

    # reset_pack_directory wipes stale files while preserving pack_icon.png and install scripts
    pack = tmp_path / "pack"
    (pack / "BP").mkdir(parents=True)
    (pack / "BP" / "pack_icon.png").write_bytes(b"ICON")
    (pack / "BP" / "stale.json").write_text("old", encoding="utf-8")
    (pack / "install.cmd").write_text("@echo custom install\n", encoding="utf-8")
    (pack / "helper.bat").write_text("@echo custom helper\n", encoding="utf-8")
    reset_pack_directory(pack, preserve_relpaths=["BP/pack_icon.png"])
    assert (pack / "BP" / "pack_icon.png").read_bytes() == b"ICON"
    assert not (pack / "BP" / "stale.json").exists()
    assert (pack / "install.cmd").read_text(encoding="utf-8") == "@echo custom install\n"
    assert (pack / "helper.bat").read_text(encoding="utf-8") == "@echo custom helper\n"

    # write_install_cmd leaves existing install.cmd untouched by default
    write_install_cmd(pack, "BP", "RP", "TestPack")
    assert (pack / "install.cmd").read_text(encoding="utf-8") == "@echo custom install\n"
    write_install_cmd(pack, "BP", "RP", "TestPack", overwrite=True)
    assert "TestPack" in (pack / "install.cmd").read_text(encoding="utf-8")
