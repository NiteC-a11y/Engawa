# ADR-0028: 背景の昼夜は「tint 層＋補間」で表す（時間帯別 scene.png 差し替えは特別な一枚だけ）

- ステータス: Accepted（実装済み・2026-07-04）
- 日付: 2026-07-04
- 関連: ADR-0010（ドット絵＝差し替え可能な皮／背景にも拡張）, ADR-0019（presentation＝意味 state ＋ config 駆動アセット・scene を含む）, ADR-0012（実環境が真実＝住人の核）, ADR-0009（不透明な隅ウィンドウ）。CLAUDE.md「次にやること：背景の時間帯バージョン」を**実装方針として確定**（旧構想の B案＝時間帯別 `scene.png` を A案＝tint 層に置換）。
- 影響: CLAUDE.md の構想「朝/昼/夕/夜の `scene.png` を `tod` で切替」（＝B案）を **A案（1枚の色膜を時刻で lerp）に方向転換**。B案は捨てず「特別な一枚を描き込みたい時だけ」に格下げ。

## 背景 / 課題
Engawa の核は「実環境（大阪の時刻・天気）への反応」（ADR-0012）。背景も**夕暮れが実時間でにじむ**と世界観と地続きになる。当初構想（CLAUDE.md「次にやること」）は **B案＝時間帯別に `scene.png` を4枚描いて `tod` で切替**だった。だが B案は:
- 絵を朝/昼/夕/夜ぶん用意する重さ（差分は主に「色と明るさ」なのに、絵を4倍持つ）。
- 切替が**離散**（4段）＝実時間の連続的なにじみが出ない。夕→夜の2時間かけたグラデが表現できない。
- 月明かり/行灯の点灯みたいな「光の増減」を絵に焼き込むしかない。

一方 2Dゲーの世界標準解は「画像差し替えでなく **1枚の tint 層＋補間**」で揃っている（Godot の CanvasModulate＝画面全体に色の膜を1枚かけ、その色を時刻で lerp／Unity でも全画面 tint sprite）。実時間駆動の癒しゲー **Usagi Shima** が Engawa とほぼ同思想（実時間で昼夜が変わる）で、実数値もそのまま流用できる。

## 決定
**背景の昼夜は「1枚の色膜（ambient tint）＋補間（lerp）」で表す。** #scene に被せる `pointer-events:none` の膜を2枚重ねるだけ（新機構ゼロ・ADR-0010 の皮レイヤに素直に乗る）。

3レイヤ構成（世界の定番レシピ）:
1. **染めの層（ambient tint）** — 時間帯の基準色を数点キーフレームで持ち、`lerp` で連続的に混ぜる。**`mix-blend-mode:multiply`（乗算）** で敷く＝ハイライトを残して自然に暗くなる（ベタ灰の膜は"曇り"に見えて失敗する）。昼＝白（無変化）、夕＝桃の暖色、夜＝青灰の寒色。
2. **光の層（2枚）** — `mix-blend-mode:screen`（加算）＋`radial-gradient`。明るさ（opacity）を時刻で lerp。tint(乗算=暗く)の**上**に足す＝夜の闇に灯りがにじむ。
   - **月明かり（glow）**: 空の隅・寒色（青白）。昼0・夕薄・夜1.0。
   - **室内灯（lamp）**: 画面上端（＝障子/部屋のある側）からの暖色（アンバー）。「日が暮れたら灯りをつける」＝夕に点き始め・夜1.0（月より少し早く立ち上がる）。＝夜に**部屋から明かりが漏れる**。scene.png 差し替えでも壊れない位置非依存の出方（上からのウォッシュ・ADR-0010）。
3. **色理論の裏付け** — 暖色光は寒色影を落とす。ゆえに夕は桃の暖色・夜は青灰の寒色＝physically 自然。月＝寒色／家の灯り＝暖色の対比も同じ理屈。

- **時刻→色は backend の純関数**（`src/daynight.py` の `layers(now)`）。View が `datetime.now()` を渡し、poll が `font`/`absent` と同じ持続フラグ方式で毎回 `{tint, glow, lamp}` を返す。JS は膜3枚に適用するだけ。**大阪時刻＝単一情報源**（環境反応の核と地続き）＋**純関数ゆえ unittest 可**（原則6）。
- **膜は #scene 内に閉じる**（`isolation:isolate`）＝乗算/加算が窓の外（暗い地）に漏れない。茶々スプライトも接地影も葉の気配も**一緒に**夜色になる（膜が上に乗る）。UI（×/リサイズ/ニャー吹き出し）は膜より前面＝染めない。
- **config で on/off**（`ENGAWA_DAYNIGHT`・既定 1）。0 で無効＝従来の固定背景（自前で照明を焼いた scene.png を使う人／目に負担な人向け）。実数値のキーフレームはコード内（皮でなく機構）。

## なぜ B案（時間帯別 scene.png）を捨てないか
A案（multiply+lerp+加算月光）を素直にやれば、**絵1枚で朝昼夕夜＋月明かり**まで出る。B案の「絵に描き込める」利点（特別なワンシーン・イベント専用背景）は、ADR-0010 の差し替え口（`ENGAWA_SCENE_BG`）でいつでも一枚差せる＝残る。世界の答えも「**まず tint 層でやれ、画像は特別な時だけ**」。よって B案は却下でなく格下げ（特別な一枚専用）。

