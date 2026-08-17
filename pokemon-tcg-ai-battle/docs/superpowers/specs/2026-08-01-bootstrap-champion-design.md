# Bootstrap Champion 選抜と R2D3 学習開始点の設計

## 1. 結論

新しい学習系列は、無作為初期重みや便宜上の固定デッキから開始しない。事前に利用可能なリモートブランチのエージェントとデッキ、公開 Kaggle データから復元したデッキ、および互換性のある既存 R2D3 checkpoint を同一条件で評価する。

その中で最も強いと判定された「デッキと方策の組」を **Bootstrap Champion** として固定する。R2D3 学習の step 0 は、その Champion の方策を直接読み込むか、ルールベース方策を actor-visible な教師履歴として模倣した重みで開始する。以後はその step 0 checkpoint を fine-tune する。

Bootstrap Champion は「最強のデッキ」と「最強のエージェント」を別々に選ぶのではなく、必ず実行可能な組み合わせとして選ぶ。

## 2. 目的と非目的

### 目的

- R2D3 の最初の学習更新より前に、既知の強い方策を初期重みへ反映する。
- デッキと方策の相性を含む同一条件の比較で、学習開始点の根拠を固定する。
- どの外部資産からどの変換経路で step 0 checkpoint を作ったかを完全に追跡可能にする。
- 学習再開時や新規デッキ取り込み時にも、初期化と継続学習を混同しない。

### 非目的

- この段階で Kaggle 提出を行うこと。
- 他メンバーのリモートブランチを書き換えること。
- Kaggle Replay の行動を expert label として学習すること。
- Bootstrap Champion の選抜結果だけで現行の提出 Champion を自動変更すること。
- 学習開始後にデッキを暗黙に差し替えること。

## 3. 用語と同一性

| 用語 | 定義 |
|---|---|
| Deck Asset | 60 枚の合法デッキ。カード列の canonical hash で識別する |
| Policy Asset | 合法行動を返すエージェント、または R2D3 checkpoint |
| Joint Candidate | 実行可能性と互換性が確認された Deck Asset と Policy Asset の組 |
| Bootstrap Champion | 学習開始前の専用評価で選抜された Joint Candidate |
| Step 0 checkpoint | RL update を 1 回も行う前の R2D3 checkpoint |
| Incumbent Champion | 継続学習中に現在採用されている checkpoint。Bootstrap Champion とは別の概念 |
| Challenger | 学習開始後に Incumbent Champion へ挑戦する checkpoint |

Joint Candidate の同一性は、少なくとも次の組の content hash で定める。

```text
candidate_id = hash(
  deck_hash,
  policy_hash,
  adapter_hash,
  runtime_config_hash,
  simulator_contract_hash
)
```

同じソースコードでも、adapter、タイムアウト、探索上限、乱数設定が違えば別の方策として扱う。

## 4. 全体アーキテクチャ

```text
外部・既存資産
  │
  ├─ チームの remote ref ────────┐
  ├─ 公開 Kaggle デッキ ────────┤
  ├─ 既存の互換 R2D3 checkpoint ──┤
  └─ local Rule v0/v1 など ────────┘
                 │
                 v
  Read-only intake / hash 固定 / qualification
                 │
                 v
        Deck / Policy Asset Registry
                 │
                 v
       互換性検査と Joint Candidate 生成
                 │
                 v
      256 局の予備選抜 → 上位 4 組を凍結
                 │
                 v
  独立した 1,024 局の代表ベンチマーク
                 │
                 v
       BootstrapChampionManifest
                 │
        ┌────────┴────────┐
        v                         v
 互換 checkpoint          ルールベース/外部 policy
 直接初期化                 教師履歴を収集し模倣
        └────────┬────────┘
                 v
          R2D3 step 0 checkpoint
                 │
                 v
          継続 RL fine-tuning
```

## 5. 候補の取り込み

### 5.1 許可する取得元

| 取得元 | 使えるもの | 条件 |
|---|---|---|
| チームの remote branch | デッキ、ルールベース agent、アダプタ | `git fetch` 後の commit を read-only snapshot 化。remote へは書かない |
| 公開 Kaggle 情報 | デッキ構成 | ルール上利用可能で出典を追跡できる公開情報のみ |
| 既存ローカル artifact | R2D3 checkpoint、実行方策、デッキ | hash、schema、モデル構成、デッキ拘束が確認できること |
| リポジトリ内 baseline | Rule v0/v1、First Legal など | 相対強度の基準として必ず含める |

Kaggle 上位デッキからはデッキだけしか復元できない場合がある。その場合、それは Policy Asset の初期重みにはならない。任意デッキ対応の Policy Asset と組み合わせて初めて Joint Candidate になる。

### 5.2 qualification

各資産は評価前に次を満たす。

