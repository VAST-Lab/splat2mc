#!/usr/bin/env python3
"""
Shared helpers for building Bedrock Edition 3DGS Gem Add-On packs:
- Source `.ply` discovery and human-readable naming
- Behavior/Resource JSON writers, catalog item definitions, and `install.cmd`
- Stale asset cleanup
- Shared `@minecraft/server` + `@minecraft/server-ui` `main.js` script generator
"""
import json
import re
import shutil
from pathlib import Path


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_install_cmd(
    pack_dir: Path,
    bp_name: str,
    rp_name: str,
    title: str,
    overwrite: bool = False,
) -> None:
    install_path = pack_dir / "install.cmd"
    if not overwrite and install_path.exists():
        return
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
    install_path.write_text(content, encoding="utf-8")


def format_ply_display_name(ply_path: Path) -> tuple[str, str]:
    """Convert a PLY filename like `IronStoneQuartz.ply` into (`Iron Stone Quartz`, `iron_stone_quartz`)."""
    stem = ply_path.stem.replace("_", " ").replace("-", " ")
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[a-zA-Z])(?=[0-9])", " ", stem)
    label = " ".join(spaced.split())
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")
    return label, slug


def discover_source_plys(source_dir: Path, fallback_files: list[Path] | None = None) -> list[Path]:
    """Return sorted `.ply` files in `source_dir`, or `fallback_files` if `source_dir` has none."""
    if source_dir.exists():
        found = sorted(source_dir.glob("*.ply"), key=lambda p: p.name.lower())
        if found:
            return found
    if fallback_files:
        existing = [p for p in fallback_files if p.exists()]
        if existing:
            return existing
    raise FileNotFoundError(f"No .ply files found in {source_dir}")


def cleanup_stale_files(directory: Path, pattern: str, keep_names: set[str]) -> int:
    """Delete files matching `pattern` in `directory` whose names are not in `keep_names`."""
    if not directory.exists():
        return 0
    removed = 0
    for path in directory.glob(pattern):
        if path.is_file() and path.name not in keep_names:
            path.unlink()
            removed += 1
    return removed


def reset_pack_directory(
    pack_dir: Path,
    preserve_relpaths: list[str] | None = None,
    preserve_scripts: bool = True,
) -> None:
    """
    Clear out an existing pack directory completely and recreate it, preserving
    explicit support files (e.g. `pack_icon.png`, particle assets) and any existing
    install/helper scripts (`*.cmd`, `*.bat`, `*.ps1`, `*.sh`).
    """
    preserved: dict[str, bytes] = {}
    if pack_dir.exists():
        if preserve_relpaths:
            for rel in preserve_relpaths:
                src = pack_dir / rel
                if src.is_file():
                    preserved[rel] = src.read_bytes()
        if preserve_scripts:
            for ext in ("*.cmd", "*.bat", "*.ps1", "*.sh"):
                for src in pack_dir.rglob(ext):
                    if src.is_file():
                        rel = src.relative_to(pack_dir).as_posix()
                        preserved.setdefault(rel, src.read_bytes())

    if pack_dir.exists():
        shutil.rmtree(pack_dir)
    pack_dir.mkdir(parents=True, exist_ok=True)

    for rel, data in preserved.items():
        dst = pack_dir / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)


def resolve_tier_selection(tiers: list[dict], selection: str, default_keys: list[str]) -> list[dict]:
    """Parse a comma-separated tier selection (indices, keys, or `all`) into matching tier dicts."""
    raw = selection.strip().lower()
    if not raw:
        chosen_keys = set(default_keys)
    elif raw == "all":
        return list(tiers)
    else:
        chosen_keys = set()
        by_num = {str(i + 1): t["key"] for i, t in enumerate(tiers)}
        by_key = {t["key"].lower(): t["key"] for t in tiers}
        for token in re.split(r"[\s,]+", raw):
            if not token:
                continue
            if token in by_num:
                chosen_keys.add(by_num[token])
            elif token in by_key:
                chosen_keys.add(by_key[token])
            else:
                raise ValueError(f"Unknown quality option '{token}'")
    selected = [t for t in tiers if t["key"] in chosen_keys]
    if not selected:
        raise ValueError("No valid quality tiers selected")
    return selected


