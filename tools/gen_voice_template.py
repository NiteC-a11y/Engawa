#!/usr/bin/env python3
"""gen_voice_template.py — 台帳（locales/strings.json）から著者向け雛形 voices/_template/strings.json を生成
（ADR-0033 決定3/6）。

雛形は**チェックイン**する（clone 後すぐコピーできる・diff で見える）。台帳を変えたらこれを再実行して
コミットする＝「雛形＝台帳から再生成した結果と一致」は tests/test_strings_registry.py が二段
（意味＋バイト）で検証し、忘れると赤になる。

使い方（repo root から）: python tools/gen_voice_template.py
出力は決定的（UTF-8・LF・indent 2・キー順=台帳順）＝バイト一致テストが成立する。
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

_TEMPLATE_COMMENT = (
    "Voice bundle template (ADR-0033). Copy this folder to voices/<your-id>/, then: "
    "1) replace each value below with your translation/transcreation (values are the Japanese defaults); "
    "2) write meta.json (label / llm_lang / optional base) and persona.md (Chacha's voice — transcreate, don't machine-translate); "
    "3) check gaps with `python tools/voice_lint.py <your-id>`; 4) run with ENGAWA_VOICE=<your-id>. "
    "Keys are defined in locales/strings.json (do not invent new keys here). "
    "Partial translation is fine — missing keys fall back to Japanese."
)


def render(registry: dict) -> str:
    """台帳 dict → 雛形 JSON 文字列（決定的・末尾改行つき）。_comment は著者向け手順に差し替え。"""
    out = {"_comment": _TEMPLATE_COMMENT}
    for k, v in registry.items():
        if k != "_comment":
            out[k] = v
    return json.dumps(out, ensure_ascii=False, indent=2) + "\n"


def main():
    reg_path = ROOT / "locales" / "strings.json"
    registry = json.loads(reg_path.read_text(encoding="utf-8"))
    dst = ROOT / "voices" / "_template" / "strings.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(render(registry))
    print(f"wrote {dst} ({len(registry) - 1} keys)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
