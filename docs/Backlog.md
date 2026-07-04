# Backlog — Engawa（縁側）

> 「これからやる動くタスク」だけを置く。確定事項は spec / adr / TECH_RULES にある。
> このファイルは未確定・未着手の在庫リスト。優先度なしのフラットなリスト。

---

## P3 — 双方向（実装済み・実機検証）
- [x] 話しかけて文脈が継続するか（「さっきの夕日」が通じるか＝長命セッション）（自動検証OK 6/27：前ターンの夕日を明示的に想起）
- [x] つぶやき中に割り込んだら止まってこっち向くか（session/cancel が実機で効くか）（自動検証OK 6/27：ambient が stopReason=cancelled で畳まれ、user ターンが主導権を取りハングなし）
- [x] 会話直後しばらく茶々が独り言で邪魔してこないか（QUIET_AFTER_USER）（自動検証OK 6/27：6s窓内 ambient 発火ゼロ→7.2sで再開）
- [x] 割り込み時の partial 出力（途中まで喋ったつぶやき）の見え方を確認（機構は確認。実機は初トークンまで ~1s 遅延あり、割り込みは「喋り出す前」に着弾しがち）
- [x] 〔見た目の小ネタ〕初トークン前に割り込むと空の「茶々 › 」行が残ってから turn-around マーカーが出る（6/27 修正）
  - 直し: `ConsoleView` のヘッダを遅延出力化＝`turn_start` では出さず、**最初の可視文字が来た時に初めて出す**。0チャンク（割り込み）や空白のみのターンはヘッダごと出ない（ANSI 消去に頼らずコンソール非依存）。改行畳み（6/27・persona「ひと続き」担保）と同じ状態機械に統合
  - 検証: 13件 PASS（割り込みシーケンスの出力 byte 一致＝marker→ユーザー返答のみ・空ヘッダ無し）
- [x] スラッシュ未実装コマンドの応答が自然か（/codex スタブ、/help、/quit）（自動検証OK 6/27）
- [x] **/arc 再生中に割り込みが効かない不具合（実装6/29・ユーザー報告）**＝`_play_arc_now` が `while True` でアーク完走までブロックしていて、その間 `on_user_input` が返らず入力ループが空かない＝再生中の話しかけが完走後まで処理されなかった（「[茶々がこちらを向いた]」が出ない）。**tick 駆動の `self.active` に載せ替えて即 return**（起→承→転→結は `_tick` が前進）に修正＝入力ループが空いて barge-in（cancel優先）が通る。副作用: /arc のビート間隔が 1s 固定→自然アーク同様 `ACTIVE_BEAT`(5〜12s) に。テスト `test_scheduler.TestArcInterruptible`(3件・載せ替え/再生中 barge-in/busy 拒否)。**自然な tick 駆動アーク／ambient の割り込みは元々動作（`test_cancel_priority_when_speaking`）＝今回は /arc 経路だけの修正**。
- [ ] **（案・低優先）「打ち始め＝こっち向く」割り込みUI（ADR-0006 の体感改善）**＝現状の barge-in は *茶々が喋っている最中（speaking 中）* に被せた時だけ「[茶々がこちらを向いた]」が出る。ビート間隔が 5〜12s なので**人手でその窓を狙うのはほぼ無理**（＝割り込み「した感」が見えにくい。※打てない訳ではない・無言の間に打てば普通に返事）。案: **web で入力欄フォーカス／最初のキー入力の瞬間に `cancel` を送る**＝「キーボードに触れる＝茶々が手を止めてこっちを向く」。タイミング依存を解消し生き物らしさとも合う。難点: web 限定（console は Enter 前に検知できない）／キーに触れる度に止まる挙動になる点の調整。確認の現実解としては当面「自分で長い返事を誘発→喋ってる間にもう一行」で足りる。

## P4 — 客人来訪（v1 召喚来訪 済み 6/27）
- [x] codex-acp 接続（ChatGPT ログイン認証、OPENAI_API_KEY 除去で事故防止）
- [x] /codex <人格> で直接召喚（取り次ぎなし・即・確実）
- [x] 客人セッションは使い捨て（spawn → 数往復 → 破棄。辞去で内部 close）
- [x] 滞在を往復数で上限（GUEST_BEATS=3・無限ループ無し）※ADR-0015 Inc2 で**3人会話の部屋に置換**＝`GUEST_BEATS`/`next_phase` の旧一方向モデルは撤去（6/30）。有界は Room の `turn_cap`＋沈黙辞去が担保
- [x] 客人の人格を召喚時に動的注入（CLAUDE.md でなく prompt へ）
- [x] 客人出力をナレーション化して茶々に渡す（「塀の向こうの声」）
- [x] 客人の声をユーザーにも表示（6/27 修正）＝当初 codex のセリフは茶々への注入 text に埋まるだけで画面に出ず「codex が喋らない」状態だった。`Narration.voice` に生セリフを載せ、`ConsoleView` が `客人 ›` 行で即表示（茶々が黙っても客人は見せる・声は1行に畳む）。召喚 spawn 失敗も可視化（「客人は来られなんだ…」）。voice 14件＋全5スイート PASS、実 codex 召喚 E2E で `客人 ›`→`茶々 ›` 往復を確認
- [x] アーク中の召喚が弾かれる問題（6/27 修正）＝箱庭アーク進行中は `active` 占有で `/codex` が「今は手が離せん」で拒否されていた。方針確認の上「アークを畳んで即通す」に変更：箱庭アークなら `_conclude`（reset＋cooldown）で畳んで客人を通す／客人来訪中は重ねない（断る）／喋り中は cancel 優先で止める。併せて tick ループと召喚が `self.active` を同時駆動する競合を `drive_lock` で直列化（cancel はロック前に出して barge-in 維持）。preempt 14件＋全6スイート PASS
- [x] 自発来訪トリガー（夕方以降 × 低確率 × クールダウン、来ない日がある）＝`GuestSource.eligible` 実装＋registry 登録（6/27：`persona=None`＝自発インスタンス、`eligible`＝`hour>=15`×`random<GUEST_VISIT_PROB`、`reset` で役を抽選、`default_sources(spawn_codex=…)` で registry へ。fake で単体18/統合8件 PASS。**実 codex 自発 E2E も成功（6/27）**＝強制駆動ハーネスで実 resident＋実 codex を spawn、役「野良の絵描き」を自動抽選→到着/世間/辞去を codex が実生成→茶々が関西弁で実反応→3/3ビート・cooldown=20・teardown 正常）
  - つまみ: `ENGAWA_GUEST_FROM_HOUR`(既定15) / `ENGAWA_GUEST_PROB`(既定0.05・低め)。cooldown は `GuestSource.cooldown_ticks=20`
  - [x] **自発来訪が殆ど来ない不具合（実装6/29・ユーザー報告）**＝自発来訪を箱庭アークと同じ抽選経路に乗せていたため `arc_prob(0.30) × prob(0.05) × random.choice 競合` の**三重ゲート**で実効レートが ~0.005/tick まで間引かれ、`prob` が「tickあたりの素確率」という説明と乖離（≒数時間に1回）。`_tick` で**自発来訪を arc 抽選から独立に判定**（`prob` が実効 per-tick 率・from_hour と cooldown だけが条件・箱庭アーク抽選は guest を除外）に変更。これで `prob=0.05` でもざっくり数十分間隔（+cooldown 20tick）になり、`prob` を上げれば素直に頻度が上がる。テスト `test_scheduler.TestAutonomousGuestVisit`(3件・arc抽選非依存/from_hour/cooldown)。`engawa.json.sample` の prob コメントも実態に整合。
