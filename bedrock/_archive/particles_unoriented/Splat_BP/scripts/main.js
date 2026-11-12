import { world, system, MolangVariableMap } from "@minecraft/server";
import { splatData } from "./splat_data.js";

let activeSplats = [];

// Listen for chat commands to spawn or clear the splat
world.beforeEvents.chatSend.subscribe((event) => {
    if (event.message === "!splat") {
        event.cancel = true;
        activeSplats.push({
            x: event.sender.location.x,
            y: event.sender.location.y,
            z: event.sender.location.z,
            dimension: event.sender.dimension
        });
        event.sender.sendMessage("\xA7a[splat2mc] Spawning splat! Type !clearsplat to remove.");
    }
    if (event.message === "!clearsplat") {
        event.cancel = true;
        activeSplats = [];
        event.sender.sendMessage("\xA7e[splat2mc] Cleared splats.");
    }
});

// Run loop every 20 ticks (1 second) to respawn particles
system.runInterval(() => {
    for (const anchor of activeSplats) {
        for (const s of splatData) {
            const vars = new MolangVariableMap();
            vars.setFloat("variable.color_r", s[3]);
            vars.setFloat("variable.color_g", s[4]);
            vars.setFloat("variable.color_b", s[5]);
            vars.setFloat("variable.scale", s[6]);

            anchor.dimension.spawnParticle("vastlab:splat", {
                x: anchor.x + s[0],
                y: anchor.y + s[1],
                z: anchor.z + s[2]
            }, vars);
        }
    }
}, 20);
