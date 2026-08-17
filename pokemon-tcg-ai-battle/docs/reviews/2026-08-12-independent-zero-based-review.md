---
project: MAGE-PTCG
document_status: external-review
as_of: 2026-08-12
reviewer: Claude Opus 5 (independent, zero-based)
authority: advisory-only
---

# 独立ゼロベース監査 — なぜ性能が安定して伸びないのか

## 0. 結論（先に3行）

1. **性能が伸びないのではない。伸びたかどうかを判定できる計測器が存在しない。** 過去1週間の全arm（約9〜12本）の判定は、真の効果 ±5pt を区別できない標本サイズで下されている。「不合格」は「効果がない」の証拠ではなく、情報がゼロだったという事実である。
2. **すでに手元にある最良のcheckpointと、実際に提出しているagentの間に約36ポイントの差がある。** Wave6 V4 は固定六で 48〜51%、Rule Agent v0 は同一条件で **12/96 (12.5%)**。それでも提出面は Rule v0 のまま、V4 の提出bundleは存在しない。研究lineの +0.5pt を追う前に、この36ptを取りに行っていない。
3. **最終提出期限は 2026-08-16。残り4日。** 判定不能な局所探索を続ける時間はない。優先順位は「① 計測器を直す ② 手持ち最良を提出する ③ deckを最適化する ④ outcome信号で1本だけ長時間学習する」であり、teacher差替え・threshold sweep・architecture bakeoffはすべて今期は捨てる。

---

## 1. まず事実関係の訂正

### 1.1 期限（最重要・要再確認）

Kaggle公式情報では Simulation Division の **最終提出は 2026-08-16**、以降 8/17〜8/31 頃まで対戦が継続して順位が確定する。つまり **8/16 までに置いたagentが2週間戦い続け、後から差し替えられない。**

`docs/status/*` は「最短でも1〜3日」「longrunは未開始」と書いているが、そのスケジュールは期限に対して成立していない。この1点だけで現行の作業順序は組み替える必要がある。

### 1.2 提出物と評価対象が別物

| 対象 | 実体 | 固定六での実測 |
|---|---|---|
| 実際の提出 (`main.py` の `_DEFAULT_AGENT`) | Rule Agent v0 + root `deck.csv`（Mega Lucario/Hariyama、SHA `2a541d7b…`） | **12/96 (12.50%)**（Archaludon deck上での直接監査値） |
| 全V4研究lineの評価対象 | Wave6 V4 + Archaludon deck（SHA `42165967…`） | 93〜98/192 (48.4〜51.0%) |
| 参考: 最強の公開相手 | `public_archaludon_cinderace_r7` | 62/96 (64.6%) |

`docs/evidence/v4-wave3-postrun-audit-20260812.md` はこの 12/96 を自分で記録し、「Rule v0 への追随は課題ではない」と正しく書いている。にもかかわらず提出面は変わっていない。**Promotion Gate が「Wave6 を +5pt 上回るか」だけを問い、「今提出しているものより強いか」を一度も問わなかったため、意思決定の対象から外れ続けた。** これが構造的な最大の欠陥である。

補足: Rule v0 の 12.5% は Archaludon deck 上の値であり、自前 deck 上ではもっと高い可能性がある。だがその測定も存在しない。**「提出中のagent+deckの実力」という最も基本的な数字が、このプロジェクトには一度も存在したことがない。**

### 1.3 提出枠が使われていない

提出は5回/日・同時2体・最終2体（要再確認）。今日から16日まで最大20回以上使えるのに、記録上のKaggle提出は 2026-07-18 の `neural-student-v1`（Validation ERROR）と entryfix のみで、有効なLBスコアは1件もない。**Kaggle LB そのものが、noiseの少ない大標本の無料評価チャネルとして丸ごと未使用である。**

---

## 2. 本質的原因（反証つき・階層順）

原因は独立ではなく積み重なっている。上の層を直さない限り下の層の議論は無意味である。

### L0（支配的原因）計測器の分解能が、測ろうとしている効果より粗い

