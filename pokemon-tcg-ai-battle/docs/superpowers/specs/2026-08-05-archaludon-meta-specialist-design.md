# メタ駆動デッキ専門方策: archaludon レーン実装設計

> 2026-08-05 策定。正典は [docs/plan/メタ駆動デッキ専門方策_提出仕様反映レビュー版_2026-08-01.md](../../plan/メタ駆動デッキ専門方策_提出仕様反映レビュー版_2026-08-01.md)。本文書は正典を再定義せず、archaludon レーンへの適用と作業構成だけを決める。

## 1. 結論

archaludon（ブリジュラス ex、core Card ID 169 / 190）を提出レーンとし、2 つの worktree で並行する。

- **Worktree 2「本命」**: 正典 §21 の P0 をその順序どおりに実装する。既存の強い方策を初期値にし、上位メタ相手プールへ特化させる。
- **Worktree 1「繋ぎ」**: ozawa の rule+CEM 方式で archaludon を強化し、本命が完成するまでの提出下限を確保する。

両者は **相手プールと `GlobalSubmissionSchedule` だけを共有**し、source tree は統合しない。比較単位は正典 §14.0 が定める提出 bundle（1 deck + 1 policy）であり、source tree を統合しても比較の正当性は増えないためである。

final submission deadline は 2026-08-16（正典 §2.5）。安全側の upload 目標は 2026-08-15。

## 2. 現行実装との乖離（事故の記録）

正典と実装が乖離した事実と原因を記録する。再発防止（§8）はこの原因に対応させる。

### 2.1 観測された乖離

`codex/meta-specialist-p0-foundation` の学習系は、初期値・相手・評価基準の 3 軸すべてが Rule Agent v0 に閉じていた。

| 軸 | 正典の要求 | 実装 | 証拠 |
|---|---|---|---|
| 相手 | census 由来の上位メタを local strength で層化（§13） | `cabt_rule_agent_v0` 単一の閉じた enum | `src/mage_ptcg/meta_specialist/actor_pool_v1.py:234` |
| 相手デッキ | opponent instance ごとに独立（§13） | subject と同一デッキのミラー固定 | 同 `:1069` の `deck_a_path=deck_b_path=deck_path_str` |
| 初期値 | 既存の強い asset を seed / teacher に（§5） | 乱数初期化 + rule v0 コーパスの BC | `experiments/2026-08-04-meta-specialist-offline-vtrace-round1.md` |

結果として rule v0 の性能上限に張り付いた。実測は alakazam 12.27%→15.07%、grimmsnarl 4.96%→9.90%、rocket 20.12%→16.97%（各 n≈650、baseline も rule v0）。

engine 側 `scripts/test_sim.py:208-222` の `run_match` は `deck_a_path` / `deck_b_path` と `agent_a_factory` / `agent_b_factory` を独立に受け取れる。ミラー固定は engine の制約ではなく実装の選択である。

### 2.2 原因

テストが正典ではなく、実装が自分で宣言した契約を検査していた。閉じた enum の値を assert するテストは、その enum が正典違反でも PASS する。全 suite 2,908 件が緑のまま、正典 §22 の受入条件が複数未実装で残った。

## 3. 作業構成

```text
        ┌──────────────────────────────────────────────┐
        │ 共有資産（read-only、版で固定）                 │
        │  OpponentPoolManifest   （§4）                │
        │  LocalStrengthManifest  （§4.3）              │
        │  SeedAssetManifest (archaludon deck)（§5.1）  │
        └───────────┬────────────────────┬─────────────┘
                    │                    │
  Worktree 1「繋ぎ」 │                    │  Worktree 2「本命」
  ozawa rule + CEM  │                    │  正典 §21 P0
  （§5）            │                    │  （§6）
                    └──────────┬─────────┘
                    提出 bundle（main.py + deck.csv）
                               ▼
              GlobalSubmissionSchedule（正典 §14.3 gate 全通し）
                               ▼
                   primary 1 件 + backup 1 件
```

