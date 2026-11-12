import { world, system } from "@minecraft/server";

system.runInterval(() => {
    const entities = world.getDimension("overworld").getEntities({ type: "gallery:imposter" });
    
    for (const entity of entities) {
        const players = world.getPlayers();
        if (players.length === 0) continue;

        const player = players[0];
        
        const dx = player.location.x - entity.location.x;
        const dz = player.location.z - entity.location.z;
        let yaw = Math.atan2(dz, dx) * (180 / Math.PI);
        if (yaw < 0) yaw += 360;

        const dy = player.location.y - entity.location.y;
        const dist = Math.sqrt(dx*dx + dz*dz);
        let pitch = Math.atan2(dy, dist) * (180 / Math.PI);

        let row = 1;
        if (pitch > 20) row = 0;
        if (pitch < -20) row = 2;

        let col = Math.floor(yaw / 22.5) % 16;
        let variantId = (col * 3) + row;

        entity.setProperty("minecraft:variant", variantId);
    }
}, 1);