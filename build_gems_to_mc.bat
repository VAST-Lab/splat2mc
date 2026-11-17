@echo off
setlocal
cd /d "%~dp0"

where uv >nul 2>&1
if %errorlevel%==0 (
    uv run python tools\build_all_packs.py %*
    goto :done
)

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" tools\build_all_packs.py %*
    goto :done
)

python tools\build_all_packs.py %*

:done
echo.
pause
