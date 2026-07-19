"""tests/test_web_behavior.py — WEB_HTML の JS 挙動を実ブラウザ(headless chromium)で検証（opt-in）。

Python 側の純ロジック（daynight.py 等）はユニットで担保できるが、その結果を DOM に
適用する JS（applyDay/render/…）は string-presence + node --check(構文) + 目視 止まりで、
**振る舞い**のバグは素通りしていた（例: /daynight off で膜をリセットせず暗いまま固まる不具合
＝PR #7）。ここは playwright で実 HTML を駆動し DOM を assert する＝その隙間を埋める seam。

既定スキップ（速い unit suite を汚さない・chromium 依存を強制しない・CI も既定は skip）。
実行:  ENGAWA_BROWSER_TESTS=1 python -m unittest tests.test_web_behavior
"""
import glob
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

_ENABLED = os.environ.get("ENGAWA_BROWSER_TESTS") == "1"

try:
    from playwright.sync_api import sync_playwright
    _HAS_PW = True
except Exception:
    _HAS_PW = False


def _find_chromium():
    """pip playwright 版とインストール済み browser のバージョンがずれても拾えるよう glob で探す。"""
    for pat in ("~/AppData/Local/ms-playwright/chromium-*/chrome-win/chrome.exe",
                "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
                "~/Library/Caches/ms-playwright/chromium-*/chrome-mac/Chromium.app/Contents/MacOS/Chromium"):
        hits = sorted(glob.glob(os.path.expanduser(pat)))
        if hits:
            return hits[-1]
    return None


_CHROME = _find_chromium() if _HAS_PW else None


@unittest.skipUnless(_ENABLED and _HAS_PW,
                     "browser tests off (set ENGAWA_BROWSER_TESTS=1 と playwright が要る)")
class TestWebBehavior(unittest.TestCase):
    """実 WEB_HTML を chromium で開き、pywebview.api.poll を mock して DOM の変化を確認する。"""

    @classmethod
    def setUpClass(cls):
        import views
        cls._tmp = tempfile.mkdtemp(prefix="engawa_web_")
        cls._html = os.path.join(cls._tmp, "engawa.html")
        with open(cls._html, "w", encoding="utf-8") as f:
            f.write(views.build_web_html())
        cls._url = "file:///" + os.path.abspath(cls._html).replace("\\", "/")

    @classmethod
    def tearDownClass(cls):
        try:
            os.remove(cls._html)
            os.rmdir(cls._tmp)
        except OSError:
            pass

    def _open(self, pw, init_script, url=None):
        # glob で拾えた chromium を優先（pip playwright とインストール版のバージョン差を吸収）。
        # 見つからなければ playwright 既定の解決に任せる（CI＝バージョン一致なので既定で launch できる）。
        b = pw.chromium.launch(**({"executable_path": _CHROME} if _CHROME else {}))
        pg = b.new_page()
        pg.add_init_script(init_script)      # ページ script より先に pywebview.api を注入
        pg.goto(url or self._url)
        return b, pg

    def test_daynight_off_resets_tint_to_neutral(self):
        """/daynight off（poll が day=null を返す）で tint 膜が中立化＝前の夜色を残さない（PR #7 の回帰止め）。

        修正前は applyDay(null) が素通りして tint に夜色 rgb(99,130,163) が残り、暗いまま固まっていた。
        """
        # __off フラグで poll の day を切替＝夜 → /daynight off をリアルに再現
        init = ("window.__off=false;"
                "window.pywebview={api:{"
                "poll:async()=>({items:[],cursor:0,font:1,absent:false,"
                "day: window.__off ? null : {tint:'rgb(99,130,163)',glow:1,lamp:1}}),"
                "send:()=>{},close:()=>{},resize:()=>{}}};")
        with sync_playwright() as pw:
            b, pg = self._open(pw, init)
            try:
                pg.wait_for_timeout(400)                    # tick が夜 day を適用
                dark = pg.evaluate("document.getElementById('tint').style.backgroundColor")
                self.assertIn("99", dark)                   # 夜色 rgb(99,130,163) が乗ってる（前提の確認）

                pg.evaluate("window.__off=true")            # /daynight off ＝以後 poll は day=null
                pg.wait_for_timeout(400)                    # tick が applyDay(null) を適用
                tint = pg.evaluate("document.getElementById('tint').style.backgroundColor")
                glow = pg.evaluate("document.getElementById('glow').style.opacity")
                lamp = pg.evaluate("document.getElementById('lamp').style.opacity")
                self.assertEqual(tint, "rgb(255, 255, 255)")  # 白＝乗算で無変化（← 修正前は夜色のまま＝バグ）
                self.assertEqual(glow, "0")                   # 月光を消す
                self.assertEqual(lamp, "0")                   # 室内灯を消す
            finally:
                b.close()


    def test_en_voice_transcript_labels_follow_resident(self):
        """en voice の転写 DOM: ソロ転写ラベルが RESIDENT（Chacha）追従・say の色分け/客人在室判定が
        display 名で正しい・転写に日本語が残らない（7/19 実機バグ＝JS の「茶々」ハードコード3箇所の回帰止め。
        Python 側の string テストは「渡す」までしか見えず、JS が使うかは DOM でしか検証できない）。"""
        import json
        import re
        import config
        import views
        import voice
        saved = os.environ.get("ENGAWA_VOICE")
        os.environ["ENGAWA_VOICE"] = "en"
        config._CFG = None
        voice._CACHE = None
        try:
            html_path = os.path.join(self._tmp, "engawa_en.html")
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(views.build_web_html())
        finally:
            if saved is None:
                os.environ.pop("ENGAWA_VOICE", None)
            else:
                os.environ["ENGAWA_VOICE"] = saved
            config._CFG = None
            voice._CACHE = None
        url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
        items = [   # 本物の poll スキーマ（turn=ソロ・say=room 確定発話）。Chacha の say を最後に置く＝在室判定の誤爆検出を鋭く
            {"id": 1, "type": "turn", "kind": "ambient", "label": None, "voice": None,
             "text": "mm, quiet morning.", "done": True},
            {"id": 2, "type": "say", "speaker": "ojichan", "text": "indeed it is.", "done": True},
            {"id": 3, "type": "say", "speaker": "Chacha", "text": "the breeze is nice.", "done": True},
        ]
        init = ("window.pywebview={api:{poll:async()=>({items:" + json.dumps(items)
                + ",cursor:3,font:1,absent:false,day:null}),send:()=>{},close:()=>{},resize:()=>{}}};")
        with sync_playwright() as pw:
            b, pg = self._open(pw, init, url)
            try:
                pg.wait_for_timeout(400)                     # tick が items を描画
                labels = pg.evaluate("[...document.querySelectorAll('#log .who')].map(e=>e.textContent)")
                self.assertIn("Chacha ›", labels)            # ソロ転写ラベル＝RESIDENT 追従（旧: 茶々 › 固定）
                guests = pg.evaluate("[...document.querySelectorAll('#log .guest .who')].map(e=>e.textContent)")
                self.assertEqual(guests, ["ojichan ›"])      # 客人スタイルは客人だけ（旧: Chacha 行も guest 色）
                self.assertEqual(pg.evaluate("guestName"), "ojichan")   # 在室判定が Chacha の say で誤爆しない
                body = pg.evaluate("document.getElementById('log').textContent")
                self.assertIsNone(re.search(r"[぀-ヿ㐀-䶿一-鿿]", body),
                                  f"en の転写 DOM に日本語が残っている: {body!r}")
            finally:
                b.close()


if __name__ == "__main__":
    unittest.main()