- [x] 客人の世間話に外部トピックを注入（ADR-0014・6/27）＝取得先ホワイトリストは **`topic_sources.json`（リポジトリ直下・config 主導／env `ENGAWA_TOPIC_CONFIG` で差し替え・コードに URL を埋めない）**。rss は `url`＋自己申告 `domain` 一致＋https＋見出し限定＋長さ/本数/サイズ上限、設定に載った源しか fetch しない＝設定が whitelist（欠損は時節 local へフォールバック）。既定は**時節(local)稼働**（二十四節気＋旬・無取得）、世間ビートに確率（`ENGAWA_TOPIC_PROB`既定0.7）で『種』として注入＝新聞調禁止・データ枠付け。30分更新（`ENGAWA_TOPIC_REFRESH_MIN`）。topics 26件＋全8スイート PASS。**実 codex E2E でトーン確認**（ご隠居が旬を自然なうわさ話に・新聞調ゼロ・茶々も具体反応）
  - [ ] やわらかRSS の実 URL を精査して `topic_sources.json` に `domain` を合わせ `enabled:true` に（鮮度の追加・据え置き）
    - 精査メモ(6/27): NHK 全カテゴリ実取得＝硬い時事/災害でトーン不一致＝不採用寄り（cat3 文化が一番マシだが混在）。本命候補 **tenki.jp サプリ（季節・暮らし/七十二候コラム）= 縁側のトーンに合致**だが、2010年告知の RSS サービスページ(`tenki.jp/webservice/rss/`)は **404**＝現行フィード URL が要特定（`tenki.jp/suppl/` の feed リンク or 別の柔らかい源を当たる）。当面 時節(local) だけで十分弾むので急がない。自作 `_parse_rss_titles` は RSS/Atom 実フィードで動作確認済み
  - [x] **人格マッチ topic の源拡充（7/1）**＝`topic_sources.json` の `kind:"local"` に inline `topics`＋`persona` を持たせる仕組み（`_local_topics`）を追加し、行商人→相場/値・絵描き→色/光・ご隠居→昔話/近所・風流人→句/季語・旅人→道中 の5役ぶんを既定投入。**併せて人格マッチの方向を修正**（`persona in t["persona"]` は実 persona＝長い句で不発だった→`_persona_matches`＝タグが役名の一部に含まれれば一致・「行商」⊂「…行商人」）。候補が2→7に増え「ネタ2個で直近回避クランプ」も自然緩和。**実 codex で役ごとに色が変わるのを確認（7/1）**。
  - [x] **同じ話題への粘着対策（7/1・実機報告）**＝色分けは効いたが客人が同じ話題を粘る。(1) 種の**クールダウン** `TOPIC_COOLDOWN`（`topic.cooldown`・既定2＝種を置いたら次まで客人ターンを空ける・毎ターン振らない）(2) `guest_air` に「深追いせず話は移してええ・前に出た話は繰り返さん」を追記。`_topic_recent`（重複回避）と合わせ三重。テスト `test_scheduler`（cooldown で3ターン中1回だけ種）＋`test_sources`（anti-dwell 文言）＝全214 PASS。**残: 実 codex で粘着が減ったかの目視。**
  - [x] **客人トピック注入を Room 経路へ復活（regression・6/30 → 7/1 実装）**＝ADR-0014 のトピック注入は旧一方向来訪モデル（`GuestSource.next_phase`→`_pick_topic`/`_topic_instr`）に実装されていたが、Inc2 で客人が 3人会話の部屋（`conversation.Room`）に移った際、`room_guest_prompt(persona, window, kind)` が ctx/topics を受け取らないため**休眠＝実質ロスト**していた（A2 6/30 で旧モデルごと削除）。**ambient「縁側の空気」方式で復活**（ユーザー方針＝機械的注入でなく、天気と同型に種を頭の隅へ置き、口に出すかは常に codex 判断＝会話が自然に流れた時だけ発火）。実装: 選定＝`sources.pick_topic_text`(純関数・確率/履歴なし)／文言＝`prompts.guest_air`＋`room_guest_prompt(air=None)`(後方互換)／確率(`TOPIC_PROB` を「種が空気に混じる確率」へ転用)・直近回避(`Scheduler._topic_recent`)・場面判定(**CHIME/REPLY のみ**・到着/辞去は入れない)＝`scheduler.guest_say`。`conversation.Room` は不変。取得基盤は健在＝再利用。テスト13件追加(sources 10・scheduler 3)＝全196 PASS。ADR-0014 に「更新（ambient 再設計）」節・engawa.json.sample の prob コメント整合。**残: 実 codex E2E で“たまに自然に季節ネタ”のトーン確認（ユーザー）**。
- [x] **3人会話の時間感覚ズレを修正（7/1・実機報告）**＝夜22時なのに「夕暮れ」「日が落ちる前に」等。原因＝room プロンプトに現在時刻が無く persona 名の時間帯（例「夕暮れに道を訪ねてきた旅人」）に引っ張られる。`prompts.ambient_line(ctx)`（時刻＋天気＋「設定より今を優先」）を `room_guest_prompt`/`room_resident_prompt` 冒頭へ必ず注入（scheduler が build_context で ctx を渡す）。天気も ambient_line に集約し guest_air は種のみに簡素化。テスト（ambient_line・room prompt に時刻・scheduler で両者に注入）＝全218 PASS。
- [ ] 時節の挨拶差し替え（月初・季節の変わり目・祝日）※茶々の自発挨拶側（客人ネタとは別口）
- [ ] **（構想）箱庭アーク「新聞」＝茶々が新聞をのぞき込む（7/2 ユーザー着想「茶々が新聞読むシーンもあってええね ｗ」）**
  - **実装**: `sources.default_sources` に `BoxGardenArc("新聞", gate=…, phases=[…])` を1個足すだけ（雀/猫/風と同型・Open-Closed）。gate 案＝朝っぽい時間＆雨でない（新聞＝朝・紙が舞うのは晴れ）。下書き: 起「一枚の新聞紙が風に乗って縁側にふわりと舞い込んできた」／承「茶々はその上にのそりと乗っかって見出しをじっと見下ろしている」／転〔下記〕／結「読むのに飽きたか、新聞紙の上でくるりと丸くなった」。
  - **おもしろい接続＝topics 再利用**: いま季節ネタ(`fetch_topics`)は**客人しか使っておらず、客人には"新聞調禁止"**（うわさ話で振れ）。だが**茶々が見出しを猫なりに半分わかったフリで呟く**のは、その禁じた新聞調の"正しい/可愛い版"＝客人経由とは別ルートで季節ネタが世界に出る（2系統が繋がる）。転ビートの案: **(A) 実 topic を差す**（見出し＝fetch した季節ネタ・`_neko_ten` 同様の callable で ctx["topics"] から拾う）／**(B) 純ネタ**（「風がめくった三面記事に前足をちょん」だけ・topics 非依存）。まず B で入れて後で A を足すのが安全。
  - **注意**: アークを増やすとテンポ観測の箱庭構成が変わる＝**代打/テンポの半日観測が済んでから入れる**。テストは既存 `BoxGardenArc` 同様（phases 進行・gate・結了）。
