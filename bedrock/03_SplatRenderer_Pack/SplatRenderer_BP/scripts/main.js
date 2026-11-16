import SampleManager from "./SampleManager.js";
import { scriptBox } from "./ScriptBox.js";

const sm = new SampleManager();
sm.registerSamples({ scriptBox: [scriptBox] });
import { world, system, ItemStack } from "@minecraft/server";
import { ActionFormData, FormCancelationReason } from "@minecraft/server-ui";

const PACK_TITLE = "03_Full_GS_Pack (Full Gaussian Splat Renderer)";
const PACK_SUBTITLE = "Full Oriented Splat Cloud (800, 1500, 2400 Splats) + Particle Stream";
const CMD_PREFIX = "splat";
const ENTITY_ID = "custom:splat_cloud";
const ITEM_PREFIX = "custom:splat_gem_item_";
const ICON_PREFIX = "textures/items/splat_gem_";
const MENU_ITEM_ID = "custom:splat_menu";
const CATALOG_NAME = "Splat Gem Catalog";
const GEM_NAMES = ["Citrine Splat Cloud [800 Splats]", "Citrine Splat Cloud [1500 Splats]", "Citrine Splat Cloud [2400 Splats]", "Amethyst Splat Cloud [800 Splats]", "Amethyst Splat Cloud [1500 Splats]", "Amethyst Splat Cloud [2400 Splats]"];
const SLUG_MAP = {"citrine_800": 0, "citrine_1500": 1, "citrine_2400": 2, "amethyst_800": 3, "amethyst_1500": 4, "amethyst_2400": 5, "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5};

const interactCooldown = new Map();
const greetedPlayers = new Set();

function canInteract(player) {
	const now = system.currentTick;
	const last = interactCooldown.get(player.id) ?? -10;
	if (now - last < 5) return false;
	interactCooldown.set(player.id, now);
	return true;
}

function createGemItem(variant) {
	const st = new ItemStack(`${ITEM_PREFIX}${variant}`, 1);
	st.nameTag = `§r§d✦ ${GEM_NAMES[variant]}`;
	st.setLore([
		`§7${PACK_TITLE}`,
		"§eInteract on any block to place",
		"§8Crouch + Interact to pick up"
	]);
	return st;
}

function addOrDropItem(player, itemStack, dropLocation = player.location) {
	const inv = player.getComponent("minecraft:inventory")?.container;
	if (inv) {
		const leftover = inv.addItem(itemStack);
		if (leftover) {
			player.dimension.spawnItem(leftover, dropLocation);
		}
	} else {
		player.dimension.spawnItem(itemStack, dropLocation);
	}
}

function hasItem(container, typeId) {
	for (let i = 0; i < container.size; i++) {
		const item = container.getItem(i);
		if (item && item.typeId === typeId) return true;
	}
	return false;
}

function ensureCatalogItem(player) {
	const inv = player.getComponent("minecraft:inventory")?.container;
	if (!inv || hasItem(inv, MENU_ITEM_ID)) return false;
	const menuStack = new ItemStack(MENU_ITEM_ID, 1);
	menuStack.nameTag = `§r§b✦ ${CATALOG_NAME}`;
	menuStack.setLore([
		`§7${PACK_TITLE}`,
		"§eRight-Click / Use to open Gem Menu"
	]);
	inv.addItem(menuStack);
	return true;
}

function giveGemItem(player, variant) {
	addOrDropItem(player, createGemItem(variant));
	player.dimension.playSound("random.pop", player.location, { volume: 0.9, pitch: 1.25 });
	player.onScreenDisplay.setActionBar(`§aAdded §f${GEM_NAMES[variant]} §ato inventory`);
}

function giveAllGems(player) {
	for (let i = 0; i < GEM_NAMES.length; i++) {
		addOrDropItem(player, createGemItem(i));
	}
	player.dimension.playSound("random.levelup", player.location, { volume: 0.6, pitch: 1.4 });
	player.onScreenDisplay.setActionBar(`§aAdded all ${GEM_NAMES.length} gems to inventory!`);
}

function clearSpawnedGems(player) {
	const ents = player.dimension.getEntities({ type: ENTITY_ID });
	for (const e of ents) e.remove();
	player.dimension.playSound("random.fizz", player.location, { volume: 0.5, pitch: 1.2 });
	player.onScreenDisplay.setActionBar(`§eCleared ${ents.length} placed gem(s).`);
}

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

async function showFormWhenReady(player, form, attempts = 15) {
	for (let i = 0; i < attempts; i++) {
		if (!player.isValid()) return null;
		const res = await form.show(player);
		if (!res.canceled || res.cancelationReason !== FormCancelationReason.UserBusy) {
			return res;
		}
		await system.waitTicks(4);
	}
	return null;
}

export async function openGemMenu(player) {
	const form = new ActionFormData()
		.title(`✦ ${CATALOG_NAME}`)
		.body("§7Select a gem to add to your inventory.\n§8Place: Interact block | Pick up: Crouch + Interact");

	form.button(`§a✦ Give All ${GEM_NAMES.length} Gems`, `${ICON_PREFIX}1`);
	for (let i = 0; i < GEM_NAMES.length; i++) {
		form.button(GEM_NAMES[i], `${ICON_PREFIX}${i}`);
	}
	form.button("§c✕ Clear Placed Gems");
	form.button("§e⚡ Toggle Particle Stream (5k Splats)");

	const res = await showFormWhenReady(player, form);
	if (!res || res.canceled || res.selection === undefined) return;

	const sel = res.selection;
	if (sel === 0) {
		giveAllGems(player);
	} else if (sel >= 1 && sel <= GEM_NAMES.length) {
		giveGemItem(player, sel - 1);
	} else if (sel === GEM_NAMES.length + 1) {
		clearSpawnedGems(player);
	} else if (sel === GEM_NAMES.length + 2) {
		toggleParticleStream(player);
	}
}

export function initPlayer(player) {
	ensureCatalogItem(player);
	player.sendMessage(
		`§b✦ [${PACK_TITLE}] §7Right-click §e${CATALOG_NAME} §7(or §f/scriptevent ${CMD_PREFIX}:menu§7) to pick gems. §8(/scriptevent ${CMD_PREFIX}:help)`
	);
}

export function printPackHelp(player) {
	player.sendMessage(`§b§l[${PACK_TITLE}] §r§7- ${PACK_SUBTITLE}`);
	player.sendMessage(`  §e/scriptevent ${CMD_PREFIX}:menu §7- Open Gem Picker UI | §e:items §7- Give all 6`);
	player.sendMessage(`  §a/scriptevent ${CMD_PREFIX}:spawn <0-${GEM_NAMES.length - 1}> §7- Spawn in front | §e:clear §7- Clear`);
	player.sendMessage("  §e/scriptevent sample:run §7- Stream 5,000 oriented splat particles at looked-at block");
	player.sendMessage("§dControls: §fInteract Block §7to Place | §fCrouch + Interact §7to Pick Up");
}

// Compact startup notice + Catalog item when player spawns (or after /reload)
world.afterEvents.playerSpawn.subscribe((event) => {
	const player = event.player;
	if (!player || greetedPlayers.has(player.id)) return;
	greetedPlayers.add(player.id);
	system.runTimeout(() => {
		if (player.isValid()) {
			initPlayer(player);
		}
	}, 40);
});

system.runTimeout(() => {
	for (const player of world.getAllPlayers()) {
		if (!greetedPlayers.has(player.id)) {
			greetedPlayers.add(player.id);
			initPlayer(player);
		}
	}
}, 60);

// 0. CATALOG MENU TRIGGER: Right-Click / Use Catalog item in air
world.beforeEvents.itemUse.subscribe((event) => {
	const { source: player, itemStack } = event;
	if (!itemStack || itemStack.typeId !== MENU_ITEM_ID) return;
	event.cancel = true;
	if (!canInteract(player)) return;
	system.run(() => {
		openGemMenu(player);
	});
});

// 1. PICKUP MECHANIC: Crouch + Interact (Sneak + Right-Click/Tap) on a Gem Entity
world.beforeEvents.playerInteractWithEntity.subscribe((event) => {
	const { player, target } = event;
	if (!target || target.typeId !== ENTITY_ID) return;

	if (!player.isSneaking) {
		if (canInteract(player)) {
			const variant = target.getComponent("minecraft:variant")?.value ?? 0;
			const name = GEM_NAMES[variant] ?? GEM_NAMES[0];
			system.run(() => {
				player.onScreenDisplay.setActionBar(`§b✦ ${name} §7(Crouch + Interact to pick up)`);
			});
		}
		return;
	}

	if (!canInteract(player)) return;
	event.cancel = true;

	const variant = target.getComponent("minecraft:variant")?.value ?? 0;
	const clampedVariant = Math.max(0, Math.min(GEM_NAMES.length - 1, variant));
	const gemName = GEM_NAMES[clampedVariant];
	const loc = target.location;
	const dim = target.dimension;

	system.run(() => {
		if (!target.isValid()) return;
		target.remove();
		addOrDropItem(player, createGemItem(clampedVariant), loc);
		dim.playSound("random.pop", loc, { volume: 0.9, pitch: 1.25 });
		player.onScreenDisplay.setActionBar(`§aPicked up §f${gemName}`);
	});
});

// 2. PLACEMENT & CATALOG BLOCK-CLICK: Interact on any Block while holding Catalog or Gem Item
world.beforeEvents.playerInteractWithBlock.subscribe((event) => {
	const { player, itemStack, block, blockFace } = event;
	if (!itemStack) return;

	if (itemStack.typeId === MENU_ITEM_ID) {
		event.cancel = true;
		if (!canInteract(player)) return;
		system.run(() => {
			openGemMenu(player);
		});
		return;
	}

	if (!itemStack.typeId.startsWith(ITEM_PREFIX)) return;

	const suffix = itemStack.typeId.slice(ITEM_PREFIX.length);
	const variant = parseInt(suffix, 10);
	if (Number.isNaN(variant) || variant < 0 || variant >= GEM_NAMES.length) return;

	if (!canInteract(player)) return;
	event.cancel = true;

	const dim = block.dimension;
	const offsets = {
		Up: { x: 0.5, y: 1.0, z: 0.5 },
		Down: { x: 0.5, y: -1.5, z: 0.5 },
		North: { x: 0.5, y: 0.0, z: -0.5 },
		South: { x: 0.5, y: 0.0, z: 1.5 },
		West: { x: -0.5, y: 0.0, z: 0.5 },
		East: { x: 1.5, y: 0.0, z: 0.5 }
	};
	const off = offsets[blockFace] ?? offsets.Up;
	const spawnPos = {
		x: block.location.x + off.x,
		y: block.location.y + off.y,
		z: block.location.z + off.z
	};

	system.run(() => {
		const ent = dim.spawnEntity(ENTITY_ID, spawnPos);
		ent.triggerEvent(`splat:set_variant_${variant}`);
		ent.nameTag = GEM_NAMES[variant];

		dim.playSound("dig.amethyst_block", spawnPos, { volume: 1.0, pitch: 1.15 });
		player.onScreenDisplay.setActionBar(`§dPlaced §f${GEM_NAMES[variant]}`);

		const gm = player.getGameMode();
		if (gm !== "creative" && gm !== "Creative") {
			const inv = player.getComponent("minecraft:inventory")?.container;
			if (inv) {
				const slot = player.selectedSlotIndex;
				const cur = inv.getItem(slot);
				if (cur && cur.typeId === itemStack.typeId) {
					if (cur.amount > 1) {
						cur.amount -= 1;
						inv.setItem(slot, cur);
					} else {
						inv.setItem(slot, undefined);
					}
				}
			}
		}
	});
});

// 3. COMMAND HANDLER: `/scriptevent <prefix>:menu`, `:spawn <0..5>`, `:items`, `:clear`, `:help`
system.afterEvents.scriptEventReceive.subscribe((event) => {
	const id = event.id.toLowerCase();
	const player = event.sourceEntity;
	if (!player) return;

	const msg = (event.message ?? "").trim().toLowerCase();

	if (id === `${CMD_PREFIX}:menu` || id === "gem:menu") {
		openGemMenu(player);
		return;
	}

	if (id === `${CMD_PREFIX}:catalog` || id === "gem:catalog") {
		if (ensureCatalogItem(player)) {
			player.onScreenDisplay.setActionBar(`§bAdded §f${CATALOG_NAME} §bto inventory`);
		} else {
			openGemMenu(player);
		}
		return;
	}

	if (id === `${CMD_PREFIX}:help` || id === "gem:help") {
		printPackHelp(player);
		return;
	}

	if (id === `${CMD_PREFIX}:items` || id === "gem:items") {
		giveAllGems(player);
		return;
	}

	if (id === `${CMD_PREFIX}:clear` || id === "gem:clear") {
		clearSpawnedGems(player);
		return;
	}

	if (id === `${CMD_PREFIX}:spawn` || id === "gem:spawn") {
		let variant = 0;
		if (msg.length > 0) {
			if (msg in SLUG_MAP) {
				variant = SLUG_MAP[msg];
			} else {
				const parsed = parseInt(msg, 10);
				if (!Number.isNaN(parsed) && parsed >= 0 && parsed < GEM_NAMES.length) {
					variant = parsed;
				} else {
					player.sendMessage(`§cUnknown gem '${msg}'. Use 0..${GEM_NAMES.length - 1}:`);
					printPackHelp(player);
					return;
				}
			}
		}

		const dim = player.dimension;
		const view = player.getViewDirection();
		const horizLen = Math.hypot(view.x, view.z) || 1.0;
		const spawnPos = {
			x: Math.floor(player.location.x + (view.x / horizLen) * 3.0) + 0.5,
			y: Math.floor(player.location.y),
			z: Math.floor(player.location.z + (view.z / horizLen) * 3.0) + 0.5
		};

		const ent = dim.spawnEntity(ENTITY_ID, spawnPos);
		ent.triggerEvent(`splat:set_variant_${variant}`);
		ent.nameTag = GEM_NAMES[variant];

		dim.playSound("block.amethyst_block.chime", spawnPos, { volume: 1.0, pitch: 1.1 });
		player.onScreenDisplay.setActionBar(`§aSpawned §f${GEM_NAMES[variant]}`);
	}
});
