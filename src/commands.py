#!/usr/bin/env python3
"""commands.py — スラッシュコマンドの Command パターン（ADR-0029 Phase 1）。

`Scheduler._command` の if/elif 増殖と、help テキストが定義から分離してズレる問題を止める。
各コマンドは `names`（別名込み）と `run(ctx, line, parts)` を持ち、`CommandRouter` が名前で振り分ける。
`ctx` は薄い adapter（今は View だけ）＝controller が出来たら足す（先取りで作り込まない・Codex 第2R）。

第一 PR は縁側操作のうち **無結合な `/font` と `/daynight` だけ** を移す（view＋config だけに依存）。
他の /model /restart /game /codex /arc は依存 controller が出来てから（ADR-0029 抽出順）。
いずれも縁側への操作＝茶々に流さない（ADR-0007）。ロジックは Scheduler から verbatim 移設＝振る舞い不変。
"""
import os

import config
import daynight

FONT_MIN, FONT_MAX = 0.8, 2.2   # /font の文字倍率クランプ（正本。engawa_main._ui_config / scheduler の再輸出と揃える）


class CommandContext:
    """コマンドに渡す薄い依存束（ADR-0029）。今は View だけ。controller が出来たら足す。"""
    def __init__(self, view):
        self.view = view


class Command:
    names = ()                                   # 反応するコマンド名（別名込み・先頭 / つき）

    async def run(self, ctx, line, parts):       # parts=line.split() / args は parts[1:]
        raise NotImplementedError


class CommandRouter:
    """コマンド名→ハンドラの登録制ディスパッチャ。未登録は has()=False で呼び側にフォールバックさせる。"""
    def __init__(self, command_list):
        self._by_name = {name: cmd for cmd in command_list for name in cmd.names}

    def has(self, name):
        return name.lower() in self._by_name

    async def dispatch(self, ctx, line, parts):
        cmd = self._by_name.get(parts[0].lower())
        if cmd is None:
            return False
        await cmd.run(ctx, line, parts)
        return True


class FontCommand(Command):
    """/font: 文字サイズをアプリ内でライブ調整（明示保存方式・ADR-0007／Backlog P5）。
    引数なし=今の倍率を表示／数字=その倍率にライブ適用（このセッション）／save=engawa.json[ui].font に保存。
    web 表示だけの設定＝console は端末フォント依存なので no-op（注記のみ）。"""
    names = ("/font",)

    async def run(self, ctx, line, parts):
        view = ctx.view
        args = parts[1:]
        cur = view.current_font()
        if cur is None:                                  # set_font 非対応（console 等）
            view.system("  文字サイズは web 表示だけの設定や（console は端末のフォントで変えてな）。")
            return
        if not args:                                     # /font ＝今の値を表示
            view.system(f"  今の文字サイズ: {cur:g} 倍（/font 1.4 で変更・/font save で保存）")
            return
        if args[0] in ("save", "保存"):                  # /font save ＝今の倍率を engawa.json に永続化
            if config.set_value("ui", "font", round(cur, 3)):
                msg = f"  文字サイズ {cur:g} 倍を保存した（次からもこの大きさ）。"
                if "ENGAWA_UI_FONT" in os.environ:       # env が優先＝次回もそちらが効く（正直に告知）
                    msg += " ※ただし環境変数 ENGAWA_UI_FONT が立っとるので次回はそっちが優先や。"
                view.system(msg)
            else:
                view.system("  保存できんかった（engawa.json に書けん）。")
            return
        try:                                             # /font <倍率> ＝ライブ適用
            n = float(args[0])
        except ValueError:
            view.system(f"  文字サイズは数字で（例 /font 1.4）。今は {cur:g} 倍。")
            return
        n = max(FONT_MIN, min(FONT_MAX, n))              # 0.8〜2.2 にクランプ
        view.set_font(n)
        view.system(f"  文字サイズを {n:g} 倍にした（/font save で次回も）。")


class DayNightCommand(Command):
    """/daynight: 背景の昼夜 tint の on/off（永続）とプレビュー（デバッグ再生＝/arc と同筋・ADR-0028/0029）。
    on|off=機能の有効無効を engawa.json[ui].daynight に保存（ライブ反映・/font save 方式）／HH:MM=時刻固定／
    demo [from to secs]=夕→夜早送り／auto=プレビュー解除して実時間へ／引数なし=状態表示。
    web 表示だけ＝console は縁側の窓が無いので no-op。解析・時刻表示は daynight の純関数。"""
    names = ("/daynight", "/tod", "/空")

    async def run(self, ctx, line, parts):
        view = ctx.view
        args = parts[1:]
        cur = view.current_daynight()
        if cur is None:                                  # 非対応（console 等＝背景が無い）
            view.system("  背景の昼夜は web の縁側窓だけの見た目や（console には空が無いんよ）。")
            return
        spec = daynight.parse_override(" ".join(args))
        mode = spec["mode"]
        enabled = view.daynight_enabled()
        if mode in ("enable", "disable"):                # 機能そのものの on/off＝永続保存（/font save と同じ流儀）
            on = mode == "enable"
            view.set_daynight_enabled(on)                # ライブ反映（トグルは実時間へリセット）
            state = "有効" if on else "無効"
            if config.set_value("ui", "daynight", 1 if on else 0):
                msg = f"  背景の移ろいを{state}にして保存した（次からもこの状態）。"
                if "ENGAWA_DAYNIGHT" in os.environ:      # env が優先＝次回もそちらが効く（正直に告知・/font save と同じ）
                    msg += " ※環境変数 ENGAWA_DAYNIGHT が立っとるので次回はそっちが優先や。"
            else:
                msg = f"  背景の移ろいを{state}にした（ただし engawa.json に保存できんかった＝次回は既定に戻る）。"
            view.system(msg)
            return
        if mode == "show":                               # /daynight ＝今の状態
            head = "有効" if enabled else "無効"
            if not enabled:
                view.system(f"  背景の移ろいは今 {head}（/daynight on で有効化）。")
            elif cur["mode"] == "pin":
                view.system(f"  {head}・今は {daynight.format_minute(cur['minute'])} に固定中（/daynight auto で実時間へ）。")
            elif cur["mode"] == "demo":
                view.system(f"  {head}・今は夕→夜の早送り再生中（/daynight auto で実時間へ）。")
            else:
                view.system(f"  {head}・実時間の空や（/daynight 18:30=固定・/daynight demo=早送り・/daynight off=無効化）。")
            return
        if mode == "bad":
            view.system("  使い方: /daynight on|off（有効無効を保存）・HH:MM（固定）・demo（夕→夜早送り）・auto（実時間へ）。")
            return
        if not enabled and mode in ("pin", "demo"):      # 無効中はプレビューしても見えない＝促す
            view.system("  今は背景の移ろいが無効や（/daynight on で有効にしてから見てな）。")
            return
        view.set_daynight(spec)                          # auto/pin/demo＝プレビュー（一時・保存しない）
        if mode == "auto":
            view.system("  空を実時間に戻した。")
        elif mode == "pin":
            view.system(f"  空を {daynight.format_minute(spec['minute'])} の色に固定した（/daynight auto で実時間へ）。")
        else:                                            # demo
            view.system(f"  {daynight.format_minute(spec['from'])}→{daynight.format_minute(spec['to'])} の移ろいを {spec['secs']:g} 秒で流すで（終わったら実時間に戻る）。")


def default_router():
    """第一 PR で移す縁側操作コマンド（font/daynight）の Router。追加は controller 抽出に合わせて。"""
    return CommandRouter([FontCommand(), DayNightCommand()])
