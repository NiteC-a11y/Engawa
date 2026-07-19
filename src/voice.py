#!/usr/bin/env python3
"""voice.py — 茶々の「声」バンドル解決（ADR-0022・方言/言語を voice 単位で差し替え）。

- 選択: env `ENGAWA_VOICE` > `engawa.json[voice].id` > 既定 `ja-osaka`（＝組み込み・ファイル不要・現状維持）。
- バンドル: `voices/<id>/` ＝ `meta.json`（base/label/llm_lang）＋ `persona.md`（声の本体＝transcreation・
  機械翻訳しない）＋ `strings.json`（UI シェル文言の上書き・任意）。欠落は継承 `<voice> → <base> → 組み込み既定`。
- persona の底は `persona.RESIDENT_PERSONA`（ja-osaka）＝**ゼロ設定/バンドル欠損でも現状維持**（起動を止めない）。
- `loc(key, default=None)`: UI シェル文言の解決（ADR-0033 決定4＝**3段**）: `<voice>+<base>` strings →
  呼び側 default（動的既定＝absence 系 random プール専用）→ **`locales/strings.json`（台帳＝キー＋日本語既定の
  単一正本）** → キー名そのまま（欠損が画面で一目でバレる・起動は止めない・決定10）。src の静的文言は
  台帳が正本＝インライン既定は書かない（静的照合テストで強制）。LLM 注入文言は対象外（言語は `lang_note()`）。
- 台帳は専用ローダー（決定13）＝読込結果 `ok/missing/malformed/wrong-shape` を保持し debuglog に一度だけ出す
  （`_read_json` の「全部 {} に畳む」は任意 bundle 用・正本には使わない）。置き場は repo 直下 `locales/`
  （frozen 時 `sys._MEIPASS/locales`・`ENGAWA_LOCALES_DIR` で差し替え＝テスト用）。
- `lang_note()`: llm_lang 時だけ LLM 注入末尾に足す出力言語指示1行。prompts と sources の**両ビルダー群が共用**
  （「住人に届く全注入」の概念単位で漏らさない・7/19 のソロ経路穴の教訓＝tests/test_injection_lang.py が列挙検証）。
- 置き場は repo 直下 `voices/`（frozen 時は `sys._MEIPASS/voices`＝spec の datas 同梱・views._base_dir と同流）。
  `ENGAWA_VOICES_DIR` で差し替え可（テスト・exe 外の自作 voice 用）。
- voice は spawn 時に確定＝ライブ切替なし（長命セッションに焼き込む・ADR-0022 決定5）。キャッシュは
  `_CACHE=None` でリセット（テスト用・config._CFG と同じ流儀）。
"""
import json
import os
import sys

import config
import debuglog
import persona

log = debuglog.get("voice")

DEFAULT_ID = "ja-osaka"     # 組み込みの底（persona.RESIDENT_PERSONA・ファイル不要）

_CACHE = None               # 解決済み voice dict（1プロセス1回・テストは None でリセット）


def _voices_dir():
    if os.environ.get("ENGAWA_VOICES_DIR"):
        return os.environ["ENGAWA_VOICES_DIR"]
    if getattr(sys, "frozen", False):                    # PyInstaller onefile（views._base_dir と同流）
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(base, "voices")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "voices")


def _locales_dir():
    """台帳（locales/strings.json）の置き場（_voices_dir と同流・ENGAWA_LOCALES_DIR はテスト/特殊環境用）。"""
    if os.environ.get("ENGAWA_LOCALES_DIR"):
        return os.environ["ENGAWA_LOCALES_DIR"]
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        return os.path.join(base, "locales")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "locales")


_REGISTRY = None            # (data, state) の cache。テストは None でリセット（_CACHE と同流）


def _load_registry():
    """台帳の専用ローダー（ADR-0033 決定13）。任意 bundle と違い**単一正本**なので失敗を握りつぶさず
    `ok / missing / malformed / wrong-shape` を識別して debuglog に一度だけ出す（起動は止めない・決定10）。
    値は空でない str のみ採用（空文字・非文字列は欠損扱い＝ドロップ数もログへ）。`_comment` は台帳の注記。"""
    path = os.path.join(_locales_dir(), "strings.json")
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        log.debug("locales 台帳が無い: %s（全キーがキー名表示になる＝同梱漏れを疑う）", path)
        return {}, "missing"
    except (ValueError, OSError) as e:
        log.debug("locales 台帳が読めない: %s (%s: %s)", path, type(e).__name__, e)
        return {}, "malformed"
    if not isinstance(raw, dict):
        log.debug("locales 台帳の形が不正（root が dict でない）: %s", path)
        return {}, "wrong-shape"
    data = {k: v for k, v in raw.items()
            if k != "_comment" and isinstance(v, str) and v}
    dropped = len(raw) - 1 - len(data) if "_comment" in raw else len(raw) - len(data)
    if dropped:
        log.debug("locales 台帳: 空/非文字列の値 %d 件を欠損扱いでドロップ", dropped)
    return data, "ok"


