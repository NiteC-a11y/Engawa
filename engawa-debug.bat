@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

rem Debug launcher: ENGAWA_DEBUG + web window, plus a separate window that tails
rem engawa.log. The app runs in THIS console (blocking; shows tracebacks).
rem Watch the tail window for "chacha steps away" / "chacha returns" and timing.
rem ASCII-only: cmd.exe parses .bat in the OEM codepage, so non-ASCII comments
rem get mojibake'd and run as commands. Keep this file ASCII.
set "ENGAWA_DEBUG=1"
set "ENGAWA_UI=web"
set "PYTHONUTF8=1"

rem Make sure engawa.log exists so the tail window does not error immediately
rem (Python's FileHandler appends, so any existing content is preserved).
if not exist "engawa.log" type nul > "engawa.log"

rem Log-tail window (separate; last 20 lines then follow, UTF-8).
start "engawa.log tail" powershell -NoExit -Command "Get-Content -LiteralPath '%~dp0engawa.log' -Wait -Encoding utf8 -Tail 20"

python src\engawa_main.py

endlocal