**主張**: 過去1週間のすべての「合格/不合格」判定は、統計的にコイン投げである。

**証拠1 — 必要標本の計算。** 2群独立、p≈0.5 のとき差の標準誤差は `SE = 0.7071/√n`（pt単位では `70.71/√n`）。

| 1armあたり局数 | 差のSE | +5pt を検出できるか |
|---:|---:|---|
| 24 | 14.4pt | 不可能 |
| 48 | 10.2pt | 不可能 |
| 96 | 7.2pt | 不可能 |
| 192 | 5.1pt | 検出力 ≈ 50%（コイン投げ） |
| 384 | 3.6pt | 検出力 ≈ 78% |
| **1,560** | **1.8pt** | **検出力 80%, α=0.05** |

さらに実測の過分散を入れる必要がある。`v4-eval-noise-results-20260812.md` の Wave6 seed1 は同一checkpointの96局×3blockで SD **7.51pt**（二項理論値 5.10pt）。分散拡大係数 ≈ 2.2。したがって **+5pt を正しく検出するには 1armあたり 3,000局前後が要る。** 実際に使われたのは 24〜192局である。

**証拠2 — 事前ゲートの設計そのものが機能していない。** 現行ゲートは「両seed が対応baseline以上」「合計 +5pt」「6相手中4以上で非悪化」。n=24/seed のとき、真に +5pt のarmが「両seed非悪化」を満たす確率は約 0.4、真に 0pt のarmでも約 0.25。**真陽性と偽陽性がほぼ区別できない。** 「6相手中4以上非悪化」は 1cell 4局なので純粋な乱数フィルタである。

**証拠3 — 観測パターンが選択後の平均回帰と完全に一致する。**
`tomatomato-96` arm: fixed-six 24局 screen で 17/24 vs 11/24（+25pt!）→ shadow-B 48局で −5.73pt。
`tomatomato-24` arm: fixed-six 96局で +6.77pt → shadow-B 48局で −5.21pt。
チームはこれを「開発poolへの過適合／未知相手へ汎化しなかった」と解釈している。**より単純で、かつ数値と整合する説明は「noisyなscreenで上振れしたものを選び、別のnoisy標本で測り直したので平均へ戻った」である。** 真の効果 0 を仮定して、SE 14pt のscreenで +25pt が出る確率、その後 SE 10pt の再測定で −5pt が出る確率は、どちらも珍しくない。汎化の失敗を証明したければ、screen側とshadow側の両方で n を noise floor 以下まで下げる必要がある。それは一度も行われていない。

**証拠4 — 「seed反転」も同じもの。** seed0 +3勝 / seed1 −3勝 のような反転が全armで繰り返し観測され、「seed依存性」「GRU trajectory増幅」「seed-specific calibration」という因果仮説が積み上がっている。n=24/seed では、真の効果が ±10pt 以下のとき符号反転は**最も起こりやすい結果**である。仮説を立てる前に、noise で説明できることを排除していない。

**なぜこうなったか（機構）**: `scripts/measure_v4_checkpoint_strength.py` の評価ループは opponent × seat × game の**三重forループを単一プロセスで直列実行**している。並列化コードは一切ない。一方 DEC-024 では別laneで「16 worker、約2 game/s」を実測済みである。**評価が遅い → 24局screenで済ませる → noiseで判定する、という因果連鎖の起点は、書き忘れられた process pool 一つである。**

**反証条件**: 同一checkpoint同士を 1,000局以上で比較して差が noise floor 未満に収まらない、あるいは並列化しても throughput が改善しない場合、この診断は誤り。

### L1 目的関数が目的の代理になっていない（across-arm で実証済み）

各armの validation NLL と勝率変化を横に並べると、これは 24局の話ではなく **arm間の相関** なので比較的信頼できる。