def _registry():
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _load_registry()
    return _REGISTRY[0]


def registry_state():
    """台帳の読込状態（ok/missing/malformed/wrong-shape・診断用）。"""
    _registry()
    return _REGISTRY[1]


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}            # 無い/壊れ → 継承の下位へ（起動を止めない・config と同じ流儀）


def _read_text(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except Exception:
        return None


def _resolve():
    vid = (config.get_str("ENGAWA_VOICE", "voice", "id", DEFAULT_ID) or "").strip() or DEFAULT_ID
    builtin = {"id": DEFAULT_ID, "base": None, "label": DEFAULT_ID,
               "llm_lang": None, "persona": None, "strings": {}}
    if vid == DEFAULT_ID:
        return builtin
    d = os.path.join(_voices_dir(), vid)
    meta = _read_json(os.path.join(d, "meta.json"))
    ptext = _read_text(os.path.join(d, "persona.md"))
    if not meta and ptext is None:                       # バンドル無し → 組み込みへ（黙って現状維持しない＝label に痕跡）
        builtin["label"] = f"{DEFAULT_ID}（voice '{vid}' が見つからん）"
        return builtin
    base = (meta.get("base") or "").strip() or None
    strings = {}
    if base and base != vid:                             # 継承: <base> を敷いて <voice> で上書き
        strings.update(_read_json(os.path.join(_voices_dir(), base, "strings.json")))
        if ptext is None:
            ptext = _read_text(os.path.join(_voices_dir(), base, "persona.md"))
    strings.update(_read_json(os.path.join(d, "strings.json")))
    return {"id": vid, "base": base, "label": (meta.get("label") or "").strip() or vid,
            "llm_lang": (meta.get("llm_lang") or "").strip() or None,
            "persona": ptext, "strings": strings}


def current():
    global _CACHE
    if _CACHE is None:
        _CACHE = _resolve()
    return _CACHE


def persona_text():
    """住人に注入する声の本体（ACP=cwd の CLAUDE.md／OpenAI=system・ADR-0026 の両 backend 共通）。"""
    return current()["persona"] or persona.RESIDENT_PERSONA


def llm_lang():
    """出力言語ノブ（base が日本語以外の時だけ効かせる任意項・prompts.py が参照・ADR-0022 決定3）。"""
    return current()["llm_lang"]


_LANG_NAMES = {"en": "English", "ja": "Japanese", "fr": "French", "de": "German",
               "es": "Spanish", "zh": "Chinese", "ko": "Korean"}   # コードは名前で指示（"Respond in en" を避ける）


def lang_note():
    """llm_lang が立つ時だけ、LLM 注入の末尾に足す出力言語の指示1行（ADR-0022 決定3）。
    JP 方言（llm_lang=None）では空文字＝注入文は1バイトも変わらない。persona は英語で書かれているだけでは
    言語を縛らない（80発話の実測で lang note 無し経路はほぼ100%日本語・7/19）＝住人に届く注入は
    prompts 側（user/room）も sources 側（ambient/arc/transition）もすべてこれを後置する。"""
    lang = llm_lang()
    return f"\n(Respond in {_LANG_NAMES.get(lang, lang)}, staying in character.)" if lang else ""


def label():
    """起動行の表示名（`茶々=<label>`）。"""
    return current()["label"]


def resident_name():
    """住人の**表示名**（transcript の話者タグ・console prefix・game プレイヤー名）。strings の
    `resident_name` で voice ごとに差し替え（en=Chacha＝チップ「Chacha」と画面内で揃える・7/19 ユーザー判断）。
    既定は固有名「茶々」。宛先解決（conversation.resolve_addressee）は文面ベースで茶々/Chacha 両対応済み＝
    表示名を変えてもロジックは壊れない。"""
    return loc("resident_name")


def loc(key, default=None):
    """UI シェル文言の解決（ADR-0033 決定4＝3段）: `<voice>+<base>` strings → 呼び側 default →
    台帳（locales/strings.json）→ **キー名そのまま**（台帳欠損が画面で一目でバレる・起動は止めない）。
    呼び側 default は**動的既定専用**（absence_leave/return・guest_timeout_leave＝JP は random プール）＝
    src の静的文言は台帳が正本でインライン既定は書かない（tests/test_strings_registry.py が強制）。"""
    s = current()["strings"].get(key)
    if isinstance(s, str) and s:
        return s
    if default is not None:
        return default
    r = _registry().get(key)
    return r if r else key
