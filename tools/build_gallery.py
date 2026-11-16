#!/usr/bin/env python3
"""
Unified Builder for the 3-Tier Bedrock Edition 3DGS Gem Packs:
- `bedrock/01_Imposter_Pack`  (Tier 1: Angle-Pinned Spherical Imposter — 3 Quality Tiers x 2 Gems)
- `bedrock/02_Poly_Mesh_Pack` (Tier 2: Shrinkwrap Surface Map + 6-View Orthographic Bake — 3 Mesh Styles x 2 Gems)
- `bedrock/03_Full_GS_Pack`   (Tier 3: Full Oriented Splat Cloud & Particle Stream — 3 Density Presets x 2 Gems)

All 3 packs:
- Print a clear in-game Startup Help Banner when the player spawns or joins the world,
  stating which pack is active and listing the exact `/scriptevent` commands to spawn
  one gem at a time, give items, or clear entities.
- Support **Crouch + Interact** to pick up any gem into an item with a 128x128 thumbnail
  and 3D/Imposter attachable (rendered in hand & when dropped!).
- Support **Interact** on any block to place the held gem down.
"""
import argparse
import json
import shutil
import time
from pathlib import Path

from render_imposter import GaussianScene, render_imposter_atlas
from export_mesh import export_shrinkwrap_polymesh, render_6view_ortho_atlas
from export_geo_splat import export_geo_splat
from export_oriented import export_oriented_bedrock


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_install_cmd(pack_dir: Path, bp_name: str, rp_name: str, title: str) -> None:
    content = f"""@echo off
setlocal
echo Installing {title} to Bedrock development folders...

set "BEDROCK_DEV=%appdata%\\Minecraft Bedrock\\Users\\Shared\\games\\com.mojang"
if not exist "%BEDROCK_DEV%" (
    set "BEDROCK_DEV=%localappdata%\\Packages\\Microsoft.MinecraftUWP_8wekyb3d8bbwe\\LocalState\\games\\com.mojang"
)

if not exist "%BEDROCK_DEV%" (
    echo [ERROR] Could not locate Minecraft Bedrock com.mojang directory.
    pause
    exit /b 1
)

set "TARGET_BP=%BEDROCK_DEV%\\development_behavior_packs\\{bp_name}"
set "TARGET_RP=%BEDROCK_DEV%\\development_resource_packs\\{rp_name}"

if exist "%TARGET_BP%" (
    echo Removing old {bp_name}...
    rmdir /s /q "%TARGET_BP%"
)
if exist "%TARGET_RP%" (
    echo Removing old {rp_name}...
    rmdir /s /q "%TARGET_RP%"
)

echo Copying fresh {bp_name} and {rp_name}...
xcopy /y /e /i "%~dp0{bp_name}" "%TARGET_BP%" >nul
xcopy /y /e /i "%~dp0{rp_name}" "%TARGET_RP%" >nul

echo Done! Cleanly installed {bp_name} and {rp_name} to:
echo   %BEDROCK_DEV%
pause
"""
    (pack_dir / "install.cmd").write_text(content, encoding="utf-8")