| | Worktree 1 | Worktree 2 |
|---|---|---|
| 分岐元 | `origin/agents/ozawa-grimmsnarl-rule+RL` | `codex/meta-specialist-p0-foundation`（§6.0 の監査結果で確定） |
| branch | `feature/archaludon-cem` | `feature/meta-specialist-canonical` |
| 位置づけ | 提出下限の確保 | 本命 |
| 停止条件 | 本命が同一 schedule で上回った時点で凍結 | なし |

`cg/`（simulator）は `origin/main` と ozawa branch でバイト同一（`git diff` 空）であり、両 worktree で同じ engine 版を pin できる。

## 4. 共有資産: 相手プール

### 4.1 供給源

正典 §13 の opponent instance を 3 系統から構成し、重複は正規化 hash で除去する。

| 系統 | 件数 | 状態 | 作業 |
|---|---:|---|---|
| ozawa `opponents/`（`ozawa-grimmsnarl-rule+RL` が最新・最大） | 49 | 勝率 ±CI、archetype、決定 latency、`SOURCE.md` が揃う | manifest 形式への変換 |
| 他 remote agent branch の root agent | 17 | `main.py` + `deck.csv` を完備 | `opponents/` 形式へ包む、強度実測 |
| Kaggle census（snapshot SHA-256 `82c4b0ee…`、2026-08-01） | Gold 22 / Silver 283 | 未取得 | 正典 §16 の取得規律で実装（Worktree 2） |

系統 2 のうち ozawa プールに存在しないもの: `nihei-MegaLopunny`(2026-08-04)、`pimc-search_kinoshita`(2026-08-04)、`nihei-hydreigon-deckout`、`nihei-comfey-library-out`、`nihei-festival-lead`、`nihei-cynthias-garchomp`、`nihei-alakazam`、`nihei-double_dqn_houdin`、`ozawa-metal-psychic-search`。

### 4.2 正規化と利用境界

重複除去キーは **60 枚 canonical multiset の SHA-256 × policy content hash**。ozawa プール内の凍結版（`ozawa_crustle_v2` 等）と branch の現行版は別 policy version として両方登録し、sampling は新しい方を既定にする。

`opponents/*/SOURCE.md` は全件が local offline evaluation のみに限定し、再配布と as-is 提出を禁じている。したがって全件 `usage_boundary=local_eval_only` とし、**提出 bundle への混入を package test で機械検査する**（正典 §5、§22）。

`registry.yaml` は現状 fail-open（未記載 = 有効）である。未資格の相手が黙って学習プールに入るのは正典 §5 に反するため、**fail-closed へ反転**させる。

### 4.3 local strength calibration

正典 §13 の 2 軸を分離する。

- `source_rank_band`: census 由来の provenance。メタ分析と holdout 層化にだけ使う。
- `local_strength_band`: 固定 schedule で実測した強さと CI。**curriculum の primary sampling key**。

ozawa の vs θ_v2 勝率 ±CI（49 体、n≈500）を初期 calibration として採用し、archaludon subject を固定 schedule で当てて `LocalStrengthManifest` を確定する。band 境界は点推定でなく CI 下限で切る。

ozawa が確立した速度階層を運用規律として継承する: ≤1ms = 学習に使用可 / 1–30ms = 評価専用 / >30ms = 不採用。探索相手を学習ループに入れると収集が桁で遅くなるため実効的な制約である。

sampling weight は 2026-08-01 census の観測デッキ比率を正とする。ozawa の tier 区分は 2026-07-11 の round-robin 由来であり、同記録が「オーロンゲが 10 日で 5%→52.4%」の激変を残しているため頻度としては古い。weight 差し替え時は新しい league epoch を開始する（正典 §10.3）。

## 5. Worktree 1「繋ぎ」: archaludon rule + CEM

### 5.1 seed asset

archaludon の既知構築は 3 件、canonical には 2 系統である（正典 §5 に登録済み。file SHA-256 は commit `b744b84a64a417f33f153e392ed049d90460f4f0` 時点の記録であり、取り込み時に再検証する）。2 系統の差は 1 枚だけで、`1182` ×4 と `1182` ×3 + `1213` ×1 である。

