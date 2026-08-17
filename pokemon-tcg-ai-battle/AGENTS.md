# AGENTS.md — Pokemon TCG AI Battle リポジトリ共通ガイド

Claude Code、Gemini、Codex などの AI エージェントが共有する指示書。リポジトリ共通ルールはこのファイルに集約し、ツール別ファイルへ重複して書かない。`docs/plan/` 配下を編集するときは、追加で [docs/plan/AGENTS.md](docs/plan/AGENTS.md) に従う。

## プロジェクト概要

Kaggle「The Pokemon Company - PTCG AI Battle Challenge Simulation」向けに、合法な 60 枚デッキとゲーム状態に応じた行動を返す AI エージェントを開発する。

| パス | 内容 |
|---|---|
| `main.py` | 提出エージェントのエントリポイント |
| `deck.csv` | 提出デッキ |
| `src/` | 再利用する実装 |
| `scripts/` | データ確認・評価・補助スクリプト |
| `docs/` | コンペ情報、ガイド、設計資料 |
| `experiments/` | 実験・Kaggle 提出の記録 |
| `report/` | Strategy Division 等の報告資料 |
| `data/`, `submissions/` | Git 管理外のデータと提出生成物 |

## 正典と参照順

- コンペ仕様と提出形式は [docs/competition.md](docs/competition.md) をリポジトリ内の入口とする。ただし、規則・期限・提出制限は変更されうるため、重要な判断の前に Kaggle 公式ページで再確認する。
- 実装・設計は [docs/plan/MAGE_PTCG_v5_README.md](docs/plan/MAGE_PTCG_v5_README.md) の優先順位に従う。文書間で矛盾した場合、同 README が指定する正典を優先する。
- 実際の API やシミュレーター挙動が文書と異なる場合は、差異を隠さず報告する。推測でどちらかへ合わせない。
- カードデータと配布エンジンはコンペ参加目的に限って扱い、ライセンスと Kaggle Rules を優先する。
- 現在の作業状況は [docs/status/current_status.md](docs/status/current_status.md) と [docs/status/handoff.md](docs/status/handoff.md) を入口とし、設計正典と混同しない。

## 開発クリティカルパス（2026-07-14 第三者レビュー反映）

第三者レビュー判定は「ARCHITECTURE SOUND / EXECUTION SCOPE CORRECTED」であり、全体アーキテクチャを維持したまま提出 critical path を縮小する。詳細は [docs/plan/design/00_overall_plan.md](docs/plan/design/00_overall_plan.md) を正とする。

- critical path は `P0 → C1 → C2a／C2b → C3／C4 → C5` とし、O1〜O3（Competition Intelligence 拡張、Deck-Policy 最適化、Advanced Solver／Tier A）は Optional とする。active な大型 Slice は最大 2 つまでとする。
- P0（Continuous Submission Baseline）は全期間継続し、Tier D（Rule Agent v0）と Tier E（First Legal）を常時 build 可能に保つ。Submission Factory を最終工程だけにしない。
- Rule Agent v0 が現 Champion である。Rule Agent v1 は Rule v0 へ 105–95 で非昇格であり、Knowledge opinion／counterexample 源として扱う。Promotion Gate 通過なしに Champion を変更しない。
- cabt の合法手判定を hard truth とする。Rule、Playbook、Knowledge prior は soft とし、探索候補を削除しない。
- ActorInformationView に相手の非公開情報を含めない。Stable ActionKey をシステム横断の行動同一性とする。
- Kaggle Replay の行動を expert label として直接学習に使わない。
- Competition data の取得可否を C3／C4／C5 の開始条件にしない。
- 意味のある実装・評価・統合の後は、[docs/status/current_status.md](docs/status/current_status.md)、[docs/status/handoff.md](docs/status/handoff.md)、必要に応じて [docs/status/decisions.md](docs/status/decisions.md) と `docs/evidence/` を更新する。進捗率は Evidence なしに変更しない。

## ドキュメント正典と Notion 同期