def generate_pack_script_js(
    pack_title: str,
    pack_subtitle: str,
    cmd_prefix: str,
    entity_id: str,
    item_prefix: str,
    variants: list[dict],
    include_sample_runner: bool = False,
) -> str:
    """
    Generate the Bedrock @minecraft/server script for any of the 3 packs:
    - Startup/Init help message printed on world join & reload
    - Single-gem spawning via `/scriptevent <prefix>:spawn <0..5|slug>`
    - Item giving via `/scriptevent <prefix>:items`
    - Clearing via `/scriptevent <prefix>:clear`
    - Crouch + Interact to pick up gem into item
    - Interact on block to place gem down
    """
    gem_names = [v["label"] for v in variants]
    slug_map = {v["slug"]: v["idx"] for v in variants}
    for v in variants:
        slug_map[str(v["idx"])] = v["idx"]

    sample_imports = (
        'import SampleManager from "./SampleManager.js";\nimport { scriptBox } from "./ScriptBox.js";\n\n'
        "const sm = new SampleManager();\nsm.registerSamples({ scriptBox: [scriptBox] });\n"
        if include_sample_runner
        else ""
    )
    extra_help_line = (
        f'	player.sendMessage("  §e/scriptevent sample:run §7- Stream 5,000 oriented splat particles at looked-at block");\n'
        if include_sample_runner
        else ""
    )

    return f"""{sample_imports}import {{ world, system, ItemStack }} from "@minecraft/server";

const PACK_TITLE = {json.dumps(pack_title)};
const PACK_SUBTITLE = {json.dumps(pack_subtitle)};
const CMD_PREFIX = {json.dumps(cmd_prefix)};
const ENTITY_ID = {json.dumps(entity_id)};
const ITEM_PREFIX = {json.dumps(item_prefix)};
const GEM_NAMES = {json.dumps(gem_names)};
const SLUG_MAP = {json.dumps(slug_map)};

const interactCooldown = new Map();
const greetedPlayers = new Set();

function canInteract(player) {{
	const now = system.currentTick;
	const last = interactCooldown.get(player.id) ?? -10;
	if (now - last < 5) return false;
	interactCooldown.set(player.id, now);
	return true;
}}

export function printPackHelp(player) {{
	player.sendMessage("§8====================================================");
	player.sendMessage(`§b§l[${{PACK_TITLE}}] §r§7- ${{PACK_SUBTITLE}}`);
	player.sendMessage("§fSpawn one gem at a time in front of you:");
	for (let i = 0; i < GEM_NAMES.length; i++) {{
		player.sendMessage(`  §a/scriptevent ${{CMD_PREFIX}}:spawn ${{i}} §7-> §f${{GEM_NAMES[i]}}`);
	}}
	player.sendMessage(`  §e/scriptevent ${{CMD_PREFIX}}:items §7- Give all 6 Gem items to your inventory`);
	player.sendMessage(`  §e/scriptevent ${{CMD_PREFIX}}:clear §7- Remove spawned gems`);
	player.sendMessage(`  §e/scriptevent ${{CMD_PREFIX}}:help  §7- Show this command list again`);
{extra_help_line}	player.sendMessage("§dControls: §fCrouch + Interact §7to Pick Up | §fInteract on Block §7to Place");
	player.sendMessage("§8====================================================");
}}

// Print startup banner when player spawns in the world (or after /reload)
world.afterEvents.playerSpawn.subscribe((event) => {{
	const player = event.player;
	if (!player || greetedPlayers.has(player.id)) return;
	greetedPlayers.add(player.id);
	system.runTimeout(() => {{
		if (player.isValid()) {{
			printPackHelp(player);
		}}
	}}, 40);
}});

// Fallback for /reload when player is already spawned in the world
system.runTimeout(() => {{
	for (const player of world.getAllPlayers()) {{
		if (!greetedPlayers.has(player.id)) {{
			greetedPlayers.add(player.id);
			printPackHelp(player);
		}}
	}}
}}, 60);

// 1. PICKUP MECHANIC: Crouch + Interact (Sneak + Right-Click/Tap) on a Gem Entity
world.beforeEvents.playerInteractWithEntity.subscribe((event) => {{
	const {{ player, target }} = event;
	if (!target || target.typeId !== ENTITY_ID) return;

	if (!player.isSneaking) {{
		if (canInteract(player)) {{
			const variant = target.getComponent("minecraft:variant")?.value ?? 0;
			const name = GEM_NAMES[variant] ?? GEM_NAMES[0];
			system.run(() => {{
				player.onScreenDisplay.setActionBar(`§b✦ ${{name}} §7(Crouch + Interact to pick up)`);
			}});
		}}
		return;
	}}

	if (!canInteract(player)) return;
	event.cancel = true;

	const variant = target.getComponent("minecraft:variant")?.value ?? 0;
	const clampedVariant = Math.max(0, Math.min(GEM_NAMES.length - 1, variant));
	const itemId = `${{ITEM_PREFIX}}${{clampedVariant}}`;
	const gemName = GEM_NAMES[clampedVariant];
	const loc = target.location;
	const dim = target.dimension;

	system.run(() => {{
		if (!target.isValid()) return;
		target.remove();

		const itemStack = new ItemStack(itemId, 1);
		itemStack.nameTag = `§r§d✦ ${{gemName}}`;
		itemStack.setLore([
			`§7${{PACK_TITLE}}`,
			"§eInteract on any block to place",
			"§8Crouch + Interact to pick up"
		]);

		const inv = player.getComponent("minecraft:inventory")?.container;
		if (inv) {{
			const leftover = inv.addItem(itemStack);
			if (leftover) {{
				dim.spawnItem(leftover, loc);
			}}
		}} else {{
			dim.spawnItem(itemStack, loc);
		}}

		dim.playSound("random.pop", loc, {{ volume: 0.9, pitch: 1.25 }});
		player.onScreenDisplay.setActionBar(`§aPicked up §f${{gemName}}`);
	}});
}});

// 2. PLACEMENT MECHANIC: Interact on any Block while holding a Gem Item
world.beforeEvents.playerInteractWithBlock.subscribe((event) => {{
	const {{ player, itemStack, block, blockFace }} = event;
	if (!itemStack || !itemStack.typeId.startsWith(ITEM_PREFIX)) return;

	const suffix = itemStack.typeId.slice(ITEM_PREFIX.length);
	const variant = parseInt(suffix, 10);
	if (Number.isNaN(variant) || variant < 0 || variant >= GEM_NAMES.length) return;

	if (!canInteract(player)) return;
	event.cancel = true;

	const dim = block.dimension;
	const offsets = {{
		Up: {{ x: 0.5, y: 1.0, z: 0.5 }},
		Down: {{ x: 0.5, y: -1.5, z: 0.5 }},
		North: {{ x: 0.5, y: 0.0, z: -0.5 }},
		South: {{ x: 0.5, y: 0.0, z: 1.5 }},
		West: {{ x: -0.5, y: 0.0, z: 0.5 }},
		East: {{ x: 1.5, y: 0.0, z: 0.5 }}
	}};
	const off = offsets[blockFace] ?? offsets.Up;
	const spawnPos = {{
		x: block.location.x + off.x,
		y: block.location.y + off.y,
		z: block.location.z + off.z
	}};

	system.run(() => {{
		const ent = dim.spawnEntity(ENTITY_ID, spawnPos);
		ent.triggerEvent(`splat:set_variant_${{variant}}`);
		ent.nameTag = GEM_NAMES[variant];

		dim.playSound("dig.amethyst_block", spawnPos, {{ volume: 1.0, pitch: 1.15 }});
		player.onScreenDisplay.setActionBar(`§dPlaced §f${{GEM_NAMES[variant]}}`);

		const gm = player.getGameMode();
		if (gm !== "creative" && gm !== "Creative") {{
			const inv = player.getComponent("minecraft:inventory")?.container;
			if (inv) {{
				const slot = player.selectedSlotIndex;
				const cur = inv.getItem(slot);
				if (cur && cur.typeId === itemStack.typeId) {{
					if (cur.amount > 1) {{
						cur.amount -= 1;
						inv.setItem(slot, cur);
					}} else {{
						inv.setItem(slot, undefined);
					}}
				}}
			}}
		}}
	}});
}});

// 3. COMMAND HANDLER: `/scriptevent <prefix>:spawn <0..5>`, `:items`, `:clear`, `:help`
system.afterEvents.scriptEventReceive.subscribe((event) => {{
	const id = event.id.toLowerCase();
	const player = event.sourceEntity;
	if (!player) return;

	const msg = (event.message ?? "").trim().toLowerCase();

	if (id === `${{CMD_PREFIX}}:help` || id === "gem:help") {{
		printPackHelp(player);
		return;
	}}

	if (id === `${{CMD_PREFIX}}:items` || id === "gem:items") {{
		const inv = player.getComponent("minecraft:inventory")?.container;
		if (!inv) return;
		for (let i = 0; i < GEM_NAMES.length; i++) {{
			const st = new ItemStack(`${{ITEM_PREFIX}}${{i}}`, 1);
			st.nameTag = `§r§d✦ ${{GEM_NAMES[i]}}`;
			st.setLore([
				`§7${{PACK_TITLE}}`,
				"§eInteract on any block to place",
				"§8Crouch + Interact to pick up"
			]);
			inv.addItem(st);
		}}
		player.sendMessage(`§a[${{PACK_TITLE}}] Added all ${{GEM_NAMES.length}} Gem items to your inventory!`);
		return;
	}}

	if (id === `${{CMD_PREFIX}}:clear` || id === "gem:clear") {{
		const dim = player.dimension;
		const ents = dim.getEntities({{ type: ENTITY_ID }});
		for (const e of ents) e.remove();
		player.sendMessage(`§e[${{PACK_TITLE}}] Cleared ${{ents.length}} spawned gem entities.`);
		return;
	}}

	if (id === `${{CMD_PREFIX}}:spawn` || id === "gem:spawn") {{
		let variant = 0;
		if (msg.length > 0) {{
			if (msg in SLUG_MAP) {{
				variant = SLUG_MAP[msg];
			}} else {{
				const parsed = parseInt(msg, 10);
				if (!Number.isNaN(parsed) && parsed >= 0 && parsed < GEM_NAMES.length) {{
					variant = parsed;
				}} else {{
					player.sendMessage(`§cUnknown gem '${{msg}}'. Use 0..${{GEM_NAMES.length - 1}}:`);
					printPackHelp(player);
					return;
				}}
			}}
		}}

		const dim = player.dimension;
		const view = player.getViewDirection();
		const horizLen = Math.hypot(view.x, view.z) || 1.0;
		const spawnPos = {{
			x: Math.floor(player.location.x + (view.x / horizLen) * 3.0) + 0.5,
			y: Math.floor(player.location.y),
			z: Math.floor(player.location.z + (view.z / horizLen) * 3.0) + 0.5
		}};

		const ent = dim.spawnEntity(ENTITY_ID, spawnPos);
		ent.triggerEvent(`splat:set_variant_${{variant}}`);
		ent.nameTag = GEM_NAMES[variant];

		dim.playSound("block.amethyst_block.chime", spawnPos, {{ volume: 1.0, pitch: 1.1 }});
		player.sendMessage(
			`§a[${{PACK_TITLE}}] Spawned §f${{GEM_NAMES[variant]}}§a! (§eCrouch + Interact§a to pick up, §eInteract§a on block to place)`
		);
	}}
}});
"""