| asset | main.py | ロジック | usage_boundary |
|---|---:|---|---|
| `tomatomato_archaludon` | 1,096 行 | ルールベース | `local_eval_only` |
| `lucifer19_battlecore` | 1,102 行 | ルールベース（構造は tomatomato とほぼ同一） | `local_eval_only` |
| `plamen06_steel` | 1,242 行 | ヒューリスティック + 任意 beam search（`SP_BUDGET` 既定 2.0 秒） | `local_eval_only` |

`lucifer19_battlecore` と `plamen06_steel` の `deck.csv` は byte 同一である。出典 Kaggle kernel は別であり、同一である理由は未確認。

### 5.1.1 デッキ強度の評価（測定軸に注意）

**archaludon デッキ自体は弱くない。** 単一相手の勝率で判断すると誤る。

| 測定 | 値 | 条件 |
|---|---|---|
| vs θ_v2（rocket）勝率 | 12.6〜15.2% | n=500、Wilson 95% CI、2026-07-25 |
| 11 対戦相手の round-robin 平均勝率 | **0.716（総合 1 位）** | `tomatomato_archaludon`、各ペア n=50、seed=42、2026-07-11 |
| ozawa 自帯での A05 期待損失（頻度 × 敗率） | **8.9%（損失 2 位）** | 自チーム順位 #1,698/5,695 時点 |

12〜15% は鋼がロケット相性で不利なことを示すだけであり、デッキの一般的な弱さではない。ozawa の CEM が鋼族を「構造負け」として棄却したのも θ_v2 rocket 視点の測定である（正典が要求する「archaludon を我々の方策で操って上位メタに勝てるか」とは方向が逆）。

したがって `local_strength_band` は**単一相手ではなく相手プール全体での実測**で決める（§4.3）。

### 5.1.2 提出デッキとしての実績

**どの branch の root `deck.csv` も 169/190 を含まない。** archaludon 構成は全て `opponents/*` 配下の評価用フィクスチャであり、チームの提出デッキとして採用された実績はまだない。したがって本レーンは既存提出の改良ではなく新規レーンである。

**deck.csv が `bundle_allowed` か否かは qualification で判定する**（正典 §5）。判定前に提出 bundle へ入れない。`main.py` は 3 件とも確定で `local_eval_only` であり、提出方策の出発点にできない。

### 5.2 操縦方策: 2 段構え

archaludon 用の強い既存方策は存在しない。ozawa の θ は archetype 固有（grimmsnarl の 3,766 行 `snapshot_main.py` に紐づく 25 ノブ）であり、アーキタイプを跨いだ移転を支持する証拠もない（ozawa の CEM 記録は「パートナー構成でプランが変わる場合は分割」と結論している）。したがって段階的に作る。

`rl/agents/<id>/` は `importlib.import_module("rl.agents.<id>.adapter")` による動的解決であり、**中央レジストリの編集は不要**である。adapter の契約は `make_agent(params)`、`deck()`、`AGENT_ID`、`SOURCE`、`GATED_CONTEXTS`。

既存の追加方式は 2 通りある。grimmsnarl 方式（既存 `main.py` を `snapshot_main.py` へ移植し、リテラル定数を `PARAMS.<field>` 参照へ置換）は、**archaludon では使えない**。移植元になり得る 3 件が全て `local_eval_only` だからである。したがって example 方式（`rules.py` をゼロから書く。example の実装は 406 行）を採る。

**段階 1（先に実装し、走らせる）**: ozawa の `agents/generic_agent.py`（1,012 行、スクレイプした `meta_*` デッキの操縦に使われている汎用ルール）を archaludon デッキへ当て、`rl/agents/archaludon/` を `rl/agents/example/` テンプレートから作る。PARAM_SPEC を定義して CEM で上位メタ族へ特化させる。