def prompt_quality_tiers(pack_name: str, tiers: list[dict], default_keys: list[str]) -> list[dict]:
    """Interactively prompt the user for which quality level(s) to include in `pack_name`."""
    default_str = ",".join(default_keys)
    print(f"\nSelect quality level(s) for {pack_name}:")
    for idx, t in enumerate(tiers, start=1):
        desc = t.get("description") or t.get("label") or t["key"]
        default_tag = "  [Default]" if t["key"] in default_keys else ""
        print(f"  [{idx}] {t['key']:<8} - {desc}{default_tag}")
    print("  [all]      - Include all quality levels")

    while True:
        try:
            ans = input(f"Enter choice(s) (e.g. 2, {default_str}, 1,2, all) [Default: {default_str}]: ")
        except EOFError:
            ans = ""
        try:
            selected = resolve_tier_selection(tiers, ans, default_keys)
            print(f"  -> Selected: {[t['key'] for t in selected]}")
            return selected
        except ValueError as exc:
            print(f"  ! {exc}. Please try again.")


def write_catalog_item_json(bp_dir: Path, slug: str, icon_texture: str, display_name: str) -> None:
    """Write the clickable Gem Catalog item definition that opens the native Bedrock UI menu."""
    write_json(
        bp_dir / "items" / f"{slug}.json",
        {
            "format_version": "1.20.50",
            "minecraft:item": {
                "description": {
                    "identifier": f"custom:{slug}",
                    "menu_category": {"category": "items"},
                },
                "components": {
                    "minecraft:max_stack_size": 1,
                    "minecraft:glint": True,
                    "minecraft:cooldown": {
                        "category": slug,
                        "duration": 0.25,
                    },
                    "minecraft:icon": {"texture": icon_texture},
                    "minecraft:display_name": {"value": f"✦ {display_name}"},
                },
            },
        },
    )


def write_variant_entity_behavior_json(
    out_path: Path,
    identifier: str,
    variant_count: int,
    default_variant: int = 0,
    collision_width: float = 0.9,
    collision_height: float = 1.2,
) -> None:
    """Write a Bedrock BP entity definition supporting `variant_count` variant events."""
    default_variant = max(0, min(variant_count - 1, default_variant))
    component_groups = {}
    events = {
        "minecraft:entity_spawned": {"add": {"component_groups": [f"splat:variant_{default_variant}"]}}
    }
    for i in range(variant_count):
        component_groups[f"splat:variant_{i}"] = {"minecraft:variant": {"value": i}}
        events[f"splat:set_variant_{i}"] = {
            "add": {"component_groups": [f"splat:variant_{i}"]},
            "remove": {"component_groups": [f"splat:variant_{j}" for j in range(variant_count) if j != i]},
        }

    write_json(
        out_path,
        {
            "format_version": "1.20.0",
            "minecraft:entity": {
                "description": {
                    "identifier": identifier,
                    "is_spawnable": True,
                    "is_summonable": True,
                    "is_experimental": False,
                },
                "component_groups": component_groups,
                "components": {
                    "minecraft:variant": {"value": default_variant},
                    "minecraft:physics": {"has_gravity": False, "has_collision": True},
                    "minecraft:collision_box": {"width": collision_width, "height": collision_height},
                    "minecraft:pushable": {"is_pushable": False, "is_pushable_by_piston": True},
                    "minecraft:knockback_resistance": {"value": 1.0},
                    "minecraft:damage_sensor": {"triggers": {"cause": "all", "deals_damage": False}},
                    "minecraft:nameable": {"always_show": False, "allow_name_tag_renaming": True},
                },
                "events": events,
            },
        },
    )