Git リポジトリ内の Markdown を正典とし、Notion は共同閲覧用ミラーとする。同期規則は [docs/notion/sync_policy.md](docs/notion/sync_policy.md)、ページ対応は [docs/notion/page_map.yaml](docs/notion/page_map.yaml) を正とする。

- Notion からローカル正典への silent overwrite、双方向の自動 merge を行わない。Notion 側変更は差分提案としてレビュー後に Git へ反映する。
- 同期は exact page ID を使い、YAML front matter を Notion へ送らない。child page／database を削除しない。Notion 更新後は fetch して検証する。
- 文書の構造検証は `python scripts/docs/validate_docs.py` を使う。

## 共通規則

- 応答、コミットメッセージ、新規の日本語文書は日本語で書く。コードの識別子、API 名、既存文書の言語は維持する。
- `git commit`、`git push`、Kaggle への提出は、ユーザーから明示的に指示された場合だけ行う。
- Kaggle 提出は、学習完走、評価 PASS、Promotion Gate 通過、readiness のいずれからも自動では実行しない。実験 runner、CI、agent 指示から提出 CLI/API を呼び出してはならない。提出 package の build とローカル検証は維持してよいが、外部送信はユーザーがその時点の対象と提出実行を明示した場合だけ扱う。
- 他チームの非公開情報、認証情報、規約上利用できないデータを取得・利用しない。
- 未確認のコンペ仕様、カード効果、性能、勝率を確定事項として書かない。未確認事項には根拠と確認方法を添える。
- タスクの範囲を越える大規模リファクタ、依存更新、設計変更は独断で行わない。

## 作業開始時

1. このファイルと、対象ディレクトリにある追加の `AGENTS.md` を読む。
2. `git status --short` で既存差分を確認する。ユーザーや別作業の変更を上書き、整形、削除しない。
3. 対象に関係する README、仕様書、既存テストを読む。会話上の説明より、現在のファイルと実行結果を優先する。
4. 変更範囲と検証方法を決める。曖昧さが結果を大きく変えない場合は、妥当な仮定を明示して進める。

## エージェント運用

- 調査、実装、検証を分け、結果はファイル差分と実行ログで確認する。自己評価だけで完了としない。
- 並行作業を使う場合は、担当ファイルまたは責務を重複させない。同じファイルを複数エージェントで同時編集しない。
- 別エージェントの結果を採用する前に、主担当が差分、根拠、テスト結果を確認する。
- 生ログや大きなデータを会話や文書へ貼り付けず、判断に必要な要点、再現コマンド、成果物パスへ要約する。
- 外部サービスの状態を変える操作、秘密情報の利用、公開・提出に当たる操作は、ユーザーの承認範囲を確認してから行う。
- ブロックされた場合は、試したこと、原因、未確認事項、ユーザーに必要な判断を短く示す。ダミー結果で穴埋めしない。

## モデルと推論 effort の使い分け

モデル選択、推論 effort、並列実行、操作権限は別物として扱う。上位モデル、高い effort、`ultra` を使っても、commit、push、Kaggle 提出、秘密情報利用の権限は増えない。まずタスク深度に合うモデル family を選び、そのモデル内で最小十分な effort を選ぶ。モデル名は論理 alias とし、起動時に利用可能な実 model ID、provider、effort を確認する。指定 profile が使えない場合は黙って下位モデルへ落とさず、同等 profile への変更理由を示すか、その作業を保留する。

### 既定のモデルプール

GPT-5.6 family は、Luna を効率重視、Terra を日常業務の均衡点、Sol を最難関向けとして使い分ける。品質を優先し、モデルを使う作業は Luna / Terra とも `high` を既定とする。通常の author は Terra `high` とし、タスクが小さく oracle が明確なら Luna `high`、誤り損失または設計不確実性が高ければ Sol へ切り替える。全モデルを均等に起動せず、タスクを一度で完遂できる最小十分な profile へ直接割り当てる。安いモデルから順に失敗させる段階実行や、同じ問いへの無条件な全モデル投票は行わない。