段階 1 の目的は**性能ではなく経路の成立**である。相手プール、CEM harness、評価 schedule、採否判定を end-to-end で通し、測定された下限を得る。汎用ルールの天井は archaludon 専用ルールより低いと見込まれる（`tomatomato_archaludon` の専用 1,096 行が round-robin 総合 1 位である一方、generic_agent は任意デッキ向けである）。

**段階 2（段階 1 が走った後に追加）**: archaludon 専用 `rules.py` を実装して操縦者を差し替える。CEM harness、相手プール、評価 schedule は段階 1 のものを再利用する。§5.1.1 の測定から**強さはこの段階で作られる**と見込む。ozawa のリプレイレビュー → 実装ループは grimmsnarl で +19.6pp の実績がある。

段階 1 → 段階 2 の差し替えは、同じ相手プールと schedule で A/B できるようにする。

`rl/agents/grimmsnarl/tuned/specialists/` には `a01.json` と `a04.json` しかなく、**A05（鋼 / archaludon）用の specialist θ は存在しない**。流用できる θ は無い。

### 5.3 CEM の採用ゲート

ozawa が確立した基準をそのまま使う。

- 族全体で diff の CI95 下限 ≥ +1pp
- best 個体でなく μ を採用する（勝者の呪いを 4 回連続で観測したという記録に基づく）
- 族の優先順位は **census 頻度 × ゲイン**で決める（「ゲインでなく頻度×ゲインで選ぶ」）
- 判定は必ず大サンプル評価で行う（学習曲線の上昇は当てにしない）

採用した θ は正典 §14.3 の promotion gate へ通してから champion にする。

### 5.4 既知のリスク

ozawa の CEM 記録は鋼族について「0.128 = 構造負け、+0.80pp (ns)」として棄却している。§5.1.1 のとおりこれは θ_v2 rocket 視点の測定であり、本レーンの問いとは方向が逆である。ただし **rocket 相性が悪いこと自体は実測された事実**であり、rocket 系が上位メタに厚い場合は本レーンの不利要因として残る。census の rocket 比率（正典 §2.2: Gold #12、Silver 8/283）から相手プールの重みを決め、この相性を評価に必ず含める。

正典 §2.2 は archaludon を Gold 0/22、Silver 0/283、Bronze 24/206 と記録し、§5 で「上位使用率枠ではなく、扱いやすさ・既存資産・Bronze 脱出向けの独立仮説」と位置付けている。他レーンと同じ「頻度×ゲイン」では正当化されないレーンであることを明記する。

`tomatomato_archaludon` の Kaggle kernel 名にある「75% WR」は原著者の自己申告であり、本リポジトリでの独立検証値ではない。採用根拠に使わない。

## 6. Worktree 2「本命」: 正典 P0

### 6.0 分岐元の決定根拠（§22 条項別監査、2026-08-05）

正典 §22 の 20 条項を `codex/meta-specialist-p0-foundation` に対して条項別に監査した結果、判定は MET 5 / PARTIAL 8 / UNMET 7 である。

決定的だったのは、PARTIAL の大半が「実装が無い」ではなく **「実装もテストもあるが、どこからも import されていない」** ことである。次の 6 モジュールは importer が 0 件である（`grep` で実測確認済み）。

`curriculum_v1.py`、`calibration_v1.py`、`joint_optimization_v1.py`、`global_race_v1.py`、`orchestrator_v1.py`、`compute_planner_v1.py`

一方、学習コアは実配線されている（`collect_trajectories_v1` → `train_from_trajectories_v1` → `cli`、および `vtrace_v1` / `neural_learner_v1`）。

したがって `origin/main` から始めると、健全でテスト済みのロジックを捨てることになる。**分岐元は `codex/meta-specialist-p0-foundation` とする。**

### 6.0.1 監査が示した作業の形