def build_pack_01_imposter(repo_root: Path, scenes: dict[str, GaussianScene]) -> None:
    """
    Build Pack 01 (`bedrock/01_Imposter_Pack`):
    - Angle-Pinned Spherical Imposter (`v.snapped_yaw`, `v.snapped_pitch`) so the quad locks to the
      baked camera angle and steps crisply without smooth-tracking jumpiness!
    - 3 Quality Tiers x 2 Gems = 6 Variants:
      * 0: Citrine [Low: 12x3 (36 angles)]
      * 1: Citrine [Medium: 16x5 (80 angles)]
      * 2: Citrine [High: 24x7 (168 angles)]
      * 3: Amethyst [Low: 12x3 (36 angles)]
      * 4: Amethyst [Medium: 16x5 (80 angles)]
      * 5: Amethyst [High: 24x7 (168 angles)]
    """
    print("\n=== Building Pack 01: 01_Imposter_Pack (Angle-Pinned Spherical Imposter — 3 Quality Tiers) ===")
    pack_dir = repo_root / "bedrock" / "01_Imposter_Pack"
    bp_dir = pack_dir / "Imposter_BP"
    rp_dir = pack_dir / "Imposter_RP"

    variants = [
        {"idx": 0, "slug": "low_citrine",   "label": "Citrine Cluster [Low: 12x3 = 36 Angles]",      "ply": "Example1.ply", "cols": 12, "rows": 3, "res": 160},
        {"idx": 1, "slug": "med_citrine",   "label": "Citrine Cluster [Medium: 16x5 = 80 Angles]",   "ply": "Example1.ply", "cols": 16, "rows": 5, "res": 144},
        {"idx": 2, "slug": "high_citrine",  "label": "Citrine Cluster [High: 24x7 = 168 Angles]",    "ply": "Example1.ply", "cols": 24, "rows": 7, "res": 128},
        {"idx": 3, "slug": "low_amethyst",  "label": "Amethyst Geode [Low: 12x3 = 36 Angles]",       "ply": "Example2.ply", "cols": 12, "rows": 3, "res": 160},
        {"idx": 4, "slug": "med_amethyst",  "label": "Amethyst Geode [Medium: 16x5 = 80 Angles]",    "ply": "Example2.ply", "cols": 16, "rows": 5, "res": 144},
        {"idx": 5, "slug": "high_amethyst", "label": "Amethyst Geode [High: 24x7 = 168 Angles]",     "ply": "Example2.ply", "cols": 24, "rows": 7, "res": 128},
    ]

    for v in variants:
        i = v["idx"]
        scene = scenes[v["ply"]]
        atlas_out = rp_dir / "textures" / "entity" / f"spritesheet_{i}.png"
        icon_out = rp_dir / "textures" / "items" / f"gem_{i}.png"
        render_imposter_atlas(
            scene,
            output_atlas=atlas_out,
            output_icon=icon_out,
            cols=v["cols"],
            rows=v["rows"],
            tile_res=v["res"],
            pitch_min=-45.0,
            pitch_max=45.0,
        )
        if i == 1:
            (rp_dir / "textures" / "entity" / "spritesheet.png").write_bytes(atlas_out.read_bytes())

    # Manifests
    write_json(
        bp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "01 Spherical Imposter Pack BP",
                "description": "Tier 1: Angle-pinned spherical 3DGS imposter at Low (12x3), Medium (16x5), and High (24x7) angular density",
                "uuid": "4f183cf9-8b92-491c-b26a-8b839ef74911",
                "version": [1, 2, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "Imposter entities and items",
                    "type": "data",
                    "uuid": "c3539bc7-e314-469b-b89e-3d0d34a41f6e",
                    "version": [1, 2, 0],
                },
                {
                    "description": "Spawn commands and pickup/placement logic",
                    "type": "script",
                    "language": "javascript",
                    "uuid": "94a1f63e-52c1-443d-98ea-6217a67c52f1",
                    "version": [1, 2, 0],
                    "entry": "scripts/main.js",
                },
            ],
            "dependencies": [
                {"module_name": "@minecraft/server", "version": "1.14.0"},
                {"uuid": "a793a382-f5c7-43cf-87b6-c564175b9de3", "version": [1, 2, 0]},
            ],
        },
    )

    write_json(
        rp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "01 Spherical Imposter Pack RP",
                "description": "Tier 1: Angle-pinned spherical 3DGS imposter visuals (12x3, 16x5, 24x7 quality presets)",
                "uuid": "a793a382-f5c7-43cf-87b6-c564175b9de3",
                "version": [1, 2, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "Imposter geometry, animations, and textures",
                    "type": "resources",
                    "uuid": "7876a47a-c114-44b4-82a9-0d3ee787e915",
                    "version": [1, 2, 0],
                }
            ],
            "dependencies": [
                {"uuid": "4f183cf9-8b92-491c-b26a-8b839ef74911", "version": [1, 2, 0]}
            ],
        },
    )

    # Behavior Entity (`Imposter_BP/entities/imposter.json`)
    component_groups = {}
    events = {"minecraft:entity_spawned": {"add": {"component_groups": ["splat:variant_1"]}}}
    for v in variants:
        i = v["idx"]
        component_groups[f"splat:variant_{i}"] = {"minecraft:variant": {"value": i}}
        events[f"splat:set_variant_{i}"] = {
            "add": {"component_groups": [f"splat:variant_{i}"]},
            "remove": {"component_groups": [f"splat:variant_{j}" for j in range(len(variants)) if j != i]},
        }

    write_json(
        bp_dir / "entities" / "imposter.json",
        {
            "format_version": "1.20.0",
            "minecraft:entity": {
                "description": {
                    "identifier": "custom:imposter",
                    "is_spawnable": True,
                    "is_summonable": True,
                    "is_experimental": False,
                },
                "component_groups": component_groups,
                "components": {
                    "minecraft:variant": {"value": 1},
                    "minecraft:physics": {"has_gravity": False, "has_collision": True},
                    "minecraft:collision_box": {"width": 0.9, "height": 1.1},
                    "minecraft:pushable": {"is_pushable": False, "is_pushable_by_piston": True},
                    "minecraft:knockback_resistance": {"value": 1.0},
                    "minecraft:damage_sensor": {"triggers": {"cause": "all", "deals_damage": False}},
                    "minecraft:nameable": {"always_show": False, "allow_name_tag_renaming": True},
                },
                "events": events,
            },
        },
    )

    # Items
    for v in variants:
        i = v["idx"]
        write_json(
            bp_dir / "items" / f"gem_item_{i}.json",
            {
                "format_version": "1.20.50",
                "minecraft:item": {
                    "description": {"identifier": f"custom:gem_item_{i}", "menu_category": {"category": "items"}},
                    "components": {
                        "minecraft:max_stack_size": 16,
                        "minecraft:icon": {"texture": f"gem_{i}"},
                        "minecraft:display_name": {"value": f"✦ {v['label']}"},
                    },
                },
            },
        )

    # Script
    (bp_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (bp_dir / "scripts" / "main.js").write_text(
        generate_pack_script_js(
            pack_title="01_Imposter_Pack (Spherical Imposter)",
            pack_subtitle="Angle-Pinned 3DGS Imposter (Low 12x3, Med 16x5, High 24x7)",
            cmd_prefix="imposter",
            entity_id="custom:imposter",
            item_prefix="custom:gem_item_",
            variants=variants,
            include_sample_runner=False,
        ),
        encoding="utf-8",
    )

    # Geometry (`Imposter_RP/models/entity/imposter.geo.json`)
    # Double-sided plane (North + mirrored South face) so when angle-pinned at any 360° angle,
    # both sides render with un-mirrored UVs!
    write_json(
        rp_dir / "models" / "entity" / "imposter.geo.json",
        {
            "format_version": "1.12.0",
            "minecraft:geometry": [
                {
                    "description": {
                        "identifier": "geometry.imposter",
                        "texture_width": 16,
                        "texture_height": 16,
                        "visible_bounds_width": 4,
                        "visible_bounds_height": 4,
                        "visible_bounds_offset": [0, 1, 0],
                    },
                    "bones": [
                        {"name": "root", "pivot": [0, 0, 0]},
                        {
                            "name": "body",
                            "parent": "root",
                            "pivot": [0, 12, 0],
                            "cubes": [
                                {
                                    "origin": [-12, 0, 0],
                                    "size": [24, 24, 0],
                                    "uv": {
                                        "north": {"uv": [0, 0], "uv_size": [16, 16]},
                                        "south": {"uv": [16, 0], "uv_size": [-16, 16]},
                                    },
                                }
                            ],
                        },
                    ],
                }
            ],
        },
    )

    # Resource Pack Animation (`Imposter_RP/animations/imposter.animation.json`)
    # Locked to the discrete baked frame angle (`v.snapped_pitch`, `v.snapped_yaw`)!
    write_json(
        rp_dir / "animations" / "imposter.animation.json",
        {
            "format_version": "1.8.0",
            "animations": {
                "animation.imposter.align_to_baked_frame": {
                    "loop": True,
                    "bones": {
                        "body": {
                            "rotation": ["v.snapped_pitch", "v.snapped_yaw", 0]
                        }
                    },
                },
                "animation.imposter.hand_pose": {
                    "loop": True,
                    "bones": {
                        "root_item": {
                            "rotation": [0, 0, 0],
                            "position": [0, 2, 0],
                            "scale": 0.75,
                        }
                    },
                },
            },
        },
    )

    # Client Entity (`Imposter_RP/entity/imposter.entity.json`)
    # Dynamically selects cols/rows based on variant:
    # - variant 0, 3 -> Low  (12 cols x 3 rows)
    # - variant 1, 4 -> Med  (16 cols x 5 rows)
    # - variant 2, 5 -> High (24 cols x 7 rows)
    # And locks the bone rotation (`v.snapped_yaw = -(v.col * v.yaw_step)`, `v.snapped_pitch`)
    # to the exact discrete world angle the baked frame was rendered from!
    textures_map = {"default": "textures/entity/spritesheet_1"}
    for v in variants:
        i = v["idx"]
        textures_map[f"gem_{i}"] = f"textures/entity/spritesheet_{i}"

    write_json(
        rp_dir / "entity" / "imposter.entity.json",
        {
            "format_version": "1.10.0",
            "minecraft:client_entity": {
                "description": {
                    "identifier": "custom:imposter",
                    "materials": {"default": "imposter_alphablend"},
                    "textures": textures_map,
                    "geometry": {"default": "geometry.imposter"},
                    "animations": {"align_to_baked_frame": "animation.imposter.align_to_baked_frame"},
                    "scripts": {
                        "initialize": [
                            "v.cols = 16.0;",
                            "v.rows = 5.0;",
                            "v.pitch_min = -45.0;",
                            "v.pitch_max = 45.0;",
                        ],
                        "pre_animation": [
                            "v.tier = math.mod(query.variant, 3);",
                            "v.cols = (v.tier == 0) ? 12.0 : ((v.tier == 1) ? 16.0 : 24.0);",
                            "v.rows = (v.tier == 0) ? 3.0 : ((v.tier == 1) ? 5.0 : 7.0);",
                            "v.pitch_min = -45.0;",
                            "v.pitch_max = 45.0;",
                            "v.yaw_step = 360.0 / v.cols;",
                            "v.rel_cam_yaw = query.rotation_to_camera(1) - query.body_y_rotation;",
                            "v.raw_yaw = -v.rel_cam_yaw;",
                            "v.norm_yaw = math.mod(math.mod(v.raw_yaw + (v.yaw_step * 0.5), 360.0) + 360.0, 360.0);",
                            "v.col = math.clamp(math.floor(v.norm_yaw / v.yaw_step), 0, v.cols - 1);",
                            "v.snapped_yaw = -(v.col * v.yaw_step);",
                            "v.raw_pitch = math.clamp(-query.rotation_to_camera(0), v.pitch_min, v.pitch_max);",
                            "v.pitch_step = (v.pitch_max - v.pitch_min) / math.max(1, v.rows - 1);",
                            "v.row = math.clamp(math.round((v.pitch_max - v.raw_pitch) / v.pitch_step), 0, v.rows - 1);",
                            "v.snapped_pitch = -(v.pitch_max - (v.row * v.pitch_step));",
                        ],
                        "animate": ["align_to_baked_frame"],
                    },
                    "render_controllers": ["controller.render.imposter"],
                }
            },
        },
    )

    # Attachables for held/dropped items
    for v in variants:
        i = v["idx"]
        cols = float(v["cols"])
        rows = float(v["rows"])
        write_json(
            rp_dir / "attachables" / f"gem_item_{i}.attachable.json",
            {
                "format_version": "1.10.0",
                "minecraft:attachable": {
                    "description": {
                        "identifier": f"custom:gem_item_{i}",
                        "materials": {"default": "imposter_alphatest", "enchanted": "imposter_alphatest"},
                        "textures": {
                            "default": f"textures/entity/spritesheet_{i}",
                            "enchanted": "textures/misc/enchanted_item_glint",
                        },
                        "geometry": {"default": "geometry.imposter_hand"},
                        "animations": {"hand_pose": "animation.imposter.hand_pose"},
                        "scripts": {
                            "pre_animation": [
                                f"v.cols = {cols};",
                                f"v.rows = {rows};",
                                "v.pitch_min = -45.0;",
                                "v.pitch_max = 45.0;",
                                "v.yaw_step = 360.0 / v.cols;",
                                "v.rel_cam_yaw = query.rotation_to_camera(1) - query.body_y_rotation;",
                                "v.raw_yaw = -v.rel_cam_yaw;",
                                "v.norm_yaw = math.mod(math.mod(v.raw_yaw + (v.yaw_step * 0.5), 360.0) + 360.0, 360.0);",
                                "v.col = math.clamp(math.floor(v.norm_yaw / v.yaw_step), 0, v.cols - 1);",
                                "v.raw_pitch = math.clamp(-query.rotation_to_camera(0), v.pitch_min, v.pitch_max);",
                                "v.pitch_step = (v.pitch_max - v.pitch_min) / math.max(1, v.rows - 1);",
                                "v.row = math.clamp(math.round((v.pitch_max - v.raw_pitch) / v.pitch_step), 0, v.rows - 1);",
                            ],
                            "animate": ["hand_pose"],
                        },
                        "render_controllers": ["controller.render.imposter_attachable"],
                    }
                },
            },
        )

    lang_lines = ["entity.custom:imposter.name=Gaussian Splat Imposter Gem"]
    for v in variants:
        lang_lines.append(f"item.custom:gem_item_{v['idx']}.name=✦ {v['label']}")
    (rp_dir / "texts").mkdir(parents=True, exist_ok=True)
    (rp_dir / "texts" / "en_US.lang").write_text("\n".join(lang_lines) + "\n", encoding="utf-8")
    write_install_cmd(pack_dir, "Imposter_BP", "Imposter_RP", "01 Spherical Imposter Pack")


def build_pack_02_polymesh(repo_root: Path, scenes: dict[str, GaussianScene]) -> None:
    """
    Build Pack 02 (`bedrock/02_Poly_Mesh_Pack`):
    - Pure Shrinkwrap Surface Map + 6-View Orthographic High-to-Low Bake (`poly_mesh`)
    - 3 Mesh Styles x 2 Gems = 6 Variants:
      * 0: Citrine Cluster [Smooth Shrinkwrap: 1,280 faces]
      * 1: Citrine Cluster [Detailed Shrinkwrap: 5,120 faces]
      * 2: Citrine Cluster [Faceted Crystal Hull: 320 faces]
      * 3: Amethyst Geode  [Smooth Shrinkwrap: 1,280 faces]
      * 4: Amethyst Geode  [Detailed Shrinkwrap: 5,120 faces]
      * 5: Amethyst Geode  [Faceted Crystal Hull: 320 faces]
    """
    print("\n=== Building Pack 02: 02_Poly_Mesh_Pack (Shrinkwrap Surface Map + 6-View High-to-Low Bake) ===")
    pack_dir = repo_root / "bedrock" / "02_Poly_Mesh_Pack"
    bp_dir = pack_dir / "PolyMesh_BP"
    rp_dir = pack_dir / "PolyMesh_RP"

    variants = [
        {"idx": 0, "slug": "smooth_citrine",    "label": "Citrine Cluster [Smooth Shrinkwrap: 1280 tris]",   "ply": "Example1.ply", "mode": "smooth",   "tex_idx": 0},
        {"idx": 1, "slug": "detailed_citrine",  "label": "Citrine Cluster [Detailed Shrinkwrap: 5120 tris]", "ply": "Example1.ply", "mode": "detailed", "tex_idx": 0},
        {"idx": 2, "slug": "faceted_citrine",   "label": "Citrine Cluster [Faceted Crystal: 320 tris]",      "ply": "Example1.ply", "mode": "faceted",  "tex_idx": 0},
        {"idx": 3, "slug": "smooth_amethyst",   "label": "Amethyst Geode [Smooth Shrinkwrap: 1280 tris]",    "ply": "Example2.ply", "mode": "smooth",   "tex_idx": 1},
        {"idx": 4, "slug": "detailed_amethyst", "label": "Amethyst Geode [Detailed Shrinkwrap: 5120 tris]",  "ply": "Example2.ply", "mode": "detailed", "tex_idx": 1},
        {"idx": 5, "slug": "faceted_amethyst",  "label": "Amethyst Geode [Faceted Crystal: 320 tris]",       "ply": "Example2.ply", "mode": "faceted",  "tex_idx": 1},
    ]

    # Render the two 6-view studio texture atlases (1536x1024) for Example1 and Example2
    tex_paths = {}
    for tex_i, ply_name in [(0, "Example1.ply"), (1, "Example2.ply")]:
        scene = scenes[ply_name]
        tex_out = rp_dir / "textures" / "entity" / f"poly_gem_tex_{tex_i}.png"
        tex_out.parent.mkdir(parents=True, exist_ok=True)
        atlas_img = render_6view_ortho_atlas(scene, tile_res=512, fill_ratio=0.82)
        atlas_img.save(tex_out, optimize=True)
        tex_paths[tex_i] = tex_out

    for v in variants:
        i = v["idx"]
        scene = scenes[v["ply"]]
        geo_out = rp_dir / "models" / "entity" / f"poly_gem_{i}.geo.json"
        export_shrinkwrap_polymesh(
            scene,
            output_geo=geo_out,
            output_tex=None,  # Already rendered shared 6-view studio texture
            geometry_id=f"geometry.poly_gem_{i}",
            mode=v["mode"],
            target_size=24.0,
            tile_res=512,
        )
        # Render 128x128 item icon
        icon_img = scene.render_view(yaw_deg=-15.0, pitch_deg=18.0, tile_res=128, fill_ratio=0.84)
        icon_out = rp_dir / "textures" / "items" / f"poly_gem_{i}.png"
        icon_out.parent.mkdir(parents=True, exist_ok=True)
        icon_img.save(icon_out, optimize=True)

    # Copy pack icons
    src_icon = repo_root / "bedrock" / "01_Imposter_Pack" / "Imposter_RP" / "pack_icon.png"
    if src_icon.exists():
        (bp_dir / "pack_icon.png").parent.mkdir(parents=True, exist_ok=True)
        (rp_dir / "pack_icon.png").parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src_icon, bp_dir / "pack_icon.png")
        shutil.copy(src_icon, rp_dir / "pack_icon.png")

    # Manifests (Unique UUIDs for Pack 02)
    bp_uuid = "b2024991-61e0-49f1-9c2a-592837164501"
    rp_uuid = "a2024991-61e0-49f1-9c2a-592837164502"
    write_json(
        bp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "02 Shrinkwrap PolyMesh Pack BP",
                "description": "Tier 2: Shrinkwrap 3DGS surface mesh + 6-view studio bake (Smooth, Detailed, and Faceted options)",
                "uuid": bp_uuid,
                "version": [1, 0, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "PolyMesh entities and items",
                    "type": "data",
                    "uuid": "b2024991-61e0-49f1-9c2a-592837164503",
                    "version": [1, 0, 0],
                },
                {
                    "description": "Spawn commands and pickup/placement logic",
                    "type": "script",
                    "language": "javascript",
                    "uuid": "b2024991-61e0-49f1-9c2a-592837164504",
                    "version": [1, 0, 0],
                    "entry": "scripts/main.js",
                },
            ],
            "dependencies": [
                {"module_name": "@minecraft/server", "version": "1.14.0"},
                {"uuid": rp_uuid, "version": [1, 0, 0]},
            ],
        },
    )

    write_json(
        rp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "02 Shrinkwrap PolyMesh Pack RP",
                "description": "Tier 2: Shrinkwrap 3DGS surface mesh + 6-view studio bake (Smooth, Detailed, and Faceted options)",
                "uuid": rp_uuid,
                "version": [1, 0, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "Shrinkwrap poly_mesh models and 6-view textures",
                    "type": "resources",
                    "uuid": "a2024991-61e0-49f1-9c2a-592837164505",
                    "version": [1, 0, 0],
                }
            ],
            "dependencies": [{"uuid": bp_uuid, "version": [1, 0, 0]}],
        },
    )

    # Behavior Entity (`PolyMesh_BP/entities/poly_gem.json`)
    component_groups = {}
    events = {"minecraft:entity_spawned": {"add": {"component_groups": ["splat:variant_0"]}}}
    for v in variants:
        i = v["idx"]
        component_groups[f"splat:variant_{i}"] = {"minecraft:variant": {"value": i}}
        events[f"splat:set_variant_{i}"] = {
            "add": {"component_groups": [f"splat:variant_{i}"]},
            "remove": {"component_groups": [f"splat:variant_{j}" for j in range(len(variants)) if j != i]},
        }

    write_json(
        bp_dir / "entities" / "poly_gem.json",
        {
            "format_version": "1.20.0",
            "minecraft:entity": {
                "description": {
                    "identifier": "custom:poly_gem",
                    "is_spawnable": True,
                    "is_summonable": True,
                    "is_experimental": False,
                },
                "component_groups": component_groups,
                "components": {
                    "minecraft:variant": {"value": 0},
                    "minecraft:physics": {"has_gravity": False, "has_collision": True},
                    "minecraft:collision_box": {"width": 0.9, "height": 1.2},
                    "minecraft:pushable": {"is_pushable": False, "is_pushable_by_piston": True},
                    "minecraft:knockback_resistance": {"value": 1.0},
                    "minecraft:damage_sensor": {"triggers": {"cause": "all", "deals_damage": False}},
                    "minecraft:nameable": {"always_show": False, "allow_name_tag_renaming": True},
                },
                "events": events,
            },
        },
    )

    # Items
    for v in variants:
        i = v["idx"]
        write_json(
            bp_dir / "items" / f"poly_gem_item_{i}.json",
            {
                "format_version": "1.20.50",
                "minecraft:item": {
                    "description": {"identifier": f"custom:poly_gem_item_{i}", "menu_category": {"category": "items"}},
                    "components": {
                        "minecraft:max_stack_size": 16,
                        "minecraft:icon": {"texture": f"poly_gem_{i}"},
                        "minecraft:display_name": {"value": f"✦ {v['label']}"},
                    },
                },
            },
        )

    # Script
    (bp_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (bp_dir / "scripts" / "main.js").write_text(
        generate_pack_script_js(
            pack_title="02_Poly_Mesh_Pack (Shrinkwrap 3D Mesh)",
            pack_subtitle="Clean Shrinkwrap Surface Mesh + 6-View Studio Bake (Smooth, Detailed, Faceted)",
            cmd_prefix="polymesh",
            entity_id="custom:poly_gem",
            item_prefix="custom:poly_gem_item_",
            variants=variants,
            include_sample_runner=False,
        ),
        encoding="utf-8",
    )

    # Resource Pack Materials, Render Controllers, Client Entity, Attachables
    write_json(
        rp_dir / "materials" / "entity.material",
        {
            "materials": {
                "version": "1.0.0",
                "poly_gem_opaque:entity_alphatest": {
                    "-defines": ["ENABLE_FOG"],
                    "+states": ["DisableCulling"],
                },
            }
        },
    )

    write_json(
        rp_dir / "render_controllers" / "poly_gem.render_controllers.json",
        {
            "format_version": "1.8.0",
            "render_controllers": {
                "controller.render.poly_gem": {
                    "arrays": {
                        "geometries": {"Array.gem_geos": [f"Geometry.gem_{i}" for i in range(len(variants))]},
                        "textures": {"Array.gem_texs": [f"Texture.gem_{i}" for i in range(len(variants))]},
                    },
                    "geometry": "Array.gem_geos[query.variant]",
                    "materials": [{"*": "Material.default"}],
                    "textures": ["Array.gem_texs[query.variant]"],
                },
                "controller.render.poly_gem_attachable": {
                    "geometry": "Geometry.default",
                    "materials": [{"*": "Material.default"}],
                    "textures": ["Texture.default"],
                },
            },
        },
    )

    tex_map = {"default": "textures/entity/poly_gem_tex_0"}
    geo_map = {"default": "geometry.poly_gem_0"}
    for v in variants:
        i = v["idx"]
        t_i = v["tex_idx"]
        tex_map[f"gem_{i}"] = f"textures/entity/poly_gem_tex_{t_i}"
        geo_map[f"gem_{i}"] = f"geometry.poly_gem_{i}"

    write_json(
        rp_dir / "entity" / "poly_gem.entity.json",
        {
            "format_version": "1.10.0",
            "minecraft:client_entity": {
                "description": {
                    "identifier": "custom:poly_gem",
                    "materials": {"default": "poly_gem_opaque"},
                    "textures": tex_map,
                    "geometry": geo_map,
                    "render_controllers": ["controller.render.poly_gem"],
                }
            },
        },
    )

    write_json(
        rp_dir / "animations" / "poly_gem.animation.json",
        {
            "format_version": "1.8.0",
            "animations": {
                "animation.poly_gem.held_and_dropped": {
                    "loop": True,
                    "bones": {
                        "root": {"scale": 0.45, "position": [0, 3, 0]}
                    },
                }
            },
        },
    )

    for v in variants:
        i = v["idx"]
        t_i = v["tex_idx"]
        write_json(
            rp_dir / "attachables" / f"poly_gem_item_{i}.attachable.json",
            {
                "format_version": "1.10.0",
                "minecraft:attachable": {
                    "description": {
                        "identifier": f"custom:poly_gem_item_{i}",
                        "materials": {"default": "poly_gem_opaque", "enchanted": "poly_gem_opaque"},
                        "textures": {
                            "default": f"textures/entity/poly_gem_tex_{t_i}",
                            "enchanted": "textures/misc/enchanted_item_glint",
                        },
                        "geometry": {"default": f"geometry.poly_gem_{i}"},
                        "animations": {"held_pose": "animation.poly_gem.held_and_dropped"},
                        "scripts": {"animate": ["held_pose"]},
                        "render_controllers": ["controller.render.poly_gem_attachable"],
                    }
                },
            },
        )

    item_tex_data = {f"poly_gem_{v['idx']}": {"textures": f"textures/items/poly_gem_{v['idx']}"} for v in variants}
    write_json(
        rp_dir / "textures" / "item_texture.json",
        {
            "resource_pack_name": "02 Shrinkwrap PolyMesh Pack RP",
            "texture_name": "atlas.items",
            "texture_data": item_tex_data,
        },
    )

    lang_lines = ["entity.custom:poly_gem.name=Shrinkwrap 3D Mesh Gem"]
    for v in variants:
        lang_lines.append(f"item.custom:poly_gem_item_{v['idx']}.name=✦ {v['label']}")
    (rp_dir / "texts").mkdir(parents=True, exist_ok=True)
    (rp_dir / "texts" / "en_US.lang").write_text("\n".join(lang_lines) + "\n", encoding="utf-8")
    write_install_cmd(pack_dir, "PolyMesh_BP", "PolyMesh_RP", "02 Shrinkwrap PolyMesh Pack")


def build_pack_03_full_gs(repo_root: Path, scenes: dict[str, GaussianScene]) -> None:
    """
    Build Pack 03 (`bedrock/03_Full_GS_Pack`):
    - Full Oriented Gaussian Splat Cloud (`export_geo_splat.py`) + Particle Streamer (`/scriptevent sample:run`)
    - 3 Splat Density Tiers x 2 Gems = 6 Variants:
      * 0: Citrine Cluster [800 Surface Splats]
      * 1: Citrine Cluster [1,500 Surface Splats]
      * 2: Citrine Cluster [2,400 Surface Splats]
      * 3: Amethyst Geode  [800 Surface Splats]
      * 4: Amethyst Geode  [1,500 Surface Splats]
      * 5: Amethyst Geode  [2,400 Surface Splats]
    """
    print("\n=== Building Pack 03: 03_Full_GS_Pack (Full Oriented Splat Cloud & Particle Stream) ===")
    pack_dir = repo_root / "bedrock" / "03_Full_GS_Pack"
    bp_dir = pack_dir / "SplatRenderer_BP"
    rp_dir = pack_dir / "SplatRenderer_RP"

    # Copy base particle assets from old 02_Oriented_Splat_Pack if present
    old_pack = repo_root / "bedrock" / "02_Oriented_Splat_Pack"
    if old_pack.exists():
        for sub in ["particles", "textures/particles", "materials"]:
            src_s = old_pack / "SplatRenderer_RP" / sub
            dst_s = rp_dir / sub
            if src_s.exists():
                dst_s.parent.mkdir(parents=True, exist_ok=True)
                shutil.copytree(src_s, dst_s, dirs_exist_ok=True)
        for js_file in ["SampleManager.js", "ScriptBox.js", "SplatData.js"]:
            src_j = old_pack / "SplatRenderer_BP" / "scripts" / js_file
            if src_j.exists():
                (bp_dir / "scripts").mkdir(parents=True, exist_ok=True)
                shutil.copy(src_j, bp_dir / "scripts" / js_file)
        for ic_src, ic_dst in [
            (old_pack / "SplatRenderer_BP" / "pack_icon.png", bp_dir / "pack_icon.png"),
            (old_pack / "SplatRenderer_RP" / "pack_icon.png", rp_dir / "pack_icon.png"),
        ]:
            if ic_src.exists():
                ic_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(ic_src, ic_dst)

    variants = [
        {"idx": 0, "slug": "citrine_800",   "label": "Citrine Splat Cloud [800 Splats]",   "ply": "Example1.ply", "splats": 800,  "tex_idx": 0},
        {"idx": 1, "slug": "citrine_1500",  "label": "Citrine Splat Cloud [1500 Splats]",  "ply": "Example1.ply", "splats": 1500, "tex_idx": 0},
        {"idx": 2, "slug": "citrine_2400",  "label": "Citrine Splat Cloud [2400 Splats]",  "ply": "Example1.ply", "splats": 2400, "tex_idx": 0},
        {"idx": 3, "slug": "amethyst_800",  "label": "Amethyst Splat Cloud [800 Splats]",  "ply": "Example2.ply", "splats": 800,  "tex_idx": 1},
        {"idx": 4, "slug": "amethyst_1500", "label": "Amethyst Splat Cloud [1500 Splats]", "ply": "Example2.ply", "splats": 1500, "tex_idx": 1},
        {"idx": 5, "slug": "amethyst_2400", "label": "Amethyst Splat Cloud [2400 Splats]", "ply": "Example2.ply", "splats": 2400, "tex_idx": 1},
    ]

    for v in variants:
        i = v["idx"]
        scene = scenes[v["ply"]]
        geo_out = rp_dir / "models" / "entity" / f"splat_cloud_{i}.geo.json"
        tex_out = rp_dir / "textures" / "entity" / f"splat_cloud_{i}.png"
        export_geo_splat(
            scene,
            output_geo=geo_out,
            output_tex=tex_out,
            geometry_id=f"geometry.splat_cloud_{i}",
            max_splats=v["splats"],
            target_size=24.0,
            tile_res=512,
        )
        icon_img = scene.render_view(yaw_deg=-15.0, pitch_deg=18.0, tile_res=128, fill_ratio=0.84)
        icon_out = rp_dir / "textures" / "items" / f"splat_gem_{i}.png"
        icon_out.parent.mkdir(parents=True, exist_ok=True)
        icon_img.save(icon_out, optimize=True)

    # Ensure SplatData.js is generated in 03_Full_GS_Pack
    if not (bp_dir / "scripts" / "SplatData.js").exists():
        export_oriented_bedrock(
            repo_root / "Example2.ply",
            bp_dir / "scripts" / "SplatData.js",
            max_particles=5000,
            target_size=5.0,
        )

    # Manifests
    write_json(
        bp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "03 Full Gaussian Splat Pack BP",
                "description": "Tier 3: Full Oriented 3D Gaussian Splat Cloud entities (800, 1500, 2400 splats) & Particle Streamer",
                "uuid": "c204272a-f9df-4bb0-bd6a-d42d790854f6",
                "version": [1, 2, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "Entity definitions for full splat clouds",
                    "type": "data",
                    "uuid": "6ef19c0a-4a51-4e78-9d8c-3b27821b9d14",
                    "version": [1, 2, 0],
                },
                {
                    "description": "Script resources for oriented splat streaming & spawning",
                    "type": "script",
                    "language": "javascript",
                    "uuid": "280f1f87-c5a5-4756-b33c-0976e55d0ad3",
                    "version": [1, 2, 0],
                    "entry": "scripts/main.js",
                },
            ],
            "dependencies": [
                {"module_name": "@minecraft/server", "version": "1.14.0"},
                {"uuid": "78ec6229-ce0d-4d97-9bf3-8fe3584a86ea", "version": [1, 2, 0]},
            ],
        },
    )

    write_json(
        rp_dir / "manifest.json",
        {
            "format_version": 2,
            "header": {
                "name": "03 Full Gaussian Splat Pack RP",
                "description": "Tier 3: Full Oriented 3D Gaussian Splat Cloud visuals and double-sided particles",
                "uuid": "78ec6229-ce0d-4d97-9bf3-8fe3584a86ea",
                "version": [1, 2, 0],
                "min_engine_version": [1, 21, 0],
            },
            "modules": [
                {
                    "description": "Custom splat particles, textures, and geometries",
                    "type": "resources",
                    "uuid": "ebdde273-3e6a-4dc4-9337-2698f9767496",
                    "version": [1, 2, 0],
                }
            ],
            "dependencies": [
                {"uuid": "c204272a-f9df-4bb0-bd6a-d42d790854f6", "version": [1, 2, 0]}
            ],
        },
    )

    # Behavior Entity (`SplatRenderer_BP/entities/splat_cloud.json`)
    component_groups = {}
    events = {"minecraft:entity_spawned": {"add": {"component_groups": ["splat:variant_1"]}}}
    for v in variants:
        i = v["idx"]
        component_groups[f"splat:variant_{i}"] = {"minecraft:variant": {"value": i}}
        events[f"splat:set_variant_{i}"] = {
            "add": {"component_groups": [f"splat:variant_{i}"]},
            "remove": {"component_groups": [f"splat:variant_{j}" for j in range(len(variants)) if j != i]},
        }

    write_json(
        bp_dir / "entities" / "splat_cloud.json",
        {
            "format_version": "1.20.0",
            "minecraft:entity": {
                "description": {
                    "identifier": "custom:splat_cloud",
                    "is_spawnable": True,
                    "is_summonable": True,
                    "is_experimental": False,
                },
                "component_groups": component_groups,
                "components": {
                    "minecraft:variant": {"value": 1},
                    "minecraft:physics": {"has_gravity": False, "has_collision": True},
                    "minecraft:collision_box": {"width": 0.9, "height": 1.2},
                    "minecraft:pushable": {"is_pushable": False, "is_pushable_by_piston": True},
                    "minecraft:knockback_resistance": {"value": 1.0},
                    "minecraft:damage_sensor": {"triggers": {"cause": "all", "deals_damage": False}},
                    "minecraft:nameable": {"always_show": False, "allow_name_tag_renaming": True},
                },
                "events": events,
            },
        },
    )

    # Items
    for v in variants:
        i = v["idx"]
        write_json(
            bp_dir / "items" / f"splat_gem_item_{i}.json",
            {
                "format_version": "1.20.50",
                "minecraft:item": {
                    "description": {"identifier": f"custom:splat_gem_item_{i}", "menu_category": {"category": "items"}},
                    "components": {
                        "minecraft:max_stack_size": 16,
                        "minecraft:icon": {"texture": f"splat_gem_{i}"},
                        "minecraft:display_name": {"value": f"✦ {v['label']}"},
                    },
                },
            },
        )

    # Script
    (bp_dir / "scripts" / "main.js").write_text(
        generate_pack_script_js(
            pack_title="03_Full_GS_Pack (Full Gaussian Splat Renderer)",
            pack_subtitle="Full Oriented Splat Cloud (800, 1500, 2400 Splats) + Particle Stream",
            cmd_prefix="splat",
            entity_id="custom:splat_cloud",
            item_prefix="custom:splat_gem_item_",
            variants=variants,
            include_sample_runner=True,
        ),
        encoding="utf-8",
    )

    # Render Controller & Client Entity
    write_json(
        rp_dir / "render_controllers" / "splat_cloud.render_controllers.json",
        {
            "format_version": "1.8.0",
            "render_controllers": {
                "controller.render.splat_cloud": {
                    "arrays": {
                        "geometries": {"Array.gem_geos": [f"Geometry.gem_{i}" for i in range(len(variants))]},
                        "textures": {"Array.gem_texs": [f"Texture.gem_{i}" for i in range(len(variants))]},
                    },
                    "geometry": "Array.gem_geos[query.variant]",
                    "materials": [
                        {"*": "Material.default"},
                        {"core_*": "Material.core"},
                    ],
                    "textures": ["Array.gem_texs[query.variant]"],
                },
                "controller.render.splat_cloud_attachable": {
                    "geometry": "Geometry.default",
                    "materials": [
                        {"*": "Material.default"},
                        {"core_*": "Material.core"},
                    ],
                    "textures": ["Texture.default"],
                },
            },
        },
    )

    tex_map = {"default": "textures/entity/splat_cloud_1"}
    geo_map = {"default": "geometry.splat_cloud_1"}
    for v in variants:
        i = v["idx"]
        tex_map[f"gem_{i}"] = f"textures/entity/splat_cloud_{i}"
        geo_map[f"gem_{i}"] = f"geometry.splat_cloud_{i}"

    write_json(
        rp_dir / "entity" / "splat_cloud.entity.json",
        {
            "format_version": "1.10.0",
            "minecraft:client_entity": {
                "description": {
                    "identifier": "custom:splat_cloud",
                    "materials": {"default": "splat_cloud_blend", "core": "splat_cloud_core"},
                    "textures": tex_map,
                    "geometry": geo_map,
                    "render_controllers": ["controller.render.splat_cloud"],
                }
            },
        },
    )

    write_json(
        rp_dir / "animations" / "splat_cloud.animation.json",
        {
            "format_version": "1.8.0",
            "animations": {
                "animation.splat_cloud.held_and_dropped": {
                    "loop": True,
                    "bones": {"root": {"scale": 0.42, "position": [0, 3, 0]}},
                }
            },
        },
    )

    for v in variants:
        i = v["idx"]
        write_json(
            rp_dir / "attachables" / f"splat_gem_item_{i}.attachable.json",
            {
                "format_version": "1.10.0",
                "minecraft:attachable": {
                    "description": {
                        "identifier": f"custom:splat_gem_item_{i}",
                        "materials": {
                            "default": "splat_cloud_blend",
                            "core": "splat_cloud_core",
                            "enchanted": "splat_cloud_core",
                        },
                        "textures": {
                            "default": f"textures/entity/splat_cloud_{i}",
                            "enchanted": "textures/misc/enchanted_item_glint",
                        },
                        "geometry": {"default": f"geometry.splat_cloud_{i}"},
                        "animations": {"held_pose": "animation.splat_cloud.held_and_dropped"},
                        "scripts": {"animate": ["held_pose"]},
                        "render_controllers": ["controller.render.splat_cloud_attachable"],
                    }
                },
            },
        )

    item_tex_data = {f"splat_gem_{v['idx']}": {"textures": f"textures/items/splat_gem_{v['idx']}"} for v in variants}
    write_json(
        rp_dir / "textures" / "item_texture.json",
        {
            "resource_pack_name": "03 Full Gaussian Splat Pack RP",
            "texture_name": "atlas.items",
            "texture_data": item_tex_data,
        },
    )

    lang_lines = ["entity.custom:splat_cloud.name=Full Gaussian Splat Cloud Gem"]
    for v in variants:
        lang_lines.append(f"item.custom:splat_gem_item_{v['idx']}.name=✦ {v['label']}")
    (rp_dir / "texts").mkdir(parents=True, exist_ok=True)
    (rp_dir / "texts" / "en_US.lang").write_text("\n".join(lang_lines) + "\n", encoding="utf-8")
    write_install_cmd(pack_dir, "SplatRenderer_BP", "SplatRenderer_RP", "03 Full Gaussian Splat Pack")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the 3-Tier Bedrock 3DGS Gem Packs.")
    parser.add_argument(
        "--pack",
        choices=["all", "01", "02", "03", "imposter", "polymesh", "splat"],
        default="all",
        help="Which pack to build (default: all)",
    )
    args = parser.parse_args()

    t0 = time.time()
    repo_root = Path(__file__).resolve().parent.parent
    scenes = {
        "Example1.ply": GaussianScene(repo_root / "Example1.ply"),
        "Example2.ply": GaussianScene(repo_root / "Example2.ply"),
    }

    if args.pack in ("all", "01", "imposter"):
        build_pack_01_imposter(repo_root, scenes)
    if args.pack in ("all", "02", "polymesh"):
        build_pack_02_polymesh(repo_root, scenes)
    if args.pack in ("all", "03", "splat"):
        build_pack_03_full_gs(repo_root, scenes)

    # Remove old `02_Oriented_Splat_Pack` directory if `02_Poly_Mesh_Pack` and `03_Full_GS_Pack` exist
    old_02 = repo_root / "bedrock" / "02_Oriented_Splat_Pack"
    if (
        (repo_root / "bedrock" / "02_Poly_Mesh_Pack").exists()
        and (repo_root / "bedrock" / "03_Full_GS_Pack").exists()
        and old_02.exists()
    ):
        shutil.rmtree(old_02)
        print("✓ Replaced old 02_Oriented_Splat_Pack with 02_Poly_Mesh_Pack and 03_Full_GS_Pack")

    print(f"\n✓ Finished building all requested Bedrock packs in {time.time() - t0:.2f}s!")


if __name__ == "__main__":
    main()
