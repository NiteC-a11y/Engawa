#!/usr/bin/env python3
"""leak_probe.py — 実 LLM で「住人向け注入の全カテゴリ」を一巡する opt-in E2E ハーネス（層B・7/19）。

部分修正のあとに、触っていない注入経路が壊れていないか（言語落ち・人格崩壊・生エラー）を
**実エンジン**で見るためのスモーク。ユニット（層A: tests/test_injection_lang.py）が張る不変条件の
「実際の LLM がそれに従うか」側を測る。出自: 英語 voice の lang note 穴の測定ハーネス
（10回×4カテゴリ×2エンジン=80発話で「note 有り=40/40 英語／無し=ほぼ100%日本語」の二値を実証）。

使い方（**実 agent を回す＝課金/時間あり。unittest discover には拾われない＝手動 opt-in 実行**）:
    python tests/e2e/leak_probe.py acp    [trials=10] [categories...] [--sticky]
    python tests/e2e/leak_probe.py openai [trials=10] [categories...] [--sticky]
  例: python tests/e2e/leak_probe.py acp 3 ambient arc   # ソロ経路だけ3回ずつ・毎回新品セッション
  - acp    = 実 Claude（claude-code-acp・要 `claude` ログイン）
  - openai = ローカル OpenAI 互換（LM Studio 等・要 `lms server start`＋モデルロード）
  - 既定 voice は en（ENGAWA_VOICE で差し替え可）。JP voice では JP-flag は意味を持たない点に注意。

設計の急所（変えるとき注意）:
  - 注入文は**本番と同じビルダー**（sources.ambient_narration/event_narration・prompts.user_narration/
    room_resident_prompt）で生成＝実経路の再現。ビルダーを足したらここのカテゴリも検討（層A の canary 参照）。
  - **既定は trial ごとに新品セッション（fresh）**＝全 trial が「起動→話しかけ前の初手」＝文脈慣性なしの
    最悪条件。カテゴリ内で1セッションを使い回すと #1 以降に直前応答の言語慣性が乗り、穴の再発を
    過小検出する（codex 7/19 [中]）。慣性込みの長命セッション挙動を観たい時だけ `--sticky`
    （カテゴリごと1セッション）。JSONL の `session` 欄にどちらで測ったかを残す。
  - 判定は本番と同じ表示前ガード strip_resident_leak 通過後＝「画面に出る文字列」。JP 文字の残存を
    flag（引用か漏れかの最終分類は人が目視＝半自動）。
結果: JSONL を temp（ENGAWA_E2E_OUT で差し替え可）へ・stdout にサマリ。
"""
import asyncio
import datetime
import json
import os
import pathlib
import sys
import tempfile

os.environ.setdefault("ENGAWA_VOICE", "en")     # import 前に確定（config/voice は import 時解決）
os.environ.setdefault("ENGAWA_DEBUG", "0")

_ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))

import agent as agent_mod                       # noqa: E402  中立ポート（AgentTimeoutError）
import conversation                             # noqa: E402
import prompts                                  # noqa: E402
import sources                                  # noqa: E402
import voice                                    # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # cp932 コンソールでも日本語を落とさない

OUT_DIR = pathlib.Path(os.environ.get("ENGAWA_E2E_OUT") or tempfile.gettempdir())
TRIALS_DEFAULT = 10

# ── 種の材料（本番ビルダーに食わせる素材）────────────────────
AMBIENT_WEATHER = {"desc": "時々曇り", "temp": 28.6, "wind": 6}   # 訳語ゆれ観測用に固定

ARC_TEXTS = [    # sources.py の実 Phase 文言（静的分・_neko_ten は callable のため対象外）
    "雀が一羽、ひょいと縁側の手すりに止まった。",
    "雀は首をかしげて、板の間のあたりをちょんちょんとついばんでいる。",
    "どこかで物音がして、雀がびくっと身をすくめた。",
    "雀はぱっと羽ばたいて、軒の向こうへ飛んでいった。",
    "塀の上を、近所の三毛猫がそろりと歩いてきた。",
    "猫は身をひるがえして、軒下のどこかへ消えていった。",
    "一陣の風が、軒先のなにかをことりと鳴らした。",
    "夕立の気配か、遠くでかすかに雷が鳴った。",
]

TALK_LINES = [   # en モードのユーザーは英語で話しかける（枠は日本語のまま＝実経路）
    "Nice weather today, huh?",
    "What were you doing just now?",
    "I'm a bit tired from work.",
    "Did you see that sparrow earlier?",
    "It's pretty humid this evening.",
    "Tell me something about this porch.",
    "I might take a nap here.",
    "Do you like the rain?",
    "That mosquito coil smells nice.",
    "Good evening, Chacha.",
]


def _u(speaker, text):
    return conversation.Utterance(speaker, text)


