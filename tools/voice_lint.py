#!/usr/bin/env python3
"""voice_lint.py — voice バンドルの著者向け lint（ADR-0033 決定8・追補12/14/18/20・codex 7/19 反映）。

graceful fallback（未訳→日本語）は部分導入には優しいが、完訳したい著者に漏れを教えない——
その「見える化」を担う道具。開発側の番人は CI の掃引テスト（tests/test_ui_surfaces.py）＝これは CI に載せない。

使い方（repo root から）: python tools/voice_lint.py <voice-id> [--voices-dir DIR]
例: python tools/voice_lint.py en

strings のキーごとの状態（追補12）:
  missing            … 自分にも base にも無い＝日本語既定に落ちる
  inherited-from-base… base の訳で満たされている（自分では未定義）
  same-as-default    … 日本語既定と同値＝「訳し忘れのコピー残し」か「意図した同値」か要確認（エラーではない）
  translated         … 自分の訳で上書き済み
  unknown            … 台帳に無いキー＝訳しても使われない（typo を疑う）

culture（Inc4・codex[中]④）: 解決後の place と役 id 一式も検査＝culture 欠損/壊れ/id 欠落は
静かに日本語既定へ fallback するので findings に出す。

exit code（機械利用用・追補20）: 0=**voice 全体の完訳**（strings: missing/unknown/format/placeholder ゼロ
かつ culture: place 定義済み＋基準 id 全掲載）／1=指摘あり／2=バンドル不成立（無い/壊れ＝base・culture 含む）
"""
import argparse
import json
import os
import string
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def _placeholders(text):
    """(placeholder名集合, None) か (None, 構文エラー文字列)。壊れた format 文字列で lint を落とさない
    （著者配布物は信頼できない入力・codex[中]②）。"""
    try:
        return {name for _, name, _, _ in string.Formatter().parse(text) if name}, None
    except ValueError as e:
        return None, str(e)


def _read_json(path):
    """lint はローダーと違い失敗を報告したい＝例外を (None, err) で返す。"""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None, "root が dict でない"
        return data, None
    except FileNotFoundError:
        return None, "無い"
    except ValueError as e:
        return None, f"JSON が壊れている: {e}"


def _check_value(report, key, val, default, origin):
    """訳値1件の placeholder/format 検査（own/base 共通・codex[中]①）。"""
    got, err = _placeholders(val)
    if err:
        report["format_errors"].append((key, origin, err))
        return
    want, werr = _placeholders(default)
    if werr:                                             # 台帳側の壊れは CI が守る＝念のため素通し
        return
    if got != want:
        report["placeholder_mismatch"].append((key, sorted(want), sorted(got), origin))


def _lint_strings(report, d, voices_dir, base, registry):
    base_strings = {}
    if base:
        bs, err = _read_json(os.path.join(voices_dir, base, "strings.json"))
        if err and err != "無い":
            report["errors"].append(f"base '{base}' の strings.json が{err}")   # 継承元の壊れ＝不成立（codex[中]①）
            return
        base_strings = bs or {}

    own, own_err = _read_json(os.path.join(d, "strings.json"))
    if own_err and own_err != "無い":
        report["errors"].append(f"strings.json が{own_err}")
        return
    own = own or {}

    for key, default in registry.items():
        val = own.get(key)
        bval = base_strings.get(key)
        if isinstance(val, str) and val:
            report["states"][key] = "same-as-default" if val == default else "translated"
            _check_value(report, key, val, default, "own")
        elif isinstance(bval, str) and bval:
            report["states"][key] = "inherited-from-base"
            _check_value(report, key, bval, default, "base")   # 継承値も実行時に使われる＝検査対象
        else:
            report["states"][key] = "missing"
    for key in own:
        if key != "_comment" and key not in registry:
            report["states"][key] = "unknown"


def _lint_culture(report, d, voices_dir, base):
    """culture の解決後検査（codex[中]④）: place と基準 id 一式が voice/base で満たされているか。
    欠け＝静かな日本語 fallback を findings に出す。部分導入は許す（exit 1 止まり・壊れだけ exit 2）。"""
    import voice
    canonical = voice._load_culture_canonical()
    ids = [p["id"] for p in (canonical.get("guest_personas") or voice._BUILTIN_PERSONAS)]
    fallback_place = canonical.get("place") or voice._BUILTIN_PLACE

    def load(vdir, who):
        raw, err = _read_json(os.path.join(vdir, "culture.json"))
        if err == "無い":
            return {}, None
        if err:
            report["errors"].append(f"{who} の culture.json が{err}")
            return {}, err
        clean = voice._clean_culture(raw)
        n_raw = len(raw.get("guest_personas") or []) if isinstance(raw.get("guest_personas"), list) else 0
        n_ok = len(clean.get("guest_personas") or [])
        if n_raw and n_ok < n_raw:
            report["culture_findings"].append(f"{who} の guest_personas に不正な要素 {n_raw - n_ok} 件（id/display が str でない等＝棄却）")
        if isinstance(raw.get("place"), str) is False and "place" in raw:
            report["culture_findings"].append(f"{who} の place が文字列でない（棄却→fallback）")
        own_ids = [p["id"] for p in (clean.get("guest_personas") or [])]
        dup = {i for i in own_ids if own_ids.count(i) > 1}
        if dup:
            report["culture_findings"].append(f"{who} の役 id が重複: {sorted(dup)}")
        unknown = [i for i in own_ids if i not in ids]
        if unknown:
            report["culture_findings"].append(f"{who} に基準に無い役 id: {unknown}（locales/culture.json が正本・topic 照合に使われない）")
        return clean, None

    merged = {}
    if base:
        bclean, _ = load(os.path.join(voices_dir, base), f"base '{base}'")
        merged.update(bclean)
    oclean, _ = load(d, "この voice")
    merged.update(oclean)

    if not merged.get("place"):
        report["culture_findings"].append(f"place 未定義＝「{fallback_place}」に fallback（culture.json に place を書く）")
    covered = {p["id"] for p in (merged.get("guest_personas") or [])}
    missing = [i for i in ids if i not in covered]
    if missing:
        report["culture_findings"].append(f"役名 display 未定義の id: {missing}＝日本語既定に fallback")
    report["culture"] = {"place": merged.get("place") or f"(fallback: {fallback_place})",
                         "personas_covered": f"{len(ids) - len(missing)}/{len(ids)}"}


