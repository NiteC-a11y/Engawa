@echo off
rem English launcher: same as engawa.bat but with the English voice (ADR-0022).
rem Thin wrapper via call = no duplicated launcher logic (env wins over engawa.json).
rem If you settle on English for daily use, put  "voice": {"id": "en"}  in engawa.json
rem and launch with plain engawa.bat instead. ASCII-only (see engawa.bat).
set "ENGAWA_VOICE=en"
call "%~dp0engawa.bat"