| arm | ΔNLL (seed0) | Δ勝率（対Wave6） |
|---|---:|---|
| tomatomato-24 V4 proto | −0.030 | fixed-six +6.77pt → shadow-B −5.21pt |
| tomatomato-96 V4 short | −0.083 | screen +25pt → shadow-B −5.73pt |
| empty-selection context-only | −0.021 | seed0 −3勝 / seed1 +7勝 |
| action-balanced | −0.079 | 両seed −1勝 |
| lucifer19 V4 short | −0.043 | seed0 −1勝 / seed1 +3勝 |
| outcome-weighted (修正版) | −0.057 | seed0 −3勝 / seed1 +4勝 |
| V5 SetContext (Wave6 iso) | −0.013 | seed0 −3勝 / seed1 +5勝 |
| public OOD | −0.71 | ±0（48局） |
| strict-disagreement | −0.050 | +0.52pt（192局） |

**9本すべてで NLL が下がり、勝率の総和は 0。むしろ NLL 低下幅が最大の2本（−0.083, −0.079）が最も悪い。** これは「NLLが下がった、しかし性能改善ではない」と毎回注記しながら、次のarmでまた NLL を最小化していることと合わせて、**complete-action NLL はこのタスクの選択信号として使えない**という、この資料群で最も再現性の高い実証結果である。

理由は3つ。(a) 教師が中堅の公開agentであり、BCの上界は教師の強さ。Wave6 は既に教師と同水準なので伸びしろがない。(b) loss mass の大半は全方策が一致する強制的・自明な決定に載り、勝敗を決める少数の分岐には載らない。(c) `UniformLegalPolicyFactory` に至っては **全 legal action の logits が 0、top-1 margin 0、lower-index tie-break** である。その target を模倣する strict-disagreement arm は、定義上ラベルノイズを学習している。それを control ではなく本命として1週間走らせた。

### L2 outcome / value 信号が構造的に存在しない

`SpecialistModelV4` の出力は `PolicyOutputV4(logits, global_token, hidden_state)` のみ。**value head も Q head も無い。** 全armは行動分布の模倣である。

唯一 outcome を使おうとした signed residual も、チーム自身のレビューが正しく指摘した通り、
- screen は `decoding_mode="greedy"` の off-policy データ、
- baseline は状態価値 `V(s)` ではなく fold外 episode return の**グローバル平均**、
- したがって advantage ≈ (勝敗) − 定数 ≈ ±0.5、

であり、policy gradient estimator ではない。さらに実測 coverage は **exact context 一致 0.89%、residual 適用 0.44%、top-1 変化 0件**。**この arm は原理的に何も変えられなかった。** それでも fixed-six 24局を走らせ 19/48 を記録している。無効な介入の勝率が evidence corpus に残ることは、後続の判断を汚染する。

### L3 データ生成ループが存在しない

全V4学習の入力は **1 screen = 96局・約4,000 transitions**。857K パラメータのモデルを 1 epoch・60〜70 optimizer step で更新している。self-play は無い。継続的なデータ生成も無い。DEC-030/031 では R2D3 + PSRO + hidden 256 + batch 2048 + 5,000局という設計が完成していたが、8月に入って 96局 BC へ縮退した。

**ゲームAIで最も確実に効く唯一のレバー（大量に対局して結果から学ぶ）が、実装済みなのに止まっている。**

### L4 baseline と deliverable の不一致

ゲートは「Wave6 +5pt」。deliverable は「Kaggle LB での順位」。この2つを繋ぐ写像が存在しない。加えて学習・評価の subject deck（Archaludon）と提出 deck（Mega Lucario）が別物であり、その差を「昇格前に解消すべき課題」と正しく認識しながら、**解消作業（root deck で同じ評価を1本回す、数時間）は一度も critical path に載っていない。**

### L5 プロセスの病理

直近のセッション成果物を並べると: `frozen_residual_v1.py`, `frozen_residual_loader_v1.py`, `frozen_residual_factory_v1.py`, `measure_frozen_residual_strength_v1.py`（`--execute` を fail-closed 拒否する dry-run）, `coarse_public_residual_gate_v1.py`, `signed_residual_normalization_v1.py`, `coarse_record_residual_trainer_v1.py`（合成データのみ）, `research_logit_ensemble_v1.py`, `public_confidence_ood_v1.py`, `build_public_confidence_reference.py`, `run_meta_specialist_v4_public_confidence_ood_bc.py`（学習を実行しない契約専用runner）。

