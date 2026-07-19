@echo off
rem Kyoto-dialect launcher: same as engawa.bat but with the ja-kyoto voice (ADR-0022/0033).
rem Thin wrapper via call = no duplicated launcher logic (env wins over engawa.json).
rem If you settle on this voice for daily use, put  "voice": {"id": "ja-kyoto"}  in engawa.json
rem and launch with plain engawa.bat instead. ASCII-only (see engawa.bat).
set "ENGAWA_VOICE=ja-kyoto"
call "%~dp0engawa.bat"
