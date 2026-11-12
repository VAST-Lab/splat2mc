# Start displaying the splat
scoreboard objectives add splat_active dummy
scoreboard players set test01_clean splat_active 1
tellraw @s {"text":"Started splat: test01_clean","color":"green"}
tellraw @s {"text":"Run /function splats:stop to stop","color":"gray"}