すべて `promotion_authority=false` / `performance_evidence=false` / `longrun_allowed=false` を持ち、focused test が通っている。**品質は高い。しかし、これは「結果でないものの監査可能性」に最適化された生産ラインである。** チーム自身の自己評価が正確にそれを示している: 実装・監査・証跡 85%、実戦性能改善の検証 25%、提出 0%。

残り4日でこの比率のまま進むと、最終成果物は「極めて厳密に検証された、提出されなかった研究コード」になる。

---

## 3. これは原因ではない（現データでは判定不能）

現在「不合格」「棄却」として文書化されているが、**実際には情報がゼロであり、棄却してはいけないもの**:

| 項目 | 現在の扱い | 実際 |
|---|---|---|
| V5 SetContext architecture | 不合格（24局） | 48局のデータで35 update した結果を24局で測った。architecture の可否は判定不能。 |
| GRU reset ablation（normal/action/turn） | turn reset 不採用（24局） | 判定不能。ただし DEC-023 で別laneでは GRU がゼロ初期状態から走っていた記録があり、**V4 lane の再帰が実際に機能しているかの検証は別途必要**（安価・高価値）。 |
| teacher 品質（tomatomato / lucifer19） | 汎化不合格 | 判定不能。ただし BC の上界＝教師強度という原理的制約は別途成立する。 |
| empty selection の STOP 写像 | 棄却 | 判定不能。 |
| action-balanced weighting | 不採用 | 判定不能。 |
| outcome weighting | 不合格 | 実装バグ修正後も判定不能。 |
| logit ensemble | 改善なし | 判定不能（同一データ学習の2checkpoint平均。事前期待値もほぼ0）。 |
| Rule v0 prior alpha=1 | 打ち切り | 唯一 −8.33pt と比較的大きく、かつ機構的説明（action-type priorが target selection を無視）もあるので、**これだけは棄却が妥当**。 |
| signed residual | coverage診断 | **棄却してよい。** coverage 0.44%・top-1変化0で原理的に無効。 |

---

## 4. ChatGPT/Codex の判断で間違っている可能性が高い点

遠慮なく列挙する。

1. **「評価noiseを先に測れ」は正しかったが、その帰結を実行していない。** SD 7.51pt を測定した *後* も、24局 screen で判定を続けている。noise を測る目的は n を上げることであって、noise の存在を注記することではない。これはレビュー側とチーム側の双方の失敗。

2. **「Wave6 を凍結して zero-init bounded residual + anchor KL/L2」は、この局面では悪い助言。** 状況が要求しているのは大きく動かして大標本で測ることであり、極小容量・exact hash gate 付きの介入ではない。結果は予測可能だった: coverage 0.89%、top-1 変化 0。**exact context/action hash が組合せ的状態空間でほぼ一致しないことは、実装前に暗算で分かる。** loader / factory / evaluator / testを作った後に実測で気づいている。

3. **cross-fitted baseline を「グローバル平均 return」で実装したのは設計ミス。** これでは advantage が状態に依存しない。value ベースを名乗るなら公開状態からの `V̂(s)` 回帰が必須。レビューは後からこれを指摘しているが、その時点で既に materializer / trainer / runner が3本できている。

4. **successive halving（fixed-six → shadow-A → broad → shadow-B）の構造は正しいが、予算配分が逆。** 最初のstageこそ良いarmを捨てないだけの n が必要。24局の第1stageは選別器ではなく乱数器であり、後段のstageに渡るarmが既にバイアスされている。

5. **「shadow-B で崩れた＝未知相手への汎化失敗」という因果帰属が誤り。** 平均回帰を排除していない。この誤帰属が「weak matchup residual」「OOD gate」「confidence threshold」という一連の派生作業を正当化してしまった。存在しない病気に3日分の治療を行っている。