## 検討した代替案
- **B案＝時間帯別 scene.png を `tod` で切替**: 絵4倍・離散切替・光の増減を焼き込むしかない。却下（tint 層に置換・特別な一枚用途に格下げ）。
- **tint を JS の `new Date()` だけで完結（backend 非経由）**: 同一マシンなので値は一致するが、時刻の単一情報源（大阪時刻）を backend に集約する方が環境反応の核と整合＋純関数を unittest できる。よって backend 算出＋poll 配信を採用。
- **ベタ塗り（normal ブレンドの灰膜）で暗くする**: "曇り"に見えて失敗する（世界の定番が multiply を勧める理由）。却下。
- **染めと光を1枚に合成**: 乗算（暗く）と加算（光）はブレンドが逆＝分けないと両立しない。2枚に分離。

## 影響 / 帰結
- **新 `src/daynight.py`**: キーフレーム表＋`lerp`＋`layers(now)`（純関数・`{tint:"rgb(...)", glow:0..1, lamp:0..1}` を返す）。実数値の起点は Usagi Shima（夕 `rgb(252,217,191)`／夜 `rgb(99,130,163)`／月明かり glow・室内灯 lamp とも 昼0・夕薄・夜1.0）。
- **`views.py`**: WEB_HTML に膜3枚（`#tint` 乗算・`#lamp` 加算室内灯［上端の暖色ウォッシュ］・`#glow` 加算月光［隅の寒色］）＋`#scene{isolation:isolate}`。`WebView.poll` が `layers(datetime.now())`（override 時は仮想時刻）を毎回載せ、JS `applyDay()` が適用（`ENGAWA_DAYNIGHT=0` で無効＝tint 無し）。
- **config**: `ENGAWA_DAYNIGHT`（既定1・`engawa.json[ui].daynight` 可）。ADR-0002 の枠内（認証・課金に無関係）。
- **`/daynight` コマンド**（デバッグ再生＝`/arc` と同筋・ADR-0007 の縁側操作＝茶々に流さない）＝機能の on/off トグルとプレビューを兼ねる:
  - **プレビュー**（実時間だと夕→夜を見るのに待つので）: `/daynight HH:MM`＝固定、`/daynight demo [from to secs]`＝早送り（既定 16:00→22:00 を40秒→終わったら実時間へ自動復帰）、`/daynight auto`(=now/real)＝プレビュー解除して実時間へ。一時的＝保存しない。
  - **機能 on/off（永続）**: `/daynight on|off`＝機能を有効無効にして `engawa.json[ui].daynight` へ保存（ライブ反映・`/font save` と同じ明示保存方式・`ENGAWA_DAYNIGHT` env が立てば次回 env 優先を告知）。7/5 ユーザー要望「移ろいを設定で制御したい」に応える in-app 口。トグルはプレビューを実時間へリセット（前の固定を残さない）。無効中のプレビューは見えないので `on` を促す。
  - **仮想時刻の解決は純関数に切り出す**（`daynight.parse_override`/`override_minute`/`effective_layers`／`layers_for_minute`／`format_minute`）＝clock を持たず「real now ＋ demo 開始からの経過秒」だけで判断＝unittest 可（原則6）。View は override spec と `_t0`(monotonic)＋enabled フラグを持ち、poll が `effective_layers` の `expired` 合図で override を外す。`ENGAWA_DAYNIGHT=0`（または `/daynight off`）なら override 有無に関わらず `day=None`＝無効。
- **テスト**: `daynight.layers` の純関数（昼＝白/glow0・夜＝青灰/glow1・夕＝桃・全分の値域＝tint∈0..255 / glow∈0..1・0:00↔23:59 連続）＋override 純関数（parse_override/parse_time/override_minute の進行と終了/effective_layers の pin・demo中・demo終了→実時間+expired）＋View 配線（pin で poll が固定色・auto で実時間・demo secs=0 で自動復帰・enabled トグルで poll ゲート・機能オフで無視）＋`/daynight` コマンド（固定/早送り告知/実時間/不正/状態表示＋`on|off` でライブ＆`engawa.json` 保存＋無効中プレビューは促す・茶々に流さない）。**見た目はユーザー目視**（Chrome 拡張未接続でこちらから描画確認不可・原則の GUI 運用どおり・`/daynight demo` で待たず確認できる）。
- **行灯（室内灯 lamp）＝2026-07-05 実装済み**（3枚目の光レイヤ・上記「光の層（2枚）」）。ユーザー要望「夜は部屋から明かりが漏れて欲しい」に応え、キーフレームに `lamp` チャンネルを足し `#lamp`（上端の暖色 screen）を追加。位置非依存の障子ごしウォッシュで採用（皮＝scene.png 差し替えに強い・ADR-0010）。
- **将来（任意）**: 背景の時間帯"特別絵"は `ENGAWA_SCENE_BG` で随時。天気（曇り＝彩度を落とす等）を tint に効かせるのも同機構で自然（ADR-0012 と地続き）。室内灯を障子の桟に沿った縦帯にする等の作り込み。