ROOM_TRIALS = [  # 客人/私は英語（en モードの実態）・枠と話者ラベルは日本語のまま
    (conversation.REACT, [_u("客人", "Good evening. Mind if I rest here a moment?")]),
    (conversation.REPLY, [_u("客人", "This breeze is lovely."), _u("私", "Chacha, how has your day been?")]),
    (conversation.REPLY, [_u("私", "Chacha, what do you think of our guest?")]),
    (conversation.CHIME, [_u("私", "I heard the cicadas started early this year."), _u("客人", "Ah, they woke me at dawn, truly.")]),
    (conversation.REPLY, [_u("客人", "Does it always smell of mosquito coil here, Chacha?")]),
    (conversation.CHIME, [_u("客人", "I once painted a veranda just like this one."), _u("私", "Oh, you paint?")]),
    (conversation.MUSE, [_u("客人", "The evening sun sits low already.")]),
    (conversation.REPLY, [_u("私", "Chacha, should we offer our guest some tea?")]),
    (conversation.CHIME, [_u("私", "The forecast said rain tomorrow."), _u("客人", "Then I shall borrow the sky while it lasts.")]),
    (conversation.LEAVE_REACT, [_u("客人", "Well then, I ought to be going. Thank you for the seat.")]),
]


def _ctx():
    now = datetime.datetime.now()
    w = AMBIENT_WEATHER
    return {"weather": w, "desc": w["desc"], "raining": False,
            "tod": sources.time_of_day(now), "hour": now.hour, "now": now, "topics": []}


def build_prompt(cat, i):
    if cat == "ambient":
        return sources.ambient_narration(_ctx())
    if cat == "arc":
        return sources.event_narration(ARC_TEXTS[i % len(ARC_TEXTS)])
    if cat == "talk":
        return prompts.user_narration(TALK_LINES[i % len(TALK_LINES)], _ctx())
    if cat == "room":
        kind, window = ROOM_TRIALS[i % len(ROOM_TRIALS)]
        return prompts.room_resident_prompt(window, kind, _ctx())
    raise ValueError(cat)


CATEGORIES = ["ambient", "arc", "talk", "room"]


async def spawn(engine):
    if engine == "acp":
        import acp
        return await acp.AcpAgent.spawn_resident()
    if engine == "openai":
        import agent_openai
        return await agent_openai.OpenAIAgent.spawn_resident()
    raise ValueError(engine)


def jp_snippets(text):
    """残存日本語の周辺 30 文字スニペット（重複領域はまとめる）。"""
    spans = []
    for m in prompts._JP_RE.finditer(text):
        s, e = max(0, m.start() - 30), min(len(text), m.end() + 30)
        if spans and s <= spans[-1][1]:
            spans[-1] = (spans[-1][0], e)
        else:
            spans.append((s, e))
    return ["…" + text[s:e] + "…" for s, e in spans]


async def _close_quiet(ag):
    try:
        await ag.close()
    except Exception:
        pass


async def run(engine, trials, categories, sticky=False):
    session = "sticky" if sticky else "fresh"
    results = []
    for cat in categories:
        ag = None
        try:
            for i in range(trials):
                if ag is None:
                    ag = await spawn(engine)     # fresh=毎 trial／sticky=カテゴリ初回のみ（急所・docstring 参照）
                ptext = build_prompt(cat, i)
                rec = {"engine": engine,
                       "model": getattr(ag, "reported_model", None) or getattr(ag, "model", None),
                       "category": cat, "trial": i, "session": session}
                try:
                    raw = await ag.prompt(ptext)
                except agent_mod.AgentTimeoutError:
                    rec.update(status="timeout", raw="", shown="", jp=[])
                    results.append(rec)
                    print(f"[{engine}/{cat}#{i}] TIMEOUT", flush=True)
                    await _close_quiet(ag)       # timeout したセッションはどちらのモードでも作り直す
                    ag = None
                    continue
                shown = prompts.strip_resident_leak(raw, ptext)   # 本番と同じ表示前ガード
                snips = jp_snippets(shown)
                rec.update(status="ok", raw=raw, shown=shown, jp=snips)
                results.append(rec)
                flag = f"  JP! {len(snips)}" if snips else ""
                print(f"[{engine}/{cat}#{i}] {shown[:80]!r}{flag}", flush=True)
                if not sticky:
                    await _close_quiet(ag)
                    ag = None
        finally:
            if ag is not None:
                await ag.close()
    return results


def summarize(results, categories):
    print("\n===== SUMMARY =====")
    for cat in categories:
        rs = [r for r in results if r["category"] == cat]
        flagged = [r for r in rs if r.get("jp")]
        to = [r for r in rs if r["status"] == "timeout"]
        print(f"{cat:8s}: n={len(rs)}  JP-flagged={len(flagged)}  timeout={len(to)}")
    print("\n-- flagged detail --")
    for r in results:
        if r.get("jp"):
            print(f"[{r['category']}#{r['trial']}]")
            for s in r["jp"]:
                print(f"   {s}")


def main():
    args = [a for a in sys.argv[1:] if a != "--sticky"]
    sticky = "--sticky" in sys.argv[1:]
    if not args or args[0] not in ("acp", "openai"):
        sys.exit("usage: leak_probe.py <acp|openai> [trials] [categories...] [--sticky]")
    engine = args[0]
    trials = int(args[1]) if len(args) > 1 else TRIALS_DEFAULT
    categories = [c for c in args[2:] if c in CATEGORIES] or CATEGORIES
    print(f"voice={voice.label()} llm_lang={voice.llm_lang()} engine={engine} trials={trials} "
          f"cats={categories} session={'sticky' if sticky else 'fresh'}")
    results = asyncio.run(run(engine, trials, categories, sticky))
    out = OUT_DIR / f"leak_{engine}.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    summarize(results, categories)
    print(f"\nresults -> {out}")


if __name__ == "__main__":
    main()