6. **2 seed × 「両seed非悪化」要求は、小 n では検出力を捨てる。** 再現性の担保として seed を使うなら、seed をプールして n を倍にする方が情報量が多い。今の使い方はコイン2枚のANDを取っているだけ。

7. **「CABT に engine seed setter が無いので paired 比較は不可能」で思考が止まっている。** 分散低減の手段は engine seed だけではない。(a) 候補 vs baseline の**直接対戦**（同一 deck のミラー）は1局で相対比較が完結する最も強力な paired 相当の道具で、これは一度も使われていない。(b) 二値勝敗ではなく**サイド残り枚数差・決着ターン**を screening 指標にすれば分散が下がる。(c) 単純に n を上げる。

8. **「Kaggle Replay の行動を expert label に使わない」「Champion 変更は自動でしない」等の規律は正しい。しかし「Champion を変更しない」が「Champion を検討しない」に退化している。** 36pt の差を放置する規律は、規律ではなく麻痺。

---

## 5. 私なら設計をどう変えるか

### 5.1 捨てるもの（今期は一切触らない）

- teacher の差替え・追加収集（tomatomato / lucifer19 / R7 の permission 調整を含む）
- threshold / fraction / epoch / weight の sweep 全般
- residual sidecar 系統（exact / coarse / signed / normalization）— 4ファイル分の実装ごと凍結
- logit ensemble、reset ablation
- V4 vs V5 の architecture bakeoff
- 24局 / 48局 screen という単位そのもの
- `promotion_authority=false` 系の新規契約モジュールの追加

### 5.2 変えるもの

**評価器**: 直列 → 16 worker process pool（DEC-024 の設定をそのまま移植: OMP/MKL 1 thread、CABT のみ登録、32局/worker recycle）。加えて、
- **ミラー直接対戦モード**を追加する（同一 deck・両seat・候補 vs baseline）。これが今後の第1 screen。
- 二次指標として**サイド枚数差**と**決着ターン**を記録する（分散低減・screening用）。
- baseline に **Rule v0 と root deck** を必ず含める。

**目的関数**: teacher action の NLL を捨て、**outcome由来の advantage** に置き換える。最小構成は、
1. 大量の自己生成対局（対 broad pool、両seat）を集める
2. 公開状態から `V̂(s)` を回帰（同じデータ、cross-fit）
3. `A = G − V̂(s)` で AWR / CRR（`exp(A/β)` 重み付き BC）または filtered BC
既存の `learner_awr_crr_v1.py` / `critic_v3.py` / `critic_warmup_v3.py` が使える。PPO / V-trace はこの期間では起動しない。

**モデル**: value head を追加する（policy と共有 trunk、logits と並置）。これは architecture 変更ではなく**欠落の補完**。candidate-candidate attention（V5 の狙い）は、データが 10 倍になってから再評価する。今の 4,000 step では容量を増やしても過適合が速くなるだけ。

**ゲート**: 「Wave6 +5pt」を捨て、**「現在の提出物より強いか」を第1の問いにする**。閾値は事後に決めず、事前に「ミラー直接対戦 1,000局で 95% CI 下限 > 50%」とする。

**deck**: 独立した最適化対象として critical path に載せる（次節）。

---

## 6. 期待値順の方針ランキング

期待値 = （成功時の勝率改善）×（4日で完了する確率）で評価している。

