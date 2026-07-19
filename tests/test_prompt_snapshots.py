"""test_prompt_snapshots.py — 注入プロンプトのゴールデンスナップショット（7/19）。

「LLM に届く文字列」を voice ごとに固定ファイルと**バイト一致**で突合＝**意図せぬプロンプト変更を
diff で人に迫る**。7/19 の実機バグ（表示名切替が room の注入窓ラベルまで `Chacha「…」` に変え、
場面指示「茶々として」と分裂→茶々の口調が客人化）は、注入文が変わったのに誰も気づかない型だった。
lang note の合成テスト（test_injection_lang＝「足すべきものが有るか」）と対になる「変えるべきで
ないものが変わってないか」の守り。

- 窓（room 系）は **本番配線**＝RoomSpeakerFactory の実 Speaker 名で組む（fixture が配線を迂回すると
  そこが盲点になる・leak_probe の教訓）。
- 入力は全て固定（日時・天気・文言）＝ビルダーは乱数なし→決定的。
- **意図した変更**の時は `ENGAWA_UPDATE_SNAPSHOTS=1 python -m unittest tests.test_prompt_snapshots`
  で再生成し、diff を見てからコミットする。snapshot 未存在は初回ブートストラップとして書いて通す。
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
import config
import conversation
import prompts
import room_speakers
import sources
import voice

SNAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")
UPDATE = os.environ.get("ENGAWA_UPDATE_SNAPSHOTS") == "1"
GUEST_PERSONA = "気まぐれな旅の行商人"


def _ctx():
    now = datetime.datetime(2026, 7, 19, 9, 30)   # 7月固定＝蚊取り線香 narrate も安定して載る
    w = {"desc": "時々曇り", "temp": 28.6, "wind": 6}
    return {"weather": w, "desc": w["desc"], "raining": False,
            "tod": sources.time_of_day(now), "hour": now.hour, "now": now, "topics": []}


def _window():
    """room の窓を本番配線で組む＝話者タグは RoomSpeakerFactory の実 Speaker 名（display でなく name 側）。
    Speaker 名の一人二役が再発すれば en snapshot の窓ラベルが変わり、ここが diff で落ちる。"""
    f = room_speakers.RoomSpeakerFactory(GUEST_PERSONA, resident_speak=None,
                                         guest_agent_provider=lambda: None,
                                         context_provider=lambda: None,
                                         topics_provider=lambda: [], log=None)
    res, guest = f.speakers()
    t = conversation.Transcript()
    t.append("私", "こんにちは")
    t.append(guest.name, "ええ風ですなあ")
    t.append(res.name, "ほい")
    return t.window()


def _renders():
    ctx = _ctx()
    return {
        "ambient": sources.ambient_narration(ctx),
        "arc": sources.event_narration("雀が一羽、ひょいと縁側の手すりに止まった。"),
        "transition": sources.transition_narration("晴れ", ctx),
        "talk": prompts.user_narration("hello", ctx),
        "room_resident": prompts.room_resident_prompt(_window(), conversation.REPLY, ctx),
        "room_guest": prompts.room_guest_prompt(GUEST_PERSONA, _window(), conversation.REPLY, ctx),
    }


class TestPromptSnapshots(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self._env = {k: os.environ.get(k) for k in ("ENGAWA_VOICE", "ENGAWA_VOICES_DIR", "ENGAWA_CONFIG")}
        os.environ["ENGAWA_CONFIG"] = os.path.join(os.path.dirname(__file__), "no-such-engawa.json")
        os.environ.pop("ENGAWA_VOICES_DIR", None)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        config._CFG = None
        voice._CACHE = None

    def _check_voice(self, vid):
        os.environ["ENGAWA_VOICE"] = vid
        config._CFG = None
        voice._CACHE = None
        d = os.path.join(SNAP_DIR, vid)
        os.makedirs(d, exist_ok=True)
        for name, text in _renders().items():
            with self.subTest(builder=name):
                p = os.path.join(d, name + ".txt")
                if UPDATE or not os.path.exists(p):
                    with open(p, "w", encoding="utf-8", newline="\n") as f:
                        f.write(text)
                    continue
                with open(p, encoding="utf-8") as f:      # universal newlines＝CRLF checkout でも \n に正規化（EOL 非依存）
                    want = f.read()
                self.assertEqual(text, want,
                                 f"[{vid}/{name}] 注入文が snapshot と不一致＝LLM に届く文字列が変わった。"
                                 "意図した変更なら ENGAWA_UPDATE_SNAPSHOTS=1 で再生成して diff 確認の上コミット。"
                                 "意図してないなら回帰（7/19 の Speaker 名事故の型）。")

    def test_ja_osaka_prompts_frozen(self):
        self._check_voice("ja-osaka")

    def test_en_prompts_frozen(self):
        self._check_voice("en")


if __name__ == "__main__":
    unittest.main()
