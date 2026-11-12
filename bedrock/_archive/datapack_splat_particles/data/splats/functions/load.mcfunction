# Initialize scoreboard on load
scoreboard objectives add splat_active dummy
scoreboard players set test01_clean splat_active 0
scoreboard players set test01_clean_timer splat_active 0
tellraw @a {"text":"[splat2mc] Loaded test01_clean. Run /function splats:start","color":"gold"}