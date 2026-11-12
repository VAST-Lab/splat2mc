@echo off
setlocal
echo Installing Spherical Imposter Pack to Bedrock development folders...

set "BEDROCK_DEV=%appdata%\Minecraft Bedrock\Users\Shared\games\com.mojang"
if not exist "%BEDROCK_DEV%" (
    set "BEDROCK_DEV=%localappdata%\Packages\Microsoft.MinecraftUWP_8wekyb3d8bbwe\LocalState\games\com.mojang"
)

if not exist "%BEDROCK_DEV%" (
    echo [ERROR] Could not locate Minecraft Bedrock com.mojang directory.
    pause
    exit /b 1
)

set "TARGET_BP=%BEDROCK_DEV%\development_behavior_packs\Imposter_BP"
set "TARGET_RP=%BEDROCK_DEV%\development_resource_packs\Imposter_RP"

if exist "%TARGET_BP%" (
    echo Removing old Imposter_BP...
    rmdir /s /q "%TARGET_BP%"
)
if exist "%TARGET_RP%" (
    echo Removing old Imposter_RP...
    rmdir /s /q "%TARGET_RP%"
)

echo Copying fresh Imposter_BP and Imposter_RP...
xcopy /y /e /i "%~dp0Imposter_BP" "%TARGET_BP%" >nul
xcopy /y /e /i "%~dp0Imposter_RP" "%TARGET_RP%" >nul

echo Done! Cleanly installed Imposter_BP and Imposter_RP to:
echo   %BEDROCK_DEV%
pause