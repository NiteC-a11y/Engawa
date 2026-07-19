#!/usr/bin/env python3
"""voice_lint.py — voice バンドルの著者向け lint（ADR-0033 決定8・追補12/14/18/20）。

graceful fallback（未訳→日本語）は部分導入には優しいが、完訳したい著者に漏れを教えない——
その「見える化」を担う道具。開発側の番人は CI の掃引テスト（tests/test_ui_surfaces.py）＝これは CI に載せない。

使い方（repo root から）: python tools/voice_lint.py <voice-id> [--voices-dir DIR]
例: python tools/voice_lint.py en

キーごとの状態（追補12）:
  missing            … 自分にも base にも無い＝日本語既定に落ちる
  inherited-from-base… base の訳で満たされている（自分では未定義）
  same-as-default    … 日本語既定と同値＝「訳し忘れのコピー残し」か「意図した同値」か要確認（エラーではない）
  translated         … 自分の訳で上書き済み
  unknown            … 台帳に無いキー＝訳しても使われない（typo を疑う）

exit code（機械利用用・追補20）: 0=完訳（missing=0 かつ unknown=0）／1=指摘あり／2=バンドル不成立（無い/壊れ）
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
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


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


def lint_bundle(vid, voices_dir, registry):
    """バンドルを検査して report dict を返す純関数（CLI 出力とテストが共用）。
    registry: 台帳 dict（_comment 除去済み・キー→日本語既定）。"""
    r = {"voice": vid, "errors": [], "warnings": [], "states": {}, "placeholder_mismatch": []}
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
    base_strings = {}
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
            bs, err = _read_json(os.path.join(voices_dir, base, "strings.json"))
            base_strings = bs or {}

    own, own_err = _read_json(os.path.join(d, "strings.json"))
    if own_err and own_err != "無い":
        r["errors"].append(f"strings.json が{own_err}")
        return r
    own = own or {}

    for key, default in registry.items():
        val = own.get(key)
        if isinstance(val, str) and val:
            r["states"][key] = "same-as-default" if val == default else "translated"
            if _placeholders(val) != _placeholders(default):
                r["placeholder_mismatch"].append(
                    (key, sorted(_placeholders(default)), sorted(_placeholders(val))))
        elif isinstance(base_strings.get(key), str) and base_strings.get(key):
            r["states"][key] = "inherited-from-base"
        else:
            r["states"][key] = "missing"
    for key in own:
        if key != "_comment" and key not in registry:
            r["states"][key] = "unknown"
    return r


def _counts(report):
    c = {}
    for st in report["states"].values():
        c[st] = c.get(st, 0) + 1
    return c


def render_report(report):
    """人間可読の表（追補20・--json は実需が出るまで作らない）。"""
    lines = [f"voice_lint: voices/{report['voice']}"]
    for e in report["errors"]:
        lines.append(f"  [error] {e}")
    for w in report["warnings"]:
        lines.append(f"  [warn]  {w}")
    if report["states"]:
        c = _counts(report)
        lines.append("  keys: " + "  ".join(f"{st}={c.get(st, 0)}" for st in
                     ("translated", "inherited-from-base", "same-as-default", "missing", "unknown")))
        for st, note in (("missing", "→ 日本語既定に落ちる（訳すならここから）"),
                         ("unknown", "→ 台帳に無い＝使われない（typo を疑う）"),
                         ("same-as-default", "→ 既定と同値（意図した同値なら OK・コピー残しなら訳す）")):
            keys = sorted(k for k, s in report["states"].items() if s == st)
            if keys:
                lines.append(f"  {st} {note}:")
                for k in keys:
                    lines.append(f"    - {k}")
    for key, want, got in report["placeholder_mismatch"]:
        lines.append(f"  [warn]  '{key}' の placeholder が既定と不一致: 既定{want} / 訳{got}（実行時に壊れる）")
    return "\n".join(lines)


def exit_code(report):
    if report["errors"]:
        return 2
    c = _counts(report)
    if c.get("missing", 0) == 0 and c.get("unknown", 0) == 0 and not report["placeholder_mismatch"]:
        return 0
    return 1


def main(argv=None):
    import voice
    ap = argparse.ArgumentParser(description="voice バンドルの未訳/未知キーを見える化（ADR-0033）")
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
        print("  ✓ 完訳（missing=0・unknown=0・placeholder 一致）")
    return code


if __name__ == "__main__":
    sys.exit(main())
