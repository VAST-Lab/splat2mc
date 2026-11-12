@echo off
setlocal
echo Installing Oriented Splat Pack to Bedrock development folders...

set "BEDROCK_DEV=%appdata%\Minecraft Bedrock\Users\Shared\games\com.mojang"
if not exist "%BEDROCK_DEV%" (
    set "BEDROCK_DEV=%localappdata%\Packages\Microsoft.MinecraftUWP_8wekyb3d8bbwe\LocalState\games\com.mojang"
)

if not exist "%BEDROCK_DEV%" (
    echo [ERROR] Could not locate Minecraft Bedrock com.mojang directory.
    pause
    exit /b 1
)

set "TARGET_BP=%BEDROCK_DEV%\development_behavior_packs\SplatRenderer_BP"
set "TARGET_RP=%BEDROCK_DEV%\development_resource_packs\SplatRenderer_RP"

if exist "%TARGET_BP%" (
    echo Removing old SplatRenderer_BP...
    rmdir /s /q "%TARGET_BP%"
)
if exist "%TARGET_RP%" (
    echo Removing old SplatRenderer_RP...
    rmdir /s /q "%TARGET_RP%"
)

echo Copying fresh SplatRenderer_BP and SplatRenderer_RP...
xcopy /y /e /i "%~dp0SplatRenderer_BP" "%TARGET_BP%" >nul
xcopy /y /e /i "%~dp0SplatRenderer_RP" "%TARGET_RP%" >nul

echo Done! Cleanly installed SplatRenderer_BP and SplatRenderer_RP to:
echo   %BEDROCK_DEV%
pause