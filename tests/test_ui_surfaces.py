"""test_ui_surfaces.py — UI サーフェスの掃引二系統（ADR-0033 追補11/15/16）。

**residue sweep**: en voice で全台帳キーの解決結果に日本語が残らない（graceful fallback の静かな
日本語落ちを機械検出）。
**sovereignty canary**: 台帳の全既定を sentinel `__L10N_<key>__` に差し替えた test voice で
サーフェスを駆動し、sentinel の出現で「可視文言が台帳経由＝bundle から上書き可能」を証明
（決定1 の直接強制。日本語残存検査だけでは英語直書きのハードコードを見逃す・codex 7/19 [高]①）。
**canary**: View ポートの出力メソッドは COVERED ∪ EXEMPT に必ず分類（新メソッドを足すと分類を迫る＝
injection canary と同型・追補15）。

EXEMPT は (surface, element, reason) の型付き定数（追補15・JSON 化は実需が出たら・[低]①）。
web の DOM レベル掃引は tests/test_web_behavior.py（opt-in・追補16）が担う＝ここは文字列レベル。
"""
import json
import os
import re
import sys
import tempfile
import unittest
from dataclasses import dataclass

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config
import views
import voice

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_JP_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿ｦ-ﾟ]")


def _registry_keys():
    with open(os.path.join(ROOT, "locales", "strings.json"), encoding="utf-8") as f:
        return [k for k in json.load(f) if k != "_comment"]


@dataclass(frozen=True)
class Exempt:
    surface: str      # どのサーフェス
    element: str      # どの要素/文言
    reason: str       # なぜ意図的に残すか（必須）


# 意図的残置の台帳（理由必須・広い免除は書かない）。減らす方向で運用する。
EXEMPT = (
    Exempt("console.header", "縁側の外・箱庭〔雀|猫|風〕[起承転結] 等の label",
           "Narration.label は sources（源の名前）由来＝culture/Inc4 で吸収予定。ヘッダの label 枠ごと源の責務"),
    Exempt("console.header", "（HH:MM 時刻・罫線）", "言語中立の記号・時刻表示"),
    Exempt("view.game_*", "対局・観戦窓の固定文言", "未鍵化の後送り（Backlog）＝ゲーム章でまとめて"),
)


class _FakeResident:
    model = None
    # sessionId 無し＝OpenAIAgent 相当（tag が session= を含まない・boot.tag サーフェス用）


def _boot_tag():
    import engawa_main
    return engawa_main._resident_tag(_FakeResident())


def _surfaces():
    """sovereignty で駆動する組み立てサーフェス（loc 単発は all-keys で網羅済み＝ここは合成物）。
    web の JS 実行時合成（RESIDENT 等）は DOM テスト側（test_web_behavior）が担う。"""
    return {
        "console.header.user": views.ConsoleView._header(voice.resident_name(), "user", None),
        "console.header.transition": views.ConsoleView._header(voice.resident_name(), "transition", None),
        "console.voice_block": views.ConsoleView._voice_block(None, "hello"),
        "web.shell": views.build_web_html(),
        "boot.tag": _boot_tag(),
    }


class _VoiceFixture(unittest.TestCase):
    """voice/config を隔離して差し替える共通土台。"""
    _VOICE = None      # サブクラスが設定（"en" or sentinel を作る）

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("ENGAWA_VOICE", "ENGAWA_VOICES_DIR", "ENGAWA_CONFIG")}
        os.environ["ENGAWA_CONFIG"] = os.path.join(os.path.dirname(__file__), "no-such-engawa.json")
        self._reset()

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._reset()

    @staticmethod
    def _reset():
        config._CFG = None
        voice._CACHE = None
        voice._REGISTRY = None


class TestResidueSweepEn(_VoiceFixture):
    """en voice: 全台帳キーの解決に日本語が残らない＝「静かな日本語落ち」の全数検査。"""

    def test_every_key_resolves_without_japanese(self):
        os.environ.pop("ENGAWA_VOICES_DIR", None)          # 実 repo の voices/en
        os.environ["ENGAWA_VOICE"] = "en"
        self._reset()
        self.assertEqual(voice.llm_lang(), "en")
        for key in _registry_keys():
            with self.subTest(key=key):
                val = voice.loc(key)
                self.assertIsNone(_JP_RE.search(val),
                                  f"en で '{key}' が日本語に落ちている: {val!r}（voices/en/strings.json に訳を足す）")


