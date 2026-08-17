# メタ駆動デッキ専門方策・デッキ共同最適化基盤 設計

> **2026-08-01 実装前レビュー反映・承認仕様**
> Gold / Silver / Bronze は提出後に切り替わる実行時モードではなく、学習・分析用の層である。最終成果物は、1件の提出につき固定された1デッキ・1方策 bundle とする。

## 1. 結論

現在の学習系列を R2D3 の延長として固定せず、既存 simulator と公開・チーム内資産を再利用する新しい専門最適化基盤を作る。

基盤の中心候補は、次の 5 アーキタイプを個別に扱う **Search-guided Expert Iteration (ExIt) + recurrent actor-critic (IMPALA/V-trace)** である。

1. フーディン
2. マリィのオーロンゲ ex + ユキメノコ + マシマシラ
3. イワパレス + メガガルーラ ex
4. ロケット団のミュウツー ex + ロケット団のワナイダー
5. ブリジュラス ex

ただし、この 5 系統は **本番で同時に切り替えるモデル群ではなく、ローカルで競争させる提出候補ポートフォリオ** である。Kaggle の 1 submission は、固定された `main.py`、`deck.csv`、依存ファイル、学習済み重みを含む 1 個の bundle であり、対戦中またはレーティング上昇時に deck や checkpoint を差し替える前提を置かない。

Gold、Silver、Bronze は次の 2 用途に限定する。

- 公開 leaderboard から取得した deck provenance を分析するための **source rank band**
- 同じ checkpoint 系列へ順番に与える opponent curriculum と評価層

したがって、`Bronze model`、`Silver model`、`Gold model` を別々に production champion として作るのではなく、同じ親子関係を持つ重みを連続更新する。

```text
Foundation θ0
  -> broad / lower-band phase θ1
  -> middle-band phase θ2  （下位・中位相手を一定割合で維持）
  -> high-band phase θ3    （過去層を一定割合で維持）
  -> all-band consolidation θfinal
  -> 1 deck + 1 policy の submission bundle
```

各アーキタイプは 2〜3 個の既知の強い 60 枚構築を seed pool として持ち、デッキと方策を二つの時間尺度で最適化する。方策系列、Replay、optimizer、checkpoint、評価履歴はアーキタイプごとに独立させる。pre-curriculum の deck-policy 比較で 1 deck を確定して `deck_lock_id` と `policy_lineage_id` を発行した後は、medal curriculum をその同一 lineage の連続学習とし、phase ごとにゼロから別モデルを作らない。curriculum 開始後の deck 変更は新 branch であり、既存 lineage の continuation とは呼ばない。

ExIt + V-trace を本命とするが、比較は algorithm 差と search teacher 差に分ける。共通の actor-visible 観測、backbone 容量、environment transition、seed、opponent / 評価 schedule を使う recurrent PPO と修正版 R2D3 も対照とする。アーキタイプごとに最も強い方式を採用し、全アーキタイプへ単一方式を強制しない。

期限までに学習を完了した 2 系統以上の最終候補は、共通の未使用 schedule で **Global Submission Race** を行い、提出用の primary bundle を 1 件、必要なら backup / challenger bundle を 1 件だけ指名する。未学習 lane は `qualified_not_trained` として残し、5 系統すべての完了を P0 の blocking 条件にはしない。アーキタイプ別 champion の作成だけで設計を終了しない。

この設計は、既存の `Bootstrap Champion -> R2D3` 系列を上書きするものではない。旧系列は比較対象、移行元、緊急 fallback に限定し、新系列の manifest、Replay、checkpoint と混在させない。

## 2. 背景と根拠

### 2.1 現行手法を固定しない理由

既存 R2D3 系列は、再帰状態、C51 Double-Q、PER、CQL、demo margin、BC 補助、burn-in、unroll、n-step、resume manifest を持つ。一方、過去の開発評価は 76/384、19.8% であり、現行実装がこのタスクの最良方式である証拠はない。

また、800 局の監査では 13,452 decision 中 267 decision が複数選択であり、218/800 局、27.25% の game に少なくとも 1 回の複数選択がある。既存 PPO/R2D3 経路には、このデータを除外または不完全に変換する箇所がある。アルゴリズム比較より前に、複数選択を正しく表現する共通 action contract が必要である。

ローカル PIMC 実験には、8 opponent、約 1,599 局の pooled 評価で 0.6867 から 0.7361、+4.94 percentage points、p=0.00205 の改善記録がある。新系列ではこの結果を fresh な均衡 1,024 局 schedule と hidden-information leak 監査で再現し、再現した場合に限り PIMC を ExIt 教師へ昇格させる。

### 2.2 2026-08-02 時点の再現可能なメタ監査

現在の Gold / Silver / Bronze の比率を数値化できる sealed census は、ローカル証跡内に存在しない。旧版に記載した `2026-08-01T08:37:14` の 6,083 team、Gold 22/22、Silver 283/283、Bronze 206/304 とその構成比率は、原データと全 row を再構成できないため production 根拠から撤回する。これらを「現行使用率」や「勝率」として学習比率へ入れない。

唯一再現できる rank/deck snapshot は SHA-256 `17b694e48ce605161c5491c7cde34dbdfc31f4c1b625c3c90a51e0aecac2b188`、埋め込み取得時刻 `2026-07-15T05:30:44.635136+00:00` である。5,039 team の rank とメダル境界（Gold 1〜20、Silver 21〜252、Bronze 253〜504）を持つが、deck 構成は Gold 20/20 の全数、Silver 20/232 と Bronze 20/252 の系統標本である。したがって Silver / Bronze の比率は全数集計ではなく、CI を伴う履歴標本としてのみ扱う。

| source band | deck 取得範囲 | 2026-07-15 歴史標本の主要 archetype |
|---|---:|---|
| Gold | 20/20 | フーディン 7/20、イワパレス+メガガルーラ 4/20、ロケット団ワナイダー系 3/20、その他 |
| Silver | 20/232 の系統標本 | フーディン 6/20、イワパレス+メガガルーラ 3/20、オーロンゲ 3/20、ルカリオ 3/20、その他 |
| Bronze | 20/252 の系統標本 | フーディン 8/20、ブリジュラス+エースバーン 6/20、シロナのガブリアス 2/20、その他 |

