@echo off
setlocal

set "PACK_NAME=SplatMesh"

echo Installing %PACK_NAME% Pack to Bedrock development folders...

set "BEDROCK_DEV=%appdata%\Minecraft Bedrock\Users\Shared\games\com.mojang"
if not exist "%BEDROCK_DEV%" (
    set "BEDROCK_DEV=%localappdata%\Packages\Microsoft.MinecraftUWP_8wekyb3d8bbwe\LocalState\games\com.mojang"
)

if not exist "%BEDROCK_DEV%" (
    echo [ERROR] Could not locate Minecraft Bedrock com.mojang directory.
    pause
    exit /b 1
)

set "SOURCE_BP=%~dp0%PACK_NAME%_BP"
set "SOURCE_RP=%~dp0%PACK_NAME%_RP"

if not exist "%SOURCE_BP%\" (
    echo [ERROR] Source folder not found: "%SOURCE_BP%"
    pause
    exit /b 1
)
if not exist "%SOURCE_RP%\" (
    echo [ERROR] Source folder not found: "%SOURCE_RP%"
    pause
    exit /b 1
)

set "TARGET_BP=%BEDROCK_DEV%\development_behavior_packs\%PACK_NAME%_BP"
set "TARGET_RP=%BEDROCK_DEV%\development_resource_packs\%PACK_NAME%_RP"

if exist "%TARGET_BP%\" (
    echo Removing old %PACK_NAME%_BP...
    rmdir /s /q "%TARGET_BP%"
)
if exist "%TARGET_RP%\" (
    echo Removing old %PACK_NAME%_RP...
    rmdir /s /q "%TARGET_RP%"
)

echo Copying fresh %SOURCE_NAME%_BP and %SOURCE_NAME%_RP...
xcopy /y /e /i "%SOURCE_BP%" "%TARGET_BP%" >nul
xcopy /y /e /i "%SOURCE_RP%" "%TARGET_RP%" >nul

echo Done! Cleanly installed %PACK_NAME%_BP and %PACK_NAME%_RP to:
echo    %BEDROCK_DEV%
pause