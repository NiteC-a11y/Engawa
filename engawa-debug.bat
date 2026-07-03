@echo off
chcp 65001 > nul
setlocal
cd /d "%~dp0"

rem ── 茶々を「デバッグログ＋隅の縁側窓(web)」で起動するランチャ ─────────────
rem   ・ENGAWA_DEBUG=1 → engawa.log に主要ライフサイクル（代打なら say 茶々 (muse)）
rem   ・ENGAWA_UI=web  → frameless の隅の縁側窓
rem   ・別窓で engawa.log を自動追尾（代打トーンの目視用）
rem   間合い/確率などは engawa.json で調整（このバッチは env を最小限しか触らない）
set "ENGAWA_DEBUG=1"
set "ENGAWA_UI=web"

rem ログファイルが無いと追尾窓が即エラーになるので、空で先に用意（FileHandler は append＝中身は壊さない）
if not exist "engawa.log" type nul > "engawa.log"

rem ログ追尾窓を別で開く（最新20行＋以降を追尾・UTF-8）
start "engawa.log 追尾" powershell -NoExit -Command "Get-Content -LiteralPath '%~dp0engawa.log' -Wait -Encoding utf8 -Tail 20"

rem 茶々を起動（この窓が本体。閉じれば縁側も閉じる）
python src\engawa_main.py

endlocal
