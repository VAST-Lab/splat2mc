@echo off
setlocal
cd /d "%~dp0"

call :run_install "bedrock\01_SplatImposter_Pack"
if errorlevel 1 goto :fail
call :run_install "bedrock\02_SplatMesh_Pack"
if errorlevel 1 goto :fail
call :run_install "bedrock\03_SplatRenderer_Pack"
if errorlevel 1 goto :fail

echo.
echo All 3 Bedrock packs installed successfully!
echo.
pause
exit /b 0

:run_install
set "PACK_DIR=%~1"
set "INSTALL_SCRIPT=%~dp0%PACK_DIR%\install.cmd"
if not exist "%INSTALL_SCRIPT%" (
    echo [ERROR] Missing "%INSTALL_SCRIPT%".
    exit /b 1
)
echo.
echo === Installing %PACK_DIR% ===
echo.| call "%INSTALL_SCRIPT%"
if errorlevel 1 exit /b 1
exit /b 0

:fail
echo.
echo [ERROR] Installation failed.
echo.
pause
exit /b 1
