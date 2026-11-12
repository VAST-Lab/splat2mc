import { Vector3Utils } from "@minecraft/math";
import { world, DimensionLocation, Vector3 } from "@minecraft/server";

// When running a world with this behavior pack, type '/scriptevent sample:run' in chat to trigger this code.
export function scriptBox(log: (message: string, status?: number) => void, targetLocation: DimensionLocation) {
  const explosionLoc = Vector3Utils.add(targetLocation, { x: 0.5, y: 0.5, z: 0.5 });
  log('Creating an explosion of radius 15 that causes fire.');
  targetLocation.dimension.createExplosion(explosionLoc, 15, { causesFire: true });
  const belowWaterLoc = Vector3Utils.add(targetLocation, { x: 3, y: 1, z: 3 });
  log('Creating an explosion of radius 10 that can go underwater.');
  targetLocation.dimension.createExplosion(belowWaterLoc, 10, { allowUnderwater: true });
}