| lane | model alias | 既定 effort | 昇格 | 主用途 |
|---|---|---|---|---|
| deterministic | モデルなし | — | — | hash、schema 検証、集計、lint、テスト、定型変換 |
| bounded utility | GPT-5.6 Luna | `high` | Terra `high` | ファイル走査、証拠整形、機械修正、明確な oracle を持つ小実装 |
| exploration | Gemini 3.5 Flash | `high` | なし | 広い候補探索、実験計画案、sweep 分析、資料候補の一次探索 |
| default author | GPT-5.6 Terra | `high` | 難所だけ Sol `high` | 通常実装、テスト、デバッグ、仕様化、長めの一連作業 |
| design authority | GPT-5.6 Sol | `high` | `xhigh`、例外的に `max` | 全体設計、solver 方針、評価設計、重大な失敗の理論的切り分け |
| cross-provider audit | Claude Sonnet 5 | `high` | Fable 5 `high` | GPT 系 producer のコードレビュー、仕様・実装・テストの不整合検査 |
| selective top critic | Claude Fable 5 | `xhigh` | 例外的に `max` | R3 の独立反証、長時間の重大批評。通常作業の定型最終確認には使わない |

Claude の `effort` は応答本文、thinking、tool call 全体の token 消費傾向を制御する soft signal であり、GPT の同名 effort と同じ計算量または品質を表すとは限らない。Claude Sonnet 5 と Fable 5 は adaptive thinking を使い、`high` が既定、`xhigh` は長時間の coding / agentic work、`max` は token 制約より能力を優先する最難関向けである。alias が実行環境に存在しない場合、同じ capability を持つ利用可能モデルを明示して使う。設計判断や独立監査を、capability の足りないモデルで代替して確定扱いにしない。

### Claude 比較と採用判断

| model | Anthropic の位置づけ | effort / thinking | このリポジトリでの扱い |
|---|---|---|---|
| Claude Haiku 4.5 | 最速、near-frontier | adaptive thinking なし | Luna `high` と役割が重なるため既定プールへ入れない |
| Claude Sonnet 5 | 速度と知能の均衡 | adaptive thinking、`high` 既定、`xhigh` / `max` 対応 | cross-provider audit に `high` で採用 |
| Claude Opus 4.8 | 複雑な agentic coding と enterprise work | adaptive thinking を明示、coding は `xhigh` 推奨 | Fable 5 が使えない場合の top critic 代替。置換理由を記録する |
| Claude Fable 5 | 一般提供される Claude の最上位、長時間 agent 向け | adaptive thinking 常時有効、`high` 既定、能力重視は `xhigh` | selective top critic に `xhigh` で採用 |
| Claude Mythos 5 | Fable 5 と同系統の招待制 defensive cybersecurity model | adaptive thinking 常時有効、`high` 既定 | 目的とアクセス条件が合わないため既定プールへ入れない |