- [ ] **（構想）客人のバックエンドを選べるようにする＝Claude 客人（`/claude <人格>`）**（7/2 ユーザー着想）
  - **動機**: (1) codex(ChatGPT) 認証が面倒/不安定な時に **Claude だけで3人会話・代打をテスト**できる (2) 「Claude 同士の掛け合い」も試したい。
  - **調査結果（現状）**: 客人は `/codex` 専用＝`acp.AcpAgent.spawn_guest` が **codex-acp**（`ENGAWA_CODEX_CMD`=`npx -y @agentclientprotocol/codex-acp`）を起動。住人 茶々は `spawn_resident`＝**claude-agent-acp**（`ENGAWA_ACP_CMD`）で cwd の CLAUDE.md により人格注入。**両方とも ACP エージェント**なので、住人/客人の違いは「どの adapter を起動するか＋人格の入れ方」だけ。認証はどちらもサブスク＝**追加課金なし**。
  - **技術的な要点/注意**: Claude 客人を足すなら claude-agent-acp を起動しつつ **茶々の CLAUDE.md を読ませない**（cwd を分ける等。読むとその子も“茶々”になる）で、codex と同じく **人格を prompt で動的注入**（`spawn_guest` の人格注入経路を claude 版にも用意）。モデルは住人=`ANTHROPIC_MODEL` / codex=`CODEX_CONFIG` の既存 `_model_env` 系に倣う。関連負債 [茶々用 CLAUDE.md の別ディレクトリ運用（L139）] と地続き。
  - **設計判断**: ADR-0008 は **客人＝「別世界から来た違うAI」= Codex**（異質さが味付け）が前提。Claude 客人はこの前提を広げる＝**新 ADR で「客人のバックエンドは選べる（既定は codex＝異質さ重視）」と残す**のが筋。実装候補: ①`/claude <人格>` を `/codex` と並べて追加 ②`guest.backend`(codex|claude) を config 化＋両コマンド対応。
  - **優先度**: 低（今は代打の目視テスト優先）。codex 抜きでテストしたい実需が続くなら軽い ① から。
- [~] 〔大物・ADR-0015〕客人(visitor)に **人間アンカーで有界な3人会話** を解禁（私↔茶々／私↔客人／茶々↔客人／3人 の全組合せ）。「部屋」方式＝全員が全員の発言を聞く・宛先で応答者が決まる（取り次ぎでない）。歯止め: 人間アンカー・連続AIターン上限・来訪は有界のまま。最難関は**ターン管理**。
  - 決定（6/28）: 宛先=名前メンション＋既定茶々／連続AIターン上限=2手（ADR-0015 実装メモ）
  - [x] **Inc1**: 会話エンジン `conversation.py`（State: Greeting→AwaitingHuman⇄Responding→Leaving→Closed。AwaitingHuman は tick で AI を動かさない＝自律往復が起き得ない／Responding は cap 手で必ず人間待ちへ）。Speaker(Strategy/DI)・resolve_addressee(純関数)・Transcript(value)。ユニット17件
  - [x] **Inc2**: Scheduler/GuestSource 結線（`/codex`・自発来訪が部屋を開く・codex/茶々の双方に transcript window を渡し双方向化・`view.say` で確定発話を一様表示・沈黙で辞去・codex 使い捨て維持）。統合テスト5件。全84 PASS・web JS node --check OK
  - [ ] **Inc3**: cancel優先(ADR-0006)の部屋内統合（今は短いターンを直列化＝人間は待つ）／茶々の room ストリーミング（今は確定行表示）／**実 codex の3人会話 E2E（実機・ユーザー）**／表示・スプライト演出の磨き
    - [x] **客人がせわしない（沈黙ですぐ辞去）を緩和（6/29・ユーザー報告）**＝`idle_leave_ticks` を 4→**8**（来訪中tick 5〜12s なので ~20-48s→~40-96s）にし、**config つまみ化**（`guest.idle_leave_ticks`/`ENGAWA_GUEST_IDLE_LEAVE`・scheduler から Room へ注入）。話しかければリセット・有界は維持。テスト `test_scheduler`（しきい値前は居座る／沈黙継続で辞去・値非依存化）。
    - [x] **人間が入らないと無音で待たれて寂しい → 茶々が“人間役の代打”で場をつなぐ（7/2・ユーザー報告・ADR-0025）**＝`AwaitingHuman` で沈黙が `fill_after`（既定2tick≒15s）続くと `ResidentFilling`（茶々 MUSE→客人 REPLY の1往復）で場を回し、必ず人間待ちへ戻る。**予算 `fill_cap`（既定3）で人間不在の連続AIターンを厳密に上限**＝使い切れば `idle_leave_ticks` で辞去（終端保証）。人間が関与すると予算リセット。`fill_cap=0` で従来の純待ちへ退避可。config: `guest.fill_cap`/`fill_after_ticks`・`ENGAWA_GUEST_FILL_CAP`/`_FILL_AFTER`。テスト: conversation 5件（発火/枯渇→辞去/人間リセット/純待ち/無言スキップ）・scheduler 2件（代打が実プロンプトを流す/長引いても必ず辞去）。全224 PASS。**残: 実 codex/resident で代打のトーン目視（もてなし過ぎない自然な間か）**。
      - [x] **テンポを一定でなく減衰させる（7/2・ユーザー着想「人間は来た直後が一番賑やか→ネタ切れでゆっくり→帰る」）**＝代打の間隔を回ごとに延ばす（n回目しきい値=`fill_after + n×fill_slowdown`・既定 slowdown=1）。静的に1つの間隔値を選ぶ悩み（速すぎ/遅すぎ）を時間変化で解消。人間が関与すると予算リセット＝間隔も先頭へ（賑わい復活）。`fill_slowdown=0` で一定。config `guest.fill_slowdown`/`ENGAWA_GUEST_FILL_SLOWDOWN`・ADR-0025 に追記。テスト conversation 3件（減速/一定/人間リセット）＝全227 PASS。ビート秒は `timing.active_beat_min/max` に掛かる。**残: 実機で減衰カーブの体感目視。**
      - [ ] **（負債・様子見中 7/2）会話テンポつまみが増え過ぎ＝手調整が難しい**（ユーザー指摘「パラメーター調整が難しいアプリになってきた ｗ」）。今 `active_beat_min/max`・`fill_cap`・`fill_after`・`fill_slowdown`・`idle_leave_ticks` が **掛け算で絡む**＋単位が「tick×秒」＋**再起動必須**で試行ループが遅い。**方針（合意）: つまみを増やす/賢くするのでなく畳む**。①まず実機で"当たり"の1組を見つける（**今ここ・様子見**）→ ②いい値が出たら**既定に焼き込み**、個別つまみは engawa.json.sample のコメントへ退避＝本体から意識を消す → ③将来 **プリセット化**（`のんびり/ふつう/賑やか` 等）＋できれば **`/tempo` ライブ切替**（`/font` と同じ poll 方式・再起動不要）で **P5 の歯車⚙設定パネル(B)** に束ねる。**急いで作らない＝当たりを掴むのが先**。関連: [UI 設定をアプリ内で（L68〜74）]。

## P5 — ドット絵UI（Increment 1 済み 6/27）
- [x] **Increment 1: 最小の実UI**（6/27）＝`views.WebView`（poll 方式・ADR-0013 の View 差し替え）＋`engawa_main` web モード（`ENGAWA_UI=web`。webview メインスレッド／Scheduler 別スレッド+loop／窓閉じで signal_close→teardown）。出力は `_log`＋rev 差分を JS が poll、入力は `queue.Queue`。茶々の lazy 表示・改行畳み・客人 voice 表示も ConsoleView と揃え。WebView 12件＋全7スイート PASS。**実窓 GUI 起動は未（GUI ブロックのため手元検証はユーザー）**
  - [x] つぶやき欄のストリーミング表示（agent_message_chunk → poll で逐次）
  - [x] 入力欄（通常テキスト＋スラッシュ）を UI に（6/27 修正: 自分の発言を `あなた ›` で表示エコー＝web は端末と違い入力が自動表示されず「コメントが消える」バグだった。`send()` でログに積む。往復テスト#9 で再発防止）
  - [x] 段階導入の第一段＝枠ありウィンドウ
  - [x] 茶々 procedural アニメ（6/27・ADR-0010 の「骨」）＝まばたき/耳ピク/尻尾/胴体ゆらり（idle）＋話す＝ぴょこぴょこ/前のめり、客人来訪＝耳ピン、話しかけ＝こっち見る。state は poll（kind/voice/done/live）から JS が推定（Python は `kind` を1個足すだけ）。この state→動きの配線は Increment 3 の Aseprite 差し替え後も流用。JS は node --check 済み
  - 起動メモ（cmd）: `set "ENGAWA_UI=web" && python src/engawa_main.py`（`$env:` は PowerShell 専用・cmd は `set`。空白混入回避でクォート）
