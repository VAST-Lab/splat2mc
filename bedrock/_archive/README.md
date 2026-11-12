# Bedrock Suite Archive Directory

This directory archives historical iterations and experimental prototypes created during the development of `splat2mc`. Each folder is self-contained with its behavior and resource packs for reference or debugging.

---

### Archive Inventory

| Folder Name | Description | Key Mechanism |
|---|---|---|
| [`datapack_splat_particles/`](./datapack_splat_particles) | Java Edition particle datapack | `.mcfunction` commands with vanilla `/particle` |
| [`imposter_96_separate_textures/`](./imposter_96_separate_textures) | Multi-texture imposter prototype | 96 individual image files (`image_0.png`–`image_95.png`) swapped via entity variant definitions |
| [`imposter_server_scripted_sync/`](./imposter_server_scripted_sync) | Server-scripted imposter prototype | 16×3 spritesheet atlas driven by server-side tick script (`main.js`) syncing `minecraft:variant` |
| [`particles_unoriented_chat_command/`](./particles_unoriented_chat_command) | Original Bedrock chat-command splat pack | Camera-facing billboard (`lookat_xyz`), spawned via `!splat` chat command (output of `export_bedrock.py`) |
| [`particles_unoriented_scriptevent_test/`](./particles_unoriented_scriptevent_test) | Unoriented 40,000 particle stress test (formerly `Splats_BP-test`) | Stress test evaluating 40,000 spherical splats via `/scriptevent sample:run` using `variable.scale` billboard particles |
| [`particles_oriented_double_emission_prototype/`](./particles_oriented_double_emission_prototype) | Oriented splat prototype (formerly `MC-Splats`) | Double-sided oriented billboard particles spawned via dual front/back `spawnParticle` emissions every 20 ticks |

---

### Notes on `particles_unoriented_scriptevent_test` (formerly `Splats_BP-test`)

* **Origin & Purpose:** When migrating from the chat-command (`!splat`) approach to Mojang's modern `/scriptevent sample:run` script architecture, this Behavior Pack was created to benchmark high-density unoriented splat rendering (40,000 spherical Gaussians).
* **Missing Pair Explanation:** In the original workspace, this folder was an orphaned BP (`Splats_BP-test`) because it relied directly on the unoriented particle definition (`vastlab:splat` with `variable.scale`) provided by `Splat_RP` in `particles_unoriented_chat_command`.
* **Current Status:** Paired with a local copy of `Splat_RP` so it can be deployed and tested standalone.