Claude の比較根拠は Anthropic の [Models overview](https://platform.claude.com/docs/en/about-claude/models/overview) と [Effort](https://platform.claude.com/docs/en/build-with-claude/effort)（2026-07-12 確認）とする。Fable 5 と Mythos 5 は同じ基盤でも、利用可能性と safeguard が異なるため交換可能な alias とみなさない。

### effort と並列実行

- `high` はモデル利用時の既定とし、Luna / Terra の小実装から複数箇所の統合、原因不明のデバッグ、設計選択まで品質優先で使う。
- `medium` / `low` は既定 lane を持たない。対話レイテンシ、利用上限、反復回数が支配的で、リポジトリ固有 eval により品質維持を確認できた場合だけ明示的に下げる。
- `xhigh` は、solver core、非公開情報境界、評価設計など、反例探索と再検証が重要な単独難問に限定する。
- `max` は `xhigh` より追加計算が判断を変えうる最難関に限定する。先に問題、証拠、反証、成功条件を蒸留し、通常タスクの安心料として使わない。
- `ultra` は単なる最上位 effort ではなく、自動委譲を伴う並列実行 profile として扱う。2 件以上の独立した作業流、責務の非重複、統合 oracle、十分な token 予算があり、起動環境とユーザー指示が並列 agent を許す場合だけ使う。単一の難問は原則として Sol `xhigh` または `max` を使う。
- `fast` / priority processing はレイテンシ設定であり、推論深度の昇格として数えない。

### タスク深度と effort

| 深度 | 目安 | 既定 | 例 |
|---|---|---|---|
| R0: deterministic | 判断を含まない | モデルなし | format、集計、schema、既知テスト実行 |
| R1: bounded | 範囲と正解条件が明確 | Luna `high` または Terra `high` | 単一ファイル修正、テスト追加、データ確認 |
| R2: non-trivial | 複数箇所、原因が不明、設計選択あり | Terra `high`。難所だけ Sol `high` | 統合、性能バグ、評価パイプライン |
| R3: critical | 誤り損失が高く、独立反証が必要 | Sol `xhigh`。必要時だけ `max` ＋ Fable 5 `xhigh` | 提出 API、合法手、非公開情報境界、solver core、採用判断 |

- 見た目の難しさではなく、postcondition、変更範囲、誤り損失、可逆性で深度を決める。
- effort を上げる前に、問題、差分、成功条件、失敗ログを蒸留する。大量の生ログやリポジトリ全文へ top effort を使わない。
- モデル family の昇格と effort の昇格を同時に機械適用しない。Terra `high` で十分なら Sol へ上げず、Sol `high` で十分なら `xhigh` へ上げない。
- `xhigh` / `max` は、正典の再構成、重大矛盾、情報漏えい、不可逆な設計判断などに限定する。
- 通常作業は一人の author と deterministic gate で完了させる。レビューを増やすこと自体を品質とみなさない。

### 委譲とレビュー

- 検索、一覧化、ログ圧縮、定型 lint、独立した小変更は、境界と出力形式を固定して utility lane へ委譲できる。
- 同じファイルを複数 lane に編集させない。producer と reviewer は別 run とし、重大レビューでは別 provider を使う。
- 次のいずれかに該当する場合だけ独立レビューを追加する。
  - 合法手、60 枚デッキ、非公開情報、提出 API、評価ロジック、セキュリティへ触れる。
  - 改善幅が seed 間のばらつきに近い、または証拠が対立している。
  - 未検証の前提、低 confidence、既知の反例が残る。
  - 初回の大規模 architecture または戻しにくい設計判断である。
- reviewer は producer の成功条件に合わせて test oracle や評価条件を緩めない。
- reviewer へ渡す context pack は、目的、変更差分、根拠、失敗ログの要約、反証、未解決点に絞る。

### 再試行と運用計測

- 同一 prompt の blind retry をしない。一時障害または出力形式修復だけ同 tier で 1 回許し、それ以外は原因を `spec` / `context` / `model` / `tool` に分類して変更する。
- 不変の仕様や context pack は再利用し、類似レビューは期限を損なわない範囲で batch 化する。
- モデル比較は公開 benchmark や価格だけで決めない。採用成果あたりの総 token、再試行、待ち時間、first-pass pass 率、レビュー修正量で見直す。
- 重要な実験・設計判断では、使用した実 model ID、provider、effort、CLI version を実験記録または判断メモに残す。

GPT-5.6 family の役割、`max`、`ultra` の根拠は OpenAI の [GPT-5.6 発表](https://openai.com/ja-JP/index/gpt-5-6/)（2026-07-12 確認）とする。ただし、OpenAI と Anthropic の公開 benchmark は評価 harness、tool、token budget、effort、seed が揃っていないものを含むため、異なる表の数値を直接比較しない。上記の割当を固定的な優劣や勝率の根拠にはせず、利用可能な model ID と effort は起動時の model catalog を優先する。

非自明な設計・デバッグ・採用判断では [docs/agent/deep-reasoning.md](docs/agent/deep-reasoning.md) を使う。逐語的な思考ログではなく、前提、選択肢、反証、検証、決定、残リスクだけを短く残す。

## コミットメッセージ規約

AI エージェントがコミットメッセージを作るときは、次の形式を使う。

```text
<type>(<scope>): <要約>

- 必要な場合だけ変更点や理由を記載
```

| 要素 | 規則 |
|---|---|
| `type`（必須） | `feat` / `fix` / `docs` / `refactor` / `test` / `chore` / `perf` / `experiment` |
| `scope`（任意） | 主変更の領域。例: `agent`, `deck`, `solver`, `sim`, `data`, `submission`, `plan`。複数領域なら省略可 |
| 要約（必須） | 「何を・何のために」が分かる日本語。50 字程度を目安に体言止めとし、末尾に句点を付けない |
| 本文（条件付き） | 複数の変更、非自明な理由、互換性への注意がある場合だけ `-` 箇条書きで記載する |

内容には次の規律を適用する。

- メッセージ作成前に `git diff --staged` を確認し、会話上の意図ではなくステージ済み差分を書く。
- 1 コミットを 1 論理単位にする。関連するテストや文書は同梱してよいが、無関係な変更を混ぜない。
- `更新`、`修正`、`リファクタリング` だけの要約を避け、対象と目的を明記する。
- 実験結果を含む場合は、対応する `experiments/` の記録名を本文に含めて追跡可能にする。
- 未検証の改善や勝率を断定しない。絵文字や不要なトレーラを付けない。
- 破壊的変更は type または scope の直後へ `!` を付け（例: `feat(agent)!:`）、本文に `BREAKING CHANGE: <影響と移行方法>` を書く。
- Issue や実験記録に対応する場合だけ、本文末尾へ `Refs: #<issue>` または `Experiment: <path>` を付ける。

リポジトリには [.gitmessage](.gitmessage) を用意する。利用者が Git のコミット画面にもテンプレートを出したい場合は、任意で `git config commit.template .gitmessage` を一度実行する。エージェントはユーザーの指示なしに Git 設定を変更しない。

例:

```text
feat(agent): 合法手制約を満たす選択ポリシーの追加

fix(sim): 複数選択時の重複インデックス生成を防止

experiment(deck): 基準デッキのローカル対戦評価を記録

Experiment: experiments/2026-07-12-baseline.md
```

## 実装方針

- 変更は小さく保ち、既存の責務と公開インターフェースを尊重する。新規ファイルは責務が独立する場合に限る。
- `agent(obs_dict)` は必ず合法な形式を返す。選択肢の範囲、`minCount` / `maxCount`、インデックス重複、60 枚デッキの制約を崩さない。
- 不完全情報を完全情報として扱わない。相手の非公開札や将来の乱数結果を推論入力へ混入させない。
- 例外を broad `except` で握りつぶさない。回復可能な例外だけを具体的に扱い、その他は原因が分かる形で失敗させる。
- データ欠落や実装不備を、無意味な既定値やランダム fallback で隠さない。競技用 fallback は合法性、時間制限、適用条件をテストする。
- テストを通すためだけに競技ロジック、評価条件、データ境界を歪めない。
- 依存追加は必要最小限にし、`requirements.txt` へ固定方法と用途が分かる形で反映する。

### 実装とデバッグの手順

1. 受入条件、反証条件、変更対象、変更しない範囲を確認する。
2. `git status --short` と対象差分を確認し、既存変更を保全する。
3. バグ修正や契約追加では、可能なら先に失敗を再現するテストを書く。
4. 最小変更で実装し、対象に最も近いテストを通す。
5. デバッグは `再現 → 最小化 → 原因仮説を 2〜3 件列挙 → 計測で棄却 → 修正 → 回帰テスト` の順で行う。当てずっぽうの修正を重ねない。
6. 関連する統合テスト、合法性検査、タイムアウト検査へ検証範囲を広げる。
7. `git diff --check` と `git status --short` で受け渡す差分を確認する。

次の場合は推測で実装せず停止し、不足する決定と再開条件を示す。

- solver、評価条件、提出契約などの重要判断が未決定である。
- 既存の未コミット差分と安全に分離できない。
- 必要な公式仕様、カードデータ、シミュレーターが無く、代用が必要になる。
- 基準テストまたは実装後テストが失敗し、原因を切り分けられない。

## データ、秘密情報、生成物

- `.gitignore` を尊重し、`kaggle.json`、`.env`、`data/`、`submissions/`、モデル、大容量ログ、アーカイブを Git に追加しない。
- Kaggle の認証情報や環境変数の値をログ、文書、コミットへ出力しない。
- カードデータ、配布エンジン、提出物をチーム外へ再配布しない。
- 合成データやダミー対戦を動作確認に使う場合は、その用途を明記し、競技性能の根拠にしない。
- データが不足している場合は、期待パス、必要形式、取得元を報告し、別データで無断代用しない。

## 実験と Kaggle 提出

- 比較実験では、変更点以外のデッキ、対戦相手、seed、対戦数、シミュレーター版、時間制限を揃える。
- 勝率は対戦数と不確実性が分かる形で報告する。単一 seed や少数対戦は参考値と明記する。
- 採用判断に使う実験は `experiments/` に、日時、目的、commit hash、設定、比較対象、結果、解釈、既知の制約を記録する。
- 新しい記録は [experiments/TEMPLATE.md](experiments/TEMPLATE.md) をコピーして作る。未使用の欄を推測で埋めず、`該当なし` または `未測定` とする。
- Kaggle 提出前に、提出対象の差分、ローカル検証、デッキ合法性、秘密情報と不要ファイルの混入を確認する。
- Kaggle 提出後は、提出日時、提出名、commit hash、設定、Public LB 結果、備考を `experiments/` に記録する。結果待ちの項目は未確定のまま残す。
- Public LB だけへ過適合しない。提出回数と最終選考枠は Kaggle 公式ページで再確認する。

## テストと検証

- 変更箇所に最も近いテストから実行し、必要に応じて統合テストやローカル対戦へ広げる。
- バグ修正では、可能なら失敗を再現するテストを先に追加する。
- ランダム性を含むテストは seed を固定し、失敗時に seed と条件が分かるようにする。
- シミュレーターを使うテストでは、例外、非合法手、タイムアウト、終了処理も確認する。
- 実行できない検証は、未実施の理由と必要な環境・データを完了報告に明記する。

## 長時間実験の端末表示

- 長時間の学習・CABT・評価 runner は、TTY では `tqdm` による**単一の更新式 progress bar**を既定にする。現在値、速度、ETA、fault など人間が判断に必要な集計値を postfix に載せ、局・update ごとの行ログを出さない。
- progress stream を `tee`、pipe、行単位の logger、または carriage return を解釈しない中継へ通してはならない。これらは tqdm の上書きを壊し、同じ bar の断片を大量に出力する。端末表示は runner が直接所有する。
- 非TTYでは、10秒程度ごとの集約スナップショット（stage、完了数、速度、ETA、fault）だけを出す。詳細な進捗は artifact 内の atomic な `progress_summary.json` と stage manifest に保存し、terminal へ複製しない。
- progress bar と別に出すメッセージは stage の開始、完了、fail-closed の原因など、状態遷移に限る。長時間実験用 wrapper は標準出力・標準エラーを無条件に `tee` しない。

## ドキュメント規約

- 結論を先に書き、1 段落を 1 論点にする。3 項目以上の並列・比較は箇条書きか表を使う。
- 同じ説明を複数文書へ複製せず、正典へリンクする。仕様変更時は関連リンクと相互参照を確認する。
- 公式仕様、観測事実、設計判断、仮説を区別する。出典、確認日、再現方法のいずれかを必要に応じて添える。
- 未確定箇所は `TODO:` または `(要検証)` とし、確定情報と混在させない。
- コマンド例はコピーして実行できる形に保ち、実際のパスと環境に一致させる。

## 完了報告と引き継ぎ

完了報告はユーザーが差分を判断できる情報だけに絞り、次の順で書く。

1. **結果**: 何が使える状態になったか。
2. **変更**: 主なファイルと変更理由。
3. **検証**: 実行したコマンドと pass / fail。実行していない検証も明記する。
4. **残課題**: 未検証前提、既知の制約、ユーザー判断が必要な事項。
5. **Git 状態**: commit の有無と、今回分以外の既存差分が残っているか。

「完了」「修正済み」は検証結果がある範囲に限って使う。次の action が自明で安全なら提案してよいが、commit、push、提出を完了報告のついでに実行しない。
