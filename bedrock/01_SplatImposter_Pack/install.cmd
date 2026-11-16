@echo off
setlocal
echo Installing 01 Spherical Imposter Pack to Bedrock development folders...

set "BEDROCK_DEV=%appdata%\Minecraft Bedrock\Users\Shared\games\com.mojang"
if not exist "%BEDROCK_DEV%" (
    set "BEDROCK_DEV=%localappdata%\Packages\Microsoft.MinecraftUWP_8wekyb3d8bbwe\LocalState\games\com.mojang"
)

if not exist "%BEDROCK_DEV%" (
    echo [ERROR] Could not locate Minecraft Bedrock com.mojang directory.
    pause
    exit /b 1
)

set "TARGET_BP=%BEDROCK_DEV%\development_behavior_packs\SplatImposter_BP"
set "TARGET_RP=%BEDROCK_DEV%\development_resource_packs\SplatImposter_RP"

if exist "%TARGET_BP%" (
    echo Removing old SplatImposter_BP...
    rmdir /s /q "%TARGET_BP%"
)
if exist "%TARGET_RP%" (
    echo Removing old SplatImposter_RP...
    rmdir /s /q "%TARGET_RP%"
)

echo Copying fresh SplatImposter_BP and SplatImposter_RP...
xcopy /y /e /i "%~dp0SplatImposter_BP" "%TARGET_BP%" >nul
xcopy /y /e /i "%~dp0SplatImposter_RP" "%TARGET_RP%" >nul

echo Done! Cleanly installed SplatImposter_BP and SplatImposter_RP to:
echo   %BEDROCK_DEV%
pause