- [x] Increment 2: frameless + on_top + 画面隅へ配置固定（6/27）＝`create_window(frameless=True, on_top=True, resizable=False)`、起動後 `_screen_size()`(webview.screens→ctypes)＋`corner_xy()` で隅へ `move`（`ENGAWA_UI_CORNER` br/bl/tr/tl・既定 br）。透過なし（ADR-0009）。タイトルバー喪失の補い＝scene に `pywebview-drag-region`（掴んで移動・効かない時 `ENGAWA_UI_EASYDRAG=1` で全面ドラッグ）＋右上×ボタン（`api.close`→`window.destroy`）、`/quit` でも窓を閉じる配線。corner_xy/close は unit、新パラメータ＋隅配置は実機スモークで例外なし（3072x1728 で右下算出確認）
  - [x] 実機の見た目/操作 確認（6/27・ユーザー）: 高DPI(3072x1728)でも右下に正しく収まり、scene ドラッグ移動・on_top 前面維持・×/`/quit` 閉じ すべて OK
  - [x] **起動時の窓ちらつき解消（7/3・ユーザー報告→確認）**＝当初は既定位置(中央)で `create_window` → 起動後 `move` で隅へ寄せていたため、中央で一瞬出て隅へ飛ぶ「フラッシュ」が見えた。`_screen_size()`＋`corner_xy()` を **`create_window(x,y)` に前渡し**して最初から隅に生成（`engawa_main.run_web`）。起動後の `move` は多モニタ用の精密補正として残置（隅→隅で不可視・失敗しても位置ズレ止まりで不可視化しない）。※「一瞬窓」を最初 subprocess コンソールと誤診→遠回り（教訓メモ）。
  - [x] **文字が小さい/窓が狭い対策（6/30・ユーザー報告）**＝隅窓の幅/高を **config つまみ化**（`ENGAWA_UI_W`/`H`(既定400×520・以前360×480)・`engawa.json[ui]`・env>json>既定）＋**`resizable=True`**（Inc2 の `resizable=False` を更新・`min_size`(240,240)・frameless ゆえ実挙動は目視）。既存 `corner`/`easydrag` も env 直読み→`config` 経由に寄せ engawa.json[ui] 対応。`run_web` から `_ui_config`/`_web_window_kwargs` を純関数抽出。文字は本文 `#log` を 13→15px に。テスト `test_views.TestUiWindowWiring`/`TestBuildWebHtml`。
    - ⚠ **当初入れた `ENGAWA_UI_ZOOM`(html{zoom}) を即時撤回（6/30・ユーザー報告）**＝frameless＋`#app{height:100vh}`＋`overflow:hidden` で UI 全体を拡大すると**下の入力欄が窓外へはみ出て消える＝操作不能**になった（GUI 未検証のまま入れた事故）。zoom 機構を撤去し、レイアウトを壊さない**本文フォント拡大(`#log`15px)**へ置換（`#log` は overflow-y:auto＝スクロールするので入力欄は切れない）。教訓＝GUI 挙動はユニットで担保不可・目視必須（原則7/ADR-0019）。
    - [x] **リサイズ手段を実装（6/30・ユーザー報告「リサイズできない」→実機OK）**＝`resizable=True` だけでは frameless 窓に掴む縁が無くドラッグ不可だった。**右下に `#grip`**（nwse-resize）を置き、JS の pointer ドラッグ→`pywebview.api.resize`→`WebView.resize_window`→`window.resize`（min 240 クランプ・画面座標で差分）で実装。テスト `test_views.TestWebViewResize`(3件)＋`TestBuildWebHtml.test_has_resize_grip`、JS は node --check OK。**実機でドラッグ・リサイズ動作をユーザー確認（6/30）**。
    - [x] **文字サイズを config つまみ化（6/30・ユーザー要望「目が悪い人向け」）**＝`ENGAWA_UI_FONT`(既定1.0・0.8〜2.2)。本文/入力(`#log`/`.sys`/`.who`/`#in`)のフォントだけを `calc(BASE * var(--fz))` で拡大（`build_web_html(font)` が `:root{--fz:N}` を注入）。`#log` は overflow:auto＝スクロール、`#bar` は flex＝**入力欄を押し出さない**（窓全体 zoom の事故を踏まえた安全策）。テスト `test_views.TestBuildWebHtml`(font注入/既定/入力欄存続)＋`TestUiWindowWiring`(env)、JS node --check OK（全163 PASS）。**見た目はユーザー目視**。
    - [~] **UI 設定をアプリ内で変更可能に（6/30・ユーザー要望「env 変更は不便」）**＝今は `ENGAWA_UI_FONT`/`W`/`H` を env か `engawa.json` 編集＋再起動でしか変えられず不便。アプリ内から**ライブ**で調整したい。**2系統（補完）**:
      - [x] **(A) `/font <倍率>` スラッシュ（7/1 実装）**（例 `/font 1.4`・`/font` で現在値・`/font save` で永続化）＝ADR-0007「スラッシュ＝縁側操作（茶々に流さない）」に乗る・**本窓をごちゃつかせない**・既存入力欄で打つので **IME 切替不要**。**明示保存方式**（ユーザー選択）＝数字はそのセッションだけライブ適用（`--fz` 差し替え・再起動不要）、`/font save` で `config.set_value` が `engawa.json[ui].font` へ書き戻す（localStorage 禁止と両立・config 主導）。ライブ適用は cross-thread `evaluate_js` を避け **poll に `font` を載せる方式**（本窓＋観戦窓）。console は端末フォント依存で no-op（注記）。`ENGAWA_UI_FONT`(env) が立つと save 後も env 優先＝保存時に告知。クランプ 0.8〜2.2（`_ui_config`/`scheduler.UI_FONT_MIN/MAX`）。テスト: `test_config.TestConfigSetValue`(4)・`test_views.TestFontLiveApply`(6)・`test_scheduler.TestFontCommand`(6)＝全183 PASS・JS node --check OK。**見た目はユーザー目視**。**残: `/win <w> <h>` 相当の窓サイズのアプリ内調整（同じ `api.resize` を叩く）**。
      - **(B) 歯車アイコン⚙→設定パネル**（既存の×ボタンの隣に小さく置く）＝クリックで小オーバーレイを出し、文字サイズ(スライダ/A−A＋)・窓サイズ等を**スライダ/ボタン**で調整。**発見しやすい＝アクセシビリティ向き**（"目が悪い人" はスラッシュを知らない）。パネルは一時表示で常時クラッタにはならない（アイコン1個の追加は許容・memory「本窓をごちゃつかせない」とのトレードオフは小）。手動操作・IME 不要で嗜好に合致。
      - A/B は**同じ適用関数を叩く**形に。ライブ適用は容易：font は JS で `document.documentElement.style.setProperty('--fz', n)`（**再起動不要**）、窓は既存 `api.resize`。
      - 代替: キーボード（Ctrl +/− で文字・Ctrl 0 で戻す）。
      - 永続化は **localStorage 不可（TECH_RULES §7）**＝Python 側で `engawa.json[ui]` に書き戻す（config 主導と整合）か、当面セッション内のみ。
      - console 版にも `/font` を効かせるか（console は端末フォント依存＝No-op か注記）は要検討。
    - [x] **観戦窓(GAME_HTML)も同倍率で拡大（6/30・ユーザー報告「ゲーム版が拡大されず不自然」）**＝当初 font は本窓(WEB_HTML)だけで、観戦窓は生 GAME_HTML＝拡大されず不整合だった。GAME_HTML に `--fz` を注入（文字＋カード箱 `.card`＋行を `calc(BASE * var(--fz))` で揃えて拡大）し、`build_game_html(font)`＋`set_layout(...,font)`＋`game_open` で**窓サイズも font 倍**に（はみ出し防止・`#app{overflow-y:auto}` も安全網）。テスト `test_views.TestGameWindowFont`(4件)＋GAME_HTML JS node --check OK（全167 PASS）。**見た目はユーザー目視**。