| 層 | 状態 | 作業 |
|---|---|---|
| 末端: opponent 供給 | **VIOLATED** | `actor_pool_v1.py:234` の enum と `:1069` のミラー固定を作り直す。**他の全てがこれに依存する** |
| 孤立 6 モジュール | ロジック健全・未配線 | 実パイプラインへ配線する。新規実装ではない |
| 学習コア・bundle 契約・action 契約・privacy 走査・seed registry・lifecycle | MET / 配線済み | そのまま流用 |
| census、`MetaAnalysisManifest`、PIMC gate、`ascent_suite`/`top_band_suite`、cleanup manifest、worktree 保護 | UNMET（実装もテストも 0 件） | 新規実装 |

UNMET 7 件のうち 6 件は**テスト自体が存在しない**ため、CI は何も検査していなかった。§8 の conformance test はここを可視化する。

### 6.1 P0 の実装順

正典 §21 の P0 をその順序どおりに実装する。

| # | P0 項目 | 方針 |
|---|---|---|
| 1 | worktree 保護、submission contract、bundle smoke、active-slot | 条項別監査の結果 MET/PARTIAL の層だけ流用する |
| 2 | action-set contract、複数選択、合法性、leak test | 同上。leak test は相手 deck / policy identity まで拡張する |
| 3 | seed qualification と local strength calibration | §4 の相手プールから構築 |
| 4 | 既存最強 baseline と V-trace 系 1 lane の E2E 学習・resume | 初期値を rule v0 から**既存強エージェントの BC 蒸留**へ差し替える |
| 5 | same-lineage ladder curriculum と `static_all_band` 対照 | 新規実装 |
| 6 | primary archetype の champion | archaludon |
| 7 | cross-archetype global race、primary / backup 指名、package | 新規実装 |

### 6.1 初期値（FoundationInit）

正典 §9.3 に従い、既存の強いエージェントを teacher とする BC 蒸留で `FoundationInit` を作る。leak・fault・illegal・schema 不明がない全ての有効 teacher decision を policy target 候補とし、**敗局を既定で 0 weight にしない**。

teacher は actor-visible 観測だけを見る。scheduler は相手の deck hash / policy hash を sampling と層化に使ってよいが、teacher・student の decision feature へ渡さない（正典 §9.2）。

teacher の実体は次の優先順で選ぶ。archaludon 専用の強い既存方策が存在しないため、単一の teacher に依存しない。

1. **Worktree 1 の archaludon エージェント**（段階 1 の CEM 済み θ、後に段階 2）。同一デッキを操縦する唯一の teacher であり、これが得られ次第 primary teacher とする。ここで 2 つの worktree は競合でなく供給関係になる。
2. **他アーキタイプの強エージェント**（ozawa の grimmsnarl / crustle θ 等）。正典 §8.4 が「seed deck 専用の固定 action ID に依存しない」と規定しているため、他デッキ由来の decision も汎用的な手順知識として蒸留できる。ただし転移の実効は未測定である（§10）。
3. 上記が揃わない段階では、`rule_bc_vtrace` fallback（正典 §9.4）で algorithm 経路の成立だけを先に確認する。この段階の結果を性能主張に使わない。

teacher の選択と混合比は `TeacherDatasetManifest` に固定し、Rule Agent v0 を primary teacher にはしない。

### 6.2 前回失敗への対処

`experiments/2026-08-04-...round1.md` が記録した失敗要因への対処を明示する。

- **探索が存在しない**: 相手が固定 rule v0 ミラーでなくなること自体が最大の是正である。
- **BC 項が勾配の約 3/4 を占めた**: `bc_coefficient` を 0.02–0.05 で再探索する。停止条件は `dead_rho` > 0.5。
- **off-policy 距離の誤読**: `clip_hi` 単独では判断できない。`dlogp` から実効 ratio を見る（同記録の訂正事項）。

対照として正典 §13-5 の `static_all_band` を同一 transition budget で走らせ、段階学習そのものの効果を確認する。

## 7. 評価と Global Race

正典 §14 の gate をそのまま守る。計算資源は充足している（meta_specialist の収集は 10 worker で 2.26 games/s、ozawa の rule エージェントは 0.15–0.2ms/決定であり、§14.1 の 32,768 局でも数時間規模）。