def generate_pack_script_js(
    pack_title: str,
    pack_subtitle: str,
    cmd_prefix: str,
    entity_id: str,
    item_prefix: str,
    icon_prefix: str,
    menu_item_id: str,
    catalog_name: str,
    variants: list[dict],
    include_sample_runner: bool = False,
) -> str:
    """
    Generate the Bedrock `@minecraft/server` + `@minecraft/server-ui` script for a pack:
    - Compact 1-line startup notice & automatic Gem Catalog item on world join / reload
    - Native Bedrock `ActionFormData` button menu with gem icons (via Catalog item or `:menu`)
    - Single-gem spawning via `/scriptevent <prefix>:spawn <0..N-1|slug>`
    - Item giving via `/scriptevent <prefix>:items`
    - Clearing via `/scriptevent <prefix>:clear`
    - Crouch + Interact to pick up gem into item; Interact on block to place gem down
    """
    gem_names = [v["label"] for v in variants]
    slug_map = {v["slug"]: v["idx"] for v in variants}
    for v in variants:
        slug_map[str(v["idx"])] = v["idx"]
    primary_indices = [v["idx"] for v in variants if v.get("is_primary")] or [v["idx"] for v in variants]
    primary_icon_idx = primary_indices[0] if primary_indices else 0

    give_all_label = (
        f"§a✦ Give All {len(primary_indices)} Recommended Gems"
        if len(primary_indices) < len(variants)
        else f"§a✦ Give All {len(variants)} Gems"
    )

    sample_imports = (
        'import SampleManager from "./SampleManager.js";\nimport { scriptBox } from "./ScriptBox.js";\n\n'
        "const sm = new SampleManager();\nsm.registerSamples({ scriptBox: [scriptBox] });\n"
        if include_sample_runner
        else ""
    )
    extra_help_line = (
        '	player.sendMessage("  §e/scriptevent sample:run §7- Stream 5,000 oriented splat particles at looked-at block");\n'
        if include_sample_runner
        else ""
    )
    sample_helper = (
        """
function toggleParticleStream(player) {
	const hit = player.getBlockFromViewDirection();
	let targetLoc;
	if (hit?.block) {
		const loc = hit.block.location;
		targetLoc = { x: loc.x, y: loc.y + 1, z: loc.z, dimension: player.dimension };
	} else {
		const view = player.getViewDirection();
		const horizLen = Math.hypot(view.x, view.z) || 1.0;
		targetLoc = {
			x: Math.floor(player.location.x + (view.x / horizLen) * 3.0),
			y: Math.floor(player.location.y),
			z: Math.floor(player.location.z + (view.z / horizLen) * 3.0),
			dimension: player.dimension
		};
	}
	scriptBox((msg) => {
		player.onScreenDisplay.setActionBar(`§e${msg}`);
	}, targetLoc);
}
"""
        if include_sample_runner
        else ""
    )
    sample_menu_btn = (
        '	form.button("§e⚡ Toggle Particle Stream (5k Splats)");\n'
        if include_sample_runner
        else ""
    )
    sample_menu_case = (
        """	} else if (sel === GEM_NAMES.length + 2) {
		toggleParticleStream(player);
"""
        if include_sample_runner
        else ""
    )

    return f"""{sample_imports}import {{ world, system, ItemStack }} from "@minecraft/server";
import {{ ActionFormData, FormCancelationReason }} from "@minecraft/server-ui";

const PACK_TITLE = {json.dumps(pack_title)};
const PACK_SUBTITLE = {json.dumps(pack_subtitle)};
const CMD_PREFIX = {json.dumps(cmd_prefix)};
const ENTITY_ID = {json.dumps(entity_id)};
const ITEM_PREFIX = {json.dumps(item_prefix)};
const ICON_PREFIX = {json.dumps(icon_prefix)};
const MENU_ITEM_ID = {json.dumps(menu_item_id)};
const CATALOG_NAME = {json.dumps(catalog_name)};
const GEM_NAMES = {json.dumps(gem_names)};
const PRIMARY_INDICES = {json.dumps(primary_indices)};
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

function createGemItem(variant) {{
	const st = new ItemStack(`${{ITEM_PREFIX}}${{variant}}`, 1);
	st.nameTag = `§r§d✦ ${{GEM_NAMES[variant]}}`;
	st.setLore([
		`§7${{PACK_TITLE}}`,
		"§eInteract on any block to place",
		"§8Crouch + Interact to pick up"
	]);
	return st;
}}

function addOrDropItem(player, itemStack, dropLocation = player.location) {{
	const inv = player.getComponent("minecraft:inventory")?.container;
	if (inv) {{
		const leftover = inv.addItem(itemStack);
		if (leftover) {{
			player.dimension.spawnItem(leftover, dropLocation);
		}}
	}} else {{
		player.dimension.spawnItem(itemStack, dropLocation);
	}}
}}

function hasItem(container, typeId) {{
	for (let i = 0; i < container.size; i++) {{
		const item = container.getItem(i);
		if (item && item.typeId === typeId) return true;
	}}
	return false;
}}

function ensureCatalogItem(player) {{
	const inv = player.getComponent("minecraft:inventory")?.container;
	if (!inv || hasItem(inv, MENU_ITEM_ID)) return false;
	const menuStack = new ItemStack(MENU_ITEM_ID, 1);
	menuStack.nameTag = `§r§b✦ ${{CATALOG_NAME}}`;
	menuStack.setLore([
		`§7${{PACK_TITLE}}`,
		"§eRight-Click / Use to open Gem Menu"
	]);
	inv.addItem(menuStack);
	return true;
}}

function giveGemItem(player, variant) {{
	addOrDropItem(player, createGemItem(variant));
	player.dimension.playSound("random.pop", player.location, {{ volume: 0.9, pitch: 1.25 }});
	player.onScreenDisplay.setActionBar(`§aAdded §f${{GEM_NAMES[variant]}} §ato inventory`);
}}

function giveGemBatch(player, indices) {{
	for (const idx of indices) {{
		addOrDropItem(player, createGemItem(idx));
	}}
	player.dimension.playSound("random.levelup", player.location, {{ volume: 0.6, pitch: 1.4 }});
	player.onScreenDisplay.setActionBar(`§aAdded ${{indices.length}} gems to inventory!`);
}}

function clearSpawnedGems(player) {{
	const ents = player.dimension.getEntities({{ type: ENTITY_ID }});
	for (const e of ents) e.remove();
	player.dimension.playSound("random.fizz", player.location, {{ volume: 0.5, pitch: 1.2 }});
	player.onScreenDisplay.setActionBar(`§eCleared ${{ents.length}} placed gem(s).`);
}}
{sample_helper}
async function showFormWhenReady(player, form, attempts = 15) {{
	for (let i = 0; i < attempts; i++) {{
		if (!player.isValid()) return null;
		const res = await form.show(player);
		if (!res.canceled || res.cancelationReason !== FormCancelationReason.UserBusy) {{
			return res;
		}}
		await system.waitTicks(4);
	}}
	return null;
}}

export async function openGemMenu(player) {{
	const form = new ActionFormData()
		.title(`✦ ${{CATALOG_NAME}}`)
		.body("§7Select a gem to add to your inventory.\\n§8Place: Interact block | Pick up: Crouch + Interact");

	form.button({json.dumps(give_all_label)}, `${{ICON_PREFIX}}{primary_icon_idx}`);
	for (let i = 0; i < GEM_NAMES.length; i++) {{
		form.button(GEM_NAMES[i], `${{ICON_PREFIX}}${{i}}`);
	}}
	form.button("§c✕ Clear Placed Gems");
{sample_menu_btn}
	const res = await showFormWhenReady(player, form);
	if (!res || res.canceled || res.selection === undefined) return;

	const sel = res.selection;
	if (sel === 0) {{
		giveGemBatch(player, PRIMARY_INDICES);
	}} else if (sel >= 1 && sel <= GEM_NAMES.length) {{
		giveGemItem(player, sel - 1);
	}} else if (sel === GEM_NAMES.length + 1) {{
		clearSpawnedGems(player);
{sample_menu_case}	}}
}}

export function initPlayer(player) {{
	ensureCatalogItem(player);
	player.sendMessage(
		`§b✦ [${{PACK_TITLE}}] §7Right-click §e${{CATALOG_NAME}} §7(or §f/scriptevent ${{CMD_PREFIX}}:menu§7) to pick gems. §8(/scriptevent ${{CMD_PREFIX}}:help)`
	);
}}

export function printPackHelp(player) {{
	player.sendMessage(`§b§l[${{PACK_TITLE}}] §r§7- ${{PACK_SUBTITLE}}`);
	player.sendMessage(`  §e/scriptevent ${{CMD_PREFIX}}:menu §7- Open Gem Picker UI | §e:items §7- Give primary gems`);
	player.sendMessage(`  §a/scriptevent ${{CMD_PREFIX}}:spawn <0-${{GEM_NAMES.length - 1}}> §7- Spawn in front | §e:clear §7- Clear`);
{extra_help_line}	player.sendMessage("§dControls: §fInteract Block §7to Place | §fCrouch + Interact §7to Pick Up");
}}

// Compact startup notice + Catalog item when player spawns (or after /reload)
world.afterEvents.playerSpawn.subscribe((event) => {{
	const player = event.player;
	if (!player || greetedPlayers.has(player.id)) return;
	greetedPlayers.add(player.id);
	system.runTimeout(() => {{
		if (player.isValid()) {{
			initPlayer(player);
		}}
	}}, 40);
}});

system.runTimeout(() => {{
	for (const player of world.getAllPlayers()) {{
		if (!greetedPlayers.has(player.id)) {{
			greetedPlayers.add(player.id);
			initPlayer(player);
		}}
	}}
}}, 60);

// 0. CATALOG MENU TRIGGER: Right-Click / Use Catalog item in air
world.beforeEvents.itemUse.subscribe((event) => {{
	const {{ source: player, itemStack }} = event;
	if (!itemStack || itemStack.typeId !== MENU_ITEM_ID) return;
	event.cancel = true;
	if (!canInteract(player)) return;
	system.run(() => {{
		openGemMenu(player);
	}});
}});

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
	const gemName = GEM_NAMES[clampedVariant];
	const loc = target.location;
	const dim = target.dimension;

	system.run(() => {{
		if (!target.isValid()) return;
		target.remove();
		addOrDropItem(player, createGemItem(clampedVariant), loc);
		dim.playSound("random.pop", loc, {{ volume: 0.9, pitch: 1.25 }});
		player.onScreenDisplay.setActionBar(`§aPicked up §f${{gemName}}`);
	}});
}});

// 2. PLACEMENT & CATALOG BLOCK-CLICK: Interact on any Block while holding Catalog or Gem Item
world.beforeEvents.playerInteractWithBlock.subscribe((event) => {{
	const {{ player, itemStack, block, blockFace }} = event;
	if (!itemStack) return;

	if (itemStack.typeId === MENU_ITEM_ID) {{
		event.cancel = true;
		if (!canInteract(player)) return;
		system.run(() => {{
			openGemMenu(player);
		}});
		return;
	}}

	if (!itemStack.typeId.startsWith(ITEM_PREFIX)) return;

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

// 3. COMMAND HANDLER: `/scriptevent <prefix>:menu`, `:spawn <0..N-1>`, `:items`, `:clear`, `:help`
system.afterEvents.scriptEventReceive.subscribe((event) => {{
	const id = event.id.toLowerCase();
	const player = event.sourceEntity;
	if (!player) return;

	const msg = (event.message ?? "").trim().toLowerCase();

	if (id === `${{CMD_PREFIX}}:menu` || id === "gem:menu") {{
		openGemMenu(player);
		return;
	}}

	if (id === `${{CMD_PREFIX}}:catalog` || id === "gem:catalog") {{
		if (ensureCatalogItem(player)) {{
			player.onScreenDisplay.setActionBar(`§bAdded §f${{CATALOG_NAME}} §bto inventory`);
		}} else {{
			openGemMenu(player);
		}}
		return;
	}}

	if (id === `${{CMD_PREFIX}}:help` || id === "gem:help") {{
		printPackHelp(player);
		return;
	}}

	if (id === `${{CMD_PREFIX}}:items` || id === "gem:items") {{
		const indices = msg === "all" ? GEM_NAMES.map((_, i) => i) : PRIMARY_INDICES;
		giveGemBatch(player, indices);
		return;
	}}

	if (id === `${{CMD_PREFIX}}:clear` || id === "gem:clear") {{
		clearSpawnedGems(player);
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
		player.onScreenDisplay.setActionBar(`§aSpawned §f${{GEM_NAMES[variant]}}`);
	}}
}});
"""