- [x] Increment 3: スプライト差し替え機構＋仮の皮（6/27）
  - ★**現行（2026-07-04 刷新）**: 以下は旧履歴。今は Gemini 三毛猫の**4表情正規化シート**(`chacha.png` 704×176・0平常/1口開け/2目つむり/3反応)＋**縁側背景 `scene.png`**(障子＋板の間)。サイズは `sprite.json[display_px]` で縮尺（絵は固定・CSSスムーズ縮小）、接地影は `shadow_w/dy/h`、背景/茶々とも `ENGAWA_SCENE_BG`/`ENGAWA_SPRITE_CONFIG` か `engawa.json[assets]` で差し替え可（`views._asset_path`・ADR-0010 を背景にも拡張）。残: 表情追加（ウインク/耳ピン/びっくり）／背景の時間帯版（朝昼夕夜×`tod`）。
  - [x] 差し替え機構＋state→アニメ配線: `sprite.json`（リポジトリ直下・config／env `ENGAWA_SPRITE_CONFIG`）＝frame_w/h・scale・animations{idle/blink/talk/listen/attentive: frames,fps}。Python が PNG を **data URI で HTML へ注入**（`build_web_html`）、JS が `chaState()`→コマ切り出しで blit（`imageSmoothing` 無効）。**シート無し/欠損は procedural にフォールバック**（コード不触で皮交換）。Python ユニット＋JS `node --check`（procedural/injected 両経路）＋全8スイート緑＋frameless 窓で実描画 GUI スモーク OK
  - [x] **placeholder ドット絵を生成・有効化**（6/27）: Pillow で 32x32×7コマ（idle×2/blink/talk×2/listen=耳ピン/attentive=目大）の茶々(猫)を生成→`chacha.png`（リポジトリ直下）＋`sprite.json` enabled:true で稼働。質は仮（再描画の下敷き用）。生成器は scratchpad の gen_sprite.py
  - [x] 表示サイズ・位置を自動算出（6/27）: スプライト使用時は `#cha` の表示寸法/位置を scene 高さと frame_w/h から JS が計算（`scale` は収まる範囲で整数クランプ＝省略で最大フィット・内部は等倍でクリスプ）。どんなコマ寸法のシートを置いても CSS を毎回いじらず収まる
  - [x] 本番の絵に差し替え（6/28・Gemini 三毛猫）= ユーザーが Gemini で「猫だけ・座布団なし・グレー背景」の8コマアニメ列を生成 → 私が grayキー抜き→高さ正規化→64高に整列して 8コマシート化、さらに「コマ0由来の目/口/耳差分」2コマ(8,9)を自作追加して **`chacha.png`(80×64×**10**＝800×64)** に、`sprite.json` で state→コマ割当。抜き手順は scratchpad の extract_seq.py（gray proximity key・市松焼込みは色キー・座布団付き版は flood不可だった等の知見あり）。Aseprite 購入不要だった
    - ⚠ AI の8コマは「連続アニメ」でなく「別々の表情ポートレート」＝コマ間が不連続でループするとぴくつく。対策で**各 state を単一コマ＋CSS呼吸(scaleY・座布団無いので浮かない)**に。割当: idle/blink=0, talk/attentive=2(笑顔), listen=3(見上げ)。fast 切替(2↔7)はやめた
    - [ ] （任意）滑らかなまばたき: 「コマ0の目だけ閉じた版」を私が自作すれば顔を変えず目パチが足せる（procedural の手法を実コマに適用）
  - [ ] （構想）背景の時間帯バージョン: Gemini で朝/昼/夕/夜の和室(猫なし・座布団は背景に)→ `tod` で切替・茶々スプライトを上に合成。背景も差し替え可能アセット化（sprite.json と同じ思想）
  - [ ] （任意）天気/時刻 由来の mood も state に足す（今は talk/listen/attentive/blink/idle）
- [~] Increment 4: 客人来訪の演出（6/27 実装・GUI 確認はユーザー）＝**画面外＋気配方式**。当初の「障子に影」は *障子越しに会話するのは不自然*（障子=家の仕切り／客人=塀の向こう＝庭側）として却下→作り直し。客人は庭側＝画面外にいる扱いで姿は描かず、**到着時に庭先の葉がそよぐ気配**（`#kehai` のドリフト）＋**茶々の耳ピン反応**（既存 listen）＋会話ログで表す。信号は poll の guest voice 由来（`chacha.lastGuest` 12s 窓・立ち上がりで気配）＝Python 変更なし・sprite/procedural 両対応。JS `node --check`＋全8スイート緑
  - [ ] 実機の見た目確認（GUI）: `/codex <人格>` で庭先に葉のそよぎ＋茶々が耳ピン。手元検証はユーザー（Chrome 拡張未接続で自己描画確認は不可）
- [ ] **（構想）音をつける＝環境音＋茶々の鳴き声**（体験の厚み・環境反応の核と地続き）
  - **環境音**: 時刻/天気/アークに連動した BGS（朝の鳥、雨音、風、夕方のひぐらし 等）。`tod`・天気・アーク state を信号に web 側で鳴らす（背景の時間帯バージョンと同じ思想で state→音を配線）。
  - **茶々の鳴き声**: クリック／構った時に「にゃ」等（三毛猫なので猫声）。state（talk/listen/attentive）にも軽い反応音を当てられる。来訪・天気の移ろい・アーク起承転結にも効果音の余地。
  - **設計の筋**: 音アセットは差し替え可能な config 主導（`sprite.json`/`topic_sources.json` と同じ流儀＝`sound.json` 等でファイル/音量/有効を宣言・コードに埋めない）。**既定ミュート＋音量つまみ**（`engawa.json`）で常駐アプリでも邪魔しない（「窓を汚さない・明示操作」の方針と整合）。web は HTML5 Audio、console は無音。

## ゲーム（ADR-0017・AIが既存ゲームに参加）
> 「ゲームは自作せず既存実装に AI が参加」。Pyxel/pygame はUI総取替で不適と判断、RLCard（読める状態＋合法手）を Port&Adapter で。
- [x] **Inc1**: Game ポート核 `game.py`（GameAdapter/Player/GameSession 人数非依存/レジストリ・依存ゼロ・FakeGame でテスト）
- [x] **Inc2**: `game_rlcard.py`（RLCardAdapter・**rlcard 依存はここだけ**）＋step方式＋AI-only観戦。実 rlcard で3人BJ完走を隔離venvで検証。rlcard は**任意依存**（無くてもコア app/テストは動く・adapter テストは skip）
- [x] **Inc3**: 実 LLM プレイヤー（茶々=resident・state＋合法手→手）＋`/blackjack [見る]`＋console＋tickペース。**A方式＝基本 私＋茶々（観戦は茶々のみ）で codex 不要**、ゲームが人数を要求する時だけ客人(codex)で埋めて終局で破棄。手番プロンプトに自分のスロットを明示（全員の手札が見える blackjack 対策）。配線は FakeGame＋fake codex で全116 PASS
  - [ ] **実機 E2E（ユーザー）**: `pip install rlcard` → `/blackjack 見る`（茶々がディーラーと・codex不要）/`/blackjack`（私+茶々）。**実 claude が合法手(hit/stand)をちゃんと選ぶか**・パース外し時のフォールバック頻度・テンポ