- Successive Halving: 1,024 → 4,096 → 16,384 → 最大 32,768 局
- final schedule は candidate 学習前に hash を seal し、optimizer / deck search / CEM からアクセスさせない
- promotion gate 5 条件（層別 cluster bootstrap 片側 97.5% 下限 > 0、全 cell の simultaneous non-inferiority > −3pp、fault / timeout / illegal が 0、bundle qualification、全 cell 完了）
- 複数 challenger を同じ promotion family で比較する場合は Holm 補正

Worktree 1 と Worktree 2 の champion を同一 `GlobalSubmissionSchedule` で比較し、primary 1 件と backup 1 件を指名する。Kaggle への提出はユーザーが対象と実行を明示した場合だけ行う。

## 8. 再発防止

§2.2 の原因（テストが正典ではなく実装の自己申告を検査していた）に対応させる。手順の約束ではなく機械検査として入れる。

1. **正典 §22 の受入条件を 1 条項 1 テストへ写像する。** 未実装条項は skip / xfail ではなく **FAIL** として出す。「実装ゼロなのに全 suite が緑」を成立させない。
2. **反正典 assert を置く。** 次を直接検査する。閉じた enum を書いた時点で落ちる。
   - 相手プールが単一 policy でないこと
   - subject deck ≠ opponent deck の対戦が実際に存在すること
   - FoundationInit の provenance が rule v0 でないこと
3. **各モジュールの docstring に対応する正典の節番号を必須化**し、正典の節と実装の参照を突き合わせて「参照ゼロの節 = 未実装」を一覧出力する（`scripts/docs/validate_docs.py` の拡張）。
4. **実験記録テンプレートに「baseline の出自と、それが正典のどの条項に対応するか」欄を追加する。** 前回 baseline が rule v0 であることは記録されていたが、正典違反という判断へ繋がらなかった。

## 9. 受入条件

- 相手プールが 49 体 + 他 branch 17 体を canonical hash で重複除去した状態で manifest 化され、全件に利用境界が付いている。
- `local_eval_only` の資産が提出 bundle へ混入しないことを package test が機械検査する。
- subject deck と opponent deck が独立に指定でき、ミラー以外の対戦が実行できる。
- archaludon の deck seed が `SeedAssetManifest` で qualification され、`bundle_allowed` 判定の根拠が記録されている。
- Worktree 1 が段階 1 の CEM を完走し、採用 / 不採用の判定を大サンプル評価で出せる。
- Worktree 2 の FoundationInit が rule v0 由来でなく、その provenance が manifest で追跡できる。
- 正典 §22 の全条項が conformance test に写像され、未実装条項が FAIL として可視化されている。
- 提出 bundle が exactly one `deck.csv` と one policy lineage を持ち、top-level `main.py` を含み、bundle 上限以下で package smoke を通る。
- Kaggle 提出を自動実行する経路が存在しない。

## 10. 未確定事項

- **(要検証)**: archaludon deck.csv の `bundle_allowed` 判定。公開 Kaggle kernel 由来の 60 枚リストを提出物へ含めてよいかを競技規約と `SOURCE.md` から判定する。
- **(要検証)**: archaludon の rocket 相性の悪さが、census の相手分布のもとで致命的かどうか。相手プール全体での `local_strength_band` 実測で判定する。
- **未確認**: `lucifer19_battlecore` と `plamen06_steel` の `deck.csv` が byte 同一である理由。どちらの canonical 系統を seed に採るかの判断材料になる。
- **未確認**: Card ID `1213` の内容。2 系統の 1 枚差がこのカードであるため、系統選択に影響する。
- **(要検証)**: census 由来の上位デッキ取得に要する日数。正典 §16 の pacing（credential ごと 1 worker、2 秒間隔）での実測が必要。
- **未測定**: 他アーキタイプで学習したニューラル重みが archaludon へ転移するかどうか。正典 §8.4 の deck 非依存性は設計上の性質であり、転移の実効は未測定である。
