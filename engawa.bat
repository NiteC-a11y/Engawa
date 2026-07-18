@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

rem Everyday launcher: set env vars, launch Chacha detached, then this console closes.
rem start + pythonw (no-console interpreter) = no black window lingers while she lives.
rem Safe because the web path prints nothing to stdout (same reason the --noconsole
rem exe works). Need output/troubleshooting? Use engawa-debug.bat (blocking + log tail).
rem ASCII-only: cmd.exe parses .bat in the OEM codepage, so non-ASCII comments
rem get mojibake'd and run as commands. Keep this file ASCII.
set "ENGAWA_UI=web"
set "PYTHONUTF8=1"

start "" pythonw src\engawa_main.py

endlocal