- [x] **Inc4a**: 観戦表示を View ポート化（game_open/update/close）＋構造化スナップショット。console はテキスト維持。BlackjackRender.snapshot。
- [x] **Inc4b**: **観戦窓（別ウィンドウ・カード描画）**。WebView が対局開始で第2窓(GAME_HTML・緑フェルトの札卓)を本窓の隣に生成→snapshot を poll してカード描画→終局で閉じる。作れない環境は本窓ログへフォールバック。JS は node --check OK。
  - [ ] **実機の見た目確認（ユーザー）**: web 起動→`/blackjack 見る` で隣に札卓窓が出てカードが見えるか／位置・サイズ感／×で閉じるか
  - [x] **本窓×で観戦窓が残る不具合（実装6/29・ユーザー報告／実機OK 6/29）**＝本窓の×（`WebView.close()`）が本窓だけ destroy し観戦窓(第2窓)を残すと、`webview.start()` が返らず scheduler の teardown（finally の `view.game_close()`）に入れない＝観戦窓が残りプロセスも生き続ける。`close()` 冒頭で `game_close()` を呼び**両窓を畳んでから**本窓を閉じるよう修正。テスト `test_views.TestWebViewCloseClosesGameWindow`(2件)。**実機確認: 本体終了と同時に観戦窓も閉じるのを確認（ユーザー 6/29）**。
  - [x] **観戦窓×で「ゲームモードのまま復帰不能」になる不具合（実装6/29・ユーザー報告）**＝観戦窓の×（`_GameApi.close`）が `view.game_close()` で**窓を destroy するだけ**で `Scheduler.game` を残していた＝以後ずっと対局中扱い（平文入力は全部「手」・縁側に戻れず `/quit` でアプリごと閉じるしかない）。観戦窓×を **`WebView.request_game_abort()`**（窓を閉じる＋入力チャネル `_inq` に制御トークン `views.GAME_CLOSE_REQUEST` を積む・スレッド安全）に変更し、scheduler は `on_user_input` 冒頭でそれを受けて **`_abort_game`（お開き＝state クリア＋客人破棄＋観戦窓クローズ→縁側へ）**。`_abort_game_on_timeout` と共通の `_teardown_game` に整理。テスト `test_scheduler`(対局中×でお開き／非対局時 no-op)＋`test_views.TestGameWindowAbort`(窓を閉じ合図を積む)。
  - [x] **対局中に固まる別経路2つ（実装6/29・上記調査で判明）**＝①**tick ループの脆さ**: `_tick` の game ブロックを `try/except` で囲み、**timeout 以外の例外**（adapter 死亡=ConnectionError／rlcard 不正状態 等）も `_abort_game_on_error` でお開きに（例外が `_tick` を抜けて `_tick_loop` を殺す＝永久停止を防ぐ）。②**`/codex` の game ガード**: `_summon_guest` 冒頭で対局中なら「今は対局中や」で弾く（room と game の同時成立を防ぐ）。テスト: `test_scheduler` に異常系（AI手番の例外でお開き／adapter.play 例外でお開き／対局中 /codex 拒否）＋正常系（非対局時 /codex は通る）の4件。全150 PASS。
- [ ] （任意）観戦窓に手番リアクション台詞・対局時の hit/stand ボタン（今は本窓でテキスト入力）／pixel-art カード化
- [x] **UNO/レダックポーカーを起動可能に（6/29）**＝アダプタは元から登録済み（`game_rlcard.py`）だったが起動コマンドが `/blackjack` しか無かった。**汎用 `/game <id> [見る]`** を追加（`/blackjack`/`/bj` は別名で維持）。空/不明 id は遊べる一覧を出す。観戦/参加の人数は登録メタ `(min,max)` に**クランプ**（leduc 等2人最少のゲームを観戦 want=1 で壊さない）。UNO/leduc は**カード描画(render)未対応＝観戦窓は move をテキスト表示**（GAME_HTML の `renderText` フォールバック）／console は本窓テキスト。LLM プレイヤーの注入・手パースはゲーム非依存なのでそのまま動く。実 rlcard スモークで uno/leduc/blackjack の `legal_moves` 取得を確認。テスト `test_scheduler.TestGameMode`(+5件・/game uno・不明 id 一覧・id 省略・/blackjack 別名・人数クランプ)。**残: 実 LLM が UNO の手(色-数/ワイルド)をちゃんと選ぶかの実機 E2E（ユーザー）**。
  - [ ] （任意）UNO/leduc の**カード/盤面 render**（観戦窓を blackjack 同様に絵で）／**PettingZoo アダプタ**（盤ゲーム＝新規 `game_pettingzoo.py`・AEC/action mask・任意依存追加・三目/connect4 が軽い）／手番のリアクション台詞
- [ ] **（設計オープン）対局中に茶々/客人へ話しかける（人→AI 雑談チャネル）**＝今は対局中の平文入力は**全部「手」**として解釈され、雑談する口が無い（ADR-0017）。やるなら**ユーザーに `/say` を覚えさせず、対局中だけ内部で「手か雑談か」を仕分ける**方向（自分の番×合法手→手／それ以外→茶々への雑談。今の拒否メッセージ「その手は出せん」「他のプレイヤーの番」が雑談に化ける）。
  - **本当の難所は仕分けでなく並行性**: 茶々は同じ長命セッションで手も選ぶ。tick が裏で `茶々.prompt(手番)` を回す所へ雑談 `茶々.prompt` を被せると**同一 ACP セッションに prompt 2本同時＝チャンク混線/pending 取り違え**で壊れる。雑談注入をゲーム手番進行と**直列化**（`drive_lock` で囲む等・召喚/対局開始と同じ流儀）が必須要件。
  - 候補と論点（**まだ決め打ちしない＝もっと良い設計の余地あり**）: ①内部仕分け（move-first・else 雑談／打ち損じが雑談化する軽微な弊害）②明示 `/say <text>`（堅いが口を覚える必要）③web の宛先チップで茶々宛を雑談へ（web 限定）。客人は席埋めゲームでしか居ないのでまず茶々だけが現実的。並行性の直列化はどの案でも共通で要る。
## 多言語・多方言（voice バンドル・ADR-0022）

茶々の「声」を **voice 単位**（`ja-osaka`/`ja-kyoto`/`ja-kagoshima`/`en`…）で差し替え。**base 言語 ⟂ voice を分離**し継承（`<voice>→<base>→既定`）。中身（各地域の声）は現地が書き起こす＝transcreation（機械翻訳しない・原則#2）。設計は ADR-0022。**まず方言ユースケースで継ぎ目を安く検証**してから言語へ（YAGNI・ADR-0013）。

- [ ] **Inc1: voice 選択＋persona オーバーレイ**（最小・方言が persona 一枚で差し替わる所まで）
  - `voices/<id>/meta.json`(base/label/llm_lang)＋`persona.md`。選択は `config.get_str("ENGAWA_VOICE","voice",…,"ja-osaka")`（env>engawa.json>既定・ADR-0020 流）。
  - `acp.spawn_resident` が選んだ `voices/<id>/persona.md` を cwd の CLAUDE.md として load（既存の人格注入＝ADR-0003 に直結）。既定 `ja-osaka` がフォールバックの底＝**消せば現状維持**。起動行に `茶々=<voice.label>` 表示。
  - ユニット: voice 解決（env/json/既定）・persona パス・フォールバック。**JP 方言では `prompts.py` 不変**（persona が指示・「日本語で答えて」を足さない＝競合させない）。