def lint_bundle(vid, voices_dir, registry):
    """バンドルを検査して report dict を返す純関数（CLI 出力とテストが共用）。
    registry: 台帳 dict（_comment 除去済み・キー→日本語既定）。"""
    r = {"voice": vid, "errors": [], "warnings": [], "states": {},
         "placeholder_mismatch": [], "format_errors": [], "culture_findings": [], "culture": {}}
    d = os.path.join(voices_dir, vid)
    if not os.path.isdir(d):
        r["errors"].append(f"voices/{vid} が無い")
        return r

    meta, meta_err = _read_json(os.path.join(d, "meta.json"))
    if meta_err:
        r["warnings"].append(f"meta.json が{meta_err}（label/llm_lang/base を書ける）")
        meta = {}
    if not (meta.get("label") or "").strip():
        r["warnings"].append("meta.label が無い（起動行の 声=<label> 表示に使う）")
    if not os.path.exists(os.path.join(d, "persona.md")):
        r["warnings"].append("persona.md が無い（茶々の声の本体。base か組み込み日本語 persona に落ちる）")

    base = (meta.get("base") or "").strip() or None
    if base:                                                     # base は一段限定（追補14）
        if base == vid:
            r["errors"].append(f"meta.base が自己参照（{vid}）")
            base = None
        elif not os.path.isdir(os.path.join(voices_dir, base)):
            r["errors"].append(f"meta.base '{base}' が voices/ に無い")
            base = None
        else:
            bmeta, _ = _read_json(os.path.join(voices_dir, base, "meta.json"))
            if bmeta and (bmeta.get("base") or "").strip():
                r["warnings"].append(f"base '{base}' がさらに base を持つ（継承は一段限定＝孫の strings は効かない）")

    _lint_strings(r, d, voices_dir, base, registry)
    if not r["errors"]:
        _lint_culture(r, d, voices_dir, base)
    return r


def _counts(report):
    c = {}
    for st in report["states"].values():
        c[st] = c.get(st, 0) + 1
    return c


def render_report(report):
    """人間可読の表（追補20・--json は実需が出るまで作らない）。strings と culture を分けて表示。"""
    lines = [f"voice_lint: voices/{report['voice']}"]
    for e in report["errors"]:
        lines.append(f"  [error] {e}")
    for w in report["warnings"]:
        lines.append(f"  [warn]  {w}")
    if report["states"]:
        c = _counts(report)
        lines.append("  strings: " + "  ".join(f"{st}={c.get(st, 0)}" for st in
                     ("translated", "inherited-from-base", "same-as-default", "missing", "unknown")))
        for st, note in (("missing", "→ 日本語既定に落ちる（訳すならここから）"),
                         ("unknown", "→ 台帳に無い＝使われない（typo を疑う）"),
                         ("same-as-default", "→ 既定と同値（意図した同値なら OK・コピー残しなら訳す）")):
            keys = sorted(k for k, s in report["states"].items() if s == st)
            if keys:
                lines.append(f"  {st} {note}:")
                for k in keys:
                    lines.append(f"    - {k}")
    for key, origin, err in report["format_errors"]:
        lines.append(f"  [warn]  '{key}'（{origin}）の format 文字列が壊れている: {err}")
    for key, want, got, origin in report["placeholder_mismatch"]:
        lines.append(f"  [warn]  '{key}'（{origin}）の placeholder が既定と不一致: 既定{want} / 訳{got}（実行時に壊れる）")
    if report["culture"]:
        lines.append(f"  culture: place={report['culture']['place']}  役名={report['culture']['personas_covered']}")
    for f in report["culture_findings"]:
        lines.append(f"  [culture] {f}")
    return "\n".join(lines)


def exit_code(report):
    if report["errors"]:
        return 2
    c = _counts(report)
    clean = (c.get("missing", 0) == 0 and c.get("unknown", 0) == 0
             and not report["placeholder_mismatch"] and not report["format_errors"]
             and not report["culture_findings"])
    return 0 if clean else 1


def main(argv=None):
    import voice
    ap = argparse.ArgumentParser(description="voice バンドルの未訳/未知キー/culture 欠けを見える化（ADR-0033）")
    ap.add_argument("voice_id")
    ap.add_argument("--voices-dir", default=None)
    args = ap.parse_args(argv)
    registry, state = voice._load_registry()
    if state != "ok":
        print(f"[error] locales/strings.json が読めない（{state}）。repo root から実行しているか確認。")
        return 2
    report = lint_bundle(args.voice_id, args.voices_dir or voice._voices_dir(), registry)
    print(render_report(report))
    code = exit_code(report)
    if code == 0:
        print("  ✓ 完訳（strings: missing/unknown/placeholder ゼロ・culture: place＋役名 完備）")
    return code


if __name__ == "__main__":
    sys.exit(main())
