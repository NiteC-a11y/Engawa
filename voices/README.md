# Making your own voice for Chacha（茶々の「声」を自作する）

Engawa's resident cat Chacha can speak in your language — or your dialect — with **zero code
changes and no pull request**. A *voice* is just a folder. This guide is the whole procedure.
(設計は ADR-0022/0033。日本語既定のままでも動くので、部分的に始めて構いません。)

## 1. Voice vs locale — where things live

- A **voice** is a character rendition: persona (how Chacha talks) + UI strings. `ja-osaka` and a
  hypothetical `ja-kyoto` are the *same locale, different voices* — that's why voices are the
  top-level concept here.
- **Keys are defined in `locales/strings.json`** (the registry: every key + its Japanese default).
  **Translations and personas live in `voices/<id>/`** (your overrides). Never invent keys in your
  bundle — the registry is the catalog.

## 2. Quick start

```
1. Copy voices/_template/  →  voices/<your-id>/          (e.g. voices/fr/)
2. Edit meta.json    — label, llm_lang (see §3)
3. Write persona.md  — Chacha's voice in your language (see §4)
4. Edit strings.json — replace the Japanese values with your translations
5. Check gaps:   python tools/voice_lint.py <your-id>
6. Run:          set "ENGAWA_VOICE=<your-id>" && python src/engawa_main.py
   (or copy engawa-en.bat and change the ENGAWA_VOICE line)
```

`voices/_template/` itself is not a runnable voice — it is a generated copy of the registry.

## 3. meta.json

```json
{ "label": "Français", "llm_lang": "fr" }
```

- `label` — shown in the boot line (`voice=<label>`). Required in practice.
- `llm_lang` — language code (`en`, `fr`, …). This is what makes Chacha *speak* your language:
  it appends one instruction line to every LLM injection. Omit it for Japanese dialects
  (the injections stay byte-identical then).
- `base` — optional, **one level only**: inherit `strings.json`/`persona.md` from another voice id
  (e.g. a regional variant on top of `en`). It is *not* a language marker. Self-reference and
  missing bases are errors; a base that itself has a base is ignored beyond one level (lint warns).

## 4. persona.md — the voice itself

Transcreate, don't machine-translate. The Japanese original (`src/persona.py`) anchors Chacha's
tone in casual Kansai dialect; your job is to find the equivalent *texture* in your language
(see `voices/en/persona.md`: "casual, warm, unhurried — like someone half-dozing in the afternoon
sun"). Keep the structural rules: not an assistant, short murmurs, one continuous line, "……" is a
valid answer, never self-introduces as an AI.

## 5. strings.json

- UTF-8. Values are plain strings; emoji and leading spaces are part of the string (help lines
  align with leading spaces — keep them).
- **Placeholders like `{tag}`, `{cmd}`, `{persona}` must survive translation** — same set, same
  names. lint flags mismatches; a dropped placeholder breaks at runtime.
- An **empty string counts as missing** (falls back to Japanese) — don't use `""` to "disable" a line.
- If your translation is intentionally identical to the Japanese default, that's fine — lint lists
  it as `same-as-default` for review, not as an error.
- `_comment` is ignored.

## 6. culture.json — place & guest roles

```json
{
  "place": "Osaka",
  "guest_personas": [
    { "id": "peddler", "display": "a whimsical traveling peddler" },
    ...
  ]
}
```

- `place` — the place name Chacha mentions with the weather ("Osaka is cloudy…"). A user's
  `ENGAWA_PLACE_LABEL` setting always wins over this; you provide the default for your voice.
- `guest_personas` — the pool for spontaneous guest visits. **Keep the `id` values exactly as in
  `locales/culture.json`** (stable identifiers used for topic matching — never translate them);
  translate only `display`. See `voices/en/culture.json` for a complete example.
- Seasonal/weather vocabulary is *not* bundle data yet (measured to be a non-issue: Chacha never
  recites the weather term verbatim; documented in ADR-0022/0033).

## 7. Checking your work: voice_lint

```
python tools/voice_lint.py <your-id>
```

States per key: `translated` / `inherited-from-base` / `same-as-default` (review) /
`missing` (falls back to Japanese) / `unknown` (not in the registry — typo?).
Exit codes: `0` complete (no missing/unknown, placeholders OK) / `1` findings / `2` broken bundle.

## 8. Partial translation is normal

Missing keys **fall back to the Japanese default** — the app never breaks because of an
incomplete bundle. "Complete" (lint exit 0) is a goal for shipping, not a requirement for running.

## 9. Try it for real — checklist

- Boot line shows your `label`; talk to Chacha — she answers in your language.
- `/help`, the input placeholder, Send button, addressee chips ("To …") are translated.
- `/codex someone` — guest arrives; speaker labels and colors look right; Chacha stays in character.
- Leave it idle a few minutes — her *solo murmurs* are in your language too (this was once a bug).
- Console mode too, if you use it: `python src/engawa_main.py`.

## 10. Common failures & distribution

- **A key shows up literally on screen** (e.g. `boot_title`): registry missing/broken — run from the
  repo root, or the packaged `locales/` folder is missing.
- **Your translation doesn't appear**: `unknown` key (typo), or empty string, or broken JSON —
  run lint, it names the problem.
- **Guest names / seasonal topics still Japanese**: expected until culture.json (§6).
- Distribute your voice as the folder itself — users drop it into `voices/` (or point
  `ENGAWA_VOICES_DIR` anywhere). No code changes, no PR, no rebuild.