- [ ] **Inc2: UI シェルの i18n（英語向け）**
  - `loc("key")` で active voice の `strings.json` → base → 組み込み既定。`scheduler`(/help・system)・`views`・`engawa_main` の文言をキー化。
  - 英語 voice は `meta.base="en"`＋`llm_lang="en"`（`prompts.py` が任意で参照）＋`strings.json`（訳）。
- [ ] **Inc3（YAGNI・最初の外国語ロケールが来たら）: culture.json**
  - 季節モデル（二十四節気/旬→相応）・天気語彙・客人ペルソナを voice/base 継承で差し替え。下記の天気負債を吸収。
- 関連負債の合流先: 「茶々用 CLAUDE.md を persona/ 別ディレクトリ運用」＝`voices/<id>/persona.md` に化ける ／ 「天気座標の大阪固定→設定化」＝`culture.json`(Inc3)。
- スプライト（三毛猫）は言語中立＝不変（P5 と独立・ADR-0010/0019）。

## Open Questions（spec §15）
- [~] 長命セッションの compaction 戦略 / fork 閾値（Naraku の外部状態方式を流用できるか）
  - **染み出し不具合（7/3 実機報告）**＝長時間稼働で茶々ソロ出力に「注入プロンプトの復唱＋地の思考(英語/メタ)＋本物の台詞」が全部混じる（内部 compaction で「茶々として答えるだけ」の枠を失う筋）。Engawa は `agent_message_chunk` しか描画してない＝**モデルが本文チャンクとして吐いている**＝描画フィルタでなくモデル出力側の崩れ。
  - **対症（実装済み 7/3・commit 9d3a297）**: ①表示ガード `prompts.strip_resident_leak`（注入文の復唱＋先頭の思考を表示前に純関数で除去・原因を問わず効く・`ENGAWA_RESIDENT_GUARD` 既定1・ソロは一括描画に）＋②`/restart`（住人セッションを張り直し＝timeout 段階回復と同じ respawn 経路を共用）。テスト: `test_prompts`(8) ＋ `test_scheduler.TestRestartAndGuard`(5)。
  - **根治（実装済み 7/3・ADR-0027）**: ③茶々の「中座」＝世界観に溶かした定期セッション更新。住人ソロ発話が `absence.after_turns`(既定30＋ゆらぎ)たまったら次の idle で中座に入り、不在(`gap_sec` 既定18)の裏で `_restart_resident()`（黙って若返り）→ローカル定型で戻る。idle 限定（会話/来訪/対局中は行かない＝話の途中で忘れない）・`after_turns=0` でオフ。leave/return は LLM 非経由（`prompts.absence_leave/return`）。テスト: `test_scheduler.TestAbsenceRefresh`(8)。**発話トーン/頻度の体感は実機目視（engawa.json[absence] で調整）**。
  - **残**: 染み出し検知→自動再生成（1回崩れたターンを黙って引き直す）／room 側（`room_resident_prompt`）へのガード横展開／不在中の「空っぽの縁側」スプライト（P5 後日）。
- [ ] /codex <自由テキスト> のプロンプトインジェクション（配布時のみ要対策。検討メモ 6/27）
  - 脅威モデル: persona は `_codex_prompt` に直挿し＝注入面。ただし客人 codex も茶々もツール権限ゼロ（`acp.py`：fs/terminal=False・permission 即 cancelled）なので**最悪でも「変なテキストを吐く」だけ＝被害は体験/人格の崩れ**でセキュリティ被害ではない。守る本丸は機密でなく世界観・人格の保全
  - 注入は2段: `persona→codex→（『』で包んで）→茶々`。各ホップが面（codex が茶々を狙う2段目も）。`guest_narration` の `『…』` 枠付けは弱い緩和
  - 防御は多層（強い順）: ①封じ込め＝ツール無し・人間承認（ほぼ実装済み・王道）②入力を絞る＝**アローリスト persona**（自由入力はオプトイン化／注入面を原理的に消す・費用対効果最大）＋デリミタ＋「キャラ名として扱え」枠付け＋サニタイズ（長さ/改行/"無視して"拒否・脆い一次フィルタ）③評価で弾く＝LLM-as-judge/ガードレール分類器で persona や codex 出力を判定・カナリア。※評価は誤検知/回避ありの defense-in-depth で銀の弾丸ではない
  - 落とし所（配布時）: 既定アローリスト＋自由入力は枠付けオプトイン＋（やるなら）出力に軽い judge 1枚。完全防御は無い前提で比例した対策
- [ ] 客人の人格の作り込み度（環境イベント化なら厳密でなくてよい？）
- [ ] 「茶々が反応しない（……）」の UI 表現（既読スルー感）

## 技術的負債 / 要確認
- [x] **（設計・ADR-0026）Agent ポート抽出（7/4 実装）**＝`src/agent.py` に `Agent` Protocol＋中立 `AgentTimeoutError` を新設。`acp.ACPTimeoutError` は `AgentTimeoutError` を継承（型で正規化）、`AcpAgent` はその ACP 実装、`Scheduler` は `acp` を import せず `from agent import AgentTimeoutError` の中立例外だけ捕捉（scheduler のローカル変数 `agent` と衝突しないよう名前 import）。振る舞い不変。テスト `test_scheduler.TestAgentPort`(2)。
- [x] **（設計・ADR-0026）第2アダプタ `OpenAIAgent`（7/4 実装）**＝`src/agent_openai.py`＝OpenAI 互換 chat completions（LM Studio/Ollama）を Agent ポートで包む。API がステートレスなので**会話履歴を自前保持**（local 版の長命セッション・中座/close でリセット）。人格は `persona.py`（backend 中立に一元化＝ACP は CLAUDE.md／OpenAI は system で同じ文）。HTTP は stdlib urllib＋to_thread（**依存ゼロ**）、非ストリーミング（住人ガードが一括描画）、cancel は flag＋結果破棄（local は速い）、接続失敗/timeout は AgentTimeoutError で段階回復。`ENGAWA_RESIDENT_BACKEND=acp|openai`＋`ENGAWA_OPENAI_*`（composition root で spawn 切替・中座の再spawnも同factory）。テスト `test_agent_openai`(10)。**残: 客人(codex)側の API 化は任意（今は ACP）／実機での実 LLM トーン確認は目視（Qwen3.5-9B on 2070S 8GB を DL 済み）。**
- [ ] **（クリーンアップ）View 入力を value 化**（codexレビュー 7/4）＝観戦窓 close が `views.GAME_CLOSE_REQUEST` 特殊文字列、宛先が `"\x00<to>\x00<text>"` 文字列ワイヤ形式で Scheduler に漏れている。`InputEvent(kind,text,to)` 的な小さな value に置き換え、`View.inputs()` 内部で変換して Scheduler から `views` import を外す（UI を増やす時の衝突防止・実害小＝低優先）。
- [ ] **（任意）`ENGAWA_*_CMD` の分割を `shlex.split(..., posix=False)` に**（codexレビュー 7/4）＝今は `.split()` で空白入りパスの adapter 指定が壊れやすい。shell injection ではないが堅牢化。
- [x] **デバッグモード（7/1）**＝`ENGAWA_DEBUG=1`（config・既定オフ）で `engawa.log`（gitignore）に主要ライフサイクル（種の注入/来訪/room 開閉/say/cancel/timeout/tick 分岐）を stdlib logging で吐く（`src/debuglog.py`＝薄いラッパ・各モジュール `debuglog.get("<name>")`・`setup` は composition root）。**縁側の窓/console 本文は汚さない**（別ファイル・off は NullHandler＝no-op）。LLM 判断もの（客人が季節ネタを拾うか）の切り分け土台＝「種は入れた」は `assertLogs` でテスト可能。テスト `test_debuglog`(5)＋`test_views.TestDebugConfig`(3)＋`test_scheduler`(種 placed/skip の assertLogs 1)＝全204 PASS。**残（任意）: ACP prompt/応答の詳細ログ（今回スコープ外＝冗長/機微のため既定外し）／観測点の拡充。**
  - [x] **定量観測の強化（7/2・ユーザー要望「半日回して会話タイミングを定量的に見たい」）**＝(1) タイムスタンプを秒どまり→**日付＋ミリ秒**（`YYYY-MM-DD HH:MM:SS.mmm`・`debuglog` の Formatter に `%(msecs)03d`）。(2) 観測点を追加＝`inject 茶々 (kind)`（茶々ソロつぶやき/アーク beat/ソロ応答の起点）・`user input`（人間の駆動時刻・room/solo とも）・`next beat +Ns (active=?)`（予定の間合い＝LLM 遅延と分けてペースを見る）。既存 `say who (kind)`（room 発話）と合わせ、来訪の到着→代打(muse/reply)の間隔→辞去まで msec で追える＝代打の減衰カーブや fill 間隔を数値検証できる。テスト `test_debuglog`(timestamp 書式)＋`test_scheduler`(user input/inject の assertLogs)＝全229 PASS。