- deck が 60 枚であり、配布エンジンで合法と判定される。
- agent が actor-visible な observation のみを使う。
- 4 局の smoke 実行で、両 seat を経験し、fault、timeout、非合法行動が 0 である。
- source commit、deck hash、policy hash、adapter hash、runtime config hash を固定できる。
- native agent が特定デッキ専用なら、その拘束を registry へ明記する。

一つでも不明な場合は推測で補わず、候補から除外する。

## 6. デッキと方策の互換性

Policy Asset は次のどちらかの拘束を必ず持つ。

| 拘束 | 生成可能な組 |
|---|---|
| `EXACT_DECK` | Policy Asset に固定された `deck_hash` との組だけ |
| `ARBITRARY_LEGAL_DECK` | qualification 済み Deck Asset との組 |

`EXACT_DECK` の native agent を他のデッキで動かす、または非対応の R2D3 checkpoint に別デッキを接続することは禁止する。

## 7. 選抜ベンチマーク

### 7.1 予備選抜

- 対象: qualification を通ったすべての Joint Candidate
- 対局数: 1 候補あたり 256 局
- スケジュール: opponent × seat を可能な限り均等化
- seed namespace: `bootstrap-screen-v1`
- 通過: 上位 4 組。同点をはじめ、候補が 4 組未満なら全候補

予備選抜は計算量を絞るための開発評価であり、最終勝率として報告しない。

### 7.2 最終選抜

- 対象: 予備選抜開始後に候補内容を固定した上位 4 組
- 対局数: 1 候補あたり 1,024 局
- スケジュール: 固定 opponent × seat の均等割付。同じ対局セルで候補間の seed を共通化
- seed namespace: `bootstrap-validation-v1`
- 予備選抜の seed、学習用 opponent、checkpoint promotion 用 benchmark とは分離
- 完了条件: 1,024 局すべてが完了し、fault と schedule 欠落が 0

最終勝者は次の手順で決める。「差が 1 point 以内」という pairwise 比較をソート関数にすると非推移的になるため、まず最高値から候補集合を一度だけ作る。

1. opponent ごとの重みを等しくした score rate の最高値を求る
2. 最高値から 1 percentage point 以内の候補を最終候補集合にする
3. 最終候補集合を最低 opponent score rate の降順で並べる
4. 同値なら、全対局の score rate に対する Wilson 95% 下限の降順
5. それも同値なら、意思決定時間 p95 の昇順
6. 最後は `candidate_id` の昇順

score は `win=1`, `draw=0.5`, `loss=0` とする。対戦相手数の偏りに引きずられないよう、選抜の第一指標は opponent-equal とする。

## 8. Bootstrap Champion から step 0 checkpoint への変換

### 8.1 互換 R2D3 checkpoint の場合

`DIRECT_CHECKPOINT` 経路を使う。次の互換性をすべて検査する。

- observation encoder と Stable ActionKey の schema
- recurrent core、hidden size、C51 support、ヘッド構成
- デッキ拘束と `deck_hash`
- actor-visible 境界
- checkpoint file hash と model config hash

step 0 checkpoint には online model の重みを引き継ぎ、target model はその重みのコピーで初期化する。optimizer、scheduler、PER priority、Replay cursor は新規にし、`global_step=0` とする。これは「既存学習の resume」ではなく、「既存方策を初期重みとして転送した新学習」だからである。

### 8.2 ルールベースまたは非互換 policy の場合

`TEACHER_DISTILLATION` 経路を使う。ソースコードからニューラル重みへの直接変換はできないため、Bootstrap Champion で対局して教師 decision を収集する。

教師 dataset は次を守る。

- actor-visible public state、自分の private state、visible history、合法 Stable ActionKey、教師の選択のみを保存する。
- opponent の手札や deck 順、将来の乱数結果は保存しない。
- Kaggle Replay の行動は label に使わない。
- fault または非合法行動を含む局は dataset 全体から除外する。
- train/validation 分割は decision 単位ではなく game 単位で 80/20 に分ける。
- 現行 R2D3 の一選択 action contract で表現できる合法な教師 decision を模倣対象にする。複数選択 decision は失敗や非合法とせず、dataset manifest の `skipped_multi_select_decisions` へ計数して BC 対象から外す。
- game 結果により `win=1.0`, `draw=0.5`, `loss=0.25` の重みを masked cross entropy へ与える。これは outcome-weighted behavior cloning であり、TD 学習ではない。

R2D3 の現行 Q 出力から合法行動ごとの期待 Q を作り、それを logit とした masked cross entropy で事前学習する。validation loss が改善しなくなったら early stopping し、最良 validation 重みを step 0 とする。target model は online model のコピー、optimizer、Replay、`global_step` は新規にする。

### 8.3 デッキだけが強い場合

デッキ単体では R2D3 の初期重みにならない。任意デッキ対応方策との Joint Candidate が最終選抜に勝った場合に限り、そのデッキを Bootstrap Champion のデッキとする。

## 9. Artifact 契約

### 9.1 BootstrapChampionManifest

