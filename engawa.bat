@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

rem ── 茶々を隅の縁側窓(web)で起動するランチャ（日常使い・デバッグ無し）─────────
rem   ログ窓も要るデバッグ起動は engawa-debug.bat
set "ENGAWA_UI=web"

python src\engawa_main.py

endlocal
