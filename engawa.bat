@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

rem Everyday launcher: set env vars, then run Chacha in the corner web window.
rem Runs in THIS console (blocking) so it is reliable and shows any output.
rem Close the engawa window (or Ctrl+C here) to stop. Debug: engawa-debug.bat.
rem ASCII-only: cmd.exe parses .bat in the OEM codepage, so non-ASCII comments
rem get mojibake'd and run as commands. Keep this file ASCII.
set "ENGAWA_UI=web"
set "PYTHONUTF8=1"

python src\engawa_main.py

endlocal