class TestSovereigntyCanary(_VoiceFixture):
    """sentinel voice: 全既定を __L10N_<key>__ に差し替え → サーフェスに sentinel が現れる＝
    可視文言が台帳経由（bundle から上書き可能）である証明。ハードコードは sentinel が出ずに落ちる。"""

    def _use_sentinel_voice(self):
        tmp = tempfile.mkdtemp(prefix="engawa_sent_")
        d = os.path.join(tmp, "sentinel")
        os.makedirs(d)
        strings = {k: f"__L10N_{k}__" for k in _registry_keys()}
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"label": "sentinel"}, f)
        with open(os.path.join(d, "strings.json"), "w", encoding="utf-8") as f:
            json.dump(strings, f, ensure_ascii=False)
        os.environ["ENGAWA_VOICES_DIR"] = tmp
        os.environ["ENGAWA_VOICE"] = "sentinel"
        self._reset()

    def test_all_keys_resolve_to_sentinel(self):
        self._use_sentinel_voice()
        for key in _registry_keys():
            with self.subTest(key=key):
                self.assertEqual(voice.loc(key), f"__L10N_{key}__")

    def test_assembled_surfaces_carry_sentinels(self):
        """組み立てサーフェスの可視文言が sentinel で置き換わる（EXEMPT の要素以外）。"""
        self._use_sentinel_voice()
        s = _surfaces()
        # console: 話者 prefix（resident_name）と客人ラベル・（移ろい）suffix が台帳経由
        self.assertIn("__L10N_resident_name__", s["console.header.user"])
        self.assertIn("__L10N_ui_transition_suffix__", s["console.header.transition"])
        self.assertIn("__L10N_ui_chip_guest__", s["console.voice_block"])     # 「客人 ›」の客人
        self.assertNotIn("客人", s["console.voice_block"], "console の客人ラベルがハードコード（7/19 [中]2）")
        # web shell: 固定ラベル群＋JS の RESIDENT 定数
        for key in ("ui_chip_resident", "ui_chip_guest", "ui_chip_both", "ui_send",
                    "ui_placeholder", "ui_close", "ui_meow", "ui_you", "ui_addr"):
            with self.subTest(surface="web.shell", key=key):
                self.assertIn(f"__L10N_{key}__", s["web.shell"])
        self.assertIn('const RESIDENT="__L10N_resident_name__"', s["web.shell"])
        self.assertNotIn("客人 ›", s["web.shell"], "web JS の客人ラベルがハードコード")
        # boot tag: モデル既定・（非既定 voice なので）声= ラベル
        self.assertIn("__L10N_resident_name__=__L10N_ui_model_default__", s["boot.tag"])
        self.assertIn("__L10N_ui_voice_label__=sentinel", s["boot.tag"])


class TestSurfaceCanary(unittest.TestCase):
    """View ポートの出力メソッドは COVERED ∪ EXEMPT に必ず分類（新メソッド追加で分類を迫る・追補15）。"""

    COVERED = {"turn_start", "chunk", "turn_end", "system", "say"}            # 掃引/鍵化の対象経路
    EXEMPT_METHODS = {"game_open", "game_update", "game_close"}               # 対局窓＝未鍵化後送り（Backlog）

    def test_view_output_methods_classified(self):
        import inspect
        out = {n for n, m in inspect.getmembers(views.View, callable)
               if not n.startswith("_") and n not in ("inputs", "set_font", "current_font",
                                                      "set_daynight", "current_daynight",
                                                      "daynight_enabled", "set_daynight_enabled",
                                                      "set_absent", "set_props")}   # 表示でなく入力/設定系
        unclassified = out - self.COVERED - self.EXEMPT_METHODS
        self.assertFalse(unclassified,
                         f"未分類の View 出力メソッド: {sorted(unclassified)}（COVERED か EXEMPT_METHODS へ・理由を書くこと）")


if __name__ == "__main__":
    unittest.main()
