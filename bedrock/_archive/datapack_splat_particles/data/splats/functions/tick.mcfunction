# Check if splat should be displayed (runs every tick)
execute if score test01_clean splat_active matches 1 run scoreboard players add test01_clean_timer splat_active 1
execute if score test01_clean_timer splat_active matches 5.. run function splats:test01_clean
execute if score test01_clean_timer splat_active matches 5.. run scoreboard players set test01_clean_timer splat_active 0