| 順位 | 方針 | 期待改善 | 4日での完了確率 | 根拠 |
|---:|---|---|---|---|
| **1** | **評価器の並列化＋ミラー直接対戦の実装** | 直接は0pt。しかし以降の全判断の前提条件 | 高 | 16倍の証拠量。単独で「判定不能」を「判定可能」に変える |
| **2** | **V4 提出bundleの作成と、Rule v0 / V4 / deck 2×2 の決着測定 → 提出** | **+20〜35pt（LB実効）** | 高 | 36pt の既知ギャップ。技術的障害は packaging のみ |
| **3** | **deck 最適化** | +5〜15pt | 中〜高 | 学習不要・完全並列・評価が不偏。96 opponent ID / 75 unique deck / meta weight が既にある。現提出deckは一度も評価されていない |
| **4** | **大量自己対局 + outcome advantage（AWR/filtered BC）の1本だけの長時間学習** | +3〜10pt | 中 | 唯一の原理的に正しい学習経路。ただし4日では1回勝負 |
| 5 | GRU 再帰が実際に効いているかのablation（n≥1,000） | 診断値 | 高 | 安価。効いていなければ表現設計を根本から見直す材料 |
| 6 | 推論時の軽量 1-ply 評価（KO/サイドレース/エネルギー）の logits ブレンド | +0〜5pt | 中 | search が封じられている中での唯一の現実的な探索代替 |
| 7 | V5 candidate attention / architecture 比較 | 不明 | 低 | データ量を増やしてからでないと測れない |
| — | teacher 差替え・sweep・residual 系 | ≈0 | — | 今期は打ち切り |

---

## 7. 長時間学習を合理的に開始できる状態までの具体的実験

「長時間学習の開始条件」は、**手持ち最良が提出済みで、計測器が動いていて、学習データを大量生成できること**の3点。以下は残り4日への割り付け。

### Phase A（本日中・0〜8時間）— 計測器と提出路を同時に立てる

| # | 作業 | 完了条件 | 見積 |
|---:|---|---|---:|
| A1 | `measure_v4_checkpoint_strength.py` を process pool 化（16 worker、CABTのみ登録、1 thread BLAS、worker recycle 32） | 同一checkpointで直列版と並列版の勝率が 1,000局で CI 重複、throughput ≥ 10倍 | 2–3h |
| A2 | ミラー直接対戦 runner（同一deck・両seat・候補 vs baseline・faultカウント） | Wave6 seed0 vs Wave6 seed1 を 1,000局で測り、CI が 50% を跨ぐ（sanity） | 2h |
| A3 | V4 提出bundle（`main.py` + torch + checkpoint、CPU推論、例外/タイムアウト時 Rule v0 fallback） | **`kaggle_environments.make("cabt")` と `get_last_callable()` を使った archive-only 実行で全step完走**（2026-07-18 postmortem の G1–G6 を必ず通す）。1決定あたり p95 latency を記録 | 3–4h |

A3 は postmortem の教訓を機械的に強制すること。`__file__` 未定義の raw exec 経路のテストは既に `test_raw_exec_without_file` にある。それを V4 bundle にも適用する。

### Phase B（Day 1・8〜20時間）— 提出決定を測って、提出する

| # | 実験 | 設計 | 見積 |
|---:|---|---|---:|
| B1 | **2×2 決着測定** | {Rule v0, Wave6 V4} × {root deck, Archaludon deck} の4条件を、fixed-six ではなく **broad pool（96 ID から重複deckを除いた 24〜32相手）** に対して両seat・各条件 1,536局。meta weight で重み付けした期待スコアも併記 | 3–5h（並列後） |
| B2 | **提出** | B1 の上位2条件を、Kaggleのactive 2枠へ投入。LBスコアの取得を開始する | 1h |

B1 で初めて「提出中のagent+deckの実力」が数字になる。**この数字が存在しないまま4日を使い切ることが、現在の最大のリスク。**

法務注意: Archaludon deck は他チームの公開デッキ由来。デッキリスト自体は対戦中に公開される情報だが、**他参加者の公開資産を自分の提出に使うことが Kaggle Rules 上許容されるかは、提出前に Rules タブで必ず確認すること。** グレーなら、B3 として独立deck構築（下記）へ切り替える。

### Phase C（Day 1–2・並行）— deck 最適化

| # | 実験 | 設計 | 見積 |
|---:|---|---|---:|
| C1 | 候補deck 6〜10種（自作を含む）× 固定policy（B1の勝者）で、broad pool に対し各 1,024局 | 上位2種を確定。deck間差は policy 差より大きい可能性が高く、n=1,024 で十分検出できる | 6–8h（並列・GPU不要） |