- [x] モデルを config で選択可能に（6/28）＝住人(Claude)は子 env `ANTHROPIC_MODEL`（Claude Code が尊重・`opus`/`claude-opus-4-8`/`opus[1m]`）、客人(codex)は `CODEX_CONFIG` の `{"model":…}`（codex-acp が Codex 設定へマージ）。つまみ `ENGAWA_MODEL`/`ENGAWA_CODEX_MODEL`（engawa.json `model.{resident,guest}` 可・env 優先）。**未指定はアダプタ既定のまま＝現状維持**。`acp.py` `_model_env`/`_child_env`＋`config.get_str`、起動行に `茶々=<model>` 表示。ユニット10件追加(全55 PASS)。実機での実モデル切替確認はユーザー（GUI/実 adapter）。codex 側 `CODEX_CONFIG` 経由は web 調べ・実 codex での効きは未実測（無指定なら無害）
- [x] 常設テストの復帰（codexレビュー S3・6/28）＝stdlib `unittest` で GUI/ネット不要の回帰テストを `tests/` に新設。views(`collapse_ws`/`corner_xy`)・config clamp(S4)・sources(whitelist `_host_allowed`/RSS `_parse_rss_titles`/`time_of_day`/`build_context`/narration)・acp(EOF→`ConnectionError` S1)・`Scheduler`＋`CaptureView`＋fake resident/codex(user入力・cancel優先・arc結了・客人3ビート使い捨て)。実行 `python -m unittest discover -s tests -t .`（45件 PASS）。以前 PASS 記録のあった JS `node --check`/実 E2E はこの stdlib スイートには含めない（別軸）
- [x] session/cancel の実機 claude-code-acp 挙動（stopReason=cancelled が返るか）（確認済 6/27：cancelled で返る・エラーにならない）
- [ ] cmd /c の裏の node 取り残し → 本番常駐では **Job Object 化**で確実に刈る（`taskkill /PID /T /F` は実装済み＝acp.py `shutdown_process`。taskkill 失敗時/孤立子の最終保険として Job Object を被せる）
- [x] **ACP 握手失敗時の teardown 漏れ＋一時dir の刈り残し（codexレビュー S2・実装6/29）**＝`AcpAgent.spawn()` の握手失敗（timeout/EOF/error）を `except BaseException` で受け、task cancel＋`shutdown_process(proc)` を必ず走らせて再 raise。`spawn_resident`/`spawn_guest` は失敗時に temp dir を `shutil.rmtree(..., ignore_errors=True)`。`close()` も `_persona_dir` を rmtree。テスト: `test_acp.TestCloseRemovesPersonaDir`。※「cmd /c の裏の node 取り残し（Job Object 化）」は別軸で未対応のまま
- [~] **ACP request/prompt に用途別 timeout（codexレビュー S1・主要部実装6/29）**＝`ACPClient.request(timeout=)` で per-request timeout→`ACPTimeoutError`、timeout/例外いずれでも pending を pop。用途別: init120/session60/prompt240/send・cancel10 秒（`acp` 節で config 可変・初回 npx を見込み寛容）。受け側を **2系統ポリシー**で結線: 住人=ターン破棄→連続 `resident_restart_at`(既定2) で再起動→失敗で縁側を閉じる（長命セッション=文脈を一過性遅延で捨てない・ADR-0005）／客人=「急ぎの用で去る」定型退場（ハング client は二度叩かない・`sources.guest_timeout_leave`）／ゲーム=席を立ってお開き。`_tick_loop`・`run()` に保険 net。テスト: `test_acp.TestRequestTimeout` ＋ `test_scheduler.TestTimeoutRecovery`(5件)。
  - [x] **cancel 後の in-flight prompt の短い bounded wait**（実装6/29）＝`AcpAgent.cancel()` が notify 送出時に in-flight prompt の rid を掴み、`CANCEL_GRACE`(既定10s・`acp.cancel_grace`/`ENGAWA_ACP_CANCEL_GRACE`) 後も未決着なら `ACPClient.abort_pending(rid, result=cancelled)` で合成 `stopReason=cancelled` として畳む。これで adapter が cancelled 応答を握り潰しても prompt の全 timeout(240s)を待たない。**timeout でなく cancelled**（良性）にして住人の段階再起動カウンタを誤って進めない（本当のハングは続く新ターンが PROMPT_TIMEOUT で検出）。後から本物の応答が来ても `_dispatch` は pop 済みで無視＝二重決着なし。`request(on_start=)`/`abort_pending`/`_expedite_cancel` 追加、`close()` で grace タスクを畳む。テスト: `test_acp.TestCancelBoundedWait`(2件)＋`TestAbortPending`(2件)。全133 PASS。
- [ ] 茶々用 CLAUDE.md は persona/ 等の別ディレクトリに置く運用（リポジトリの CLAUDE.md と同名衝突回避）
- [ ] SQLite 永続化の実装（spec §11：residents/guests/events/messages/sessions）
- [ ] 環境イベントの「体感ナレーション」層（気温の生値→体感語、前ティック差分、時刻×気温）
- [x] 天気が文脈に入るのが遅い／ユーザーターンに無い（起動直後に話しかけると天気を捏造＝とんちんかん）（6/27 修正・案A+保持）
  - 直し: `Scheduler.weather` に保持。`run()` 起動時に1回 `fetch_weather`（入力受付前）、`_tick_loop` で毎tick更新、`on_user_input` が `build_context(self.weather)` を `user_narration(line, ctx)` へ。`user_narration` は天気を持たせるが「聞かれてないのに言い立てない」抑制付き。天気 None なら天気行を出さない（捏造材料を与えない）
  - 検証: fake 8件 PASS＋実 resident E2E（実天気「ところどころ曇り24.6℃」→茶々「ちょい曇って涼しい」で整合・tick未発火の起動直後状況で確認）
- [ ] 天気の座標が大阪固定（`OSAKA_LAT/LON`）→ 利用者が変更できる仕様へ（env / 設定ファイル等）
  - 備考: ナレーションの「大阪は…」という地名ラベル（`build_ambient_narration`）もハードコード。座標と連動して地名も差し替える（地名だけ大阪のまま残る事故を防ぐ）

## 発信ネタ（おまけ）
- [ ] 「AIに住人を作る」過程の記事（ACP＋人格注入、環境反応の設計）
- [ ] マルチLLM単一障害点 → ACPでベンダー非依存にした話（Mythos停止の実体験と接続）
