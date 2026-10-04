@echo off
title PocketDisplay Windows Control Client
cd /d "%~dp0"

REM Prefer pythonw (no console window); fall back to python otherwise.
where pythonw >nul 2>&1
if %errorlevel%==0 (
    start "" pythonw windows_client.py
) else (
    where py >nul 2>&1
    if %errorlevel%==0 (
        py -3 windows_client.py
    ) else (
        python windows_client.py
    )
)