deck探索は学習を止めずに走らせられる。CPUだけで完結し、GPUは Phase D が占有できる。

### Phase D（Day 1–3）— 唯一の長時間学習

| # | 作業 | 設計 |
|---:|---|---|
| D1 | データ生成 | B1勝者のpolicyで、broad pool 対戦を **10,000〜20,000局**。actor-visible transition + 最終outcome + サイド差を保存。GPU推論はDEC-026 の spawn actor + IPC 方式が実装済み |
| D2 | value head 追加 | 共有trunk に scalar head。公開状態のみ入力。D1データで `V̂(s)` を回帰（cross-fit、fold外評価） |
| D3 | AWR / filtered BC | `A = G − V̂(s)`、`w = exp(A/β)`（β は 1本だけ事前固定、sweepしない）。あるいはより頑健に「明確な勝ち局のみの filtered BC」。record単位正規化を使う |
| D4 | 判定 | **ミラー直接対戦 1,000局** で D3出力 vs D1初期policy。95% CI 下限 > 50% なら broad pool 1,536局へ、そこも通れば提出枠を差し替える |

**開始条件（すべて満たしたら D1 を起動してよい）**
- A1 完了（並列評価が動く）
- A3 完了（提出bundleが Kaggle emulator を通る）
- B2 完了（何か強いものが既にLBに載っている＝最悪ケースが確保されている）
- D1 のデータ生成速度が実測で ≥ 1,000局/時

**中止条件**
- D4 のミラー対戦で CI 下限が 50% を下回る → 打ち切り、B1勝者を最終提出とする
- fault > 0、または p95 latency が提出制限を超える → 打ち切り

### Phase E（Day 4・8/16）— 最終提出枠の確定

最終2枠は「分散した2つ」にする。同じ系統の checkpoint を2つ置かない。例: ①最も期待値の高い neural + 最良deck、②最も分散の小さい安定構成（Wave6 + 最良deck、または B1勝者）。

---

## 8. 今のチームが見落としている問題（要約）

1. **評価器が直列で書かれている。** すべての病理の物理的起点。
2. **候補 vs baseline の直接対戦を一度もしていない。** 第三者poolを経由するより桁違いにサンプル効率が良い。
3. **勝敗の二値しか見ていない。** サイド差・決着ターンを使えば必要局数が半減する。
4. **平均回帰と汎化失敗を区別していない。** これが3日分の派生作業を生んだ。
5. **提出物の実力を測ったことがない。** 12/96 という数字を書きながら提出を変えていない。
6. **deck を最適化対象として扱っていない。** TCGで最も安価に効くレバー。
7. **Kaggle LB を評価チャネルとして使っていない。** 提出枠は余っている。
8. **NLL と勝率が無相関であることを9回実証しながら、10回目も NLL を最適化している。**
9. **value / outcome 信号が model に存在しない。**
10. **期限に対してスケジュールが成立していない。** 「最短1〜3日」を4日前に書いている。

---

## 9. 守るべき境界（変更しない）

以下は今回の提案でも一切緩めない。

- CABT の合法手判定を hard truth とする。合法性・`minCount`/`maxCount`・重複禁止・60枚を崩さない。
- 相手の非公開情報を推論入力に入れない。opponent ID / seat は学習サンプル選択にのみ使い、runtime 入力と checkpoint に入れない。
- Kaggle Replay の行動を expert label として直接学習に使わない。
- 他チームの非公開情報・認証情報・規約上使えないデータを取得しない。他チームの公開デッキ利用は **Rules で明示確認してから**。
- commit / push / Kaggle 提出は、ユーザーの明示指示があるときだけ行う。
- 未確認の勝率・仕様を確定事項として書かない。

---

## 10. 一行で

**あと4日。新しい objective を探す前に、測れるようにして、既に持っている36pt を提出しろ。**
