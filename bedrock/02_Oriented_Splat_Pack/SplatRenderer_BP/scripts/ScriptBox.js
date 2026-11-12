import { system, MolangVariableMap } from "@minecraft/server";
import { splatData } from "./SplatData.js";

let runId;
const GLOBAL_SCALE_MULTIPLIER = 3.0;
// Particle max_lifetime is 5.0s = 100 ticks.
// We divide splatData across 100 ticks so every splat refreshes seamlessly without frame spikes.
const CYCLE_TICKS = 100;

export function scriptBox(log, targetLocation) {
	if (runId !== undefined) {
		system.clearRun(runId);
		runId = undefined;
		log("Cleared the active splat display.", 1);
		return;
	}

	const totalSplats = splatData.length;
	const batchSize = Math.ceil(totalSplats / CYCLE_TICKS);
	let cursor = 0;

	log(`Streaming ${totalSplats} oriented splats (${batchSize} splats/tick over ${CYCLE_TICKS}t cycle)...`, 1);

	const vars = new MolangVariableMap();

	runId = system.runInterval(() => {
		const end = Math.min(cursor + batchSize, totalSplats);

		for (let i = cursor; i < end; i++) {
			const s = splatData[i];
			const particleLoc = {
				x: targetLocation.x + s[0],
				y: targetLocation.y + s[1],
				z: targetLocation.z + s[2]
			};

			vars.setFloat("variable.color_r", s[3]);
			vars.setFloat("variable.color_g", s[4]);
			vars.setFloat("variable.color_b", s[5]);
			vars.setFloat("variable.scale_x", s[6] * GLOBAL_SCALE_MULTIPLIER);
			vars.setFloat("variable.scale_y", s[7] * GLOBAL_SCALE_MULTIPLIER);
			vars.setFloat("variable.dir_x", s[8]);
			vars.setFloat("variable.dir_y", s[9]);
			vars.setFloat("variable.dir_z", s[10]);
			vars.setFloat("variable.roll", s[11]);
			vars.setFloat("variable.opacity", s[12] ?? 1.0);

			// Single emission renders both front and back faces via 'DisableCulling';
			// core particles (s[13] === 1) write depth to occlude back-side splats from all angles
			const particleId = s[13] ? "vastlab:splat_core" : "vastlab:splat";
			targetLocation.dimension.spawnParticle(particleId, particleLoc, vars);
		}

		cursor = end >= totalSplats ? 0 : end;
	}, 1);
}
