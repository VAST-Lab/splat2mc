import { system, MolangVariableMap } from "@minecraft/server";
import { splatData } from "./SplatData.js";

var runId;
var GLOBAL_SCALE_MULTIPLIER = 10;

export function scriptBox(log, targetLocation) {
  if (runId !== void 0) {
    system.clearRun(runId);
    runId = void 0;
    log("Cleared the active splat display.", 1);
    return;
  }
  
  log(`Spawning ${splatData.length} fully-oriented splats (Double-sided)...`, 1);
  const vars = new MolangVariableMap();
  
  runId = system.runInterval(() => {
    for (const s of splatData) {
      const particleLoc = {
        x: targetLocation.x + s[0],
        y: targetLocation.y + s[1],
        z: targetLocation.z + s[2]
      };

      // Set the shared variables (Color, Scale, Roll)
      vars.setFloat("variable.color_r", s[3]);
      vars.setFloat("variable.color_g", s[4]);
      vars.setFloat("variable.color_b", s[5]);
      vars.setFloat("variable.scale_x", s[6] * GLOBAL_SCALE_MULTIPLIER);
      vars.setFloat("variable.scale_y", s[7] * GLOBAL_SCALE_MULTIPLIER);
      vars.setFloat("variable.roll", s[11]);
      vars.setFloat("variable.opacity", s[12] || 1.0);

      // --- FRONT FACE ---
      vars.setFloat("variable.dir_x", s[8]);
      vars.setFloat("variable.dir_y", s[9]);
      vars.setFloat("variable.dir_z", s[10]);
      targetLocation.dimension.spawnParticle("vastlab:splat", particleLoc, vars);

      // --- BACK FACE (Flipped Normals) ---
      vars.setFloat("variable.dir_x", -s[8]);
      vars.setFloat("variable.dir_y", -s[9]);
      vars.setFloat("variable.dir_z", -s[10]);
      targetLocation.dimension.spawnParticle("vastlab:splat", particleLoc, vars);
    }
  }, 20);
}