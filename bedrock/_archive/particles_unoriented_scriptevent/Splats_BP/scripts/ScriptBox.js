import { system, MolangVariableMap } from "@minecraft/server";
import { splatData } from "./SplatData.js";

let runId;

// Easily tune particle sizes here without regenerating the python data!
const GLOBAL_SCALE_MULTIPLIER = 0.2; 

export function scriptBox(log, targetLocation) {
  if (runId !== undefined) {
    system.clearRun(runId);
    runId = undefined;
    log("Cleared the active splat display.", 1);
    return;
  }
  
  log(`Spawning ${splatData.length} splat particles...`, 1);
  
  runId = system.runInterval(() => {
    for (const s of splatData) {
      const vars = new MolangVariableMap();
      vars.setFloat("variable.color_r", s[3]);
      vars.setFloat("variable.color_g", s[4]);
      vars.setFloat("variable.color_b", s[5]);
      
      // Apply the scale fix
      vars.setFloat("variable.scale", s[6] * GLOBAL_SCALE_MULTIPLIER);
      
      // Calculate offset natively (no external Math libraries needed)
      const particleLoc = { 
        x: targetLocation.x + s[0], 
        y: targetLocation.y + s[1], 
        z: targetLocation.z + s[2] 
      };
      
      targetLocation.dimension.spawnParticle("vastlab:splat", particleLoc, vars);
    }
  }, 5);
}