新しい[public discussion](https://www.kaggle.com/competitions/pokemon-tcg-ai-battle/discussion/727816) は、フーディンとイワパレスを多数派、ロケット団ミュウツー、オーロンゲ、シロナのガブリアス等を上位帯で見られる構成として述べる。これは後輩の「フーディン/オーロンゲ、メタのイワパレス+メガガルーラ/ロケット団」と方向は整合するが、rank 別比率や勝率を持たない定性証拠である。新しい sealed census の取得まで、履歴標本と定性証拠を合算して「現在の share」を作らない。

ここでいう比率は、固定 snapshot の各 team から復元した **観測デッキ比率** であり、対戦回数ベースの使用率、勝率、元 team の方策分布ではない。復元デッキを別の rule / checkpoint と組み合わせた opponent を leaderboard team の再現とは呼ばない。

### 2.3 初期 5 系統の decision record

- フーディンは 2026-07-15 履歴標本の Gold / Silver / Bronze すべてで最多で、現在の公開 discussion とも方向が合う。
- イワパレス + メガガルーラは履歴 Gold 4/20、Silver 標本 3/20 で、現在の定性報告でも多数派に含まれる。
- ロケット団ミュウツー + ワナイダーは履歴 Gold のロケット団ワナイダー系 3/20 と現在の定性報告の両方で支持されるが、完全に同一の60枚 variant とはまだ証明できない。
- オーロンゲ系は履歴 Silver 標本 3/20 と現在の定性報告に現れる。ただし、ユキメノコ+マシマシラの正確な variant share は fresh census まで未確定である。
- ブリジュラス+エースバーンは履歴 Bronze 標本 6/20 で最も強く観測される。上位 share とは主張せず、扱いやすさ、既存資産、Bronze 脱出向けの独立仮説として明示的に残す。

ドラパルト、メガルカリオ、シロナのガブリアス等は reserve 候補である。現在 share の推定を作れない状態で registry を自動変更せず、初期 5 系統は再現可能な履歴証拠、定性の新着証拠、利用可能資産、およびユーザー決定で固定する。full sealed census 後に prevalence と seed qualification を再報告するが、変更は別 decision record と明示承認を必要とする。

### 2.4 production メタレポート

sealed census から Gold、Silver、Bronze を別々に、少なくとも次の粒度で出力する。

- team 数、coverage、未分類 / 複数候補分類率、観測デッキ比率と bootstrap CI
- archetype、support package、exact 60-card hash の三段階集計
- core / flex card の採用率、枚数分布、同一構築の集中度、archetype diversity / HHI
- rank / score 分布、Gold 内順位、Silver 上中下・Bronze 上中下の感度分析
- 過去 snapshot と比較できる場合の流入・流出・構築差分。ただし classifier version を揃える
- replay battle outcome と opponent policy provenance が揃う場合だけ matchup / seat matrix。deck share から相性因果を推測しない
- 5 対象と reserve の seed coverage、利用可能 teacher、観測層、選定 / 選外理由

レポートは row-level classification を辿れる machine-readable JSON / Parquet と、人間向け Markdown の両方を生成する。数値は `MetaAnalysisManifest` と census hash を伴わない限り更新済みの事実として扱わない。

### 2.5 Kaggle 提出・レーティング仕様と設計上の拘束

2026-08-01 時点の[公式 Kaggle competition specification](https://www.kaggle.com/competitions/pokemon-tcg-ai-battle/overview/description)では、submission は `main.py` を top level に置き、`deck.csv` を含む `.tar.gz` bundle とする。bundle 上限は 202,400 KiB = 197.65625 MiB、1 日 5 回まで提出でき、直近 2 submission だけが active になる。提出 agent は `/kaggle_simulations/agent/` に展開され、利用可能資源は CPU 200%（2 vCPU）、RAM 12,815,744 KiB（約 12.2 GiB）、agent disk 12,388,608 KiB（約 11.8 GiB）である。GPU と network availability は前提にしない。

新 submission は自己対戦による動作確認後、個別の submission として初期 rating `mu0 = 600` から評価 pool に入り、近い skill rating の agent と継続的に対戦する。新しい agent には早期 feedback のため episode が多めに割り当てられるが、過去 submission の rating を新しい重みへ継承する仕組みとして扱ってはならない。

したがって本設計は、次の拘束を production contract とする。

1. **提出済み bundle の内容は固定**であり、学習済み重み、deck、コードを対戦後にオンライン更新して次 game へ引き継ぐことを前提にしない。
2. Bronze 用 submission を登らせた後に Silver 用 submission へ置き換えると、後者は別 submission として評価される。live ladder を学習 curriculum として使わない。
3. agent へ現在の medal、rank、rating が観測として与えられる前提を置かない。tier による runtime model switch を実装しない。
4. 5 アーキタイプはローカル候補であり、1 submission の `deck.csv` は 1 件に固定する。
5. 競技終盤は primary champion と challenger / backup の active 2 slot を意図的に管理し、第三の提出で望む champion を非 active にしない。active slot と Kaggle UI 上の final selection 最大 2 件は別フィールドとして、submission ID と操作時刻を記録する。
6. final submission deadline は 2026-08-16 23:59 UTC、すなわち日本時間 2026-08-17 08:59 であり、その後も概ね 2026-08-31 まで leaderboard 安定化のため game が継続される。`LadderMechanicsManifest` の既定 `target_safe_upload_at_utc` は 24 時間前の 2026-08-15 23:59 UTC とし、変更時は decision record を要求する。実装量より、十分に検証済みの bundle を期限前に active 化することを優先する。
7. 手動提出後は `submitted -> validation_passed -> active_confirmed -> final_selected` を別状態として記録し、submission ID、Validation log、消費した日次枠、確認者を保存する。ローカル smoke だけで実環境 Validation 済みとは扱わない。

この節の仕様 snapshot は `LadderMechanicsManifest` に固定し、Kaggle 側の表示・規約が変わった場合だけ version を更新する。

## 3. 目的と非目的

### 3.1 目的

- 既知の強いデッキと方策を初期値にし、固定された 1 bundle が初期 rating から上位 rating 帯まで登る経路全体で頑健になるようにする。
- 5 アーキタイプをローカル候補として独立最適化し、共通評価後に 1 件の primary submission と最大 1 件の backup / challenger を選ぶ。
- Gold / Silver / Bronze の source rank band と、ローカル測定した opponent strength band を分離する。
- 同じ `policy_lineage_id` の checkpoint を broad -> middle -> high -> consolidation の順に継続学習し、過去層を一定割合で残して catastrophic forgetting を抑える。
- デッキ構築とゲーム内方策を交互に最適化し、どちらの改善かを識別できるようにする。
- 大量の CPU/GPU を、独立 seed、actor、教師探索、候補デッキ、対戦セルへ安全に並列化する。
- 強い結果だけでなく、再現可能な provenance、比較 schedule、失敗理由を残す。
- Gold / Silver / Bronze の観測デッキ分布、variant、core / flex、順位傾向を同じ sealed census から詳細に報告する。

### 3.2 非目的

- Kaggle へ自動提出すること。
- live Kaggle rating を学習状態または curriculum phase として利用すること。
- Bronze / Silver / Gold ごとに互いに無関係な production model を提出し、順番に登らせること。
- rank、medal、対戦相手の非公開 identity に応じて runtime checkpoint を切り替えること。
- leaderboard score だけを唯一の学習報酬にすること。
- 非公開情報を teacher、student、critic、評価器へ与えること。
- 既存 R2D3 checkpoint や Replay を互換性検査なしで新系列へ流用すること。
- 5 系統を 1 個の万能 checkpoint にまとめること。ただし 5 系統から最終提出候補を 1 件選ぶ共通 race は必須とする。
- 5 deck を 1 submission 内で動的に選ぶこと。
- 使用率だけを強さとみなし、低使用率の有力メタデッキを排除すること。
- 物理カードのデッキ評価を Kaggle simulator での性能と同一視すること。

## 4. 検討した方式

### 4.1 既存 R2D3 の拡張

最短で実装でき、Replay 効率と sparse reward への適性がある。一方、再帰状態の staleness、自己対戦の非定常性、複数選択データの欠落、現行成績の弱さを同時に抱える。主系列にはせず、修正した公平な対照として残す。

### 4.2 完全な greenfield simulator / MuZero 系列

自由度は高いが、正確な既存 simulator がある条件で世界モデルを再学習する利点が小さい。部分観測で belief を誤ると hidden information leak が起き、期限に対して実装範囲も大きすぎるため不採用とする。

### 4.3 既存 simulator を再利用する新しい modular specialist 基盤

actor-visible observation と合法 action enumerator を共通基盤にし、ExIt、V-trace、PPO、R2D3 を交換可能にする。デッキ探索、教師、学習、評価を manifest で分離でき、既存資産も teacher または seed として取り込める。これを採用する。

## 5. 対象アーキタイプと seed pool

以下の 5 runtime ID は **ローカル最適化 lane** であり、5 件を同時提出する計画ではない。各 lane の champion は最後に共通の `GlobalSubmissionSchedule` で比較し、1 submission につき 1 deck / 1 policy を選ぶ。競技期限までの critical path では、上位メタ証拠と既存実装資産が厚い 2〜3 系統を primary、残りを reserve として計算資源を段階配分してよい。

| runtime ID | アーキタイプ | 必須 core の代表 Card ID | 初期 seed / teacher |
|---|---|---|---|
| `alakazam` | フーディン | 741, 742, 743 | 2026-07-15 履歴標本で Gold 7/20・Silver 6/20・Bronze 8/20、現在の公開定性報告、`origin/agents/nihei-alakazam` の専用方策候補 |
| `grimmsnarl_froslass_munkidori` | オーロンゲ + ユキメノコ + マシマシラ | 646, 647, 648, 860, 104, 112。別 printing / support package は明示 variant として区別。CLI alias は `grimmsnarl_froslass` | 履歴 Silver 標本のオーロンゲ系 3/20、現在の公開定性報告、`origin/agents/ozawa-grimmsnarl-rule+RL` の利用資格未確定 teacher 候補 |
| `crustle_mega_kangaskhan` | イワパレス + メガガルーラ ex | 344, 345, 756。532, 533 は別 variant として区別 | 履歴 Gold 4/20・Silver 標本 3/20 と現在の公開定性報告で支持されるメタ候補 |
| `rocket_mewtwo_spidops` | ロケット団ミュウツー + ワナイダー | 400, 401, 431 | 履歴 Gold のロケット団ワナイダー系 3/20、現在の公開定性報告、`origin/agents/ozawa-rocket-rule+RL` の利用資格未確定候補 |
| `archaludon` | ブリジュラス ex | 169, 190 | 履歴 Bronze 標本のブリジュラス+エースバーン 6/20、扱いやすさの定性報告、`tomatomato_archaludon` と `lucifer19_battlecore` / `plamen06_steel` の既存候補 |

P0 の初期 resource plan は、再現可能な履歴標本、現在の公開定性報告、既存方策資産を合わせて証拠が厚い `alakazam`、`crustle_mega_kangaskhan`、`grimmsnarl_froslass_munkidori` を primary 3 lane とする。これは fresh census による現行順位を主張するものではない。asset qualification または E2E gate が失敗した lane は、事前順序 `rocket_mewtwo_spidops`、`archaludon` で置換する。これは 5 系統 registry の削除ではなく、期限内の段階配分であり、実際に学習した状態は `CandidateSetManifest` に記録する。

各 runtime ID は optimize 開始前に 2〜3 件の `SeedAssetManifest` を registry へ列挙する。必須項目は 60 枚の canonical multiset SHA-256、raw file SHA-256、source path、immutable commit または submission / episode / player index、asset class (`deck_only` / `runnable_rule` / `checkpoint_teacher`)、利用境界 (`local_eval_only` / `teacher_only` / `bundle_allowed`)、policy compatibility、card database version、合法性結果である。カード列順に依存する canonical hash は使用しない。

公開 replay から得た資産は原則 `deck_only` であり、元 team の方策を復元したとはみなさない。`SOURCE.md` が local offline evaluation のみに制限する agent は、そのまま提出 bundle または再配布物へ混入させない。派生 checkpoint の扱いも source policy と競技規約を qualification で判定する。

既知のブリジュラス候補は上記 commit 内に固定され、file SHA-256 は `opponents/tomatomato_archaludon/deck.csv` が `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`、`opponents/lucifer19_battlecore/deck.csv` と `opponents/plamen06_steel/deck.csv` がともに `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` である。同一 canonical deck は 1 seed と数え、source だけが異なる重複で 2 件を満たしたことにしない。

必要数、provenance、利用境界、policy compatibility のいずれかが不足した runtime ID は起動時に失敗させ、別資産を推測で補完しない。

シロナのガブリアスを第 1 reserve とする。初期 5 系統を増やすのではなく、定期メタ監査または 5 系統の継続的不振があった場合の交替候補とする。

## 6. 全体アーキテクチャ

```text
Frozen Meta Census
       |
       +--> Source Rank-Band Analysis (Gold / Silver / Bronze provenance)
       |
       +--> Local Opponent Strength Calibration (measured proxy strength)
       v
Archetype Registry -> 2〜3 Deck Seed Pool -> Constrained Deck Search
       |                       |                       |
       v                       v                       |
Rule BC Foundation -> Algorithm Benchmark             |
       |                       |                       |
       +--> PIMC / ISMCTS teacher gate                |
                               |                       |
                               +-----------+-----------+
                                           v
                                  Joint Deck-Policy Race
                                           |
                                           v
                                      DeckLockDecision
                                           |
                                           v
                           Single-lineage Ladder Curriculum
                 broad -> middle -> high -> all-band consolidation
                                           |
                                           v
                           Cross-Archetype Global Submission Race
                                           |
                                           v
                    Primary Submission Bundle + Backup / Challenger
```

実装はリポジトリの正規 namespace である `src/mage_ptcg/` 配下へ、新しい `mage_ptcg.meta_specialist` package として置く。リポジトリに存在しない top-level `rl/` は新設しない。既存 simulator、observation adapter、合法手 enumerator は adapter 経由で再利用するが、旧 o6 / R2D3 の artifact identity を新系列へ持ち込まない。

主要 component は次の責務に分ける。

| component | 責務 |
|---|---|
| `meta_census` | leaderboard snapshot、公開 replay、source rank-band 分布、rate-limit-safe resume |
| `strength_calibration` | 固定 reference panel への cross-play、非推移性を保つ matchup matrix、CI、difficulty band |
| `archetypes` | core signature、variant、seed pool、mutation constraint |
| `action_set` | 単一選択と複数選択の共通 actor-visible action contract |
| `teachers` | rule agent、PIMC / ISMCTS、既存 checkpoint の teacher adapter |
| `learners` | ExIt 補助付き V-trace、recurrent PPO、修正版 R2D3 |
| `deck_search` | core-preserving mutation、広域 mutation、候補 race |
| `curriculum` | 同じ checkpoint lineage に対する broad / middle / high / consolidation phase、rehearsal floor |
| `league_eval` | opponent mixture、schedule、ascent / top-band suite、統計、昇格判定 |
| `global_selection` | 2 件以上の trained champion の共通比較、primary / backup 指名 |
| `submission` | 1 deck / 1 policy bundle、202,400 KiB 上限、top-level file、CPU-only self-play smoke、active / final-selection plan |
| `artifacts` | manifest、content hash、atomic write、resume |
| `orchestrator` | resource planning、job graph、retry、CLI |

## 7. 同一性と artifact 契約

新系列は少なくとも次の manifest を持つ。

| manifest | 必須内容 |
|---|---|
| `LadderMechanicsManifest` | 公式提出形式、bundle 上限、active submission 数、final selection 数、初期 rating、matching、competition timeline、CPU / RAM / disk、agent path の確認日付き snapshot |
| `CensusManifest` | leaderboard zip hash、snapshot UTC、team 数、tier 境界、coverage、submission 選択規則、rank ごとの raw response / replay / extracted deck identity、全行 Merkle root |
| `CalibrationReferencePanelManifest` | version 固定した複数 reference deck / policy、cross-play schedule、pair / cluster key、band boundary、pool epoch |
| `LocalStrengthManifest` | opponent instance の deck provenance、policy hash、reference-panel matchup vector、集約 score / rating、CI、strength band または `ambiguous`、calibration schedule |
| `MetaAnalysisManifest` | census hash、classifier / core-signature version、row ごとの分類・競合、tier 集計、未分類率 |
| `ArchetypeManifest` | runtime ID、core signature、variant rule、mutation boundary |
| `SeedAssetManifest` | deck multiset / raw hash、immutable source、asset class、利用境界、policy compatibility、qualification |
| `DeckGenomeManifest` | canonical 60 枚 multiset、deck hash、source provenance、親 mutation |
| `FoundationInitManifest` | 共通 encoder 初期重み、observation / action schema、teacher dataset、card embedding、model config |
| `ActionSchemaManifest` | complete action contract、candidate key、min / max、order semantics、enumerator / feature digest |
| `AlgorithmRecipeManifest` | 全 loss、係数 schedule、unroll / burn-in、sampling、target 更新、lag 上限、optimizer、RNG |
| `ComputeBudgetManifest` | environment transition、teacher simulation、learner update、sampled transition、GPU 時間、wall-clock の各上限 |
| `PolicyLaneManifest` | algorithm、archetype、deck 拘束、recipe、seed、population / league epoch identity |
| `SubjectScopedReplayManifest` | subject deck / policy、opponent、seat、behavior policy、収集範囲 |
| `TeacherDatasetManifest` | teacher hash、公開観測境界、source 別 weight / clip / effective sample size、結果別件数、除外理由、matchup cap |
| `OpponentPoolManifest` | deck / policy hash、policy type、source rank band、local strength band、archetype、sampling weight、seat、version、pool epoch、seed namespace |
| `JointArmManifest` | deck、policy foundation、学習 recipe、resource budget の組 |
| `JointRaceSchedule` | opponent × seat × seed の完全セルと round 予算 |
| `RaceRoundResult` | 完了セル、score、CI、fault、timing、resource 使用量 |
| `DeckLockDecision` | 選択 deck、比較対象、共通 FoundationInit、短期 budget、`deck_lock_id`、新 `policy_lineage_id` |
| `PromotionDecision` | incumbent、challenger、統計 gate、採否理由 |
| `RuntimeConstraintManifest` | bundle size / hash、依存、CPU-only 動作、decision / game deadline、許可 asset |
| `CurriculumManifest` | 単一 policy lineage、phase 順序、各 phase の opponent strength mixture、過去層の最低混合率、親 checkpoint、optimizer 継続 / reset 規則 |
| `ChampionManifest` | candidate class (`checkpointed_specialist` / `static_rule_bundle`)、アーキタイプ内で採用した deck / policy、全親 manifest、最終評価 |
| `CandidateSetManifest` | lane ごとの `registered_unqualified` / `qualified_not_trained` / `trained_champion` / `withdrawn`、根拠と時刻 |
| `SealedScenarioBank` | candidate 非依存の opponent / version、seat、scenario seed、最大 candidate slot、game protocol、deadline |
| `GlobalSubmissionSchedule` | trained champion 共通の pair / cluster key、top-band primary、band safety、family-wise 処理、confirm schedule |
| `SubmissionFinalSchedule` | P0 最低 game / cell replicate、interim look、alpha spending、candidate family、schedule seal |
| `GlobalSubmissionDecision` | eligible champion、primary / backup、選外理由、多重比較処理、active / final-selection intent |
| `SubmissionBundleManifest` | exactly one deck hash、policy hash、top-level files、bundle size / hash、依存、runtime smoke、active-slot intent |
| `SubmissionLifecycleManifest` | submission ID、日次枠、`submitted` / `validation_passed` / `active_confirmed` / `final_selected`、log、確認者 |
| `WorktreeProtectionManifest` | 開始 branch / HEAD、porcelain status、隔離方針、ユーザー変更の保護方法 |
| `O6ReferenceInventoryManifest` | o6 path、参照元、置換先、test、復元 commit、削除可否 |
| `CompetitionDataRetentionManifest` | Competition Data の分類、access boundary、競技後削除期限、削除証跡、保持可能 artifact の根拠 |

`CensusManifest` の各行は少なくとも `(rank, team_id, submission_id, episode_id, player_index, raw_response_sha256, raw_replay_sha256, extracted_deck_sha256, extractor_version)` を持つ。simulator / rules / observation / action schema の source commit と content hash、card database / legality version、teacher belief / search config、opponent implementation、schedule / RNG namespace、device / precision / deterministic backend 設定も該当 manifest の再現性キーに含める。

上表は論理 schema であり、実装時に必ず 1 schema = 1 file として大量の manifest file を作る必要はない。`data.json`、`run.json`、`league.json`、`evaluation.json`、`submission.json` など少数の物理 document に統合してよい。ただし論理 field と hash chain は保持する。

全 artifact は content-addressed、atomic write とする。同じ path に異なる content hash がある場合は上書きせず失敗する。Replay は deck、policy、opponent、seat、behavior version、league epoch を識別できない限り再利用しない。

## 8. action-set 方策

### 8.1 共通表現

recurrent encoder は actor が観測可能な現在状態と履歴だけを入力する。固定巨大 action ID 全体へ softmax せず、simulator が列挙した合法候補を `score(history, action_features)` で採点する。

engine へ一度に渡して world state を進める完全な action object を、全 learner で共通する 1 個の RL action と定義する。単一選択は 1 candidate、複数選択は complete set / sequence を持つ。`DecisionEnvelope` は `decision_id`、schema version、select type、stable candidate key、candidate feature schema hash、min / max selection、order semantics、enumerator digest、complete-action hash を保持する。

環境側 recurrent state、reward、discount、environment transition count は complete action の前後で一度だけ進める。複数選択 decoder の途中状態は一時的な selection context とし、world transition や疑似 reward を作らない。

### 8.2 複数選択

順序なし集合は、stable candidate key の昇順だけを許す canonical without-replacement 自己回帰分布とする。選択後は選択済み候補とそれ以下の key を mask するため、各集合はただ 1 本の生成列へ対応する。`STOP` は minimum selection を満たした後だけ合法で、maximum selection では強制する。これにより保存する列の log-prob の和が complete set action の正確な log-prob になる。

engine 上で順序が意味を持つ場合は engine sequence を complete action とし、順序なし集合と schema を分ける。全順列の確率和や近似 set likelihood は初期実装に混ぜない。将来導入する場合は別 schema version、誤差試験、全 policy lane での同一解釈を必須とする。

- 教師収集、ExIt、V-trace、PPO、R2D3 adapter、評価で同じ enumerator と canonicalization を使う。
- top-k へ縮約して元の選択を失う変換は禁止する。
- 複数選択を理由に game または episode を黙って除外しない。
- candidate key は action type、公開 source / target object identity、parameter を schema 固定順で符号化し、simulator の列挙順に依存しない。同名・同 Card ID の複数 object は actor-visible な zone / slot identity で区別し、同じ公開状態と schema で安定する。
- 同じ `DecisionEnvelope` の候補列を任意に shuffle しても、stable key へ戻した policy distribution、complete-set probability、Q target max、選択 action は不変でなければならない。
- selection 中に候補を再列挙する schema は、各段階の digest と完全 action への復号結果を保存する。

### 8.3 合法性

方策系は合法候補だけで確率を正規化する。Q 系は complete set / sequence action encoder `Q(h, complete_action_features)` を持ち、online action selection と bootstrap target max の双方で合法な complete action だけを厳密列挙する。選択後は simulator へ返す前に共通 validator を通す。

完全列挙が事前設定した memory / latency 上限を超える action schema では、R2D3 lane の qualification を失敗させる。beam search や疑似 micro-step を primary algorithm gate に黙って混ぜず、「性能負け」と「同一 contract で実行不能」を区別して報告する。micro-step 化は semi-MDP 等価性と Bellman target の別設計・試験を承認した場合だけ新 schema として導入する。

### 8.4 model contract

共通 backbone は、card ID / card metadata embedding、zone ごとの permutation-invariant encoder、公開 scalar / phase feature、直前の公開 action / reward、recurrent memory を結合する。候補 action encoder と backbone の cross-attention または bilinear scorer で可変候補を採点し、policy lane は masked logits と value head、Q lane は complete action head を持つ。

`FoundationInit` が共有するのは observation encoder、card embedding、recurrent backbone までとする。actor/value head と Q head は意味が異なるため同一 tensor を強制せず、同じ model-capacity envelope と決定的 seed で lane 別に初期化する。新しい mutation deck のカードも card database 由来 feature で表現し、seed deck 専用の固定 action ID に依存しない。

## 9. 教師と ExIt

### 9.1 teacher の種類

- アーキタイプ専用 rule agent
- actor-visible belief から determinization する PIMC / ISMCTS
- 互換性監査済みの既存ニューラル checkpoint

検索教師は train-time 専用とし、student の提出時推論は原則 1 回の recurrent forward で完結させる。推論時検索は、最終的に latency budget 内で明確な改善を再現した場合だけ別 arm として評価する。

### 9.2 hidden-information 境界

`BeliefState(h)` は actor-visible observation、公開履歴、公開済みカード、明示的に公開と定義した deck 情報だけから構成する。teacher は opponent の真の非公開手札、deck 順、将来 RNG、完全 simulator state を直接参照しない。

PIMC root は live simulator state の clone を受け取らず、redacted public snapshot と独立 determinization seed から再構築する。各 hidden state は `z ~ q(z | h)` として独立に sample し、既知 card multiset、without-replacement 制約、未知情報 prior、既知 deck 仮説を versioned belief config に固定する。opponent rollout policy は、その determinization 内で割り当てられた simulated private state だけを参照できる。

search RNG は live episode RNG から fork / copy せず、`teacher_run_id, public_history_hash, determinization_index, rollout_index` から独立に導出する。真の hidden state との一致、真の将来 RNG、真の episode seed を feature、label、teacher manifest に記録しない。

teacher が利用できる opponent deck / policy 情報にも同じ境界を適用する。学習 scheduler は opponent instance の真の deck hash や policy hash を sampling、集計、stratification に使ってよいが、それらが runtime observation に含まれない限り teacher の decision feature または determinization prior へ直接渡さない。公開済みカードから deck family を推定する場合は、真の identity ではなく `q(deck_family | public_history)` を使う。

leak test は、actor-visible history が同一で真の相手手札、deck 順、future RNG、未公開 deck identity、未公開 policy identity だけが異なる状態を作り、同じ teacher seed なら teacher target が一致することを検証する。異なる teacher seed では単一出力の一致ではなく、反復 target 分布の同等性を検定する。

### 9.3 教師データの重み

terminal outcome だけで BC / ExIt label を捨てると、カード運と相手応答の影響が大きい本タスクでは survivorship bias が生じる。したがって production の既定は次とする。

- leak、fault、illegal、schema 不明がない decision を source 別の品質規則へ通し、policy target 候補とする。source を跨いだ未校正の単一 `teacher confidence` は使わない。
- PIMC / ISMCTS は合法 complete action の root visit distribution を主 target とし、terminal outcome で hard filter しない。root value gap、search entropy、simulation 数、deadline 完了率を記録する。
- rule / checkpoint の one-hot action は、独立 development schedule で source、matchup、phase 別に校正した reliability を上限付き weight として使う。校正値がない source は production weight を持てない。
- win / draw / loss は value / return / advantage target に使用する。policy imitation に outcome weight を掛ける場合は ablation とし、loss decision を既定で 0 weight にしない。
- fault、illegal、private leak、schema 不明は game 全体を除外する。
- 同一 matchup、同一 teacher、同一 exact deck が dataset を占有しないよう cap を設ける。

旧文書にある「勝局だけ BC、敗局は BC に使わない」は新系列の既定から外す。

teacher ablation は `all_valid_quality_weighted`、cross-fitted な `positive_advantage_weighted`、旧 `win_only` を分離する。weight 関数、clip、source ごとの effective sample size を `TeacherDatasetManifest` に保存する。

### 9.4 PIMC 再現 gate と fallback

過去の +4.94 percentage points は採用根拠ではなく再現対象の仮説とする。fresh schedule は PIMC 実行前に opponent / version、seat、scenario block、root sampling、determinization 数、simulation 数、temperature、rollout policy、比較対象、deadline、統計単位を seal する。

PIMC 採用は二段 gate とする。第 1 段は search policy と no-search baseline の paired search gate、第 2 段は同一 V-trace recipe、同一 actor transition budget で学習した student distillation probe と rule-BC student の独立 held-out gate である。teacher target 生成 root、probe 学習 trajectory、search gate 評価、student 評価は互いに disjoint な seed namespace とする。

各 gate は 1,024 / 2,048 / 4,096 局で interim look を行い、family ごとの片側 alpha 0.025 を Bonferroni で各 look 0.008333 に固定する。成功条件は cluster-bootstrap lower bound が 0 より大きく、point estimate が +3 percentage points 以上であること、futility は 4,096 局時点で成功条件を満たさないこととする。途中 look で成功しない場合だけ次へ進む。両 gate を通らない限り PIMC target を本学習へ使用せず、teacher simulator transition も `ComputeBudgetManifest` に加算する。

PIMC が不採用の場合、`exit_vtrace` と偽って扱わず、rule BC で初期化した `rule_bc_vtrace` を fallback とする。search teacher なしでも algorithm comparison は継続できる。

## 10. 学習方式の公平な gate

本命は search teacher で改善した recurrent IMPALA/V-trace とするが、algorithm 自体の差と search 計算量の差を混同しないため比較を二段階に分ける。

### 10.1 Benchmark A: algorithm comparison

1. `recurrent_vtrace`: 非同期 actor と V-trace
2. `recurrent_ppo`: 同じ public-state backbone と opponent league を使う on-policy 対照
3. `repaired_r2d3`: burn-in、現在重みでの recurrent state 再構築、Double-Q、target mask、source-scoped Replay、complete-action Q を満たす価値ベース対照

共有する `FoundationInit` は actor-visible encoder、card embedding、recurrent backbone と、各 lane で意味を保てる rule BC / value initialization までとする。policy visit distribution を Q learner へ渡して「同じ ExIt」とは呼ばない。lane 固有 head と objective は `AlgorithmRecipeManifest` に固定する。

primary 比較は同じ opponent schedule と actor environment transition budget で行う。加えて learner update、sampled transition、teacher simulation transition、policy lag、GPU 時間、wall-clock curve を別々に上限・報告する。wall-clock と environment transition のどちらか一方だけを揃えた結果で優劣を断定しない。

### 10.2 Benchmark B: teacher / search contribution

Benchmark A で qualification した V-trace recipe を固定し、`rule_bc`、`pimc_exit`、`rule_plus_pimc` を比較する。PIMC target は合法 complete action の root visit distribution とし、temperature、teacher loss coefficient、state sampling rate、refresh cadence、offline-to-online 切替点を固定する。

R2D3 の Q-compatible teacher objective は別 ablation とし、Benchmark B の ExIt 結果に混ぜない。PIMC 再現 gate が失敗した場合は Benchmark B の PIMC arm を不採用にし、Benchmark A は 3 algorithm lane とも no-search foundation で継続する。その勝者を rule BC 付きで deck-policy race へ進める。

### 10.3 recipe、reward、league epoch

`AlgorithmRecipeManifest` は、V-trace の unroll / clipping / lag、PPO の recurrent rollout / GAE / epoch / ratio / advantage normalization、R2D3 の C51 / CQL / demo margin / BC / PER / n-step / replay ratio / target update を採用するか否かを含め、全 loss と schedule を固定する。recipe 変更は別 version とし、結果を混ぜない。

primary training reward は terminal の `win=+1`, `draw=0`, `loss=-1`、中間 0 とする。shaped reward は潜在関数ベースを含め別 ablation に限定し、全 lane で同じ定義を使い、promotion metric には使用しない。

V-trace trajectory は subject behavior version、masked behavior log-prob、opponent ID / version、opponent sampling distribution version、league epoch を持つ。opponent mixture 変更時は新しい league epoch を開始する。V-trace ratio が補正するのは subject behavior と learner policy の差だけであり、opponent 分布差を補正したとはみなさない。旧 epoch の trajectory は recipe で定めた固定 age window 内だけ使用するか、value-only 補助へ限定する。

medal / strength curriculum の phase 境界では model weights を必ず親 checkpoint から継承する。optimizer state を継続するか reset するか、learning-rate restart を行うかは `CurriculumManifest` に固定する。別 phase を FoundationInit から再学習した run は curriculum continuation ではなく独立 ablation と呼ぶ。

各 recipe は 3 独立 training seed の事前定義 mean を primary、median と分散を補助として lane 選択する。3 seed で recipe 一般化を断定せず、最良 seed だけを選んで方式性能としない。提出候補 checkpoint は別の candidate-selection schedule で 1 件に指名してから untouched final schedule へ進め、recipe 比較と checkpoint 選択を区別する。PBT は方式決定後に learning rate、entropy、teacher loss、replay ratio などの schedule 探索へ使用し、初期方式比較には混ぜない。

## 11. デッキと方策の二時間尺度最適化

deck search / Joint Race は curriculum より前の deck-lock stage とする。1 cycle は次の順序で行う。

1. seed pool の deck-policy 組を qualification する。
2. 共通 FoundationInit から各 seed の専門方策を同一 recipe で学習する。
3. 方策 snapshot を固定し、各 deck から制約付き mutation を生成する。
4. deck-agnostic generic、互換性を満たす incumbent、archetype-compatible の複数 anchor で安価な deck screening を行う。
5. anchor 上位、stratified random、保護された広域 mutation、共通の短い FoundationInit からの joint warm-start を事前固定枠で Joint Arm へ昇格させる。
6. incumbent deck を含む全 arm を、同じ FoundationInit、seed、step budget から再学習する。
7. sealed schedule で共同評価し、勝者だけを次 cycle の incumbent にする。
8. deck search 終了時に `DeckLockDecision` を seal し、勝者へ新しい `deck_lock_id` と `policy_lineage_id` を発行して curriculum を開始する。

incumbent が学習済みである一方 challenger が fresh という不公平を避けるため、promotion race では incumbent deck も同じ recipe と budget で再学習する。異なる deck 間で PBT の重みを直接交換しない。

固定 card ID や特定枚数を仮定する rule / checkpoint は、その依存契約を満たす mutation にしか適用しない。generic anchor は card-pool hash 内の任意合法 deck で qualification する。anchor 順位と共同再学習後順位の相関を cycle ごとに監査し、相関が事前閾値を下回る場合は anchor-only screening を停止して joint warm-start 枠を増やす。

curriculum 開始後の mutation は、必ず新 `deck_lock_id` と新 `policy_lineage_id` を持つ branch とする。既存 champion の continuation や単純 promotion とは呼ばず、FoundationInit からの公平な短期比較、全 curriculum phase、未使用 final suite を再通過させる。

## 12. デッキ mutation

- 85% は core を維持する flex slot、カード枚数、trainer、energy、support Pokémon の mutation とする。
- 15% は package 単位の広域 mutation とする。
- 60 枚、カード枚数上限、simulator legality をすべて満たす。
- core signature を失った候補は元アーキタイプとして評価せず、新アーキタイプ候補へ隔離する。
- broad lane は Successive Halving の第 1 round で少なくとも 1 slot を保護し、短期分散だけで全滅させない。
- anchor score に依存しない stratified random slot も各 round で少なくとも 1 件保護する。
- deck proposal の重複は canonical hash で除外する。

deck-only 効果を測るため、複数の固定 compatible anchor による平均と worst-anchor 評価を持つ。最終判断は共同再学習した deck-policy arm で行う。

## 13. medal curriculum と opponent league

Gold、Silver、Bronze は **source rank band** であり、復元 deck を任意の rule / checkpoint と組み合わせただけでは、その opponent が Gold-strength になるとは限らない。curriculum は次の 2 軸を分離する。

| 軸 | 意味 | 使用先 |
|---|---|---|
| `source_rank_band` | 元 leaderboard team の Gold / Silver / Bronze provenance | メタ分析、coverage、deck share、holdout stratification |
| `local_strength_band` | 固定 schedule で測定した proxy opponent の強さと CI | 学習 curriculum、難度調整、promotion evaluation |

`CalibrationReferencePanelManifest` は候補 champion から独立した複数の version 固定 reference deck / policy と cross-play schedule を持つ。`LocalStrengthManifest` は opponent instance ごとに deck hash、policy hash / type、source rank band、reference ごとの matchup vector、事前定義した集約 score / rating、CI、seat、fault、calibration pool を保存する。CI が band 境界を跨ぐ相手は `ambiguous` とし、単一 rating で非推移的な matchup matrix を捨てない。

curriculum の primary sampling key は `local_strength_band × opponent_archetype × policy_type × opponent_version` の層であり、source medal を strength label として直結させない。opponent policy/version または reference panel が変わるたびに新 `pool_epoch` を作り、再 calibration なしで旧 band を継承しない。

production policy は 1 本の checkpoint lineage を次の順序で継続学習する。比率は初期 default であり、final holdout を見ずに calibration / development pool だけで調整する。

| phase | lower-strength | middle-strength | high-strength | ambiguous | exploiters / self-play | 目的 |
|---|---:|---:|---:|---:|---:|---|
| `foundation` | 33% | 42% | 20% | 5% | 0% | 合法性、基本手順、広い matchup を安定化 |
| `ascent` | 13% | 42% | 30% | 5% | 10% | 中位から上位へ移る判断を改善 |
| `top_focus` | 8% | 22% | 50% | 5% | 15% | 上位 pool と近似 best response に集中 |
| `consolidation` | 13% | 27% | 40% | 5% | 15% | 下位・中位への catastrophic forgetting を抑えた最終固定 |

重要な規則は次のとおりである。

1. phase は Kaggle 上の live rating、rank、medal で切り替えない。ローカル gate と固定 transition budget で進める。
2. `theta_foundation -> theta_ascent -> theta_top -> theta_final` は同じ `policy_lineage_id` を持つ。
3. 過去 strength band を 0% にしない。V-trace / PPO では opponent sampling floor、R2D3 では source-stratified Replay floor で維持する。
4. final policy は tier / rating を入力に取らず、全 phase で同じ actor-visible observation contract を使う。
5. curriculum の比較対象として、同じ総 transition budget の `static_all_band` と `staged_without_rehearsal` を走らせ、段階学習そのものの効果を確認する。
6. 3 条件は同じ frozen pool epoch、各 phase の absolute transition budget、teacher simulation、learner update、policy lag 上限、training seed、optimizer restart 規則を使う。curriculum の因果比較中は各 arm から生成する動的 self-play / exploiter を使わず、事前生成・calibration 済みの共通外生 snapshot pool だけを使う。`static_all_band` は全 phase 合計の `band × archetype × policy_type × version` marginal exposure を時間順序なしで再現し、`staged_without_rehearsal` は過去 band floor だけを 0 にする。動的 self-play / exploiter を含む production recipe は別 experiment とし、因果比較へ混ぜない。満たせない実験は `feasibility_smoke` であり curriculum 効果の根拠にはしない。

各 opponent instance は deck hash、policy implementation / hash、policy type、source rank band、local strength band、sampling weight、seat protocol、scenario seed namespace、version、asset 利用境界を持つ。leaderboard 復元 deck と別 policy の組は `tier deck-conditioned proxy` と呼び、元 leaderboard 方策の再現とは主張しない。

train、candidate selection、final pool は opponent instance 単位だけでなく、可能な範囲で exact deck hash、policy family、source team provenance でも分離する。final pool には学習で未使用の proxy、historical checkpoint、archetype holdout、deck-variant holdout を含める。

単一の最新自己対戦相手だけを使わず、次を混合する。

- source rank band を層化した固定外部 proxy
- アーキタイプ別 current / historical champion
- rule teacher
- 自己対戦 checkpoint population
- exploiters / approximate best response

exploiters は対象 mixture、学習 budget、停止条件、best-response 品質指標を固定する。census は 100% coverage を目標とするが、外部 API の一時的欠損 1 件だけで全 production を永久 block しない。`CensusQualification` に最低 coverage、欠損の系統性検査、感度分析を定義し、原則 98% 以上かつ Gold 100%、欠損が特定 archetype / rank band に偏らない場合に限定して seal を許す。100% 未満であることは全 report と opponent weight に明示する。

final 評価では「低位からの ascent robustness」と「上位近傍での stationary strength」を別々に報告し、後者を primary、前者を safety gate とする。

## 14. 評価、早期選抜、昇格

学習用、候補選択用、最終評価用の opponent instance、seed namespace、schedule を分離する。candidate 学習前には candidate 非依存の `SealedScenarioBank` を hash 固定し、optimizer、deck search、PBT、teacher tuning からアクセスできないようにする。bank は opponent binary / policy / deck hash、seat、scenario RNG seed、最大 candidate slot 数、game protocol、deadline を含む。`CandidateSetManifest` 確定後かつ評価結果を開く前に trained candidate ID を slot へ bind し、実 candidate 数と alpha plan を含む `GlobalSubmissionSchedule` を seal する。scenario seed は agent 観測へ渡さない。一度結果を開示した bank / schedule は retire し、次 cycle の調整や再昇格に再利用しない。

### 14.0 評価 suite と cross-archetype 最終選抜

同じ policy が初期 rating から上位まで固定されたまま戦うことを反映し、少なくとも 2 suite を持つ。

- `ascent_suite`: lower / middle / high の事前定義した disjoint block で各 band の score と fault を測る頑健性 suite。block 順序で固定 policy の能力は変化しないため、検証済み matchmaking simulator がない限り rating-proxy trajectory と最大 drawdown は診断値に限定し、実 ladder 上昇の再現とは呼ばない。
- `top_band_suite`: high-strength proxy、historical champion、未使用 exploiters を中心に stationary performance を測る。

アーキタイプ内 champion を決めた後、`CandidateSetManifest` 上の 2 件以上の `trained_champion` を同じ `GlobalSubmissionSchedule` で比較する。`qualified_not_trained` と `withdrawn` は P0 選抜を block しない。ここで primary submission を 1 件、backup / challenger を最大 1 件だけ指名する。own-deck archetype が違う candidate 間でも schedule、pair key、cluster unit、seat、seed、runtime budget を共通化する。

Global Race は top-band の multiplicity-corrected score を primary とし、各 strength band で incumbent 比 simultaneous non-inferiority lower bound が -3 percentage points より大きいか、schedule seal 前に定めた absolute floor を満たすことを safety gate とする。safety 非通過 candidate は top-band 首位でも primary から除外する。candidate family は Holm または事前登録した Westfall-Young で処理し、可能なら別の未使用 confirm schedule で primary / backup を再確認する。

P0 の `SubmissionFinalSchedule v1` は candidate ごとに最低 4,096 完了 game、事前 seal した各 `opponent × version × seat` cell に最低 32 paired replicate、interim look 1,024 / 2,048 / 4,096 とする。唯一の `alpha_plan` は family の片側 alpha 0.025 を使い、各 candidate の look 別 p 値を `p_seq = min(1, 3 × min(p_1024, p_2048, p_4096))` へ変換した後、評価開始前に固定した K candidate の `p_seq` へ Holm 法を適用する。各 look の単独成功境界 0.008333 はこの within-candidate Bonferroni に由来し、別の alpha gate を重ねない。cell 数のため 4,096 を超える場合は最小の完全 block まで増やす。最低 schedule を完了できない candidate は `provisional_manual_candidate` としてのみ報告し、`statistically_promoted` と記録しない。

### 14.1 Successive Halving

| round | 1 arm あたりの完了 game | 用途 |
|---|---:|---|
| 1 | 1,024 | 広い候補の screening |
| 2 | 4,096 | matchup / seat 分散を含む選抜 |
| 3 | 16,384 | champion challenger 選抜 |
| final | 最大 32,768 | sealed promotion 判断 |

各 round は arm 数上限と総 game 上限も事前登録する。例として round 1 は最大 32 arm、round 2 は 8 arm、round 3 は 2 arm、final は 1 challenger と incumbent に限定する。game / arm だけを定め、候補数が増えるたび総計算量が無制限に増える設計にしない。

各 round は opponent × opponent-version × seat × scenario-seed の完全ブロックを単位とする。セル途中の結果で候補間比較をしない。早期停止は事前登録した minimum sample、futility boundary、alpha-spending を満たす場合だけ許可し、好成績を理由に未完了 schedule の候補を即昇格させない。

### 14.2 指標

- `win=1`, `draw=0.5`, `loss=0` の score rate
- opponent-equal score rate
- worst-opponent score rate
- seat 別 score rate
- fault / timeout / illegal rate
- decision latency p50 / p95 / p99
- CPU、RAM、GPU、VRAM、simulator throughput

### 14.3 promotion gate

challenger は次のすべてを満たす場合だけ incumbent に昇格する。

1. pair key `(opponent_id, opponent_version, seat, scenario_seed, replicate)` を共有する challenger と incumbent の score difference について、`SubmissionFinalSchedule.alpha_plan` から得た `p_seq` が candidate family の Holm gate を通り、effect estimate が 0 より大きい。PromotionDecision と GlobalSubmissionDecision は独自 alpha を定義せず、この plan の結果だけを参照する。
2. schedule seal 前に固定した全 `opponent × opponent-version × seat` cell で、最低 replicate と事前 power 計算を満たし、simultaneous non-inferiority lower bound が -3 percentage points より大きい。結果を見た後の cell 統合は禁止し、検出不能な細分 cell は seal 前に層化 group へ統合する。
3. fault、timeout、illegal が 0。
4. runtime bundle が提出制約内で qualification を通る。
5. final schedule の全セルが完了している。

1 archetype / cycle につき final 前に challenger を原則 1 件だけ指名する。複数件を同じ promotion family で比較する場合は Holm 法など事前指定した family-wise 補正を使う。recipe の一般比較は game を独立標本とせず training seed を上位単位にした階層的な結果も報告する。

logical fault、illegal、agent timeout は 0 条件に含める。runner / infrastructure crash、API 障害、host preemption は別分類で block 全体を challenger / incumbent とも同じ規則で再実行し、片方だけ都合よく再試行しない。再実行履歴は schedule result に残す。

改善しない系統を無理に昇格させない。自動 Kaggle submission は行わない。

## 15. 計算資源設計

resource planner は実行時に CPU、RAM、GPU、VRAM、利用可能 disk、simulator throughput を測定し、manifest へ記録する。

- CPU は environment actor、PIMC / ISMCTS、deck candidate evaluation に使う。
- GPU は batched inference と learner に使う。
- BF16、pinned memory、非同期 actor、inference batching、Replay sharding を hardware が安全に対応する場合に使う。
- P0 で実学習する primary 2〜3 archetype は各 recipe 3 独立 seed を必須とし、計算資源が増えた場合は seed、候補、対戦セル、teacher simulation を先に増やす。5 archetype × 3 algorithm × 3 seed の完全 factorial は deferred とする。
- model depth / width は同一 step の ablation で改善した場合だけ増やす。
- actor / worker 数は短い throughput sweep で増やし、throughput 低下、RAM / VRAM 圧迫、fault 増加時は自動的に前段階へ戻す。
- validation worker 12 で不安定だった過去記録を初期安全上限の参考にし、固定値としては使わない。

各 run の `ComputeBudgetManifest` は actor environment transition、teacher rollout / simulator transition、learner update、sampled transition、replay ratio、最大 policy lag、CPU core-hour、GPU-hour、wall-clock の上限を別々に持つ。資源追加による高速化と algorithm への追加計算を区別し、方式比較では同じ primary budget、実運用では最終 score を優先した performance frontier の両方を残す。

`RuntimeConstraintManifest` は競技規約と現行 runner から 202,400 KiB の bundle size、2 vCPU、12,815,744 KiB RAM、12,388,608 KiB agent disk、`/kaggle_simulations/agent/`、許可依存、CPU-only 必須動作、1 decision の p95 / p99 と hard timeout、1 game deadline、bundle content hash を固定する。GPU と network を提出時に要求してはならない。数値は manifest default とし、規約または runner 更新時は新 version として qualification をやり直す。

競技 critical path と AI エージェント運用方針は技術 architecture から分離し、`IMPLEMENTATION_PLAN.md` に置く。使用する coding model 名は可用性が変化するため、学習設計の再現性キーに含めない。

2026-08-01 から final submission deadline の 2026-08-16 までは 15 日しかない。したがって、runtime package、action correctness、1 本の強い learner、連続 curriculum、cross-archetype final selection を P0 とする。full 3-algorithm x 5-archetype x 3-seed、PIMC 本採用、広域 deck search、o6 cleanup は P0 が完成した後だけ実行する。

## 16. census の取得と rate limit

leaderboard zip は一度取得した snapshot の SHA-256 を `census_id` とし、resume 中に更新しない。

取得状態は単一 writer の SQLite に、rank、team ID、leaderboard timestamp / score、submission ID、episode ID、replay hash、deck hash、API stage、attempt、`not_before_utc` とともに保存する。

state machine は `pending`、`submission_fixed`、`episode_fixed`、`replay_fetched`、`deck_extracted`、`qualified`、`retry_wait`、`terminal_failure` を持つ。leaderboard が submission ID を直接与える場合はそれを使う。ない場合は successful / scored submission の public score 最大、submitted-at 新しい順、submission ID 小さい順で決定する。episode は完全な両 deck を含む replay のうち episode ID が最小のものを決定的に選び、候補一覧の response hash も保存する。

- raw replay cache key は `census_id/team_id/submission_id/episode_id/player_index`、抽出 deck key はこれに extractor / card-schema version を加える。
- 初期 pacing は API credential ごとに 1 worker、最大 1 in-flight request、request 間隔 2 秒とする。100 成功かつ 429 なしの window ごとに間隔を 10% だけ短縮し、floor 0.5 秒を下回らない。worker 増加は明示 throughput experiment として別 manifest にする。
- 全 response status、取得時刻、retry-relevant header、body hash を保存し、`Retry-After` があれば厳守する。
- 最初の 429 で global circuit breaker を開き、全 worker を止める。header がない場合は 60 秒から指数的に cooldown を増やす。解除時は 1 probe request だけを許し、成功後に通常 pacing へ戻す。
- 408、5xx、network reset は上限付き exponential retry、401 / 403、schema / permission error は terminal とする。分類は config version に固定する。
- 小 batch ごとに checkpoint し、process を保持したまま長時間 sleep しない。
- 同じ episode replay は 1 回だけ取得する。
- Gold 100% と全体 98% 以上を既定 production threshold とし、欠損の rank / archetype / API failure の偏りがないことを感度分析する。100% を達成できる場合はそれを優先する。threshold 未達の run は `provisional_dev` から昇格できない。

## 17. 失敗時と resume

| 状況 | 動作 |
|---|---|
| hidden-information leak | teacher dataset と該当 run を seal せず停止 |
| illegal action / fault | 該当 game を破棄し、qualification または promotion を失敗 |
| 複数選択 schema 不一致 | 変換で補わず run を停止 |
| actor crash | 完了 block は保持し、未完了 block だけ別 worker へ再割当 |
| learner / GPU crash | atomic checkpoint から再開し、optimizer / RNG / Replay cursor を検証 |
| disk 不足 | 新規 actor を止め、保持対象を削除せず cleanup plan を生成 |
| manifest hash 不一致 | 上書きせず新 output root を要求 |
| stale behavior policy | version / log-prob を復元できない軌跡を V-trace 対象から除外 |
| 評価セル欠落 | score を集計せず schedule を未完了として扱う |

## 18. CLI

```bash
# 固定メタ snapshot の取得
python -m mage_ptcg.meta_specialist census \
  --leaderboard-zip SNAPSHOT.zip \
  --state census.sqlite

# source medal とは独立に proxy opponent のローカル強度を測る
python -m mage_ptcg.meta_specialist calibrate-opponents \
  --census CENSUS_MANIFEST \
  --schedule CALIBRATION_SCHEDULE

# seed / provenance / runtime 利用境界の起動前検査
python -m mage_ptcg.meta_specialist qualify-assets \
  --archetype all \
  --registry ARCHETYPE_REGISTRY

# 共通予算で algorithm lane を比較
python -m mage_ptcg.meta_specialist benchmark-algorithms \
  --archetype alakazam \
  --opponent-pool CALIBRATED_POOL \
  --budget ALGORITHM_BUDGET

# 同じ checkpoint lineage で ladder curriculum を継続学習
python -m mage_ptcg.meta_specialist train-curriculum \
  --archetype alakazam \
  --curriculum ladder_ascent_v1 \
  --foundation FOUNDATION_MANIFEST \
  --compute auto

# 指定 phase から親 checkpoint / optimizer / RNG を検証して再開
python -m mage_ptcg.meta_specialist resume-curriculum \
  --parent-checkpoint PARENT_CHECKPOINT \
  --next-phase top_focus

# アーキタイプ内の deck-policy 共同最適化
python -m mage_ptcg.meta_specialist optimize-joint \
  --archetype alakazam \
  --curriculum ladder_ascent_v1 \
  --compute auto

# 2 件以上の trained archetype champion から提出候補を 1 + 1 件に絞る
python -m mage_ptcg.meta_specialist select-submission \
  --champion-registry CHAMPION_REGISTRY \
  --schedule GLOBAL_SUBMISSION_SCHEDULE

# sealed schedule の再評価
python -m mage_ptcg.meta_specialist evaluate \
  --challenger CHALLENGER_MANIFEST \
  --suite ascent,top_band \
  --schedule FINAL_SCHEDULE

# top-level main.py / deck.csv、bundle size、依存、self-play smoke を検査
python -m mage_ptcg.meta_specialist package-submission \
  --decision GLOBAL_SUBMISSION_DECISION \
  --output submission.tar.gz

# source rank-band だけを分析する診断。production model switch には使わない
python -m mage_ptcg.meta_specialist report-meta \
  --source-rank-band gold,silver,bronze

# 削除前の参照・容量監査
python -m mage_ptcg.meta_specialist cleanup-plan \
  --output cleanup-manifest.json
```

CLI は deck、checkpoint、Replay、opponent pool を暗黙に推測しない。全 input を manifest または config で固定する。

production CLI に `--target-tier` を与えて別 model を作る方式は廃止する。source medal の指定は census / report / evaluation stratification に限定する。学習時は `--curriculum` と親 checkpoint lineage を指定する。

`train-curriculum`、candidate selection、promotion、package は qualified seed registry と opponent calibration を既定で要求する。未完成機能の smoke test だけは明示 `--provisional-dev` と別 output root で許可し、その artifact を production namespace へ resume / promote できない。

## 19. テスト戦略

### 19.1 unit test

- canonical deck hash と 60 枚制約
- core-preserving / broad mutation の境界
- action candidate mask と illegal probability 0
- canonical without-replacement set likelihood、stable key、停止条件、集合と生成列の 1 対 1 対応
- complete-action Q の合法全列挙と target max。列挙上限超過時の明示 qualification failure
- candidate 列挙順を shuffle しても key 対応後の policy distribution、complete-set probability、Q target max、選択 action が不変
- V-trace target、PPO ratio、Double-Q target の数値例
- manifest content hash と atomic write
- census state machine、submission / episode tie-break、player index、429 circuit breaker、resume

### 19.2 integration test

- 単一選択と複数選択を含む deterministic replay
- complete action の前後で environment transition / recurrent update が一度だけ進むこと
- teacher collection から ExIt dataset seal まで
- actor / learner crash 後の完全 resume
- seed pool から Joint Arm、race、PromotionDecision までの少数局 E2E
- 5 runtime ID の config / asset qualification
- broad -> middle -> high -> consolidation が同じ policy lineage と親 checkpoint chain を持つこと
- source tier / live rating を observation に入れず、tier ごとの runtime model switch が存在しないこと
- cross-archetype race から exactly one deck / policy の primary bundle が生成されること
- `submission.tar.gz` の top-level `main.py`、`deck.csv`、202,400 KiB 上限、`/kaggle_simulations/agent/` 相当 path での CPU-only import / self-play smoke
- 手動提出記録の `submitted -> validation_passed -> active_confirmed -> final_selected` 状態遷移。ローカル test は `validation_passed` を生成できない

### 19.3 security / fairness test

- actor-visible history を固定し、真の opponent hand、deck order、future RNG、未公開 deck identity、未公開 policy identity を変えても同じ teacher seed の target が変化しないこと
- live simulator clone / RNG が PIMC constructor へ渡らないこと
- teacher と student の observation contract が一致すること
- clustered bootstrap、simultaneous non-inferiority、alpha-spending、Holm 補正を含む実経路で A/A race の誤昇格率が gate と整合すること
- algorithm lane 間で environment step、seed、opponent schedule が一致すること

### 19.4 performance test

- actor 数、inference batch、learner batch ごとの games/sec
- RAM / VRAM high-water mark
- p95 decision latency
- long-run fault / deadlock / file descriptor leak

## 20. o6 と不要 artifact の削除

o6 の削除は score を直接改善せず、競技終盤の fallback と再現性を失う危険がある。したがって **2026-08-16 の final submission までは o6 の物理削除を critical path から外す**。

競技期間中に行うのは次だけとする。

1. `O6ReferenceInventoryManifest` に o6 V4 path、参照元、置換先、Git 復元 commit を列挙する。
2. 新系列が参照する interface だけを adapter または feature flag で分離する。
3. 新 runtime bundle に o6 不要 artifact が混入しないことを package test で確認する。
4. tracked / untracked artifact を削除しない。

競技終了後、または primary submission が十分安定した後に別 task / branch で次を行う。

- runner、integrity check、test、document の参照移行
- contract test と少数局 E2E
- tracked o6 code / test / evidence / tombstone / script の削除
- `rg`、test collection、bundle diff による参照切れ確認

`runs/`、Replay、checkpoint、cache は一括削除しない。cleanup planner が path、size、content hash、参照元、再生成方法、保持理由、復元可能性を列挙する。

現 champion、seed deck、teacher checkpoint、再現 manifest、ユーザーの dirty worktree 変更は競技中保持する。ただし Competition Data に該当する raw replay、deck snapshot、card data、抽出データはチーム内の競技目的に access を限定し、競技終了後に公式 Rules に従う `CompetitionDataRetentionManifest` で削除対象、期限、確認者を固定する。コード、モデル、hash だけを残せるかも Competition Data と分けて判定し、raw data を「永続再現 artifact」として無期限保持しない。untracked file は復元不能になり得るため、明示 cleanup manifest に含まれ、参照がないことを確認したものだけ削除する。広い glob、repository root、`runs/` 全体を destructive command の対象にしない。

実装開始前に current branch、HEAD、`git status --porcelain=v1` を `WorktreeProtectionManifest` へ記録する。現在の dirty worktree では checkout、merge、reset、untracked cleanup を行わない。clean base の隔離 worktree を使い、実装に必要な未 commit 資産がある場合は非破壊の patch / archive と content hash で明示移送する。

## 21. 実装の分割

期限と score 寄与で P0 / P1 / deferred に分ける。

### P0: final submission に必須

1. worktree 保護、公式 submission contract、bundle smoke、active-slot 運用記録
2. action-set contract、複数選択、合法性、hidden-information leak test
3. seed qualification、DeckLockDecision、固定 reference panel による local strength calibration
4. 既存最強 baseline と V-trace 系 1 lane の end-to-end 学習・resume
5. deck lock 後の same-lineage ladder curriculum と exposure-matched `static_all_band` 対照
6. primary 2〜3 archetype の champion 作成
7. cross-archetype global race、primary / backup 指名、submission package

### P1: P0 完了後に score 改善が見込めるもの

8. PIMC teacher の再現 gate と distillation probe
9. deck mutation と小規模 Joint Race
10. 残り archetype の qualification / training
11. repaired R2D3 または recurrent PPO の追加比較

### Deferred: 競技後でもよいもの

12. 5 archetype x 3 algorithm x 3 seed の完全 factorial
13. full-scale PBT、広域 deck search、最大 32,768 game final
14. o6 物理削除と大規模 artifact cleanup

各段階は独立した受入 test を持ち、後段が未完成でも前段の artifact を検証できるようにする。現在の dirty worktree を直接上書きせず、実装計画で隔離作業領域を確定する。

## 22. 受入条件

### 22.1 P0 submission readiness

期限前に手動提出候補を作る P0 の最低条件は次のとおりとする。提出候補 class は、curriculum で更新する `checkpointed_specialist` と、immutable policy hash を持つ `static_rule_bundle` の 2 種だけを許す。

- 1 submission が exactly one `deck.csv`、one policy identity、top-level `main.py` を持ち、202,400 KiB 以下で CPU-only package smoke を通る。`checkpointed_specialist` は exactly one checkpoint lineage も持つ。
- Gold / Silver / Bronze を runtime model selector として使わない。
- `checkpointed_specialist` では broad / middle / high / consolidation が同じ親子 checkpoint chain で継続され、phase ごとの別 model 再学習と区別できる。`static_rule_bundle` はこの chain を偽装せず、immutable policy hash と `not_applicable_static_policy` 理由を記録する。
- P0 primary lane と実際に使う代替 lane の seed pool を immutable provenance、asset class、利用境界付きで qualification できる。残り lane は qualification 未完了なら `registered_unqualified`、完了済みなら `qualified_not_trained` と区別し、P0 submission を block しない。
- Gold 100%、全体 98% 以上を既定 threshold として census を seal でき、欠損感度を報告できる。
- 3 source rank band の archetype / variant / exact deck、core / flex、順位感度、過去差分を `MetaAnalysisManifest` 付きで出力できる。
- proxy opponent を source medal ではなく local strength と CI で calibration できる。
- local strength は固定 reference panel の cross-play matrix から求め、境界を跨ぐ CI は `ambiguous`、policy version 変更は新 `pool_epoch` として扱える。
- 単一・複数選択を捨てず、全 learner / teacher が同じ complete-action contract を使い、1 complete action が 1 environment transition に対応する。
- hidden-information leak と illegal action を自動 test で検出できる。真の deck / policy identity も leak test に含める。
- 少なくとも既存 baseline と主 learner を同じ評価 schedule で比較できる。学習方式間は同一 training budget を要求するが、学習しない `static_rule_bundle` の training cost は 0 として別報告する。3 algorithm 完全比較は P1 / deferred としてよい。
- PIMC を使う場合、公開情報だけから再構築され、再現 gate と distillation probe を通らない target が production に入らない。
- アーキタイプ別に独立した specialist を学習、停止、resume、評価できる。
- deck と policy の交互最適化を行う場合、同じ FoundationInit と Joint Race で公平に比較できる。
- `ascent_suite` と `top_band_suite` を分離し、final policy が全 local strength band で fixed bundle のまま評価される。
- `ascent_suite` の各 band safety gate と、`SubmissionFinalSchedule v1` の最低 game / cell replicate / alpha を満たさない結果を統計的昇格と呼ばない。
- 2 件以上の `trained_champion` を bind した cross-archetype `GlobalSubmissionSchedule` から primary 1 件、backup / challenger 最大 1 件を指名できる。
- promotion gate を満たさない candidate が champion または submission decision へ昇格しない。
- active slot 2 件の意図を記録し、望む champion を第三の提出で非 active にしない運用手順がある。
- active slot と final selection を分け、手動提出後の Validation / active / final-selected 状態を submission ID と log で追跡できる。
- cleanup manifest なしに user artifact を削除しない。
- dirty worktree を記録・保護し、remote branch / Git history を cleanup scope に含めない。
- Kaggle submission を自動実行しない。

### 22.2 Full framework completion

P1 を含む framework 全体の完了には、P0 条件に加えて次を要求する。

- 5 runtime ID すべての seed pool を immutable provenance、asset class、利用境界付きで qualification できる。
- 5 lane の状態が `registered_unqualified`、`qualified_not_trained`、`trained_champion`、`withdrawn` のいずれかで追跡され、未学習を champion と誤表示しない。
- PIMC を production teacher として使う場合は paired search gate と独立 student distillation gate の双方を通る。
- deck mutation を使う場合は deck lock 後の変更を新 branch とし、full curriculum と未使用 final suite を再通過する。

## 23. 実装後の最初の実験

全計算量を投入する前に、提出仕様と single-lineage curriculum を先に検証する。

1. `LadderMechanicsManifest`、submission package smoke、active-slot 手順を固定する。
2. complete action-set と hidden-information leak gate を通す。
3. census を seal し、source rank band と固定 reference panel による local opponent strength を分離して calibration する。
4. 既存 champion / Rule v0 と主 learner の end-to-end smoke を 1 archetype で通す。
5. primary lane の seed deck を共通 FoundationInit と短期 budget で比較し、`DeckLockDecision` 後に lineage を開始する。P0 では broad mutation を行わない。
6. 同じ pool epoch、transition / teacher / learner budget、aggregate exposure で次を比較する。
   - `static_all_band`
   - `staged_without_rehearsal`
   - `ladder_ascent_v1`（過去 strength band の混合 floor あり）
7. primary 2〜3 archetype に限定し、各 3 training seed で同じ learner / curriculum を学習してアーキタイプ内 champion を作る。
8. PIMC は paired search gate と独立 student distillation gate の両方を通った場合だけ本学習へ入れる。不採用でも P0 は停止しない。
9. 2 件以上の `trained_champion` を `GlobalSubmissionSchedule` で比較し、primary 1 件と backup / challenger 最大 1 件を固定する。
10. bundle size、top-level file、依存、CPU-only runtime、decision latency、self-play、fault 0 を確認してから手動提出する。
11. 手動提出後に実環境 Validation、active slot、final selection を別々に確認し、submission ID と log を記録する。

最終的に採用するのは `Bronze model`、`Silver model`、`Gold model` のいずれかではない。**同じ checkpoint lineage を全 curriculum phase で更新した 1 個の final model と、その model に対応する 1 個の deck** である。

最初の比較で既存の checkpointed R2D3 系が V-trace より強い場合は、その方式を primary learner に採用する。静的 Rule 系が最強の場合は `static_rule_bundle` として、complete-action、runtime qualification、Global Race、final safety gate を同条件で通したうえで primary submission に採用してよいが、primary learner や single-lineage curriculum の成果とは呼ばない。ExIt + V-trace を本命とすることは、期限内の実測結果に反して採用することを意味しない。

## 24. レビューで修正した主要論点

| 旧設計の曖昧さ / リスク | 修正後 |
|---|---|
| Bronze / Silver / Gold ごとに別 champion を作るように読める | 同じ checkpoint lineage の curriculum phase と定義 |
| submission 後に tier に応じて model を切り替えられるように読める | rank / medal は runtime input にせず、1 fixed bundle で登る |
| 5 archetype の最終提出方法がない | cross-archetype Global Submission Race を追加 |
| Gold deck + 任意 policy を Gold-strength opponent とみなし得る | source rank band と local strength band を分離 |
| 新 submission が前 submission の ladder progression を継承する前提になり得る | submission ごとに `mu0=600` から別評価と明記 |
| teacher が真の opponent deck / policy identity を使う余地 | actor-visible posterior だけを許可し leak test を拡張 |
| 敗局の teacher labels を一律破棄 | source 別に校正した quality weight とし、outcome hard filter を撤回 |
| local smoke を Kaggle Validation と同一視 | 手動提出後の Validation / active / final-selected を別状態で記録 |
| Competition Data を無期限保持し得る | 競技中 access 制限と競技後 retention / deletion manifest を追加 |
| single lineage の途中で deck mutation できる | pre-curriculum の DeckLockDecision 後に lineage を発行し、後の mutation は新 branch |
| local strength を単一 rating だけで決める | 固定 reference panel の matchup matrix、CI、ambiguous、pool epoch を導入 |
| ascent suite を実 rating 上昇の再現と呼び得る | disjoint band の頑健性 suite とし、rating proxy は検証済み simulator がない限り診断値 |
| P0 の最小 final schedule がない | 4,096 game、32 paired replicate/cell、固定 interim alpha を事前登録 |
| 20 以上の manifest file を実装する過剰設計 | logical schema と physical document を分離して統合可能にした |
| 5 archetype x 3 algorithm x 3 seed 等を期限内に全実施 | P0 / P1 / deferred に分割 |
| o6 削除を score critical path に含める | final submission 後へ延期 |
| full census 1件欠損で全工程 block | Gold 100% / 全体98% + missingness audit を既定 threshold にした |
