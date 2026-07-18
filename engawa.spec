# -*- mode: python ; coding: utf-8 -*-
# engawa.spec — PyInstaller ビルド定義（茶々の縁側 / Windows onefile）
# ローカルビルド: pyinstaller engawa.spec  → dist/engawa.exe
#
# 方針（1行ずつ理由）:
#   - onefile + noconsole … frameless 常駐GUI。端末窓は不要（run_web/webview.start がメインスレッドを掴む）。
#   - WebView2 の native DLL は pyinstaller-hooks-contrib 同梱の hook-webview.py が
#     collect_data_files('webview', subdir='lib') + collect_dynamic_libs('webview') で自動収集する
#     （webview/lib の WebView2Loader.dll / WebBrowserInterop.x64.dll / runtimes/ を同梱）。ここでは重複記述しない。
#   - pythonnet(clr) / clr_loader は hook-clr.py / hook-clr_loader.py が Python.Runtime.dll 等を面倒みる。
#   - ただし pywebview のバックエンドと clr_loader の実装は「実行時に動的 import」されるため PyInstaller の
#     静的解析で漏れうる → hiddenimports に明示（下記）。
import os

# SPECPATH … このファイルのあるディレクトリ（＝リポジトリ直下）を PyInstaller が注入する。
SRC = os.path.join(SPECPATH, 'src')

hiddenimports = [
    'webview.platforms.edgechromium',   # Windows の実バックエンド。guilib が動的選択するので静的解析に載らない → 明示。
    'clr_loader.netfx',                 # Windows は .NET Framework ローダ経由で pythonnet を起動。動的 import なので明示。
]

# excludes … サイズ肥大防止。理由:
#   tkinter        … 実在する唯一の未使用GUI toolkit（stdlib 同梱）。Tcl/Tk を丸ごと引き込むため除外＝数MB削減。
#   PyQt5/6・PySide2/6・gi(gtk) … pywebview の他バックエンド。この環境には未インストール（除外は無害・将来混入への防波堤）。
#   rlcard         … /game の任意依存（未インストール）。onefile の起動確認には不要。
#   test 系        … pytest / _pytest はビルド不要。
excludes = [
    'tkinter',
    'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'gi',
    'rlcard',
    'pytest', '_pytest',
]

a = Analysis(
    [os.path.join(SRC, 'engawa_main.py')],   # エントリポイント。
    pathex=[SRC],                            # config/views/… は engawa_main と同階層の flat import → src を検索パスへ。
    binaries=[],
    datas=[                                   # 茶々スプライト/縁側背景を assets/ として同梱（bundle 内 assets/ へ配置）。
        (os.path.join(SPECPATH, 'assets', 'sprite.json'), 'assets'),   # 皮設定（sheet 参照は隣の chacha.png を相対解決）。
        (os.path.join(SPECPATH, 'assets', 'chacha.png'), 'assets'),    # 三毛猫 4表情シート（起動時 dataURI 化）。
        (os.path.join(SPECPATH, 'assets', 'scene.png'), 'assets'),     # 縁側背景（障子＋板の間）。
        (os.path.join(SPECPATH, 'voices', 'en'), os.path.join('voices', 'en')),   # 英語 voice バンドル（ADR-0022。voice._voices_dir が frozen 時 sys._MEIPASS/voices を解決）。
    ],                                       # views._base_dir() が frozen 時 sys._MEIPASS/assets を指すので runtime で見つかる。
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
# 注記: views.py は起動時に assets/chacha.png・scene.png・sprite.json をディスクから読んで dataURI 化する。
#       上の datas で bundle 内 assets/ へ束ね、views._base_dir() が frozen 時 sys._MEIPASS を基準にするので
#       exe でも実スプライト/背景が出る。欠損時は従来どおり procedural cat + CSS グラデにフォールバック（安全）。
#       engawa.json / topic_sources.json は個人設定/任意なので同梱しない（未検出→コード既定にフォールバック）。

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,      # onefile: バイナリ/データを EXE に内包（COLLECT を作らない）。
    a.datas,
    [],
    name='engawa',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,       # UPX 未導入・DLL 圧縮は WebView2 で不具合が出がち → 使わない。
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,   # --noconsole（frameless 常駐GUI）。
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,       # .ico 未整備 → 既定アイコン。用意でき次第 icon='path\\to\\app.ico' を差す。
)
