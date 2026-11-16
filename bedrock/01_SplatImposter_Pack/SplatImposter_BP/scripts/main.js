import { world, system, ItemStack } from "@minecraft/server";

const PACK_TITLE = "01_Imposter_Pack (Spherical Imposter)";
const PACK_SUBTITLE = "Angle-Pinned 3DGS Imposter (Low 12x3, Med 16x5, High 24x7)";
const CMD_PREFIX = "imposter";
const ENTITY_ID = "custom:imposter";
const ITEM_PREFIX = "custom:gem_item_";
const GEM_NAMES = ["Citrine Cluster [Low: 12x3 = 36 Angles]", "Citrine Cluster [Medium: 16x5 = 80 Angles]", "Citrine Cluster [High: 24x7 = 168 Angles]", "Amethyst Geode [Low: 12x3 = 36 Angles]", "Amethyst Geode [Medium: 16x5 = 80 Angles]", "Amethyst Geode [High: 24x7 = 168 Angles]"];
const SLUG_MAP = {"low_citrine": 0, "med_citrine": 1, "high_citrine": 2, "low_amethyst": 3, "med_amethyst": 4, "high_amethyst": 5, "0": 0, "1": 1, "2": 2, "3": 3, "4": 4, "5": 5};

const interactCooldown = new Map();
const greetedPlayers = new Set();

function canInteract(player) {
	const now = system.currentTick;
	const last = interactCooldown.get(player.id) ?? -10;
	if (now - last < 5) return false;
	interactCooldown.set(player.id, now);
	return true;
}

export function printPackHelp(player) {
	player.sendMessage("§8====================================================");
	player.sendMessage(`§b§l[${PACK_TITLE}] §r§7- ${PACK_SUBTITLE}`);
	player.sendMessage("§fSpawn one gem at a time in front of you:");
	for (let i = 0; i < GEM_NAMES.length; i++) {
		player.sendMessage(`  §a/scriptevent ${CMD_PREFIX}:spawn ${i} §7-> §f${GEM_NAMES[i]}`);
	}
	player.sendMessage(`  §e/scriptevent ${CMD_PREFIX}:items §7- Give all 6 Gem items to your inventory`);
	player.sendMessage(`  §e/scriptevent ${CMD_PREFIX}:clear §7- Remove spawned gems`);
	player.sendMessage(`  §e/scriptevent ${CMD_PREFIX}:help  §7- Show this command list again`);
	player.sendMessage("§dControls: §fCrouch + Interact §7to Pick Up | §fInteract on Block §7to Place");
	player.sendMessage("§8====================================================");
}

// Print startup banner when player spawns in the world (or after /reload)
world.afterEvents.playerSpawn.subscribe((event) => {
	const player = event.player;
	if (!player || greetedPlayers.has(player.id)) return;
	greetedPlayers.add(player.id);
	system.runTimeout(() => {
		if (player.isValid()) {
			printPackHelp(player);
		}
	}, 40);
});

// Fallback for /reload when player is already spawned in the world
system.runTimeout(() => {
	for (const player of world.getAllPlayers()) {
		if (!greetedPlayers.has(player.id)) {
			greetedPlayers.add(player.id);
			printPackHelp(player);
		}
	}
}, 60);

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
	const itemId = `${ITEM_PREFIX}${clampedVariant}`;
	const gemName = GEM_NAMES[clampedVariant];
	const loc = target.location;
	const dim = target.dimension;

	system.run(() => {
		if (!target.isValid()) return;
		target.remove();

		const itemStack = new ItemStack(itemId, 1);
		itemStack.nameTag = `§r§d✦ ${gemName}`;
		itemStack.setLore([
			`§7${PACK_TITLE}`,
			"§eInteract on any block to place",
			"§8Crouch + Interact to pick up"
		]);

		const inv = player.getComponent("minecraft:inventory")?.container;
		if (inv) {
			const leftover = inv.addItem(itemStack);
			if (leftover) {
				dim.spawnItem(leftover, loc);
			}
		} else {
			dim.spawnItem(itemStack, loc);
		}

		dim.playSound("random.pop", loc, { volume: 0.9, pitch: 1.25 });
		player.onScreenDisplay.setActionBar(`§aPicked up §f${gemName}`);
	});
});

// 2. PLACEMENT MECHANIC: Interact on any Block while holding a Gem Item
world.beforeEvents.playerInteractWithBlock.subscribe((event) => {
	const { player, itemStack, block, blockFace } = event;
	if (!itemStack || !itemStack.typeId.startsWith(ITEM_PREFIX)) return;

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

// 3. COMMAND HANDLER: `/scriptevent <prefix>:spawn <0..5>`, `:items`, `:clear`, `:help`
system.afterEvents.scriptEventReceive.subscribe((event) => {
	const id = event.id.toLowerCase();
	const player = event.sourceEntity;
	if (!player) return;

	const msg = (event.message ?? "").trim().toLowerCase();

	if (id === `${CMD_PREFIX}:help` || id === "gem:help") {
		printPackHelp(player);
		return;
	}

	if (id === `${CMD_PREFIX}:items` || id === "gem:items") {
		const inv = player.getComponent("minecraft:inventory")?.container;
		if (!inv) return;
		for (let i = 0; i < GEM_NAMES.length; i++) {
			const st = new ItemStack(`${ITEM_PREFIX}${i}`, 1);
			st.nameTag = `§r§d✦ ${GEM_NAMES[i]}`;
			st.setLore([
				`§7${PACK_TITLE}`,
				"§eInteract on any block to place",
				"§8Crouch + Interact to pick up"
			]);
			inv.addItem(st);
		}
		player.sendMessage(`§a[${PACK_TITLE}] Added all ${GEM_NAMES.length} Gem items to your inventory!`);
		return;
	}

	if (id === `${CMD_PREFIX}:clear` || id === "gem:clear") {
		const dim = player.dimension;
		const ents = dim.getEntities({ type: ENTITY_ID });
		for (const e of ents) e.remove();
		player.sendMessage(`§e[${PACK_TITLE}] Cleared ${ents.length} spawned gem entities.`);
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
		player.sendMessage(
			`§a[${PACK_TITLE}] Spawned §f${GEM_NAMES[variant]}§a! (§eCrouch + Interact§a to pick up, §eInteract§a on block to place)`
		);
	}
});