| フィールド | 内容 |
|---|---|
| `schema_version` | `bootstrap-champion-v1` |
| `bootstrap_champion_id` | manifest 本体の content hash |
| `candidate_registry_id` | 候補一覧の固定 ID |
| `screen_benchmark_id` | 256 局予備選抜の ID |
| `validation_benchmark_id` | 1,024 局最終選抜の ID |
| `candidate_id` | 勝者の Joint Candidate ID |
| `deck` | ID、hash、snapshot path、source provenance |
| `policy` | ID、hash、kind、runtime、adapter、source provenance |
| `compatibility` | `EXACT_DECK` または `ARBITRARY_LEGAL_DECK` |
| `initialization_mode` | `DIRECT_CHECKPOINT` または `TEACHER_DISTILLATION` |
| `score_summary` | opponent-equal、worst-opponent、overall、CI、seat 別、fault |
| `selected_at` | UTC timestamp。ID 計算には含めない |

### 9.2 BootstrapCheckpointManifest

| フィールド | 内容 |
|---|---|
| `schema_version` | `bootstrap-checkpoint-v1` |
| `bootstrap_checkpoint_id` | manifest 本体の content hash |
| `bootstrap_champion_id` | 初期化元 |
| `initialization_mode` | 初期化経路 |
| `model_config_hash` | encoder、recurrent core、C51、action schema |
| `online_weights_sha256` | step 0 重み |
| `target_equals_online` | 必ず `true` |
| `optimizer_state` | 学習開始時に必ず `fresh` にするという契約値 |
| `global_step` | 必ず `0` |
| `deck_hash` | 学習用に固定するデッキ |
| `teacher_dataset_id` | 模倣時のみ必須 |
| `source_checkpoint_id` | 直接初期化時のみ必須 |

artifact は atomic write し、再実行時は ID と hash の一致を確認する。同じ output path に別の内容がある場合は上書きせず失敗する。

`bootstrap-checkpoint-v1` は重み転送用 artifact であり、Replay/population/optimizer/RNG に結び付く通常の `r2d3-checkpoint-v3` resume payload とは区別する。学習 service がこの重みを読んだ後に fresh optimizer と学習状態を構築する。

## 10. 継続学習との境界

- 新規学習 root は `BootstrapCheckpointManifest` と Champion の deck snapshot を明示的に受け取る。
- `deck.csv` を暗黙に上書きしない。学習 CLI には snapshot path を渡す。
- step 0 作成前に収集した通常の RL Replay は、deck、policy、schema、population identity が新系列と一致しない限り自動再利用しない。
- step 0 以後は、既存の checkpoint 毎オフラインベンチマークと Champion/Challenger 契約を使う。
- 新しい外部 agent や deck が登場した場合、それを新たな opponent や教師として学習へ追加できるが、過去の Bootstrap Champion と step 0 の provenance は書き換えない。
- 系列ごと作り直す場合だけ、新しい Bootstrap Champion 選抜を行う。

## 11. 失敗時の扱い

| 状況 | 動作 |
|---|---|
| qualification を通る候補が 0 | 学習を開始せず失敗 |
| 最終選抜に fault またはセル欠落 | Champion を選ばず失敗 |
| checkpoint の互換性不明 | 直接初期化を禁止し、実行可能なら教師模倣へ分類 |
| 教師データに hidden information | dataset seal と学習を中断 |
| 教師が非合法行動を返す | 該当 game を破棄し、qualification を失敗 |
| deck-only asset に対応 policy がない | Joint Candidate を作らない |
| 既存 output と ID が不一致 | 上書きせず別 root を求める |

## 12. 受入条件

実装完了は次のすべてを満たした状態とする。

- remote branch と公開 deck を read-only に hash 固定し、Deck/Policy Asset として一覧化できる。
- deck-policy 互換性が不正な組を実行前に拒否できる。
- 256 局予備選抜と1,024 局最終選抜の固定 schedule を再現できる。
- 選抜結果が `BootstrapChampionManifest` に固定される。
- 互換 checkpoint と教師模倣の両経路が、`global_step=0`、fresh optimizer、`target=online` の checkpoint を生成する。
- actor-visible 境界と Stable ActionKey の合法性を自動テストで検査できる。
- 新規学習が bootstrap manifest、step 0 checkpoint、deck snapshot の一致を検査してから開始する。
- 長時間実験を実行しなくても、合成候補と少数局の E2E で全 stage の契約を検証できる。

## 13. 現在の artifact との関係

既存の hard Grimmsnarl 256 局 chunk は、通常の RL fine-tuning 候補データとしては有効である。ただし、Bootstrap Champion と step 0 checkpoint が未確定のため、現時点で新学習系列へ seal しない。

新たに選ばれた deck、policy schema、population identity とその chunk の identity が一致することを確認できた場合だけ再利用する。一致しない場合は、過去の実験証跡として保存するが、新系列の初期 Replay には入れない。
