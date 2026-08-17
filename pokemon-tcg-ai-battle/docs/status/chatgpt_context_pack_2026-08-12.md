---
project: MAGE-PTCG
document_status: context-pack
as_of: 2026-08-12
canonical_source: git
purpose: ChatGPT に単独で添付して現状・証拠・次の実験をレビューさせるための非正典 snapshot
---

# MAGE-PTCG / V4 strict-disagreement context pack

この文書は、リポジトリを初めて読む分析者が、別の会話履歴や生ログなしで現在の判断を再構成できることを目標にした。数値・SHA・パスは、2026-08-12 時点でローカルに存在する JSON、evidence、コードから確認した。ここにない実験、checkpoint、カード効果、性能を推測で補ってはいけない。

## 0. 先に結論

- 目的は、Pokemon TCG AI Battle Challenge の Simulation 部門で合法な 60 枚デッキと実戦方策を作り、最終的に説明可能な Strategy 成果物へつなぐこと。Rule Agent v0 が現在の Champion / rollback であり、V4 学習経路は research-only で自動昇格しない。
- 現在の主対象は、公開ゲーム状態・actor-visible な自分の情報・CABT が提示する合法 ActionKey だけを使う recurrent V4 policy である。実行時に rule teacher、相手の非公開情報、将来の乱数を読まない。
- 既存 strict-paired DAgger は、対応する Wave6 baseline 93/192 (48.44%) に対して candidate 101/192 (52.60%)、+8 勝 / +4.17 ポイント、全 4 評価 fault 0。ただし seed0 は +7 勝、seed1 は +1 勝、相手別改善は反転しており、事前の「約 +5pt、seat と相手の安定した非悪化」を満たしたとは扱わない。
- strict-disagreement の抽出、supervision_weight loss mask、CPU preflight、fixed-six/shadow pool の freeze、GPU復旧後の2-seed pilot、fresh checkpoint、fresh fixed-six評価まで完了した。pilot report は `RESEARCH_ONLY_COMPLETE` / `cuda:0` / fault 0 だが、fresh strict は 94/192 対 Wave6 baseline 93/192（+0.52pt）に留まり、性能改善のpromotion gateは不合格である。shadow-B full evaluation、broad/meta-weighted評価、長時間学習は未実施である。
- 最初の CUDA unavailable は通常 Codex sandbox から WSL2 の /dev/dxg が不可視だったことが原因だった。sandbox 外の read-only smoke は成功したが、同時 CUDA process の後、NVML/PyTorch が GPU lost 状態へ遷移した。WSL shutdown 後も回復せず、Windows の nvidia-smi は GPU is lost. Reboot the system to recover this GPU を返した。保留していた再起動要求を`shutdown.exe /r /f /t 0`で完了させた後、Windows実ブート時刻、6006/6005、Windows/WSL nvidia-smi、PyTorch CUDA、V4モデル転送がすべて成功した。現在はGPUを使用可能な状態である。
- 次の本命は、permission が明示された `tomatomato_archaludon` を現行 policy SHA で新規収集・sealし、seed対応 strong-teacher BC を比較すること。R7は `local_eval_only` / `smoke_ok=false` のため trainingに使わない。commit、push、Kaggle submission、Champion変更はしていない。

## 1. どの文書を正典とするか

正典は Git 内 Markdown と実行 artifact であり、Notion はミラーである。概念設計の入口は [docs/plan/MAGE_PTCG_v5_README.md](../plan/MAGE_PTCG_v5_README.md)、全体設計は [docs/plan/design/00_overall_plan.md](../plan/design/00_overall_plan.md)、教師/生徒設計は [docs/plan/design/03_machine_learning_teacher_student_plan.md](../plan/design/03_machine_learning_teacher_student_plan.md)。現在地は [docs/status/current_status.md](current_status.md) と [docs/status/handoff.md](handoff.md) が入口で、過去の判断をまとめた一次 evidence は次の通り。

- [docs/evidence/v4-performance-history.md](../evidence/v4-performance-history.md)：Wave 3〜6、DAgger Wave 1/2 の性能履歴。
- [docs/evidence/v4-wave3-postrun-audit-20260812.md](../evidence/v4-wave3-postrun-audit-20260812.md)：targeted Wave3 が完走済みで、Wave6 を下回ったことの監査。
- [docs/evidence/v4-strict-disagreement-preflight-20260812.md](../evidence/v4-strict-disagreement-preflight-20260812.md)：seed0/1 の strict mask、confusion、teacher tie、shadow-A/B。
- [docs/evidence/v4-strict-disagreement-shadow-evaluation-20260812.md](../evidence/v4-strict-disagreement-shadow-evaluation-20260812.md)：既存 strict-paired と Wave6 の frozen shadow-A 評価。
- [docs/evidence/v4-gpu-access-recovery-20260812.md](../evidence/v4-gpu-access-recovery-20260812.md)：sandbox 境界と read-only CUDA smoke。
- [docs/evidence/v4-gpu-pilot-oom-20260812.md](../evidence/v4-gpu-pilot-oom-20260812.md)：同時 CUDA process、OOM、kernel/NVML、WSL 再起動後の GPU lost。

この pack は非正典の snapshot である。設計やコードを変更したら、まず status/evidence を更新し、その後にこの pack を再生成する。文書の数値だけを変更して実験結果を上書きしてはいけない。

## 2. プロジェクト目的と大きな設計判断

### 2.1 目的

MAGE-PTCG は、ポケモンカードゲーム AI Battle Challenge の Simulation 部門で高い実戦レーティングを目指し、戦略ロジックを Strategy 部門で説明可能な形に整理する統合 AI システムである。目的関数は live/freeze メタへの勝率だけでなく、未知デッキ・未知方策への頑健性、10 分以内の安定実行、invalid/exception/timeout/crash の抑制、時変メタへの適応、凍結期間の再現性を含む。

大会上の基本制約は、合法なカードリスト、60 枚デッキ、各プレイヤー最大 10 分、Kaggle Simulation、提出制限である。重要な設計上の真値は独自カード解釈ではなく CABT の実測 legality/transition である。

### 2.2 critical path と Champion

第三者レビューの判定は ARCHITECTURE SOUND / EXECUTION SCOPE CORRECTED。提出 critical path は P0 → C1 → C2a/C2b → C3/C4 → C5 に縮小し、P0 (Continuous Submission Baseline) を全期間継続する。Rule Agent v0 は合法性を保証する Champion / rollback / teacher 初期値であり、Rule Agent v1 は v0 に 105–95 で非昇格、opinion/counterexample 源に留める。

V4 Student は、Rule v0 との非劣性、paired evaluation、unknown holdout、fault/latency、clean package の promotion gate を通らない限り Champion にならない。PROMOTION_READY は次の research arm を開始できる機械判定であり、長時間学習・Champion 変更・Kaggle 提出の自動許可ではない。

### 2.3 teacher と runtime の分離

Teacher は深い探索・ルール・リーグで学習用 target を作る。Student/runtime は公開情報境界と時間制約の下で軽量に動く。V4 DAgger は学生が実際に到達した公開 prefix に teacher target を付け直す supervised overlay であり、teacher を runtime に戻す RL ではない。Kaggle Replay の実際の action は expert label として直接学習せず、必要なら public information set 上で再解析した regret を使う。

## 3. V4 実装構造（現在の active core）

V4 は「カード単純分類器」ではない。CABT が決めた一つの合法 semantic action domain 上で、公開/actor-visible state と現在の prefix に条件付けて candidate/STOP logits を返し、shared decoder が一つの完全 action を確定する recurrent legal-action policy である。

| 層 | 主なファイル | 責務 |
|---|---|---|
| actor-visible boundary | src/mage_ptcg/meta_specialist/actor_visible_v2.py, actor_visible_features_v1.py | CABT observation を actor-visible typed state、public projection、local semantic candidate へ閉じる。raw observation は保持しない。 |
| legality / action identity | actions.py, cabt_json_contract_v1.py, cabt_legality_v1.py | Stable semantic ActionKey、option type、min/max、ordered/unordered、legal mask、complete action enumeration、physical alias の束ね方。 |
| relational representation | representation_v4.py | exchangeable entity/class refs、公開 entity、candidate、semantic prefix を canonical relational state に変換する。serial locator を semantic identity に使わない。 |
| neural model | neural_model_v4.py | card/entity/type/owner/zone/scalar/relation/prefix/candidate embedding、GRU、semantic candidate logits、STOP head。checkpoint と live implementation SHA を検証する。 |
| runtime policy | neural_policy_v4.py, runtime.py | game ごとに fresh decision object を作り、GRU を一 complete action に一度だけ進め、shared decoder で CABT option index を commit。例外/illegal/timeout は random fallback にせず fail-closed。 |
| trajectory / actor pool | trajectory_v1.py, trajectory_schema_v3.py, actor_pool_v1.py, collect_trajectories_v1.py | actor-visible transition、prefix chain、behavior log probability、fault provenance を process 分離・atomic record として保存する。 |
| sealed recurrent dataset | recurrent_dataset_v4.py, local_dataset_v2.py | source/teacher-quality/selection manifest、episode continuity、partition、record/content hash、checkpoint identity を検証し、stream reader を構成する。 |
| recurrent BC | recurrent_bc_v4.py | research-only uniform/optional action weights、record-group GRU、semantic complete-action NLL、TBPTT、checkpoint/resume、imitation metrics。 |
| DAgger / strict | dagger_v4.py, scripts/run_meta_specialist_v4_dagger_bc.py, scripts/build_v4_strict_disagreement_report.py | captured public chain の teacher relabel、episode mixing、strict disagreement metadata と supervision mask を作る。 |
| evaluation / gate | evaluation_protocol_v2.py, measure_v4_checkpoint_strength*.py, v4_imitation_metrics.py, v4_promotion_gate.py | subject deck/opponent/seat/base seed/protocol/checkpoint SHA を閉じ、fault 0 と seed/seat/opponent guardrail を判定する。 |

### 3.1 入力

runtime が受け取る入口は ActorVisibleDecisionStateV2 と CABT の decision envelope である。特徴化された SpecialistModelInputV1 / SpecialistStepInputV1 は次を含む。

- 場、active/bench、HP/max HP、damage/status、当ターン登場、付与済み energy/tool/pre-evolution、stadium/context/effect など公開 entity。
- actor の hand、deck reveal/looking、discard、残り side など本人が合法的に見られる情報。
- opponent の公開場、公開 discard、公開 count 等。相手 hand/deck/prize の内容そのものは含めない。
- turn/step/selection type/context/min/max/option count、残り counters、使用済みフラグ、公開 history、時間などの state scalar（V4 feature schema は state scalar 41 個）。
- 現在の CABT legal candidate class、source/target/host、action type、numeric/card/attack/skill argument、allowed alias count、選択 prefix、STOP 可否。

候補幅は通常最大 512、完全 action の列挙は 65,536 を超えたら fail-closed、card collection は 60 枚上限で検査する。card vocab は PAD=0; UNK=1; official_card_id=k => k+1 の固定規則で、欠落や SHA drift を既定値で隠さない。

### 3.2 出力と一手の意味

model は semantic class ごとの logits と、STOP が許されるときの STOP logit を返す。legal mask と shared semantic decoder が、complete action の conditional prefix distribution を構成し、最後に CABT option index/selection count へ変換する。

Option type は 0 NUMBER, 1 YES, 2 NO, 3 CARD, 4 TOOL_CARD, 5 ENERGY_CARD, 6 ENERGY, 7 PLAY, 8 ATTACH, 9 EVOLVE, 10 ABILITY, 11 DISCARD, 12 RETREAT, 13 ATTACK, 14 END, 15 SKILL, 16 SPECIAL_CONDITION。完全 action は bounds、unique key/index、unordered の canonical ascending、ordered の sequence preservation、stale envelope を検証する。

physical card alias の個数を semantic class probability に掛けてはいけない。同じ意味の physical alias は semantic choice を決めた後に一つへ lexicographic に束ねる。これを逆にすると alias 数だけ確率が増える。one-choice domain は recurrent hidden state は進めるが policy loss denominator からは除外でき、forced count は診断へ残す。

一つの physical record に属する prefix rows は同一 state を共有し、GRU transition は一回だけ実行する。complete action が commit された時だけ hidden を一回更新し、abort では hidden を進めない。game ごとに fresh policy object を作るため、前 game の hidden は次 game に漏れない。

### 3.3 非公開情報境界

actor が合法的に見られる自分の hand 等は入力してよいが、次は入力・teacher query・trace・DAgger relabel に使わない。

- 相手の未公開 hand、deck 順序/内容、prize 内容、将来の draw/乱数。
- engine 内だけに存在する private state や、hidden endpoint を推測して補った card identity。
- teacher が異なる action を選んだ後の counterfactual state。strict DAgger は同じ recorded public prefix chain を最後まで使う。

runtime trace は public projection、selection type/context/min/max、semantic complete-action log probability、candidate count/collision と fault provenance だけを保存し、private hand/prize/deck/order、raw hidden key tree、physical option index は保存しない。trajectory_v1.py と submission_privacy.py は forbidden private fields を reject する。CABT legality は hard truth、Rule/Playbook/Knowledge prior は soft candidate ordering であり、soft で legal action を削除してはいけない。

## 4. teacher / DAgger / strict disagreement / loss mask

### 4.1 teacher target

DAgger の teacher factory は、sealed public model_input / step_input 上で teacher logits を計算する。forced sole STOP は teacher query を作らず target mass (1.0,) とする。それ以外は semantic logits（STOP 可なら STOP を含む）を stable softmax に通し、最大 mass、同率なら低い index を target とする。teacher version は今回の strict screen で b89ca316191957b26e5afa37c6cd121f61ba43435724aa6b982b3b06b07ff6e。

teacher の target は「その teacher の public choice」であって正しさの証明ではない。今回の teacher は UniformLegalPolicyFactory で、eligible prefix の top-1 margin は 0（legal domain 内が tie、lower-index tie-break）だった。平均 teacher target probability は seed0 で約 0.1492、seed1 で約 0.1480、entropy は約 1.96/1.97 である。

### 4.2 通常 DAgger

学生を CABT で rollout し、学生が実際に訪れた complete game の公開 prefix を保存する。各 prefix を teacher で relabel し、base sealed recurrent selection と episode/component 単位で混ぜる。selected overlay は research_only=true、promotion_authority=false。これは distribution shift（学生が teacher と違う action を取った後の状態が通常 teacher dataset に少ない）を減らすための supervised data augmentation である。

### 4.3 strict disagreement の定義

strict_disagreement_metadata_v4 は各 recorded transition の各 public prefix について、

1. 学生が実際に選んだ semantic action を sealed legal domain の student_index へ写像する。
2. 同じ step_input chain を teacher で relabel した teacher_index と比較する。
3. student_index != teacher_index を disagreement とする。
4. teacher target action type ∈ focus set、mean behavior_log_probability <= threshold、disagreement の AND を eligible とする。

eligible が一つでもある game は、recurrent context を壊さないため全 transition を complete episode として保持する。ただし loss-bearing なのは eligible prefix だけで、teacher の最初の違う token 後に counterfactual state は生成しない。forced sole STOP は query なしで、forced-stop disagreement は 0 として扱う。

今回の固定 filter は teacher target action type {9,13,14} = {EVOLVE, ATTACK, END}、mean behavior log-probability <= -0.2。-0.5 では mass が約 106/110、-1.0 は 0 になるため、threshold は単なる「低信頼」の名前ではなく data budget を決める制御である。student or teacher target の対称 filter は -0.2 で seed0 867 (+16)、seed1 990 (+5) と増分が小さいため、最初の arm は teacher-target-only に固定する。

### 4.4 supervision_weight loss mask

以前は selected complete episode を選んでも trainer に mask が伝わらず、episode 内全 prefix が loss-bearing だった。今回の修正では RecurrentBCStepV4.supervision_weight（既定 1.0、有限値 [0,1]）を追加した。

- eligible disagreement prefix: supervision_weight=1.0。
- 同じ selected episode の他 prefix: supervision_weight=0.0。GRU hidden context には通すが、loss/NLL denominator/positive STOP metric には寄与させない。
- train/evaluation/positive STOP metrics は quality_weight * supervision_weight * reach_mass を使う。
- selected objective hash と DAgger record hash に mask を含め、mask 変更を別 experiment identity とする。
- report に supervised_prefix_count を追加し、selection metadata の effective mass と突合する。

したがって effective_loss_mass は、selected game 数のラベルではなく、実際に supervised gradient を持つ prefix の reach_mass * quality_weight の合計として読む。今回の strict overlay は research-only で、production teacher authority ではない。

## 5. 評価プロトコルと比較の読み方

主要な公平比較は Archaludon subject deck、fixed-six の 6 opponent、両 seat、seed ごと 96 局（各 opponent×seat 8 局）、base seed 10100000、最大 2,000 step、共通 evaluation protocol SHA 0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba。subject deck は opponents/public_archaludon_cinderace_r7/deck.csv、SHA 42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e。fault、identity mismatch、未完了、timeout が一件でもあれば比較を採用しない。

ただし、古い v4-fixed-heldout-* と新しい v4-wave6-current-evaluator-* / DAgger evaluation は evaluator implementation 世代が違う。勝率が異なる artifact を一つの時系列へ連結しない。NLL、imitation top-1、24 局 screen、4 局/cell shadow は、それぞれ別の postcondition であり、総合強度の証明へ足し合わせない。

## 6. Wave / seed / experiment の判断履歴

### 6.1 teacher-only V4 の初期履歴

以下は [v4-performance-history](../evidence/v4-performance-history.md) に記録された teacher-only / ordinary BC 系列で、後の strict DAgger と混ぜない。

| 段階 | 主設定 | 結果（fault） | seed差 / 解釈 |
|---|---|---|---|
| 初期 Wave3 | train 512 試合系列、validation 128 系列、1 epoch。seed0 NLL 1.3369→0.7522、seed1 1.3133→0.7319 | 各 candidate 29/96、V2 比較 23/96（fault 0） | NLL と短期勝率が改善したため 3 epoch へ進んだが、後の一般化は未確定。 |
| 初期 Wave4 | 512/128 系列を 3 epoch | 2 candidate 合計 99/192 (51.56%, fault 0) | 検証精度は再現したが、seat と END/EVOLVE/ATTACK の弱さが残った。 |
| action-weight 試行 | under-represented action を重くする | 24 局で通常 loss と同じ 4/24 | オフライン action metric を上げても実戦差が出ず不採用。 |
| 初期 Wave5 | 通常 loss、各 seed 1,536 optimizer updates | 95/192 (49.48%, fault 0) | Wave4 未満。ozawa_crustle_v2 が 5/32 と弱く、loss/勝率乖離を確認。 |
| 初期 Wave6 | 通常 loss、各 seed 4,096 updates / 8 epoch | 89/192 (46.35%, fault 0) | 学習損失を下げるだけの延長を停止。 |

固定 holdout の現在比較に使う Wave6 current evaluator は seed0 47/96 (48.96%)、seed1 51/96 (53.13%)、計 98/192 (51.04%)。同一 checkpoint の別 evaluator 世代で seed0 45/96、seed1 44/96、計 89/192 があるため、artifact family を混ぜない。

### 6.2 DAgger Wave1

48 局から 2,643 transitions を収集し、base train 28,000 / validation 6,808 と混ぜて 3 epoch。seed0/1 の initial NLL は 1.0004、best は 0.8342 / 0.8318、各 1,629 updates。base seed 12300000、固定6、両 seat、各候補と対応 Wave6 を 192 局ずつ、全 768 局 fault 0。

| arm | seed0 | seed1 | 合計 |
|---|---:|---:|---:|
| DAgger candidate | 100/192 (52.08%) | 110/192 (57.29%) | 210/384 (54.69%) |
| 対応 Wave6 | 84/192 (43.75%) | 108/192 (56.25%) | 192/384 (50.00%) |
| 差 | +16 (+8.33pt) | +2 (+1.04pt) | +18 (+4.69pt) |

先手は合計 +14.58pt、後手は -5.21pt。合計の改善だけで長時間化せず、seed/seat gate を導入した。

### 6.3 DAgger Wave2 一様 / balanced_v1

比較条件は Archaludon、fixed-six、両 seat、各 seed 96 局、base seed 10000000、max steps 2,000、fault 0。

| arm | seed0 | seed1 | 合計 | Wave6 current evaluator との差 |
|---|---:|---:|---:|---:|
| 一様 DAgger | 52/96 (54.17%) | 44/96 (45.83%) | 96/192 (50.00%) | 98/192 に -2局 (-1.04pt) |
| balanced_v1 | 50/96 (52.08%) | 50/96 (52.08%) | 100/192 (52.08%) | +2局 (+1.04pt) |
| Wave6 基準 | 47/96 (48.96%) | 51/96 (53.13%) | 98/192 (51.04%) | 基準 |

一様は先手側を上げたが後手側を悪化。balanced は小さい +1.04pt に留まり、事前 gate（約 +5pt、seat guardrail、相手別非悪化）未達として長時間化しなかった。

### 6.4 targeted DAgger Wave3

focus は過去の弱点仮説に基づく Kiyotah/Nihei/Ozawa、主に seat1。available 96 episode から DAgger 42 episode（実 fraction 0.3043478）を混ぜ、2 seed、各 epoch 96 optimizer updates、3 epoch / 累積 288 updates。完走・checkpoint/report finalization は正常で、旧 status=running は stale。

| arm | seed0 | seed1 | 合計 | fault |
|---|---:|---:|---:|---:|
| Wave6 current evaluator | 47/96 (48.96%) | 51/96 (53.13%) | 98/192 (51.04%) | 0 |
| targeted Wave3 | 52/96 (54.17%) | 41/96 (42.71%) | 93/192 (48.44%) | 0 |
| candidate − baseline | +5 | -10 | -5 (-2.60pt) | — |

seat0 は Wave3 42/96 vs Wave6 46/96 (-4.17pt)、seat1 は 51/96 vs 52/96 (-1.04pt)。opponent 合計は Kiyotah -2、Nihei -4、Ozawa -3、Skarin 0、Sue +2、Yaroslav +2（各32局）。focus 3 相手は Wave6 55/96 → Wave3 46/96 (-9.38pt)、non-target 3 相手は 43/96 → 47/96 (+4.17pt)。target overlay の約 60% が non-target へ流れ、EVOLVE target mass は 0 だったため、soft focus で弱点を直せるとは言えず不採用。

### 6.5 strict-paired DAgger Wave4（現時点の最新完走 candidate）

report: runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/bc.json（schema meta-specialist-v4-dagger-paired-bc-report-v1、status RESEARCH_ONLY_COMPLETE、promotion_authority=false）。seed0/1 の screen、transition、init checkpoint を paired manifest で分離し、同じ seed の入力を別 seedへ流用しない。manifest SHA は 47d75eec59c8d058523a3c0b41319bf47edb04856022e9aadbaa5f52f786250b。

| arm | seed0 | seed1 | 合計 | fault |
|---|---:|---:|---:|---:|
| Wave6 基準 | 43/96 (44.79%) | 50/96 (52.08%) | 93/192 (48.44%) | 0 |
| strict-paired candidate | 50/96 (52.08%) | 51/96 (53.13%) | 101/192 (52.60%) | 0 |
| 差 | +7 | +1 | +8 (+4.17pt) | — |

条件は subject deck SHA 42165967…, fixed-six、両 seat、各 opponent×seat 8 局、base seed 10100000、max steps 2000、protocol SHA 0f98f699…。candidate の opponent 別 wins（baseline→candidate）は次の通り。

| opponent | seed0 | seed1 | 解釈 |
|---|---:|---:|---|
| kiyotah_lucario | 10→12 (+2) | 9→11 (+2) | 安定改善 |
| nihei_megalopunny | 8→11 (+3) | 10→10 (0) | 改善/同等 |
| ozawa_crustle_v2 | 6→3 (-3) | 6→9 (+3) | seed 反転 |
| skarin_dragapult | 6→6 (0) | 3→6 (+3) | seed1 のみ改善 |
| sue124_alakazam | 6→8 (+2) | 12→5 (-7) | seed 反転、最大の guardrail risk |
| yaroslav_crustleaware_lucario | 7→10 (+3) | 10→10 (0) | 改善/同等 |

seat 別 candidate−baseline は seed0 が seat0 +2/48、seat1 +5/48、seed1 が seat0 -2/48、seat1 +3/48。offline validation でも seed1 の ATTACK top1 29.8%、END 56.1%、STOP 86.7%に対し、seed0 は ATTACK 46.9%、END 53.4%、STOP 60.0%で、seed variance が残る。

### 6.6 promotion gate の位置付け

別系統の Wave6 V4 vs V2 baseline promotion gate は、V4 seed0 45/96、seed1 44/96、V2 baseline 21/96 と独立再実行 22/96、平均差 +0.23958、fault 0、imitation complete-action top1 約 0.80116/0.80179、root 約 0.79377/0.79607、STOP 0.89394/0.86364 で PROMOTION_READY になった。しかしこれは「V4 route が V2 より強い」という gate であり、strict DAgger の candidate を Champion へ変更する許可ではない。

## 7. strict preflight の一次数値（seed0 / seed1）

正典 JSON: runs/meta-specialist-v4-strict-disagreement-preflight-20260812.json、SHA-256 3e1120066b8d42c5187ec1d65a23aac74d44b2034783eef2995c2ebb6cc321bf。preflight report は内部 artifact SHA 1137df3acc79ca90cf876ef34d41a8040b38baa5a77794868a916a525f7dc269 を保持する。screen は fault-free valid 96 games。

| seed | screen path | screen SHA | transitions SHA | transition records | broad disagreement games / prefix | screen non-forced mass | strict selected games (train/val) | eligible / supervised prefix mass | mass / screen |
|---:|---|---|---|---:|---:|---:|---:|---:|---:|
| 0 | runs/meta-specialist-v4-archaludon-dagger-wave6-screen-v2/screen.json | 9ad78bf31f41d307916b25d238544ea5060e0df9a8e16b5ca72a8e3977fc00e3 | 2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce | 4,763 | 95 / 3,076 | 4,498 | 88 (68 / 20) | 851 / 851 | 18.92% |
| 1 | runs/meta-specialist-v4-archaludon-dagger-wave6-screen-seed1-v2/screen.json | aed7438628ffdcb4a4d0c11a844c6ddea4a75017be44912180e3f6dc90abf1f1 | 2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26 | 5,590 | 96 / 3,707 | 5,357 | 91 (65 / 26) | 985 / 985 | 18.39% |

ここで broad の prefix は recorded chain 上の disagreement prefix で、strict report の eligible_transition_count は action type/confidence filter 後の prefix である。strict action report は runs/meta-specialist-v4-strict-disagreement-wave6-seed{0,1}/action-9-13-14-threshold-m02.json。seed1 report の screen SHA/transition SHA は表と一致し、teacher policy version は b89ca316191957b26e5afa37c6cd121f61ba43435724aa6b982b3b06b07ff6e。

### 7.1 action filter の比較

| seed | filter | threshold -0.2 | threshold -0.5 | threshold -1.0 |
|---:|---|---:|---:|---:|
| 0 | teacher target only | 851 prefix / 88 games | 106 / 38 | 0 / 0 |
| 0 | student OR teacher target | 867 / 89 | 106 / 38 | 0 / 0 |
| 1 | teacher target only | 985 / 91 | 110 / 48 | 0 / 0 |
| 1 | student OR teacher target | 990 / 91 | 110 / 48 | 0 / 0 |

-0.2 の対称化増分は seed0 +16、seed1 +5。{9,13,14} は EVOLVE/ATTACK/END の teacher target であり、単に legal domain に存在した action type ではない。

### 7.2 action confusion と teacher tie

preflight の disagreement confusion（effective loss mass = prefix count）は次の通り。

| seed | false negative（teacher 対象 / student 対象外） | false positive（student 対象 / teacher 対象外） | within-type | unrelated |
|---:|---:|---:|---:|---:|
| 0 | 1,424 | 32 | 390 | 1,230 |
| 1 | 1,737 | 28 | 482 | 1,460 |

teacher target-only は false negative と within-type を主に拾い、false positive はほぼ除外する。teacher top-1 margin は strict threshold -0.2/-0.5 で seed0/1 とも 0.0。これは UniformLegalPolicyFactory が legal choices を tie とし lower index を選んだ結果であり、teacher/student agreement は teacher correctness の証明ではない。teacher target probability と entropy も低信頼であるため、strict arm の効果を「良い oracle の模倣」と解釈しない。

### 7.3 fixed-six の位置付け

fixed-six は kiyotah_lucario, sue124_alakazam, skarin_dragapult, ozawa_crustle_v2, nihei_megalopunny, yaroslav_crustleaware_lucario。既存評価 JSON は runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/evals/ の baseline-seed{0,1}-96.json と dagger-seed{0,1}-96.json。Wave2 の弱点観測を見て選んだ development pool なので、ここでの非悪化を未知 opponent への汎化証拠と呼ばない。opponent×seat joint ledger は strict-paired JSON に保存されておらず、未取得として扱う。

### 7.4 shadow-A

shadow-A は fixed-six と重ならない 6 opponent を freeze した development-external diagnostic cohort。

- manifest: runs/meta-specialist-v4-shadow-pool-20260812/shadow_pool_manifest.json
- manifest SHA: 6ddaf3588bb22869a808fd75f84721b640dde6d75f665a11beb10f578af72107
- source pool manifest: opponents/pool_manifest.json、SHA e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca
- IDs: aristophanivan_multiply, kiyotah_abomasnow, masamikobayashi_garchomp, naoto714_kangaskhan, naoto714_slowking, yaminh_agent
- evaluation outputs: runs/meta-specialist-v4-shadow-eval-20260812/{baseline-seed0-48,baseline-seed1-48,strict-paired-seed0-48,strict-paired-seed1-48}.json

同一 subject deck、両 seat、各 opponent×seat 4 局、base seed 10100000、max steps 2000、protocol SHA 0f98f699…で、Wave6 baseline は seed0 30/48、seed1 30/48、合計 60/96 (62.50%)。既存 strict-paired candidate は seed0 29/48、seed1 36/48、合計 65/96 (67.71%)、差 +5.21pt、全 4 JSON comparison_status=valid, fault 0。ただし seed0 -1/48、seed1 +6/48、cell 4 局なので promotion evidence ではない。

### 7.5 shadow-B

shadow-B は新規 strict-disagreement candidate の promotion-untouched 外部診断用に、arm 選択後に固定した。まだ CABT、fault、速度、強度を測っていない。

- manifest: runs/meta-specialist-v4-shadow-pool-20260812-b/shadow_pool_manifest.json
- manifest SHA: 27e43feecad8d66bc80c2f43c23e9276d42398bf3f47eeba2bc5914087c168e0
- role: promotion_untouched_test_candidate
- IDs: biohack44_crustlecounter2, harukiharada_crustle, kiyotah_iono, naoto714_ursaluna, pilkwang_lucario_alakazam, prvsiyan_grimmsnarl
- shadow_a_manifest は上記 shadow-A SHA を参照し、fixed-six / shadow-A / shadow-B で deck/policy SHA の重複なしを freeze 時に検証した。

新規 strict candidate が完走した場合でも、まず fixed-six 192 局 → shadow-B frozen cohort の順で、seed/seat/opponent/fault/action metric を確認する。shadow-B の未測定を勝率ゼロや成功として補ってはいけない。

## 8. GPU 問題の全履歴と現在状態

### 8.1 通常 sandbox の false negative

通常 Codex command は bwrap sandbox 内で /dev が限定される。再現値:

~~~

## 14. Pilot完走後の一次結果（2026-08-12）

### 14.1 学習完走

実行した固定条件は、対応する Wave6 seed0/seed1 screen・transition・init checkpoint、hidden/embedding `128/64`、TBPTT `8`、3 epoch、patience `1`、learning rate `0.0003`、gradient clip `1.0`、DAgger fraction requested `1/3`、strict action types `9,13,14`、mean behavior log-probability `<= -0.2`、device `cuda:0` である。初回のGPU OOM出力とは別の output rootを使った。

report: `runs/meta-specialist-v4-archaludon-dagger-wave5-strict-disagreement-pilot-rerun-20260812`、SHA-256 `09bb90523093de626a2b1913fc693fc519b2d8feebf121756308e3ac8fa1c109`、schema `meta-specialist-v4-dagger-bc-report-v1`、status `RESEARCH_ONLY_COMPLETE`、elapsed 約9,535秒、`promotion_authority=false`。

| seed | best epoch | initial validation NLL | best validation NLL | updates | checkpoint file SHA | tensor SHA |
|---:|---:|---:|---:|---:|---|---|
| 0 | 1 | 0.602076 | 0.551747 | 1,740 | `bf8d7337b4ba5b4bce6bd186d2685e618ef0ae61212fbf54bf06e2de60afc7d3` | `42876048a4a4e8fe6cff8fcd8811c617b941aad928db542c72baad65da81a71d` |
| 1 | 2 | 0.689207 | 0.604715 | 1,731 | `561e9d84b20d3e0db4d32f93daa84bd780edad0710ca6847cbddfc87ff40faf8` | `6426e3bdc79b615d87f53882fd3b1666b0820e42331c0091a53aba73033baab8` |

Strict reportの質量は seed0/1 で、available 96 games、transitions 4,763/5,590、disagreement 2,983/3,592、selected episodes 88/91、eligible/effective loss mass 851/985。selected non-forced massは4,415/5,250、actual DAgger mixtureは0.120879/0.124487。selected episode内でeligible外はhidden context-onlyで、loss denominatorへは入らない。全eligible prefixがfirst prefix中心で、teacherはUniformLegalのtie（top1 margin 0）なので、NLLやagreementの改善を戦略的teacher qualityの証拠と解釈しない。

### 14.2 fresh fixed-six評価

subject deckは `opponents/public_archaludon_cinderace_r7/deck.csv`（SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`）。fixed-six、両seat、各 opponent×seat 8局、base seed `10100000`、max steps `2000`、fault 0、evaluation protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`、evaluator implementation SHA `6298bfe03697609141c19f2520290c602fe4a4e3c2b23f16bb2267f29c56a835` である。

| arm | result | score | seat0 / seat1 | faults | JSON SHA |
|---|---:|---:|---:|---:|---|
| fresh strict seed0 | 48W/48L / 96 | 50.00% | 24/24, 24/24 | 0 | `9459686a36058e449ba73e735724c5d7b9a9698f3d7589abcbc79b4edc622651` |
| fresh strict seed1 | 46W/50L / 96 | 47.92% | 23/25, 23/25 | 0 | `d5a73acb2116bd1e79ae2ef399867fbeb858854a0352dda40729a20c441502a5` |

Wave6 baselineは seed0 43/96、seed1 50/96、合計93/192。fresh strictは合計94/192で差は +1局 / +0.52ポイント。seed0は+5局だがseed1は-4局で、旧 strict-paired の+4.17ptを再現しない。相手別の新規candidate合計は、Kiyotah 18、Nihei 19、Ozawa 14、Skarin 15、Sue 17、Yaroslav 11 wins。baseline合計（19,18,12,9,18,17）と比べると相手別の反転が大きく、一般化の証拠ではない。

CABTにはengine seed setter/APIがなく、game-level ledger/state/action hashもない。したがってこの比較は独立層化（opponent×seat×training seed×repetition）であり、common-random-number paired/McNemar結果ではない。

### 14.3 判定と次の実験

判定は「GPU復旧、loss mask semantics、2-seed学習、fault-free runtimeは成功。strict-disagreementの実戦性能改善は不合格」。longrun、Champion変更、Kaggle提出は行わない。UniformLegal threshold/fraction/epochの細密探索も打ち切る。

次の最短高情報量アームは、現行判断記録で training-local が許可された `tomatomato_archaludon` の現行policy/deck SHAを再検証し、24局の新規 teacher collection → seal → seed0/1 matched BCである。既存のtomatomato 16局probe（全敗・旧policy SHA）、旧BC smoke（teacher ID/policy SHA不一致）、R7 record（permission drift）は再利用しない。R7は `local_eval_only` / `smoke_ok=false` なので所有者の明示許諾・manifest更新なしにtrainingへ使わない。
/usr/lib/wsl/lib/nvidia-smi
Failed to initialize NVML: GPU access blocked by the operating system
ls -l /dev/dxg
No such file or directory
torch 2.11.0+cu128 / CUDA 12.8
torch.cuda.is_available() = False
torch.cuda.device_count() = 0
~~~

この段階の CUDA unavailable は runner/checkpoint/GPU hardware の性能失敗ではない。/dev/dxg が sandbox 外で見えるかを先に確認する。

### 8.2 sandbox 外 read-only smoke は成功

承認済み sandbox 外で同じ workspace を診断し、以下を確認した。

~~~
GPU 0: NVIDIA RTX PRO 5000 Blackwell
driver 595.95
memory.total 48935 MiB
torch 2.11.0+cu128, torch.version.cuda 12.8
torch.cuda.is_available() = True, device_count = 1
device 0 = NVIDIA RTX PRO 5000 Blackwell (12, 0)
torch.randn((2048,2048), device="cuda") + matmul + torch.cuda.synchronize(): success
V4 runner _resolve_device("cuda:0"): success
~~~

従って初期の sandbox 制約については、driver/CUDA wheel/GPU 本体が原因とは言えない。GPU 学習は同一の承認済み sandbox 外境界で起動する必要がある。

### 8.3 初回 strict pilot の CUDA OOM

GPU smoke 成功後、対応する Wave6 seed0/1 checkpoint と screen を使った bounded pilot を起動した。設定は次の通り。

~~~
output: runs/meta-specialist-v4-archaludon-dagger-wave5-strict-disagreement-pilot
lane: archaludon
seeds: 0,1
init: Wave6 seed0 / seed1 対応 checkpoint
selection: runs/meta-specialist-two-lane-readiness/recurrent-selection/archaludon.json
dagger_fraction: 1/3
strict action types: 9,13,14
strict mean behavior log probability: <= -0.2
epochs: 3
device: cuda:0
~~~

sealed selection shard 12 個を private spool へコピーし、hash/identity/screen/checkpoint binding/CPU strict preflight は通過した。しかし scripts/run_meta_specialist_v4_dagger_bc.py:1359 の model .to(device) で torch.AcceleratorError: CUDA error: out of memory。15 分以上の input 展開後に学習へ入れず、bc.json、best/last checkpoint、fresh evaluation は一つも生成していない。これは新しい性能負けではなく、resource/driver context の診断 failure である。

### 8.4 同時 CUDA process とその後の kernel/NVML error

停止時に別 workspace の CUDA process が同じ WSL2 GPU bridge を使っていた。

~~~
PID 3474717
cwd /home/bfe-lab-ono/av-suara
python common/separation/BSS_AI/experiments/avsuara_gc_fastmnmf/enhancer/run_gc_ena_spectral_policy.py \
  --dataset-root ROS2/output/gc_ena_online_60s_v1 --device cuda \
  --grid-levels 20 --steps 2000 --frame-batch 256 --hidden 128 --depth 3 \
  --progress-every 100 --output ROS2/output/gc_ena_spectral_policy_60s_gpu_v1
~~~

別作業の所有物なので勝手に kill していない。pilot 終了時と process 終了後も nvidia-smi は Failed to initialize NVML: N/A、PyTorch は is_available=False, device_count=0, cudaGetDeviceCount(): ... invalid argument。kernel log に次が残った。

~~~
dxgkio_query_adapter_info: Ioctl failed: -22
dxgvmb_send_create_allocation: send_create_allocation failed ffffffb5
dxgkio_create_allocation: Ioctl failed: -75
~~~

したがって単に別 process が終わるのを待つだけでは足りず、WSL2 bridge の再初期化が必要になった。

### 8.5 WSL shutdown と Windows host reboot の矛盾

ユーザー承認後に wsl.exe --shutdown を実行し、WSL を起動し直した。しかし再起動後も dxgvmb_send_open_adapter failed: -22、PyTorch Found no NVIDIA driver on your system が続いた。

Windows 側 C:\Windows\System32\nvidia-smi.exe -L:

~~~
Unable to determine the device handle for gpu 0000:01:00.0:
GPU is lost. Reboot the system to recover this GPU
~~~

PnP は Status: OK, ProblemCode: null と表示されるが、NVIDIA runtime は lost。対象 InstanceId への pnputil /restart-device は Access is denied で変更されなかった。

ユーザーから「再起動した」と報告された一方、現在確認できる Windows evidence は実 host reboot と一致しない。

- Win32_OperatingSystem.LastBootUpTime: **2026-08-10 13:48:41.500 +09:00**。
- 直近の Kernel-General startup event: **2026-08-10 13:48:42**。
- 2026-08-12 の event は Kernel-Power suspend/resume（BootId 220）で、host reboot の新しい起動時刻ではない。

したがって「WSL は再起動したが Windows host reboot は成立していない」、または再起動操作が別の suspend/resume だった、というのが現在の観測に整合する。ユーザー発言を否定するためではなく、CUDA 再開条件を判断するための read-only evidence として扱う。host reboot を Codex が勝手に実行してはいけない（全 Windows/WSL/Codex 作業を終了する）。

### 8.6 GPU復旧の実測と現在の再開状態

当時の状態は nvidia-smi が GPU lost、WSL/PyTorch が CUDA unavailable、Windows PnP 表示だけが正常という不整合だった。ユーザー承認後、保留していたWindows再起動を`shutdown.exe /r /f /t 0`で強制完了し、以下を再測定した。

~~~
Win32_OperatingSystem.LastBootUpTime: 2026-08-12 03:01:36.500 +09:00
EventLog 6006 (停止): 2026-08-12 03:00:59
EventLog 6005 (開始): 2026-08-12 03:01:52
Windows/WSL nvidia-smi: NVIDIA RTX PRO 5000 Blackwell
driver: 595.95; memory.total: 48935 MiB
torch: 2.11.0+cu128; cuda.is_available: True; capability: (12, 0)
2048x2048 CUDA matmul + synchronize: success
V4 SpecialistModelV4(card_vocabulary_size=1267, hidden_dim=128, embedding_dim=64)
parameters: 857474; .to(cuda:0) + GPU tensor op: success
~~~

復旧後に残留`av-suara`/旧pilot CUDAプロセスがないこと、GPU使用量約1.9 GiB・空き約46 GiBを確認した。従って、今回のGPU lostの直接原因は未完了のWindows host rebootであり、実再起動完了後に復旧したと判定する。次の順序を守る。

1. ユーザーが全 Windows/WSL 作業を終了してよい時点で、実際の Windows host reboot を明示承認する。
2. WSL 起動後、同じ実行境界で nvidia-smi -L、torch.cuda.is_available(), device_count, mem_get_info, 2048 行列積 + synchronize を read-only で確認する。
3. V4 model 単独 .to(cuda:0) と小 batch forward を確認する。
4. 同じ固定条件、別 output directory の 2-seed strict pilotを実行し、`runs/meta-specialist-v4-archaludon-dagger-wave5-strict-disagreement-pilot-rerun-20260812`へ完走済みである。seed0/1 best checkpoint、report、strict massを保存した。
5. fresh fixed-six 192局も完了したが、94/192 対 Wave6 baseline 93/192（+0.52pt）でpromotion gate不合格。shadow-B、broad/meta-weighted、longrunへはまだ進まない。
6. nvidia-smiが正常化しない間は再学習しない。次は qualified teacher arm、best-of-many seed 選択禁止、Champion変更、Kaggle submission禁止を継続する。

## 9. 現在のコード変更・テスト・artifact

### 9.1 strict overlay の変更

今回の strict preflight に関係する active code は次の通り。V4 新規ファイルは現在の dirty worktree では untracked と表示されるが、evidence の実行時に import された live code closure である。commit はしていない。

- src/mage_ptcg/meta_specialist/recurrent_dataset_v4.py: RecurrentBCStepV4.supervision_weight と [0,1] validation、stream reader での mask 伝播。
- src/mage_ptcg/meta_specialist/recurrent_bc_v4.py: train/evaluate/positive STOP metrics の mask、objective hash への mask、context-only prefix の扱い。
- src/mage_ptcg/meta_specialist/dagger_v4.py: recorded public prefix 上の strict disagreement、teacher-target action type、behavior confidence、forced STOP、teacher tie-break。
- scripts/run_meta_specialist_v4_dagger_bc.py: strict-disagreement flags、seed-specific input binding/report。
- scripts/analyze_v4_strict_disagreement_preflight.py, scripts/build_v4_strict_disagreement_report.py: seed別 confusion/filter/report の再計算。
- tests/meta_specialist/test_dagger_v4.py, test_recurrent_bc_v4.py, test_recurrent_dataset_v4.py, test_run_meta_specialist_v4_dagger_bc.py: strict mask、episode boundary、hash、privacy/legal contract 回帰。

V4 model/runtime の active additions は neural_model_v4.py, neural_policy_v4.py, representation_v4.py, runtime.py, actor_visible_*, actions.py 等。V-trace/critic/旧 v1/v3 実装が存在しても current Champion/production authority ではない。

### 9.2 実行済みテスト

証拠に記録された実行結果:

~~~
tests/meta_specialist/test_run_meta_specialist_v4_dagger_bc.py
tests/meta_specialist/test_recurrent_bc_v4.py
tests/meta_specialist/test_dagger_v4.py
tests/meta_specialist/test_recurrent_dataset_v4.py
60 passed, 1 skipped

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q \
  tests/meta_specialist/test_dagger_v4.py \
  tests/meta_specialist/test_run_meta_specialist_v4_dagger_bc.py \
  tests/meta_specialist/test_run_meta_specialist_v4_dagger_screen.py \
  tests/meta_specialist/test_measure_v4_checkpoint_strength.py \
  tests/meta_specialist/test_measure_v4_checkpoint_strength_shadow.py
45 passed

py_compile: pass
git diff --check: pass（evidence記録時）
~~~

この pack 更新後の docs validator/diff-check は末尾の「今回の検証」に記録する。上記は全 test suite pass の主張ではなく、記載された対象テストの結果である。GPU runner contract の test pass も実 GPU 学習を保証しない。

### 9.3 主要 artifact / SHA 一覧

| artifact | path | SHA / status |
|---|---|---|
| recurrent selection manifest | runs/meta-specialist-two-lane-readiness/recurrent-selection/archaludon.json | b3044504df1192ce072377f1ddfbeeafdf071a715ef896076b5adb1471eaf0cc |
| subject deck | opponents/public_archaludon_cinderace_r7/deck.csv | 42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e |
| strict preflight | runs/meta-specialist-v4-strict-disagreement-preflight-20260812.json | 3e1120066b8d42c5187ec1d65a23aac74d44b2034783eef2995c2ebb6cc321bf |
| seed0 screen | runs/meta-specialist-v4-archaludon-dagger-wave6-screen-v2/screen.json | 9ad78bf31f41d307916b25d238544ea5060e0df9a8e16b5ca72a8e3977fc00e3 |
| seed1 screen | runs/meta-specialist-v4-archaludon-dagger-wave6-screen-seed1-v2/screen.json | aed7438628ffdcb4a4d0c11a844c6ddea4a75017be44912180e3f6dc90abf1f1 |
| seed0 transitions | .../wave6-screen-v2/screen.transitions.jsonl | 2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce |
| seed1 transitions | .../wave6-screen-seed1-v2/screen.transitions.jsonl | 2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26 |
| strict-paired report | runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/bc.json | status=RESEARCH_ONLY_COMPLETE, promotion false |
| paired seed manifest | runs/.../paired-seed-manifest.json | 47d75eec59c8d058523a3c0b41319bf47edb04856022e9aadbaa5f52f786250b |
| shadow-A manifest | runs/meta-specialist-v4-shadow-pool-20260812/shadow_pool_manifest.json | 6ddaf3588bb22869a808fd75f84721b640dde6d75f665a11beb10f578af72107 |
| shadow-B manifest | runs/meta-specialist-v4-shadow-pool-20260812-b/shadow_pool_manifest.json | 27e43feecad8d66bc80c2f43c23e9276d42398bf3f47eeba2bc5914087c168e0 |
| common opponent pool manifest | opponents/pool_manifest.json | e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca |

strict-paired checkpoint file/tensor SHA は report JSON に保存されている。seed0 file 8f014c776b1f2f9fd3ebcda1701911e3558a16b4b311d716701e5941d9c6ef45 / tensor efd69ee53823f99c8082f70429a40dd276e6dd87a28ac03ce036fbe37019d76b、seed1 file ec08ace5fb25352758a9f950694134ef6544ec69b23c00047101e588e3d06319 / tensor 17682967a16c955ccd009858e036ef69e54d3efcd32bb0de83bebb64aa7c0244。Wave6 init checkpoint は seed0 file 9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de / tensor 36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a、seed1 file 5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6 / tensor 046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a。

### 9.4 status / evidence files

主要な読み取り順は次の通り。

~~~
docs/status/current_status.md
docs/status/handoff.md
docs/status/sol_analysis_context_2026-08-11.md
docs/evidence/v4-performance-history.md
docs/evidence/v4-wave3-postrun-audit-20260812.md
docs/evidence/v4-strict-disagreement-preflight-20260812.md
docs/evidence/v4-strict-disagreement-shadow-evaluation-20260812.md
docs/evidence/v4-gpu-access-recovery-20260812.md
docs/evidence/v4-gpu-pilot-oom-20260812.md
docs/evidence/v4-promotion-gate-20260811.md
runs/meta-specialist-v4-strict-disagreement-preflight-20260812.json
runs/meta-specialist-v4-strict-disagreement-wave6-seed{0,1}/*.json
runs/meta-specialist-v4-shadow-{pool-20260812,pool-20260812-b,eval-20260812}/
runs/meta-specialist-v4-archaludon-dagger-wave4-strict-paired/
~~~

生ログ全文、model weights、カード配布データ、private state、認証情報はこの pack に含めていない。

## 10. 再現・監査コマンド（秘密情報なし）

以下はローカルで証拠を再確認するための入口であり、外部送信・commit・提出をしない。<...> は実際の artifact へ置き換える。

~~~bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle

# dirty worktree と文書構造
git status --short
git diff --check
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/docs/validate_docs.py

# artifact SHA（preflight / manifests / selection）
sha256sum \
  runs/meta-specialist-v4-strict-disagreement-preflight-20260812.json \
  runs/meta-specialist-v4-shadow-pool-20260812/shadow_pool_manifest.json \
  runs/meta-specialist-v4-shadow-pool-20260812-b/shadow_pool_manifest.json \
  runs/meta-specialist-two-lane-readiness/recurrent-selection/archaludon.json

# strict CPU report（既存 screen を読むだけ）
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/analyze_v4_strict_disagreement_preflight.py \
  --screen runs/meta-specialist-v4-archaludon-dagger-wave6-screen-v2/screen.json \
  --transitions runs/meta-specialist-v4-archaludon-dagger-wave6-screen-v2/screen.transitions.jsonl \
  --output /tmp/strict-preflight-seed0.json

# fixed-six strength measurement（既存 checkpoint を使う場合のみ）
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/measure_v4_checkpoint_strength.py \
  --checkpoint <checkpoint.pt> \
  --subject-deck-csv opponents/public_archaludon_cinderace_r7/deck.csv \
  --subject-archetype-id archaludon --games-per-seat 8 \
  --base-seed 10100000 --max-steps 2000 --output <eval.json>

# promotion gate は純粋な JSON 判定。外部提出をしない
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/evaluate_v4_promotion_gate.py \
  --candidate <candidate-seed0.json> <candidate-seed1.json> \
  --baseline <baseline-seed0.json> <baseline-seed1.json> \
  --imitation <candidate-imitation.json> --output <promotion-gate.json>

# GPU復旧を確認した read-only smoke（同じ sandbox 外境界で実行）
nvidia-smi -L
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python - <<'PY'
import torch
print(torch.__version__, torch.version.cuda)
print(torch.cuda.is_available(), torch.cuda.device_count())
if torch.cuda.is_available():
    print(torch.cuda.get_device_name(0), torch.cuda.mem_get_info())
    x = torch.randn((2048, 2048), device='cuda')
    _ = x @ x
    torch.cuda.synchronize()
PY

# Windows 側の reboot 矛盾を read-only で確認（PowerShell）
powershell.exe -NoProfile -Command \
  "Get-CimInstance Win32_OperatingSystem | Select-Object LastBootUpTime"
powershell.exe -NoProfile -Command \
  "Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-General'} -MaxEvents 5 | Select-Object TimeCreated,Id,Message"
C:\Windows\System32\nvidia-smi.exe -L
pnputil /enum-devices /class Display
~~~

GPU復旧条件は上記smokeで満たした。bounded commandは同じinput bindingと新しいoutput rootを使い、`--seeds 0,1 --epochs 3 --dagger-fraction 1/3 --strict-disagreement-targets --strict-disagreement-action-types 9,13,14 --strict-max-mean-behavior-log-probability -0.2 --device cuda:0`を固定して実行中である。既存OOM outputへ上書きしていない。pilot完走後にmatched controlを同budgetで作成する。

## 11. ChatGPT に依頼する分析

この Markdown だけを根拠に、次を分けて分析してほしい。

### 11.1 原因究明

1. 通常 sandbox の /dev/dxg 不可視、sandbox 外 smoke 成功、同時 CUDA process、NVML N/A、kernel dxg ioctl/allocation error、WSL shutdown 後も GPU lost、Windows host reboot evidence の矛盾を、resource boundary / CUDA context contention / Windows driver lost のどこまで確定できるか分類する。
2. nvidia-smi の GPU lost と PnP Status OK/ProblemCode null の不一致をどう読むか。どの read-only evidence が host reboot の成否を最も強く判定するか。
3. OOM をモデル容量の問題と断定してよいか。別 process の VRAM 使用量が NVML 異常で未測定なことを含め、どの最小 smoke が仮説を切り分けるか。

### 11.2 pilot 続行可否

1. host reboot → CUDA smoke → V4 model transfer → small forward → 同一条件 pilot の順序で十分か。
2. 同一条件を保つべきパラメータ（hidden/embedding、batch、epochs、selection、seed、dagger fraction、threshold）と、切り分けのために変更を許す条件を分ける。
3. OOM 再発時にだけ allocator、batch、model capacity を順に調べる fail-closed plan を提案する。GPU が lost のまま再実行する提案はしない。

### 11.3 matched control と停止条件

新規 strict arm は seed0→seed0、seed1→seed1 の input binding を守る。strict overlay と同 budget の matched control（focus-only または mask-off を明示）を用意する。以下を最低限の停止条件として、閾値の妥当性を批判してほしい。

- fault 0、両 seed が対応 Wave6 以上。
- 合計はおおむね +5pt 以上、seat の一方が約 -3pt より悪化しない。
- 6 opponent 中 4 以上が非悪化、worst matchup の drop を制限。
- ATTACK/EVOLVE/END の validation metric を seed 間で同時に確認。seed1 の ATTACK 29.8% のような collapse を見逃さない。
- fixed-six は development evidence、shadow-B は promotion-untouched diagnostic として別々に扱う。4 局/cell shadow-A の +5.21pt を promotion evidence にしない。

### 11.4 次の実験候補

情報利得順に最大 2 件へ圧縮してほしい。候補は、(a) GPU recovery smoke + 同一 bounded strict pilot、(b) strict vs mask-off/focus-only matched control、(c) teacher tie を解消する別 teacher/search target の offline quality audit、(d) fixed-six の seed/opponent/action 分解である。各実験について固定条件、成功/失敗基準、結果後の分岐、既存 artifact との identity 混同防止を示してほしい。

### 11.5 分析上の注意

- 観測事実、設計意図、仮説、未確認事項、推奨実験を分ける。
- seed/seat/opponent/games/base seed/evaluation protocol/checkpoint SHA を省略して勝率を比較しない。
- 初期 teacher-only Wave、DAgger Wave1/2/3、strict-paired、shadow-A を一つの改善曲線へ連結しない。
- teacher target tie を teacher correctness と呼ばない。student agreement は label quality の証明ではない。
- loss mask の context-only prefix と supervised prefix を区別する。selected game 数を gradient mass と取り違えない。
- private information leakage、physical alias multiplicity、forced domain、selection bias、stale progress、engine/evaluator reproducibilityを review から外さない。
- 研究提案は code path、回帰テスト、artifact identity、fail-closed 条件を明記し、大規模 refactor や提出を先に勧めない。

最後に、ChatGPT の回答は仮説・計画であり、Codex が対象コード、JSON、SHA、テスト結果を再照合するまで採用決定ではない。Sol/高性能モデルの回答も権限や証拠の代替ではない。

## 12. 未解決点の一覧

1. 初回OOM時に同時CUDA processが占有した正確なVRAM量はNVML異常で未測定。従って初回OOMがintrinsic model sizeかcontext corruptionかは厳密には未確定。ただしホスト復旧後のGPU空き約46 GiBとV4単独転送は成功した。
2. 新規strict-disagreement checkpoint、fresh imitation metrics、fixed-six 192局は完了した。結果は fresh strict 94/192 対 Wave6 baseline 93/192（+0.52pt）。shadow-B full evaluation、broad/meta-weighted評価、longrunは未実行である。
4. strict teacher は legal uniform tie で top1 margin 0。teacher quality/search teacher の再設計余地が大きい。
5. strict-paired の +4.17pt は seed1 +1 局、opponent/seat 反転を含み、一般化・長時間化の根拠として弱い。
6. fixed-six の opponent×seat joint ledger は未保存。必要なら同一 artifact identity を保った別集計を作る。
7. Wave3 targeted overlay の約60%が non-target、EVOLVE target mass 0。action focus が実際の supervised gradient に反映されるか、mask 後の新 arm で再検証が必要。
8. V4 unit/contract tests は豊富だが、今回の pilot で targeted tests を再実行したとは主張しない。GPU longrun、Kaggle runtime、未知 opponent の汎化をこの pack は主張しない。

## 13. 今回この pack を更新した時点の検証

このファイル自体を追加した後に主担当が実行する検証結果をここへ追記する。未実施の項目を pass と書かない。

~~~
file: docs/status/chatgpt_context_pack_2026-08-12.md
git diff --check: 更新後に実行済み（pass）
python scripts/docs/validate_docs.py: 更新後に実行済み（`Validated 13 canonical documents.`）
targeted pytest: 今回は未再実行。過去 evidence の 60 passed/1 skipped, 45 passed は今回のpassとして扱わない。
pilot report SHA: `09bb90523093de626a2b1913fc693fc519b2d8feebf121756308e3ac8fa1c109`
fresh fixed-six JSON SHA: seed0 `9459686a36058e449ba73e735724c5d7b9a9698f3d7589abcbc79b4edc622651`、seed1 `d5a73acb2116bd1e79ae2ef399867fbeb858854a0352dda40729a20c441502a5`
git status --short: 未コミットの既存dirty差分を保持。commit/push/提出なし。
~~~

## 14. 追補 — qualified teacher collection / BC の実測結果

### 14.1 何を実行したか

strict-disagreement の fresh fixed-six が Wave6 baseline をほぼ上回らず、UniformLegalを本命teacherと扱えないことが確定したため、次の bounded arm として、現行判断記録で `training-local` が明示的に許可された `tomatomato_archaludon` を新規収集した。R7は強い固定六スコアを持つが `local_eval_only` / `smoke_ok=false` のため使っていない。

事前に照合した identity は次の通り。

| artifact | value |
|---|---|
| pool manifest | `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca` |
| teacher policy (`opponents/tomatomato_archaludon/main.py`) | `8908af5caad296820a6ce5a9c8d388f04869eb499b308ac446142d9dcdaced9e` |
| decision ref | `docs/decisions/2026-08-05-archaludon-teacher-derivation.md` / `e64cc3f3e74bf5b96932438b4718af3079f56d1c7da64bc27524d02432e3a6fc` |
| subject deck bytes | `opponents/tomatomato_archaludon/deck.csv` / `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` |

collection commandの固定条件は、24局、base seed 5,400,000、16 opponents、opponent seed 11、workers 8だった。結果は24/24完了、fault 0、records 1,386、outcome 18 win / 6 loss、seat subject-first 16 / subject-second 8、unlabelled 0。manifestは `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-24/teacher_dataset_manifest.json`（SHA `ffb18429302782bd57e58a689d151aad807dcb3139e37ecb2b99130afd3cd408`）。

seal済み index は `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-24/snapshot_index.json`（SHA `23a5613a45d54a1e718abf9cdb9ac81134044bbcd181e66daec49f8402f5c72c`）。examples 1,386、splitは train 894 / development 428 / test 64、dataset snapshot root SHAは `b69c3dc06e4f2903d3f3637137e7644f79cceb1270081f97f08cd1e299e26dab`。BC scriptがindex shardsから計算した snapshot IDは `022e295dec2d1893f76d3ffa9d347f283b2a1182c57c8107b391c9589a89205d`。

### 14.2 BC checkpointの形式と結果

これはV4 recurrent BCではなく、`scripts/run_bc_distillation.py` の `SpecialistPolicyModelV1` foundation initializationである。したがって、V4 checkpoint evaluatorへ拡張子やdescriptorを合わせて接続することはしていない。seed0/1を同一 snapshot・同一 foundation init ID `ed3038f2d21ced4a59d24580f588894818c10bc74737d550993a2d3c65c0c343` から、別 seed、2,000 steps、examples-per-step 64、microbatch 16、torch threads 2、skip 0で実行した。

| seed | elapsed | first loss | last loss | final checkpoint file SHA | V1 fixed-six |
|---:|---:|---:|---:|---|---:|
| 0 | 1,016.5 s | 1.876205 | 0.080851 | `6db6d6ecb777ca0369d4c06d1533a4ed5fbdd92025388fd11fedff12ec43146e` | 29/96 (30.21%) |
| 1 | 1,025.7 s | 1.807806 | 0.099362 | `94c83f89023ddb787c5293bd78495096966e6ba4b2c89cff0e5b33ecdc264fd8` | 29/96 (30.21%) |

checkpoint filenameと実ファイル SHAは一致し、run summary SHAは seed0 `5005e35a5fc6ab27d7713d613cfa7d2c848900ba193889321de7ebc6f4708c25`、seed1 `13adeb4c16c346085123dc4b2d0945ed485a879974172ed957d77025492a6baa`。評価JSON SHAは seed0 `7bb7f0b309bffcbfdbd50bafa27985993298b314f9bae5fdabd08928a07f1abe`、seed1 `bce41adfc7deea3b67d3800317b4f5227f44f74aaa1b2445de385421fefba63a`。評価条件は同一subject deck、固定6、両seat、各 opponent×seat 8局、96局/seed、base seed 10,100,000、max steps 2,000、protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`、fault 0。

両 seed は同じ29/96だったが、既存 Wave6 V4 baseline 93/192（約48.44%）を下回った。従って、BC lossが下がったこと、2 seedで完走したこと、teacher collectionがfault 0だったことは、実戦性能向上を意味しない。直接V1-BC armは不合格で、longrun・Champion変更・提出へは進まない。

### 14.3 ChatGPTで評価するときの重要な問い

1. `SpecialistModelV1` foundation BCとV4 recurrent policyの間に、既存の正規変換／fine-tune pathがあるか。無いなら、V1 checkpointをV4候補と比較するのは設計上不適切である。
2. qualified teacherの actor-visible record をV4 recurrent datasetへ変換する場合、semantic ActionKey、physical alias、episode continuity、public-only境界、teacher permission、split identityをどのartifactで固定するか。
3. V4へ強teacherを接続できない場合、Rule v0を直接teacherの代替とせず、legal candidate上の固定 prior として `alpha=0` / `alpha=1` の residual/hybridを同一checkpoint・seed・評価で比較する最短設計は何か。
4. 29/96という低い結果が、teacher quality、V1 model topology、直接BCの目的、runtime mismatchのどれに起因するかを、追加の長時間学習なしに分離する最小 offline/short-screenを提案すること。

## 15. 更新後の検証メモ

この追補後に、主担当は次を再実行して結果を置き換える。

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/docs/validate_docs.py
git diff --check -- docs/evidence/performance-first-audit-20260812.md docs/status/current_status.md docs/status/handoff.md docs/status/chatgpt_context_pack_2026-08-12.md
```

targeted pytestは未実行。collection/seal/BC/evalの一次artifactは上記pathとSHAで固定されている。commit、push、Kaggle提出、Champion変更はしていない。

## 16. 追補 — qualified teacher を V4 recurrentへ接続した throwaway prototype

### 16.1 目的と実装範囲

V1 foundation BC（`SpecialistPolicyModelV1`）をV4候補へ直接使うと fixed-six 29/96まで崩れたため、同じ qualified teacher snapshotをV4 topologyへ正しく接続できるかを、productionコードを変更せず in-memory prototypeで検証した。対象は現行判断記録が `training-local` を明示的に許可する `tomatomato_archaludon` の新規 snapshotだけである。R7（`public_archaludon_cinderace_r7`）は `local_eval_only` / `smoke_ok=false` のため使っていない。

raw collection record と sealed `snapshot-0000.json` を `record_id` / content hashで照合し、`RecurrentRecordAuthorityRowV3` と `_project_record_steps_v4` を使って episode単位に並べた。結果は次の通り。

| split | records | V4 steps | episodes | episode length (min/median/max) | errors |
|---|---:|---:|---:|---:|---:|
| train | 894 | 1,037 | 15 | 18 / 62 / 87 | 0 |
| development | 428 | 498 | 8 | 20 / 62 / 77 | 0 |
| test | 64 | 学習へ投入せず | — | — | 0 |

split混在episodeは0。teacher targetは actor-visible state、semantic/physical action情報、public-only recordに限定した。なお、これは再現用scriptやproduction converterではなく、同じコード契約を使った一時的な実験である。今後実装へ昇格する場合は、ActionKey alias、physical candidate mapping、episode continuity、permission manifest、split identity、loss maskを回帰テストで固定する必要がある。

### 16.2 V4短期学習条件とNLL

Wave6 V4 seed0/seed1 checkpointから対応seedを維持して初期化した。V4 configは card vocab 1267、hidden 128、embedding 64、state scalar 41、`epochs=2`、`lr=1e-4`、`tbptt=8`、`quality_weight=1`、`supervision_weight=1`、`burn_in=1`、GPU `cuda:0`、各30 optimizer updates、research-only uniform weightである。

| seed | 初期 validation NLL | best validation NLL | delta | best epoch | checkpoint path | file SHA | tensor SHA |
|---:|---:|---:|---:|---:|---|---|---|
| 0 | 0.5079805662 | 0.4775825091 | -0.0303980571 | 1 | `runs/meta-specialist-v4-qualified-tomatomato-bc-prototype-20260812/seed-0/best-recurrent-bc-v4.pt` | `aa2b99f646e96e0157a41e9a73747901c76dbb7823c657d4bb9bced2fdb3523e` | `32be3ebf24932ca1b2ba188b7e3143aaaa0a6e96b73c505112fb20b085d404e6` |
| 1 | 0.5275904815 | 0.5055810650 | -0.0220094166 | 1 | `runs/meta-specialist-v4-qualified-tomatomato-bc-prototype-20260812/seed-1/best-recurrent-bc-v4.pt` | `b49c716b7833084547c42fdce0623d18b4ec9194ac3d100aeec0e0378057253b` | `57ecee61e9a3c14d44f665d09246866f1dc13c42e896ce1eb3395dd061c29c78` |

NLLは両seedで改善したが、これはCABT実戦性能を意味しない。固定条件は subject deck `opponents/tomatomato_archaludon/deck.csv`（SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`）、fixed-six 6 opponents、両seat、base seed `10100000`、max steps `2000`、protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`、fault 0。CABT engineにseed setterとgame-level ledgerがないため、同じbase seedでもpaired evaluationではない。

### 16.3 fixed-six screenとconfirm

候補screen JSONのSHAは seed0 `3a076e3c4a5c363a66b1e569bdef91b0e6107881e8fbd30d4b6d05586fc0ad0e`、seed1 `457ef56b422005b182b8a1ab4f493e5b33371aeaec99c5e63a1573bbc98097bf`。Wave6 24局対照は seed0 `e446f5155455f574b3fb78d96ae4c3297a6367b7d34b97313f4b2e202c4c0cb2`、seed1 `11e96260c2c8613a3797a4adaef23f4af497c690ab280010d73d21a5c5296390`。screenはWave6各11/24、候補各12/24だった。

事前のsuccessive-halving条件（両seedが基準以上なら96局confirm）により、各96局へ拡大した。

| arm | seed0 | seed1 | 合計 | seat / faults |
|---|---:|---:|---:|---|
| Wave6 baseline | 43/96 (44.79%) | 50/96 (52.08%) | 93/192 (48.44%) | 既存同条件 / 0 |
| qualified-teacher V4 prototype | 49/96 (51.04%) | 57/96 (59.38%) | **106/192 (55.21%)** | cand seed0 21/48,28/48; seed1 27/48,30/48 / 0 |
| 差 | +6.25pt | +7.29pt | **+13 wins, +6.77pt** | — |

candidate confirmation JSON SHAは seed0 `dfb20cc60465c341b2dd1e05f841c8148e39c17c313dd8b2b042ea693e47a5d8`、seed1 `da65ed930eca3a4b36b17e53fbd1f0cd81574f687f66ed1e03ca132774aaae64`。fixed-sixは開発poolであり、これだけで長時間化やChampion変更へ進めない。

### 16.4 shadow-B結果と不合格判定

shadow-Bは arm選択後に凍結したpromotion-untouched cohortで、manifest `runs/meta-specialist-v4-shadow-pool-20260812-b/shadow_pool_manifest.json`、SHA `27e43feecad8d66bc80c2f43c23e9276d42398bf3f47eeba2bc5914087c168e0`。候補は `biohack44_crustlecounter2`、`harukiharada_crustle`、`kiyotah_iono`、`naoto714_ursaluna`、`pilkwang_lucario_alakazam`、`prvsiyan_grimmsnarl`。fixed-six / shadow-Aとのcanonical deck/policy SHA重複はfreeze時に排除済みだが、強度は未測定だった。

runnerは当初manifest v1だけを受理し、実際のfreeze成果物v2を即時拒否する契約不一致があった。v1/v2の双方をハッシュ・asset・pool identity検証付きで受け付ける最小修正を入れ、v2受理回帰テストを追加した。専用TMPDIRで `tests/meta_specialist/test_measure_v4_checkpoint_strength_shadow.py` は `3 passed`。通常pytestの初回失敗はpytest capture用一時ファイル消失（`FileNotFoundError`）であり、テスト内容の失敗ではない。

shadow-Bは各opponent×seat 4局、48局/seedで、候補とWave6を同じ subject deck/protocolで評価した。

| arm | seed0 | seed1 | 合計 | faults |
|---|---:|---:|---:|---:|
| Wave6 baseline | 29/48 (60.42%) | 27/48 (56.25%) | 56/96 (58.33%) | 0 |
| qualified-teacher V4 prototype | 24/48 (50.00%) | 27/48 (56.25%) | **51/96 (53.13%)** | 0 |
| 差 | -10.42pt | 0pt | **-5 wins / -5.21pt** | — |

候補shadow-B JSON SHAは seed0 `7780610ca9f5bcc65ac3dc36a252579801a59bc22411895b9cbc6d71db053f6a`、seed1 `eb10e3ec0ab976786f2629af713f7c85335d9a6e5cbe321c1bc162896ea13a75`。Wave6対照SHAは seed0 `c4a3000b26d76f68c06b76bfa18e13aa30c0a5c79cf4a36586b008a71331140e`、seed1 `2baffe1364e4f9a9c752c1e01eed5ea10cd9c02ba407f5867cda74bfbac1972b`。candidate seed0は `pilkwang_lucario_alakazam` で0/8、Wave6は6/8だった。

このため、V4 qualified-teacher prototypeは **fixed-six改善・shadow-B汎化失敗**。長時間学習、Champion変更、提出は不可。NLLの改善を性能証拠としない。候補の悪化がseed0へ偏っているため、現時点で「teacherが悪い」と一因へ断定せず、(a) 24局 teacher collectionの coverage不足、(b) V4変換のsemantic/physical alias、(c) episode/split sampling、(d) opponent interactionのselection noiseを分離する。

## 17. 最新の総合判定とChatGPTへ依頼する分析

### 観測事実

- GPUは復旧済みで、strict pilot、V4 prototype学習、CABT評価は `cuda:0` / fault 0で実行できた。
- UniformLegal strict-disagreement fresh armは固定six 94/192でWave6 93/192に+0.52ptだけ。再現性・longrun gate不合格。
- qualified teacherのV1直接BCは両seed29/96でV4基準以下。不採用。
- qualified teacherをV4へin-memory変換したprototypeはfixed-six 106/192対93/192で+6.77ptだったが、shadow-B 51/96対56/96で-5.21pt。汎化ゲート不合格。
- shadow-B v2 schema受理の最小修正と3件の契約テストはpassした。これは評価可能性の修正であり、性能改善ではない。

### ChatGPT（Sol xhigh/max等）へ投げる質問

1. fixed-sixのみ+6.77pt、shadow-Bで-5.21ptになったV4 prototypeについて、teacher quality、V4変換のActionKey/physical alias、episode continuity、sample selection、opponent interactionのどれを最小追加実験で切り分けるべきか。長時間学習を禁止したまま情報利得最大の順序を示してほしい。
2. 24局・1,386 recordsのqualified teacher collectionで、V4の train 1,037 steps / validation 498 stepsへ変換した設計が、teacher targetのcoverage・重複prefix・semantic action aliasを十分に持つか。必要なoffline contract metric（target type、root/continuation、legal domain、duplicate semantic key、teacher confidence）を定義してほしい。
3. shadow-Bでseed0だけが `pilkwang_lucario_alakazam` 0/8へ崩れた。V4がこの相手に何をしているかを、private informationなし、同じ actor-visible state/action logだけで診断する最小trace設計を提案してほしい。
4. Rule v0をteacherの代替ではなくlegal priorとしてV4へ加える residual/hybridについて、`alpha=0` / `alpha=1`を超えて探索しない固定条件、legal mask、fail-closed、out-of-distribution fallback、seed/seat/opponent gateを設計してほしい。
5. current evidenceから「teacherを変える」「V4 converterをproduction化する」「Rule-neural hybridを先に作る」「public-only search-Qを作る」の優先順位を、実装量・性能期待・再現性・規約境界・GPU時間で比較してほしい。

### ChatGPT回答を採用する際の禁止事項

- fixed-six 106/192を汎用勝率と呼ばない。shadow-B 51/96の失敗を隠さない。
- 24局screen、4局/cell shadow、同じbase seedをpaired evidenceとして扱わない。
- V1 BC loss低下やV4 validation NLL低下をCABT性能改善へ置換しない。
- R7 `local_eval_only` / `smoke_ok=false`をtraining permissionと解釈しない。
- Rule v0の固定six 12/96を強teacherの上限としない。
- 新しい候補を長時間学習、Champion変更、Kaggle提出へ自動昇格しない。

ChatGPTの回答は仮説・計画として取り込み、対象コード、JSON、SHA、テスト結果と突合してから次の実装へ進む。

## 18. 追補後の検証メモ

この追補後に主担当が実行した検証:

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest -q -s tests/meta_specialist/test_measure_v4_checkpoint_strength_shadow.py
=> 3 passed in 0.09s（専用TMPDIR）

shadow-B 4評価
=> candidate/base 各 seed 48局、全て exit 0、fault 0、comparison_status=valid
```

追補後の docs validator は `Validated 13 canonical documents.`、対象6ファイルの `git diff --check` はpass、shadow runnerの `py_compile` もpassだった。commit、push、Kaggle提出、Champion変更はしていない。

## 19. 追補 — Rule v0 main-action residual alpha=1 の診断結果

### 19.1 仮説と固定条件

qualified-teacher V4 prototypeはfixed-sixで+6.77ptだったが、shadow-Bで-5.21ptとなった。そこで、V4を長時間再学習する前に、Rule v0をteacherとして再収集せず、合法なsemantic candidate domain上の固定priorとしてだけ加える最小診断を行った。

V4 `SpecialistNeuralDecisionSessionV4.logits` のbase neural logitsを保持し、`selection_type=0`（main selection）の候補だけへ次の正規化Rule v0 priorを加えた。

| option type | action | Rule v0 priority | prototype prior |
|---:|---|---:|---:|
| 9 | EVOLVE | 600 | 0.6 |
| 8 | ATTACH | 500 | 0.5 |
| 7 | PLAY | 400 | 0.4 |
| 10 | ABILITY | 300 | 0.3 |
| 13 | ATTACK | 200 | 0.2 |
| 14 | END | -1000 | -1.0 |

target selectionではRule v0のdamage/hp scoreを使わなかった。理由は、V4 `SpecialistStepInputV1`へ公開されるsemantic candidateがoption typeとendpoint/Pokemon snapshotを保持する一方、元Rule v0の任意option `damage` scalarを同一形式で完全には保持していないためである。欠落した情報を推測して補完せず、main actionだけのside adapterに限定した。

alpha=1を事前登録し、勝率を見てalphaを変えなかった。candidate checkpoint、subject deck、shadow-B manifest、base seed `10100000`、max steps `2000`、4 games/opponent/seat、protocol/evaluator identity、fault denominatorは直前のalpha=0 shadow-Bと同一。変更は実行プロセス内のmonkey-patchだけで、Rule v0本体、V4 source、checkpoint bytes、agent identity、Champion、提出packageは変更していない。

### 19.2 結果

| arm | seed0 | seed1 | 合計 | fault |
|---|---:|---:|---:|---:|
| alpha=0（直前候補） | 24/48 (50.00%) | 27/48 (56.25%) | 51/96 (53.13%) | 0 |
| alpha=1 | 25/48 (52.08%) | 18/48 (37.50%) | **43/96 (44.79%)** | 0 |
| alpha=1 - alpha=0 | +1勝 / +2.08pt | -9勝 / -18.75pt | **-8勝 / -8.33pt** | — |

alpha=1 JSON SHAは seed0 `2258ddc1147c6dc1cb674761d4819a1df7ede7a6ad1ab683c1f9bb6990300ce0`、seed1 `ecf8934e2cd1614a3dbb88a94efb49f2bb18ecb9efea099b8a3ead0fc8a5b485`。seed0のper-opponentは `biohack44 6/8, harukiharada 7/8, kiyotah_iono 2/8, naoto714_ursaluna 8/8, pilkwang 0/8, prvsiyan 2/8`。seed1は `biohack44 5/8, harukiharada 4/8, kiyotah_iono 1/8, naoto714_ursaluna 6/8, pilkwang 1/8, prvsiyan 1/8`。seed1は両seat 9/24で、特定相手だけでなくseat横断の全体崩れになった。

### 19.3 判定

- 単純なRule v0 action-type priorは、seed間で安定せず不採用。
- alphaの後追いsweepは行わない。今回のalpha=1は「Rule priorが効けば性能が上がる」ことも証明していない。
- Rule v0を使う次の設計は、semantic/physical alias、candidate domain、confidence、OOD、seat/opponent gateを明示する別契約にする。
- V4 teacher contractのproduction化を先に行う場合も、shadow-Bで同じseed1崩壊を早期検出するscreenを必須にする。
- longrun、Champion変更、Kaggle提出は引き続き不可。

### 19.4 ChatGPTへ追加で依頼する論点

1. alpha=1のseed1崩壊を、prior magnitudeの問題、V4 seed-specific logit calibration、main/continuation semantic mismatch、teacher snapshot coverageのどれから疑うべきか。
2. target damage/hpをactor-visible公開境界へ追加することが合法・安全・有益か。追加する場合のfeature schema hashとprivate information boundaryをどう更新するか。
3. 単純priorを捨て、`rule proposal`を候補生成へ使わず、neural confidenceが低いmain decisionだけへfail-closed overrideする最小比較設計を示してほしい。

## 20. 追加のoffline被覆監査（2026-08-12）

24局のqualified `tomatomato_archaludon` snapshotをraw recordと突合した結果、raw 1,386 records / 24 episodes、train/development/test = 894/428/64 records、episode = 15/8/1、episode split混在0だった。V4 projectionはtrain 1,037 steps、development 498 steps、test 74 loss rows（学習未投入）である。

| partition | records | loss rows | quality-weighted rows | capped rows |
|---|---:|---:|---:|---:|
| train | 894 | 1,037 | 1,031.5 | 11 |
| development | 428 | 498 | 496.0 | 4 |
| test | 64 | 74 | 73.5 | 1 |

snapshotのubiquitous near-duplicate groupは1つ、16 recordsでweight 0.5、残り1,370 recordsはweight 1.0。near-duplicate groupは三分割へまたがるため、重みで影響を半減しているが、同一近似stateの存在自体は消えていない。

raw selection typeは `0=715, 1=595, 9=70, 8=6`。positive target operationは `CARD 802, PLAY 374, ATTACK 150, ATTACH 107, EVOLVE 47, YES 44, NO 26, END 19, RETREAT 18, NUMBER 6`。prefix長は0が1,386、1が162、2が34、3が9、4が8、5が5、6が3、7が2で、V4教師信号はfirst prefixに強く偏る。

raw `teacher.status` は全件availableだが、外部teacherはpolicy distributionを公開しないため `behavior.status` は全件unavailable。さらに16 recordsはteacher mass selectionが空で、V4 lossではSTOP targetへ正規化された。これは「teacherがSTOPを選んだ」ことと「selectionが記録できずSTOPへ写像された」ことを区別できないため、強teacherの次回collectionでは empty selection を明示的に unavailable/context-only とするか、STOP hard targetとして採用するかを固定すべきである。

physical legal domainは2〜28候補。semantic alias重複を含むrecordは655/1,386、selected action側で複数physical aliasが同一semantic actionへ写るrecordは90件。model_input_idは1,371 groups、異なるtargetのconflictは0、同一inputの反復最大16件だった。したがってshadow-B失敗をteacher qualityだけで断定せず、empty selection、RETREAT type、alias canonicalization、24 episode被覆を分離対象とする。

同じpermission済みteacher・subject deckで96局の新規collectionを別rootへ開始済みである（進行中、結果未確定）: `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96/`。

## 21. 提出deckと評価subject deckのidentity注意

現在のroot `deck.csv` は raw SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、deck identity `deck-0fd28b79fc39ffc55f77`、内部 variant `DV-000007` のMega Lucario/Hariyama系60枚である。この期間のArchaludon V4学習・fixed-six・shadow-A/Bは、`opponents/public_archaludon_cinderace_r7/deck.csv` または `opponents/tomatomato_archaludon/deck.csv`（実験記録上のsubject deck SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`）を使用した。従って、今回のArchaludon candidate勝率をroot `deck.csv`の提出性能へ直接転記してはいけない。

root deckは既存dirty差分であり今回変更していない。Archaludonを提出候補へ進めるには、提出deckとsubject/evaluation deckのidentityを一致させるか、root deckを対象に別laneでteacher収集・学習・評価をやり直す必要がある。

## 22. 96局 qualified-teacher snapshot を使った再試行（2026-08-12）

### 22.1 目的とデータ identity

24局 snapshot の被覆不足を切り分けるため、現行 policy SHAと `training-local` permission が確認できる `tomatomato_archaludon` を、古い artifactを再利用せず96局新規収集した。collection rootは `runs/meta-specialist-teacher-records/archaludon-teacher-tomatomato-96/`。96/96 games、fault 0、records 5,146、outcome 60 win / 36 loss、seat 48/48だった。

| artifact | 値 |
|---|---|
| manifest SHA | `b5a5bd30d0e0807c90ea65307e9665c01921842bfedc9abd4557ea02775b53ff` |
| snapshot index SHA | `b5cc75c82ee321cb7841b99f80d49fd6759e56d060af435200239a45b36bc72f` |
| dataset snapshot root SHA | `38a361ec571e2d8ba9546db333fd48f33ffb72d7d8526ba304f4be80235c559a` |
| snapshot ID | `6eeb7b730fb8a064ed14801570c62f279927710f45b971fbc343da7f3b569ff` |
| split | train/development/test = 3,351/966/829 records |
| episode split | 63/18/15 episodes、混在0 |
| duplicate cap | 48 records |
| subject deck SHA | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` |

研究用 `scripts/run_v4_qualified_teacher_snapshot_bc.py` はsnapshotのtrain/development/test境界を保持し、test 829 recordsを学習・検証へ投入しない。V4 projectionはtrain/development 3,860/1,108 steps。今回の短期trainerは `uniform_research_1.0; sealed_cap_reported_only` で、sealed capの統計は記録するがlossへcap重みを混在させない。report SHA `57d96ded9d07fa9a70b22a0f3c8319c1d0f9a34f9c067f1194625b0a3a34cc04`、objective SHA `f9349d0deffdb077580f82996049287e56429a99645aa6e551daab924f4d6f53`、trainer SHA `d543b9e1c60bc91c23aaed50c107c6eadcb1cc49e7b1f23e7a0c69d82c649845`。

### 22.2 V4 short training

Wave6 V4 seed0/seed1から、epochs=1、patience=0、lr=1e-4、TBPTT=8、burn-in=1、cuda:0、各63 optimizer updatesで実行した。

| seed | init validation NLL | best validation NLL | delta | checkpoint file SHA | tensor SHA | elapsed |
|---:|---:|---:|---:|---|---|---:|
| 0 | 0.574510 | 0.491043 | -0.083467 | `6067a9fe8ed9ab9289c48b782b88520c64e94044921ae641d2bff6596569d789` | `f8a4818b609031504ad65af3f759872400182d3cd8fe8f4749e938bba1d56754` | 127.1s |
| 1 | 0.587545 | 0.521108 | -0.066437 | `f26cc2c20898176d5b318328b3d384176bdaee4eaa226c399585a8dd48dc4459` | `28a4937ede8b57269d4fb3277bf8c7b3a19bf33bf60d305963fdefd6ca0fc281` | 128.0s |

### 22.3 fixed-six screen

同一subject deck、fixed-six、両seat、base seed `10100000`、max steps `2000`、protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`、fixed evaluator SHA `6298bfe03697609141c19f2520290c602fe4a4e3c2b23f16bb2267f29c56a835`、fault 0で各24局を実行した。Wave6 baselineは各11/24、96局 short armはseed0/seed1とも17/24。screenでは両seedが+6勝だが、screenだけをpromotion evidenceとしない。

candidate screen JSON SHAはseed0 `af0637c5ebd25f82b8be5ff29cf95500be5ff23b332d9f629cb5ab5a3478e915`、seed1 `65ff44a03b539c92c4fb19a826ee6e8002aeb74a0e89daa512846dfdab64addc`。

### 22.4 shadow-B結果と判定

promotion-untouched shadow-B manifest SHA `27e43feecad8d66bc80c2f43c23e9276d42398bf3f47eeba2bc5914087c168e0`、6 opponents × 2 seats × 4 games = 48局/seed、fault 0で実行した。shadow evaluator SHAは `088cadb0017738ffd41da722fe0456696ab02d82755b6280ec4fc67047896e35`。candidate JSON SHAはseed0 `b22ca91cbca3b90b743a9f044924e39597726ed4b26903374a2ab85b2ddff65e`、seed1 `01f69d01b09d6677e9d25d2686a06356b8b582cca770c7c2a31777442fe0d0da`。

| arm | seed0 | seed1 | aggregate score | fault |
|---|---:|---:|---:|---:|
| Wave6 baseline | 29/48 (60.42%) | 27/48 (56.25%) | 56/96 (58.33%) | 0 |
| 96局 qualified-teacher V4 short | 26/48 (54.17%) | 24W/23L/1D (51.04%) | 50.5/96 (52.60%) | 0 |
| difference | -3勝 / -6.25pt | -2.5 score / -5.21pt | -5.5 score / -5.73pt | — |

`pilkwang_lucario_alakazam` はseed0 0/8、seed1 2/8。`kiyotah_iono` と `prvsiyan_grimmsnarl` でもseed1が低い。従って、24局から96局へデータを増やしても、fixed-sixのNLL/screen改善は未使用shadow-Bへ一般化しなかった。**96局 qualified-teacher V4 short armは汎化ゲート不合格**。長時間学習、Champion変更、Kaggle提出は不可。CABT engine seed setterがないためgame-level paired/McNemarではなく独立層化比較である。

### 22.5 現時点の判断とChatGPTへの質問

単に同じteacher collectionを増やして同じV4 BCを延長する方針は採用しない。次の候補は、(a) empty selection/STOP、RETREAT、semantic alias、episode continuityを明示したV4 teacher contract、(b) public-only action-value/search target、(c) shadow-Bの弱相手を対象にしたconfidence/OOD付き residual である。root `deck.csv` とArchaludon subject deckは別identityなので、ここでの勝率を提出性能と混同しない。

ChatGPTには次を求める。

1. 24局→96局でも fixed-six改善が shadow-Bで崩れた原因を、teacher quality、coverage、alias/STOP mapping、V4 capacity、opponent interactionの優先順位で切り分ける。
2. `pilkwang_lucario_alakazam` 0/8・2/8と `kiyotah_iono`/`prvsiyan_grimmsnarl` の低下を、actor-visible traceだけで診断する最小設計を示す。
3. 同じV4 BCの長時間化を避けつつ、performance-firstで次に1本だけ実行するなら、public search/value、weak-matchup residual、teacher contractのどれを選ぶか。成功/失敗ゲートと必要局数も明記する。

## 23. empty selection context-only 診断（2026-08-12）

96局 snapshotで `teacher.mass_rows.selection=[]` の70 recordsをV4でSTOP hard targetへ写像していたため、研究用 `scripts/run_v4_qualified_teacher_snapshot_bc.py --exclude-empty-selection` を追加した。空selectionはGRUのhidden contextとして通すが `supervision_weight=0`、他の条件は同じ。train/developmentで46/14 records（46/14 steps）をlossから除外し、test 829 recordsは未使用。

| item | seed0 | seed1 |
|---|---:|---:|
| init validation NLL | 0.506920 | 0.528839 |
| best validation NLL | 0.485650 | 0.501906 |
| checkpoint file SHA | `ae70404c8df7aadfa9c04aa0bf579f9136437e87e4b5b74827dffa28c89ea7e4` | `61dd24350bd8be87cdaa811d6726191175b499a5886bfaa961e98e7cb146378f` |
| checkpoint tensor SHA | `f22afc60d6c8c17a2d74b9cf4e9af81025240332710d0552cc9c52b3c3e91f48` | `2cc00549857ffcef00ac86481659193f5e09b15c2b37da4e9baffd949ea0467d` |

fixed-six 24局 screen（fault0）はseed0 8/24 (33.33%、seat1 0/12)、seed1 18/24 (75.00%)。Wave6同条件は各11/24。report SHA `8a2dbd10af7d30b5a14be9ab345be26dd1cd811389249a7c375321d3c302950e`、screen JSON SHAはseed0 `0f6e9e7597dfc938348e3959f4bfe1ed4c16a4adef9800313da7fba08298a81c`、seed1 `609fbde00a6e5fb07b9e159a8a0b77a0552ffbb8bf19f3428221533a349671a8`。

一方のseedがseat横断で崩れ、もう一方が改善したため、空selectionのSTOP写像だけを変える仮説は棄却した。shadow-Bへは拡大せず、同じteacher/V4 BCの長時間化・threshold sweepも行わない。この結果は、主要問題が単一のSTOP label noiseではなく、seed-specific calibration/trajectory sensitivity、semantic alias、coverage、objectiveとoutcomeの乖離の組合せである可能性を強める。

## 24. pre-registered action-balanced objective（2026-08-12）

同じ96局 snapshot・Wave6 initで、既存 `ACTION_BALANCED_WEIGHTS_V1` を一度だけ適用した。`EVOLVE=1.5`、`RETREAT/ATTACK=1.25`、`END=1.5`、`STOP=0.75`、通常 action 1.0/0.75の固定mappingで、weight sweepなし。epochs=1、lr=1e-4、TBPTT8、各63 updates、test除外、subject deck/protocol/faultを固定した。

report SHA `7d55180191933484f821cd89a879b7b0e73836d10abf7e1742f30810bce74728`。validation NLLはseed0 0.574510→0.495455、seed1 0.587545→0.524334。checkpoint file/tensor SHAはseed0 `fa540ec6f7ee685b9336ae35974106a0a8b8cd8ffee57c128d605c8395e1213f` / `bd6b007d8b03cc7637f629cbcd42f3dd3c34ee372a6f66b0b4b16ae615090da2`、seed1 `1b2e1b23a023574ec58a6c7f11f8dfc3e7a1c33d892036b343fb3c60a7ad54d9` / `b7b5401491190dc8feb433e61244849e3bee73b8cc3609cfad2cda51321a438c`。

fixed-six 24局 screen（fault0）はseed0 10/24、seed1 10/24（Wave6各11/24）。両seedともbaseline未満なのでshadow-Bへ進めない。NLLを下げるだけのmacro-action weightingも実戦改善へ転化しなかったため、現行V4 qualified-teacher BCのSTOP/weight/epoch局所探索はここで打ち切る。

## 25. qualified teacher `lucifer19_battlecore` の多様性 arm（2026-08-12）

### 25.1 収集・snapshot identity

`tomatomato_archaludon` とは別系統の、現在の判断記録で `training-local` が許可されている `lucifer19_battlecore` を一度だけ収集した。旧artifactは再利用していない。collection rootは `runs/meta-specialist-teacher-records/archaludon-teacher-lucifer19-48/` で、48/48 games、fault 0、records 2,790、outcome 40W/8L、seat 32/16だった。

| 項目 | 値 |
|---|---|
| teacher policy SHA | `c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c` |
| collection manifest SHA | `1570bc1e2664fc6f60d126a6e0517cca1a2bca066976803ff954e6a6dfbe6424` |
| snapshot index SHA | `fca5b1d7c559d5cd6925dca4bd60c5b8e3a2ac80c949fafd6ed0cacc59bcbfd3` |
| snapshot shard SHA | `e63c8b5db91fd7cf8a16c3f013a11053f82d54d00cb8b02f43a4e2a4084f937e` |
| snapshot ID | `cf83f38937915205597818cad89efbf48ff1f6ef9e5477bb79621a438357ced9` |
| dataset snapshot root SHA | `5064542ae045054ee9864bc67fd11f62cc8bc3a16d019190743fca00d4bb45b2` |
| split | train/development/test = 1,928/436/426 records |
| subject deck | `opponents/lucifer19_battlecore/deck.csv`, raw SHA `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6` |

教師policyの対戦強度40/48 (83.33%)は、教師labelが正しいことの直接証拠ではない。外部policy distributionは公開されず、actor-visibleなrecordのみを抽出している。また、lucifer subject deckのraw bytesはtomatomato subject deck（`42165967...`）と異なるため、両teacherの勝率や候補を単純合算しない。

### 25.2 V4 short BC と固定六の結果

Wave6 V4 seed0/seed1 checkpointを初期値として、research runnerでepochs=1、lr `1e-4`、TBPTT=8、burn-in=1、cuda:0、各35 optimizer updatesを実行した。test 426 recordsは学習・検証に使っていない。report SHAは `17f4e0c207875522530ff4e32b214cd5872a28eb878e53a2995d9d9b44fb33f7`、objective SHAは `9f159bfd5640a9e63fbc5ecc15e85b1042eec94e1ac515234704d1a59781c6ad`、trainer SHAは `d543b9e1c60bc91c23aaed50c107c6eadcb1cc49e7b1f23e7a0c69d82c649845`。

| seed | initial NLL | best NLL | checkpoint file SHA | tensor SHA |
|---:|---:|---:|---|---|
| 0 | 0.500953 | 0.457705 | `9058fd71fed68f9c0eaec2ed4a64fae16b0ece201279696900ee544a0dcaefa6` | `72628bc590241a9f0d87e4082930a5b47cbe778bc3d6761597c2f99c693988a5` |
| 1 | 0.519161 | 0.480082 | `b57e76cf29199d4a9f058273002dd4deafc8535abccccffbc5fef94bcbcb25a0` | `52b68b12c203e30d4376724151a41244e2d27d3149ba5ee9ffe34ff63f547308` |

同一lucifer subject deck、fixed-six 6 opponents、両seat、2 games/seat、base seed `10100000`、max steps `2000`、fault 0で各24局を評価した。JSON SHAは candidate seed0 `be784b5c349b5ef23f1be4bbbabc77939a39a09f5fa1c39c2d7323c49de02e69`、candidate seed1 `474c2f5d5dc5bdb74a9b57beecf1c952375713f3ae50dbe203d88fd2b3433f6d`、Wave6 seed0 `060eb79a46630d8bb4da6748661701166a286bfeefac90570964e59d006db9ff`、Wave6 seed1 `a5339eaeb1b0f72423ff0c05ce656ce40b738d94e67da64e84bd977f1d3a5b92`。

| arm | seed0 | seed1 | aggregate | fault |
|---|---:|---:|---:|---:|
| Wave6 baseline | 15/24 (62.50%) | 10/24 (41.67%) | 25/48 (52.08%) | 0 |
| lucifer19 V4 short | 14/24 (58.33%) | 13/24 (54.17%) | 27/48 (56.25%) | 0 |
| candidate − baseline | -1勝 / -4.17pt | +3勝 / +12.50pt | +2勝 / +4.17pt | — |

合計は+2勝だが、seed0はbaseline未満、seed1の+3勝が合計を作る。従って、teacher系統を変えたV4 short BCでもseed反転は残り、再現可能な昇格根拠にはならない。shadow-Bへは進めず、longrun、Champion変更、提出は行わない。

### 25.3 この結果からの判断

lucifer teacherの40/48という強度シグナルは、teacherのlabel qualityやV4投影の妥当性と分離して扱う。同じV4 BCでteacher差し替え、epoch、STOP、macro-action weightingを追加sweepする期待値は低い。次に一つだけ性能実験を選ぶ場合は、public-only action-value/search targetまたはshadow-B弱相手向けのconfidence/OOD residualを、固定条件・2 seed・fixed-sixからshadow-Bへ進むbounded比較として事前登録する。teacher contract（empty selection、RETREAT、semantic alias、episode continuity）の修正は、その比較に必要な最小契約テストとして並行して閉じる。

ChatGPTへ追加で問いたい点は次の通り。

1. lucifer teacherの40/48 strengthとV4候補のseed反転（14/24 vs 13/24、baseline 15/24 vs 10/24）を、teacher quality、subject deck差、coverage、semantic projection、optimizer calibrationのどの順で切り分けるべきか。
2. `pilkwang_lucario_alakazam`等のshadow-B弱相手に、actor-visible traceのprivacy boundaryを守ったまま、最初のtrajectory divergenceとsemantic alias/RETREAT/STOPを結び付ける最小診断は何か。
3. 同じBC系列を止めて public-only value/search と weak-matchup residual のどちらを先に1本だけ実行すべきか。必要な成功ゲート・局数・失敗時の打切り条件を明示してほしい。

## 26. 現時点の総括（ChatGPTへ渡す際の要約）

GPUはWindowsホスト再起動後に復旧済みで、RTX PRO 5000 Blackwell、PyTorch `2.11.0+cu128`、`cuda:0`、V4 model transfer/matmulは正常。strict-disagreement、tomatomato 24/96局、empty-selection context-only、action-balanced、lucifer19 48局の各bounded armを実行したが、NLL低下やfixed-six改善がshadow-B・seed横断の実戦改善へ安定して転化したarmはない。特にqualified-teacher V4 96局はfixed-six両seed17/24からshadow-B aggregate 50.5/96（Wave6 56/96）へ崩れ、lucifer19 48局もfixed-six aggregateだけ+2勝でseed0がbaseline未満だった。

従って現状ラベルは「実装/GPUは回復・進捗、性能は複数候補で停滞、再現性と汎化が主課題」。longrun、Champion変更、Kaggle提出はまだ不可。root `deck.csv`（Mega Lucario/Hariyama系、SHA `2a541d7...`）とArchaludon subject deck（tomatomato `42165967...`、lucifer `fbe6ab...`）も別identityであり、Archaludon結果をroot提出性能へ転記しない。

## 27. lucifer subject deck の Pilkwang 公開trace診断（2026-08-12）

弱相手である `pilkwang_lucario_alakazam` の原因切り分け用に、lucifer subject deck上でcandidate seed0/1とWave6 seed0/1を各4 games/seat、計8局/armで実行した。CABT engine seed setterがないためpaired evidenceではなく、promotion authorityもない。shadow-B manifest SHA `27e43feecad8d66bc80c2f43c23e9276d42398bf3f47eeba2bc5914087c168e0`、subject deck SHA `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`、base seed `10100000`、max steps `2000`、fault0。

| arm | seat0 | seat1 | 合計 | trace rows / redacted |
|---|---:|---:|---:|---:|
| lucifer candidate seed0 | 1W/3L | 1W/3L | 2W/6L | 409 / 123 |
| lucifer candidate seed1 | 0W/4L | 1W/3L | 1W/7L | 448 / 125 |
| Wave6 seed0 | 1W/3L | 3W/1L | 4W/4L | 361 / 94 |
| Wave6 seed1 | 2W/2L | 2W/2L | 4W/4L | 454 / 108 |

physical/private aliasを出さない公開projectionのため、action typeは全armで空。semantic selection type/context/count/log-probabilityのみを比較した。seat別 rows / 平均 complete-action log probabilityは、candidate seed0 `217/-0.2973, 192/-0.3412`、candidate seed1 `215/-0.3135, 233/-0.3463`、Wave6 seed0 `219/-0.3971, 142/-0.3877`、Wave6 seed1 `231/-0.3374, 223/-0.3260`（各組はseat0, seat1）だった。

候補はWave6と異なるselection trajectoryを取り、特にcandidate seed0はseat1のrowsが増え、Wave6 seed0はseat1のrowsが少ない。しかしredacted rowsが約26〜34%であるため、これだけでRETREAT・alias・STOPの誤りと断定できない。現時点の有力仮説は、少数のsemantic decision差がGRU後続trajectory・ゲーム長・seat calibrationへ増幅されること、そして同じbase seedでもCABT engine乱数が非pairedで局面が一致しないことである。

artifactは `runs/meta-specialist-v4-shadow-traces-20260812-lucifer/` に保存した。JSON SHAは candidate seed0 `50f57d5299ba45b284808e48175021e5261871393de29ca0ab9478a6e1c36767`、candidate seed1 `9d6b93701cc8fa2e77e33863cfea01db8b93b0c96656af1d84c66f43a76654a5`、Wave6 seed0 `2b1a8cd3e8e4491744a8b419831449eca81a7c647f42f9ef4792f4b0c484d79b`、Wave6 seed1 `ef64a75fb8ccb74e7660a75b4d4c1cea472abe5af0a1e350ecc0ca18670e22bc`。JSONL SHAは順に `863ead36ed8f6b6a7d9c8b033edadae4a26ac72c70d9f76d237e0f895343f0a9`、`622cf30f0c46b4be0e7c06f82198716c22ba4272dfa690488b2a15c5e561d791`、`6aec5b8a02b4d193c7d610c3d97acde90e539c91cf47f76d8e727e5571be6f68`、`589c2588903553d8d4380f1a8da4ccf67cd25e3627a2cd9ccc29542d9a2e3f43`。

このtraceは、次に実装する場合の最小要件を示す。productionへ物理indexを漏らさず、semantic decisionのfirst divergence、selection type 11/12/14、empty selection、recurrent commitの一致をゲーム単位で記録できるdiagnostic ledgerが必要である。現行traceはgame outcomeとの行単位joinを持たないため、原因確定ではなく設計入力として扱う。

## 28. outcome-weighted V4 BC pilot（2026-08-12、最新）

### 28.1 目的と実装境界

現行V4 recurrent BCで、teacher actionを全て同じ重みで学習する代わりに、sealed episodeの最終outcomeを研究用quality weightとして使うと、弱いteacher labelの影響を抑えられるかを一度だけ検証した。これは性能を最優先するためのbounded research armだが、production runtime・submission agent・teacher artifact・deckは変更していない。

新規実装は `src/mage_ptcg/meta_specialist/outcome_weighted_v4.py` と `scripts/run_v4_qualified_teacher_snapshot_bc.py --outcome-weighted`。契約は `RESEARCH_ONLY_OUTCOME_WEIGHTED_V4`、max-normalized固定重み `win=1.0 / draw=2/3 / loss=1/3`。重みの探索、test partitionの利用、promotion authorityはない。各episode内の全prefixへ同じ重みを付け、欠損・不一致outcomeは拒否する。

### 28.2 入力identity・学習結果

対象teacherは `lucifer19_battlecore`。policy SHA `c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c`、collection manifest SHA `1570bc1e2664fc6f60d126a6e0517cca1a2bca066976803ff954e6a6dfbe6424`、snapshot index SHA `fca5b1d7c559d5cd6925dca4bd60c5b8e3a2ac80c949fafd6ed0cacc59bcbfd3`、snapshot shard SHA `e63c8b5db91fd7cf8a16c3f013a11053f82d54d00cb8b02f43a4e2a4084f937e`、snapshot root SHA `5064542ae045054ee9864bc67fd11f62cc8bc3a16d019190743fca00d4bb45b2`、subject deck SHA `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`。snapshot splitはtrain/development/test=1,928/436/426 records、test 426は除外。train episode outcomeは29W/6L、validationは5W/2L。

report `runs/meta-specialist-v4-qualified-lucifer19-48-outcome-weighted-bc-20260812/report.json` のSHAは `1d2dda7caa93f37b453977494cc22acf7ef740fd87b4367e2662fd2c2771c2c8`、objective SHA `1ca5807dc54410206cc82f19c613ba1387393cdf016cecdedf3e87f3a44f5d34`、trainer SHA `d115bd58767ca6ba45016806d5135e713b5c6e0a4a2a4ce96590b1290f307b91`。Wave6 init、1 epoch、lr=1e-4、TBPTT=8、burn-in=1、cuda:0、各35 updates。NLLはseed0 0.559368→0.501640、seed1 0.571340→0.517306。

### 28.3 fixed-six screenとゲート

同一lucifer subject deck、fixed-six、両seat、2 games/opponent×seat（24局/seed）、base seed 10100000、max steps 2000、fault0で評価した。candidate artifactは次の通り。

| arm | seed0 | seed1 | seat内訳（seat0 / seat1） | JSON SHA |
|---|---:|---:|---|---|
| outcome-weighted candidate | 12/24 (50.00%) | 11/24 (45.83%) | 5/12 / 7/12 ; 7/12 / 4/12 | seed0 `a68fcac3fa46c6e0cea48c85fcb68e6e1fe2532c9cb109a0fd31590605dda45d`; seed1 `bb7a35a075c41121c803f03930c32b1a862473240b81096ab1fa8cef73d89301` |
| Wave6 baseline | 15/24 (62.50%) | 10/24 (41.67%) | 9/12 / 6/12 ; 5/12 / 5/12 | seed0 `060eb79a46630d8bb4da6748661701166a286bfeefac90570964e59d006db9ff`; seed1 `a5339eaeb1b0f72423ff0c05ce656ce40b738d94e67da64e84bd977f1d3a5b92` |

candidate aggregateは23/48、baselineは25/48（-2勝、-4.17pt）。seed0はbaselineより3勝少なく、seed1は1勝多いだけで、seed1 seat1は4/12対5/12へ悪化した。従って「両seed・両seatでbaseline以上、fault0」という事前ゲートに不合格。shadow-B、longrun、Champion変更、Kaggle提出は実行しない。

### 28.4 解釈と次判断

このarmはoffline NLLとoutcome weightingの実装契約が正しく動くことは示したが、CABTのseed横断性能改善を示していない。Lucifer teacherの48局強度（40W/8L）を否定する結果でもなく、teacher label quality、subject deck identity、semantic projection、episode coverage、optimizer calibrationが未分離である。ただし、現行V4 BCにteacher差し替え・単純weight・epoch延長を重ねる期待値は下がった。

### 28.5 実装不備の訂正: 旧 outcome arm と修正版 outcome arm を分離する

28.2〜28.4の旧 outcome-weighted armは、学習実装の検証中に重要な不備が判明したため、性能証拠として再解釈する必要がある。旧reportのtrainer SHAは `d115bd58767ca6ba45016806d5135e713b5c6e0a4a2a4ce96590b1290f307b91` であり、各episodeの全stepに同じ quality weight `q` を掛けながらepisode lossを `Σq` で割っていた。この構造ではepisode内のqは分子・分母で完全に相殺される。最小勾配テストでuniform sequenceとq=1/3 sequenceのgradientが一致することを再現したため、旧candidateの23/48対baseline25/48は「outcome weightが効かなかった」ことの診断であり、実効weightの失敗結果ではない。旧report（SHA `1d2dda7caa93f37b453977494cc22acf7ef740fd87b4367e2662fd2c2771c2c8`）、旧checkpoint、旧評価JSONは履歴として保存し、書き換えない。

現行の修正は `src/mage_ptcg/meta_specialist/recurrent_bc_v4.py` のoutcome modeで、episode qualityを正規化分母から除外して勾配へ残すもの。trainer SHAは `bbe8c151a78d36daeb0a7da995d54d65fef7c94892dec513d0d4610334fa4308`。修正版rootは `runs/meta-specialist-v4-qualified-lucifer19-48-outcome-weighted-corrected-bc-20260812/`、report SHA `03021ad432b7de828da1f4a4297f1c4421c7c658f3cc4931b6df22e8590aa589`、objective SHA `1ca5807dc54410206cc82f19c613ba1387393cdf016cecdedf3e87f3a44f5d34`、mode `RESEARCH_ONLY_OUTCOME_WEIGHTED_V4`、weights `win=1.0 / draw=2/3 / loss=1/3`、test 426 records除外、1 epoch・35 updates/seed・fault0である。validation complete-action NLLはseed0 `0.5593681099→0.5019691924`、seed1 `0.5713402831→0.5183927419`。checkpoint file/tensor SHAはseed0 `c3ac8683e7fe4ef15f00b1560cfed701ba0202c216f1cefe2c95b630c0357eff` / `57372d0f0dcd3f1e3f494ddd7dec391884e14708c7ff71a37d3cc91c058d4d43`、seed1 `d2aa3f696746ab0330b080af4d9627db9dece38f6c64b432188be87a3f23cc75` / `5f68695b61e70721c8198a2946820e090b97a9228a0fff0285c7f7811b1d124a`。

修正版 fixed-six は同一 `lucifer19_battlecore` subject deck（SHA `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`）、6 opponents、両seat、2 games/opponent×seat、base seed `10100000`、max steps `2000`、protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`、fault0で評価した。候補seed0は12/24（seat0 8/12、seat1 4/12）、seed1は14/24（8/12、6/12）。Wave6 baselineはseed0 15/24（9/12、6/12）、seed1 10/24（5/12、5/12）。候補合計26/48、baseline25/48（+1勝、+2.08pt）だが、seed0が3勝不足しseat1も4/12対6/12へ悪化したため、事前の両seed・両seat・fault0ゲートに不合格。評価JSON SHAはseed0 `3ff17a81bf3d95795216f3fa0c4bf1d5941889fc2d6958dfdcd198b948f9fde9`、seed1 `30656422dd405d78e6ade83d6a9cf1f78c2100fe788ae194d3e52045ac622833`。shadow-B、longrun、Champion変更、Kaggle提出は行わない。

この修正版は「重みが実際に勾配へ作用する」ことを検証したが、seed符号反転を解決しなかった。したがって同じLucifer snapshotに対するloss-focused／単純outcome weightの再sweepは停止する。次の候補は、public-only value/search targetまたは評価時IDを入力へ漏らさないweak-matchup residualのどちらか一つを、2 seed固定六→合格時のみshadow-Bの順に事前登録する。root提出deck（Mega Lucario/Hariyama、SHA `2a541d7...`）とArchaludon subject deckは別identityであり、全結果を提出性能へ転記しない。

次候補を一つだけ選ぶなら、public-only value/search targetまたはshadow-B弱相手向けweak-matchup residualを、2 seed固定六→合格時のみshadow-Bの同じゲートで比較する。root `deck.csv`（Mega Lucario/Hariyama系 SHA `2a541d7...`）とArchaludon subject deckは別identityなので、全ての上記値をKaggle提出性能へ転記してはならない。

## 29. V5 SetContext sidecar bounded pilot（2026-08-12）

V4本体を変更せず、V4 checkpointからstrict transferする研究専用V5 SetContext sidecarを実装した。V5はvalid candidate mean/count contextとcandidate residualを追加し、head最終層をzero-init、STOPはV4 base globalから算出する。V5 model/loader、policy adapter、V5 trainer、fixed-six evaluator、per-seed pilot runnerとfocused testsを新規追加した。V4 actor pool/production runtime/Championは変更していない。詳細証跡は `docs/evidence/v5-set-context-pilot-20260812.md`。

Lucifer19 sealed snapshotを固定入力にした。subject deck raw SHAは `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`、snapshot index SHAは `fca5b1d7c559d5cd6925dca4bd60c5b8e3a2ac80c949fafd6ed0cacc59bcbfd3`、snapshot shard SHAは `e63c8b5db91fd7cf8a16c3f013a11053f82d54d00cb8b02f43a4e2a4084f937e`、dataset root SHAは `5064542ae045054ee9864bc67fd11f62cc8bc3a16d019190743fca00d4bb45b2`。train/development/testは1,928/436/426 records、testは未使用、V4 projectionは2,179/501 steps、35/7 episodes、objective SHA `9f159bfd5640a9e63fbc5ecc15e85b1042eec94e1ac515234704d1a59781c6ad`。

学習条件は両seedとも1 epoch、patience0、lr `1e-4`、TBPTT8、burn-in1、35 updates、`cuda:0`、uniform objective。seed0のV5 validation NLLは `0.457705→0.444270`、best checkpoint file/tensor SHAは `4fdc30147d71e50740fd206641b11c1cff4f9ff14935a847cd9c6af381636c26` / `9393a0ef6345b7dd7aab5b792a4148f82ea8d7095b91a1275356de3b27a2855a`。seed1は `0.480082→0.451917`、file/tensor SHAは `7c5ce8282686f91e65b21f39e168b4669343251549bc41e326ebbe043d006270` / `6d08a8e9c54d3d2cf1a3ffd4bd41138887866cd8b997e5640183d57c1a333ac2`。V5 implementation digestは `8a6558579337447cc140ce98441e4bc90c55c26908eace502ab35655a475bfc4`。

実CABT fixed-six（6 opponents×2 seats×2 games=24局/seed、base seed10100000、protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`、fault0）は次の通り。

| arm | seed0 | seed1 | aggregate | seat |
|---|---:|---:|---:|---|
| Wave6 baseline | 15/24 | 10/24 | 25/48 | seed0 9/12,6/12; seed1 5/12,5/12 |
| V5 SetContext | 12/24 | 12/24 | 24/48 | seed0 6/12,6/12; seed1 8/12,4/12 |

V5はNLLを下げたが、seed0がbaselineより3勝下回り、seed1もseat1が4/12へ悪化した。対応seed以上・両seat非悪化・fault0の事前ゲートに不合格。candidate評価JSON全体SHAはseed0 `33926562b8247eee852390c7585bcdea480664caf0c725aa08f44623ac203af4`、seed1 `4a3d0d99dc46053953a562960b6b0800777244a19bd3af5c6b04bf1083fe8f06`。

この結果から、V5 SetContextの長時間化、head magnitude sweep、shadow-B、Champion変更、Kaggle提出は行わない。現状ラベルは「GPU/実装/証跡は回復・拡充、offline NLL改善は出るが、seed横断の実戦汎化は未達」。次は同じV4 BCの局所探索ではなく、training-local permission済みteacherをV4 topologyへ正しく投影するmatched armの可否確認、またはpublic-only value/search targetのbounded比較を選ぶ。R7は`local_eval_only`/`smoke_ok=false`、root提出deckとArchaludon subject deckも別identityのため使わない。

## 30. V5 architecture isolation の訂正（Wave6 baseから正式再実験）

§29の最初のV5 runはLucifer19 V4-BC checkpointを初期値にしていたため、V5 architecture単独の比較ではない。初回runは別の診断armとして保持し、以下のWave6 base runを正式なarchitecture isolationとする。

対応するWave6 V4 checkpointからV5へstrict transferし、同じLucifer19 sealed snapshot、1 epoch、lr `1e-4`、TBPTT8、burn-in1、35 updates/seed、`cuda:0`、fixed-six 24局/seedで実行した。結果は次の通り。

| arm | seed0 | seed1 | aggregate | seat |
|---|---:|---:|---:|---|
| Wave6 baseline | 15/24 | 10/24 | 25/48 | seed0 9/12,6/12; seed1 5/12,5/12 |
| V5 from Wave6 | 12/24 | 15/24 | 27/48 | seed0 5/12,7/12; seed1 9/12,6/12 |

V5 candidate評価JSON全体SHAはseed0 `f30d1465c5ae001beb64bdec97d133fc419fc9dd16686ce0c5420384004f014c`、seed1 `4591dac90ffcc4b2fedc7bdb91c5f70f8c4bc5e6060c81561c77f3aeeb777981`。学習report SHAはseed0 `2b5e650a0d8ca716940976a36e93b22ae7cfc3d6fbcf0b612309761d9a3249bf`、seed1 `e589a2c2627de28430d84d316d2f147c5d5b5ae6fbbc208ce5069b68fe38a68e`。checkpoint file SHAはseed0 `f3ecdb31f389f0cd7ccfc4959f1486f67d35956a3655ff12b69bd2f263b8c44b`、seed1 `46750a0069a9f2c25b1ad6181e25508104bee91334cd3012f00c3298c65af46f`。

V5はaggregateで+2勝だが、seed0が3勝下回り、seed0 seat0が5/12対9/12へ悪化した。従って対応seed以上・両seat非悪化・fault0の事前ゲートに不合格。seed1の+5勝を理由に長時間化・shadow-B・Champion変更・提出へ進まない。§29の初回runと§30の正式isolationは、base checkpoint identityが異なるため別armとして扱う。

## 31. 次の性能主線: 公開 on-policy outcome ledger と residual/OOD preflight

V5 SetContextをWave6 baseから正式isolationした結果、seed0 `12/24`（Wave6 `15/24`）、seed1 `15/24`（Wave6 `10/24`）、aggregate `27/48`対`25/48`、fault0となった。seed1の改善はあったが、seed0が3勝不足し、seat0も5/12対9/12へ悪化したため、事前promotion gateは不合格。V5の長時間化、shadow-B、Champion変更、Kaggle提出は行っていない。V5の初回Lucifer V4-BC base armとはcheckpoint identityが異なるため、両者を同じarchitecture evidenceとして合算しない。

この不合格後、public-only search/Qをすぐ実装する方針は採らず、既存のV4 actor-pool screenからweak matchupの公開on-policy outcome ledgerを作る方針に移った。search側は`search_teacher_v1.py`がvalues/standard errors/current policyをblendingするだけで、公開状態からのdeterminization・合法action後の状態遷移・rollout・Q/visit生成が未実装である。CABT native searchはhidden stateとopaque `search_begin_input`を要求し、過去監査で6–16秒block、SIGSEGV、binary identity不一致があるため、実target生成は別プロジェクトとして後回しにする。

### 31.1 入力artifactと境界

対象はWave6 V4 seed0/seed1のfixed-six actor-pool screenである。各96局、fault0、同一Archaludon subject deck `opponents/tomatomato_archaludon/deck.csv`（SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`）。seed0 screen JSON SHAは`9ad78bf31f41d307916b25d238544ea5060e0df9a8e16b5ca72a8e3977fc00e3`、transition JSONL SHAは`2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce`。seed1はそれぞれ`aed7438628ffdcb4a4d0c11a844c6ddea4a75017be44912180e3f6dc90abf1f1`、`2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26`。

RuntimeDecisionTraceの`public-v1-representable`以外はduplicate/redactedでaction typeや候補identityが空になるため、診断の正典は`screen.transitions.jsonl`のsealed actor-visible payloadとした。transitionには`model_input`、semantic `prefix_steps`、chosen semantic complete action、behavior log probability、episode/game join keyがある。相手ID・seatは集計・training component選択だけに利用し、runtime入力、checkpoint、提出identityへ渡さない。非公開hand/deck/prize、raw observation、physical option index、opaque payloadは使わない。

### 31.2 weak cellの記述結果

fixed-sixのweak cellを`ozawa_crustle_v2`、`skarin_dragapult`、`sue124_alakazam`と定義し、2 seedで合算するとloss 58局、win 38局、loss transitions 2,698、win transitions 2,149だった。1局あたりaction typeは次の通り。

| outcome | games | transitions/game | PLAY(7) | ATTACH(8) | EVOLVE(9) | RETREAT(12) | ATTACK(13) | END(14) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| loss | 58 | 46.52 | 13.43 | 4.52 | 1.33 | 0.31 | 5.16 | 2.21 |
| win | 38 | 56.55 | 15.32 | 5.11 | 1.58 | 0.79 | 7.16 | 1.29 |

lossでENDが多く、winでATTACK/RETREATが多いという同時出現はある。しかしwinの方が平均ゲーム長も長いため、早期敗戦・局面差・seat/opponent interactionが混ざる。これは因果action value、counterfactual Q、残差biasの根拠ではない。完全な診断資料は`docs/evidence/v4-public-onpolicy-outcome-ledger-20260812.md`。

### 31.3 次のbounded preflightとgate

1. actor-visible `model_input`/`step_input`/V4 logitsだけからcandidate-domain size、top1-top2 margin、entropy、prefix長、STOP可否、normalized surprisalを再計算する。
2. train側公開特徴分布を固定し、OOD基準とconfidence thresholdを勝率を見ずに一度だけ決める。未知・malformed・privacy欠落はV4 unchangedへfail-closed。
3. weak opponent ID/seatはsample/component selectionにだけ使い、runtime residualへ入れない。eligible外はGRU context-onlyでもloss denominatorへ入れない。
4. zero-init public residualまたはloss-only overlayを、Wave6対応seed0/1、同じdataset/protocol、fixed-six 24局/seedでalpha=0 controlと比較する。
5. 両seed・両seatが対応Wave6以上、fault0、target/non-target metric大幅悪化なしの場合だけshadow-Bへ進む。aggregateだけ正、seed反転、seat崩壊、OOD特徴欠落ならresidual系列を終了。

Rule v0の単純action-type alpha=1（EVOLVE/ATTACH/PLAY/ABILITY/ATTACK/ENDの固定prior）は既にshadow-Bで43/96、alpha=0相当51/96となり、seed1が18/48まで崩れた。これを再利用・後追いsweepしない。promotion authorityはfalseのまま。

## 32. 公開 confidence / OOD preflight の実測結果（2026-08-12）

Wave6 seed0/1 の sealed `screen.transitions.jsonl` を対応する Wave6 V4 checkpointへ再入力し、actor-visible `model_input` / `step_input` だけから semantic logits を再計算した。seed0 は 4,763 transitions / 10,094 prefix rows、seed1 は 5,590 / 11,841、両方 `VALID`・fault 0。保存 payloadにはlogitsが無いため、margin/entropyはこのhash-bound replayで得た。

先頭 prefix 10,353 rowsの全体は、domain median 4、top1-top2 margin median 1.9652、entropy median 0.3956、target NLL median 0.1114、normalized NLL median 0.0736、STOP available 9.35%。weak cell（ozawa / skarin / sue、4,847 rows）は margin 1.8456、entropy 0.4311、NLL 0.1254、normalized NLL 0.0804で、その他より低confidence方向だった。ただしoutcome差はゲーム長・局面・seatの交絡を含み因果解釈しない。replay smoke 100 transitionの保存behavior log-probabilityとの誤差はmean `7.22e-08`、max `6.67e-07`、target欠落0。

完全なpublic state signatureはcross-seed交差2件だけでOODには不適切。実装した `public_confidence_ood_v1.py` の粗いbucket（selection type/context、実効domain bin、prefix depth、STOP、option-type set、公開entity count、全card-bag mask count）は、train prefixでseed0 371種、seed1 375種、交差311、Jaccard約0.715だった。bagごとに細分化するvariantはcross-seed新規率が上がるため採用しない。最終pilotではcross-seedではなくfrozen base-corpusをreferenceにし、bucket仕様・SHA・閾値を固定する。

詳細証跡は `docs/evidence/v4-public-confidence-ood-preflight-20260812.md`。研究専用 `src/mage_ptcg/meta_specialist/public_confidence_ood_v1.py` と `tests/meta_specialist/test_public_confidence_ood_v1.py` を追加し、effective domain（allowed+STOP）、replay誤差、forced context-only、metadata不変、reference SHA fail-closedを6 focused testsで確認した。相手ID/seatは層別・学習サンプル選択だけに使い、runtime/checkpointへ入れない。まだ残差学習・shadow-B・longrun・Champion変更・提出は行わない。

## 33. 公開 bucket reference artifact の生成（2026-08-12）

参照集合をcross-seedの比較値のままにせず、Wave6 seed0 screenの `partition=train` を学習側公開分布として固定する研究用builder `scripts/build_public_confidence_reference.py` を追加した。builderは各行をcanonical `parse_transition_payload_v4`で再検証し、`model_input`・`prefix_steps.step_input`だけからbucketを数える。envelopeのopponent ID、seat、policy identity、game/component identityは集計にも出力にも使わない。source JSONL SHA、partition、bucket histogram、forced prefix数、privacy flagsをJSONへ保存し、unknown partition・空partition・不正payloadはfail-closedで拒否する。focused testsは2 passed。

生成artifactは `runs/meta-specialist-public-confidence-ood/reference-seed0-train-v1.json`、artifact SHA `f96062c741f55aa7382e393d5e119b68e6b3c1635df8612b8d0c299f5303b096`。source transition SHAは `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce`、3,678 transitions、7,784 prefix rows、371 buckets、forced 4,318 rows、rare threshold 2である。

このreferenceはWave6 screen由来の診断用固定値で、shadow-Bのuntouched testやpromotion evidenceではない。この節の作成時点では `PublicEligibilityPolicyV1.min_normalized_surprisal=0.5` は暫定値だったが、後続の `configs/meta_specialist/public_confidence_ood_policy_v1.json` で bucket仕様・rare threshold `2`・confidence `0.5` を固定済みである。reference欠落・SHA不一致・privacy flag違反・eligible外のloss混入時はV4 unchangedとし、残差学習を開始しない。再現コマンドは evidence §「固定reference artifact」に記載した。

同じreferenceと対応seed0 Wave6 checkpointを `scripts/measure_public_confidence_ood.py` でCPU再生した。出力 `runs/meta-specialist-public-confidence-ood/replay-seed0-train-v1.json` のSHAは `d9fa79d0f5b03e24ea77850a0e2a358718710365a29418536b37148df402d844`。3,678 transitions / 7,784 prefix rowsのうち forced/context-only 4,318、non-forced 3,466、eligible 458（全体5.88%、non-forced 13.21%、eligible transition 405）、target欠落0。reasonは forced 4,318、below focus 3,008、high normalized surprisal 322、rare public bucket 136、mean normalized surprisal(non-forced) 0.18845だった。これは暫定policyがepisode全体へ広くlossを掛けていないことの診断であり、性能改善の証拠ではない。seed1/validationの別source replay結果は下記に追記し、threshold tuningではなくeligible外loss denominatorの確認へ進む。

同じseed0 train referenceを変えずにseed1 trainとseed0 validationも再生した。seed1 trainはnon-forced 3,712中eligible 629（16.95%、全体7.62%、eligible transition 569）、seed0 validationはnon-forced 1,032中eligible 172（16.67%、全体7.45%、eligible transition 157）、target欠落0。artifact SHAは seed1 `9b2838a87371e23fba8a46ae4933c9d874025b59c9fae3ebcbaa53c074a11973`、seed0 validation `ccfe576be1e0e0f9221e982bc7db54c6e0995427f41468e9a08e8256543bd1e4`。runnerはreference source SHAとreplay transition source SHAを別々に保存し、`--reference-source-sha256`でfrozen referenceを明示固定する。これらは分布・eligible massの診断であり、seed間性能改善の証拠ではない。thresholdを結果に合わせて変えず、暫定 `min_normalized_surprisal=0.5` / rare `2` を固定候補としてeligible外loss denominatorを先に検証する。

## 34. 公開confidence/OOD policyの事前登録

replay結果に合わせた後付け調整を避け、seed0 self-referenceを2-seed pilotへ流用しないため、Wave6 seed0/seed1 train sourceを固定順（seed0→seed1）で束ねる `scripts/build_public_confidence_reference_bundle.py` を追加した。bundle artifactは `runs/meta-specialist-public-confidence-ood/reference-wave6-seed0-seed1-train-bundle-v1.json`、artifact SHA `7dcf1cef7639a6a8b14ffcf6591bf3808fb7bed8edd85d82da369b3b6f511cda`、ordered source-list SHA `b21c329a2ab599ba80e02294052a19ed4d770ac0a7335fc6197f33e3567206cb`。2 sources・7,570 transitions・16,043 prefix rows・435 buckets・forced 8,865で、source SHAはseed0 `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce`、seed1 `2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26`。source orderを含めhash-boundだが、opponent/seat/policy/game/component identityは出力しない。focused testsはbundle 3件、single-source回帰と合わせて5 passed。

policy manifest `configs/meta_specialist/public_confidence_ood_policy_v1.json` の更新後SHAは `ae5396b19280049d9ceb3cea2b87ceeceaf8268a8fb747a3abfc9fb394cfd697`。`min_normalized_surprisal=0.5`、rare threshold `2`、`focus_on_ood=true`、`promotion_authority=false`、`longrun_allowed=false` を固定した。

forced effective-domain 1 とineligible（reference不一致、confidence不足、malformed等）は `context_only` とし、GRU contextは進めるが `supervision_weight=0`、loss numerator/denominatorから除外する。eligible non-forcedだけが `loss_bearing` である。runtimeは opponent ID、seat、policy identity、hidden fieldを受け取らず、IDはtraining component選択の層別に限定する。manifestは `promotion_authority=false`、`longrun_allowed=false`、`status=pre_registered_diagnostic_policy_not_yet_connected_to_training` であり、現時点では学習へ接続していない。次の実作業はこのdenominator契約をtrainerで検証してから、2-seed fixed-six residual pilotを起動することだ。

実trainerの分母契約は、V4既存trainerを変更せずfocused回帰で確認した。`tests/meta_specialist/test_recurrent_bc_v4.py::test_public_context_only_mask_is_excluded_from_trainer_denominator_and_gradient` は、eligible外stepをcontext-onlyで通したsequenceがeligible stepだけのsequenceと同じNLL・gradient・parameter updateになることを確認する（focused 2 tests pass）。これは実データへのconfidence overlay接続や性能改善ではなく、pilot前のloss-mask契約確認である。実screen overlayを使う2-seed学習、CABT評価、shadow-Bはまだ開始していない。

実screen overlayへ進む前の契約専用runner `scripts/run_meta_specialist_v4_public_confidence_ood_bc.py` も追加した。9 parameterized/focused tests pass。runnerはsealed public rowのrecord/group/episode_start/hidden contextを保持し、public scorerからeligible=1 / context-only=0を生成するが、単一source reference、manifest schema/privacy/status違反、`train=True`、`training_requested=True`、CLI実行はfail-closedする。common bundle loaderはartifact SHA、ordered source-list SHA、2 source SHA、bucket/privacy/promotion authorityを再検証する。現時点では実screen overlay学習、checkpoint生成、CABT評価を呼び出さない。

## 35. common bundleでの両seed replay確定と次の接続条件

上記の単一seed reference replayは履歴診断として残し、2-seed作業の判断にはcommon bundle replayを正とする。対応Wave6 checkpointを同一の2-source bundleへ再入力した結果は次の通り。

| replay | transitions / prefix rows | forced | non-forced | eligible prefix | eligible rate (non-forced) | eligible transition | target missing | replay artifact SHA |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| seed0 train | 3,678 / 7,784 | 4,318 | 3,466 | 395 | 11.396% | 345 | 0 | `00954fa622d2c1d749efaf3239fb3b9e30f8e01d12d16a70747e360ea12045a7` |
| seed1 train | 3,892 / 8,259 | 4,547 | 3,712 | 437 | 11.773% | 384 | 0 | `5974a7e715752691ff86ec5e5a1fae09b6db4411fe597224291a53107802dbe0` |

両 replayはbundle artifact SHA `7dcf1cef7639a6a8b14ffcf6591bf3808fb7bed8edd85d82da369b3b6f511cda`、ordered source-list SHA `b21c329a2ab599ba80e02294052a19ed4d770ac0a7335fc6197f33e3567206cb`、policy `min_normalized_surprisal=0.5` / rare `2` / `focus_on_ood=true` を共有する。checkpoint descriptor SHAはseed0 `486918dc4e6d62bfa66f925a14a1133ec654304bb1db6e39a51974159e6c9051`、seed1 `6c6a5903c9efd19d2e73fe0c26d307f9b1eb692a9b2a2c6ecce05a703e770bf9`。privacy flagsは全てfalseで、target missingは両seed0件だった。

この値は eligible mask の健全性と provenance の証拠であって、CABT勝率・teacher quality・promotion evidenceではない。次に許可されるのは、common bundleとpolicyのSHA検証、各seedのscreen/checkpoint対応、eligible外を同じrecord/groupのcontext-onlyとして保持、fixed-last 1 epochの2 seed学習、同じfixed-six 24局/seedでのalpha=0 control比較までである。両seed・両seat非悪化とfault0を満たさない場合はshadow-Bへ進めず、残差系列を打ち切る。best epoch選択を固定budgetの成功証拠へ使わない。

契約専用runnerは学習を開始しないfail-closed moduleであり、実screen overlayへの接続はこの節の時点では未実行である。新たに接続runnerを作る場合も、V4 production/actor poolを改変せず、研究用artifactと`promotion_authority=false`を維持する。

## 36. 3:56以降の補足packと最新pilot失敗・修正

3:56以降の詳細な時系列、GPU復旧、既存armの結果、common bundle、public OOD executor、初回seed0実学習の失敗、根因、修正テスト、修正版rerunの状態は、専用補足packへ切り出した。

参照: `docs/status/chatgpt_context_pack_since_0356_2026-08-12.md`

最新の重要事実は次の通り。

- GPUはRTX PRO 5000 / CUDA 12.8として正常復旧している。初回pilotの失敗はOOMではない。
- seed0 controlは1 epoch・74 updates完了し、validation NLL `4.230611736653588→1.9670050386459597`。
- candidateは`ValueError: training sequence contains no post-burn-in decoder rows`で停止した。
- 原因はeligible prefixが一つもないcontext-onlyゲームを、candidateの独立trainer sequenceへ渡したepisode materialization境界のbug。
- eligibleが一つ以上あるゲーム内のeligible外prefixはcontext-onlyのまま保持し、eligibleが一つもないゲームだけをcontrol/candidate双方から除外する修正を入れた。
- 修正後のexecutor/plan testsは`10 passed`、py_compile、diff-check pass。
- 初回rootを上書きせず、`runs/meta-specialist-v4-public-confidence-ood-pilot-exec-rerun-20260812`でseed0を再実行中。
- 修正版seed0/seed1/fixed-six評価が完了するまで、promotion、shadow-B、longrun、Champion変更、Kaggle提出は不可。

## 37. 修正版public OOD seed0完了

修正版root `runs/meta-specialist-v4-public-confidence-ood-pilot-exec-rerun-20260812/seed-0/`でcontrol/candidateの両armが1 epoch・68 updates完了した。report SHAは`69079b399cf7f1c979ca300a9e223b9e0a242a2afb0d2d5abd5cd761bbf85629`。

| partition | transitions | prefixes | eligible | context-only | effective mass |
|---|---:|---:|---:|---:|---:|
| train | 3,552 | 7,515 | 395 | 7,120 | 395.0 |
| validation | 1,077 | 2,291 | 127 | 2,164 | 127.0 |

all-context-only gameを除外したためcommon replayの全3,678/7,784からmaterialized数は減っているが、eligible mass 395は不変。selected game内部のeligible外はweight0/context-onlyで残る。

controlはinitial/last validation NLL `4.20920850694899→1.9791238543714986`、elapsed 147.8167秒。best file SHA `37789504dc72da9cadce844d15f12e3425bb768c387838ff1fc26b61b9e01f54`、tensor SHA `f3f29d27f81fa070052d5c5f42bd541f0a34483f5ec44447ee78f985aceb589c`。

candidateはinitial/last validation NLL `2.9896370932064227→2.280730761257948`、elapsed 92.9603秒。best file SHA `081e60caa1fb59ff577e5761a01fc17666e780c18978c8a3b9329193e263a0e9`、tensor SHA `f08982fd812518eadf771afac61eb5a48163004e45c1073746502a7521c07002`。

両armの学習完走はloss-mask接続の証拠であり、勝率改善・promotion証拠ではない。次はseed1、その後fixed-sixでcontrol/candidate/Wave6を比較する。

## 38. 3:56以降の最終更新とChatGPT Proレビュー反映

seed1 rerun、fixed-six評価、resume/checkpoint形式の境界問題、public OOD gate判定の詳細は専用補足packへ追記した。

参照: `docs/status/chatgpt_context_pack_since_0356_2026-08-12.md`

最終fixed-sixは public-OOD candidate `22/48`、Wave6 `22/48`、matched control `9/48`。seed0 candidate `10/24`対Wave6 `11/24`、seed1 candidate `12/24`対Wave6 `11/24`で、seed0が下がり、seed1 seat1も4/12対6/12へ悪化した。従ってaggregate同点でも事前ゲート（対応seed以上・両seat非悪化・fault0）不合格である。public OOD系列はthreshold再調整・shadow-B・longrunへ進めない。

ChatGPT Proレビューに基づき、次の主線を「teacher差し替え＋V4全体fine-tune」から次へ切り替える。

1. 同一checkpointの反復評価でCABT評価noiseを定量化する（engine seed setterが無いため独立層化評価として扱う）。
2. 既存candidateのpolicy drift（Wave6とのaction change、KL/JS、first divergence、hidden divergence、module delta）をsealed replayで比較する。
3. Wave6のnormal carry / complete-action reset / turn resetを比較し、GRU trajectory amplificationを切り分ける。
4. qualified teacherのphysical→semantic→V4 decoder→physical round-tripを全recordで監査する。
5. その後、frozen Wave6 residual＋anchor KL、uniform logit ensemble、cross-fitted Monte Carlo value/AWR、public-belief search-Qの順に一つずつbounded比較する。
6. shadow-Bは既に複数armの選択に使ったdevelopment-external poolであり、次の本命候補を作る前に候補・deck・policy SHAが重複しないshadow-Cをfreezeする。

新しい長時間学習は、同一checkpoint評価揺れを上回る改善、2 seedまたはensembleでdevelopment +3pt程度、shadow-C正、broad 12〜20 opponentでmeta-weighted +3pt程度、seat片側-5pt超の崩壊なし、fault0、Rule v0/current submissionとの直接比較、外部320〜640局程度の証拠が揃うまで開始しない。現時点では目標未達であり、longrun開始可能な候補は存在しない。

## 39. レビュー後の追加実測（ensemble / recurrence）

研究専用のuniform semantic-logit ensembleを実CABTへ接続した。Wave6 seed0+seed1 ensembleはfixed-six 24局で11/24（seat0 6/12、seat1 5/12、fault0）で、同blockのWave6単体と同率だった。同一checkpointを独立hiddenへ複製したreset ablationは、seed0のnormal/action/turnが各12/24、seed1が15/24、14/24、11/24、全fault0。CABT seed setterがないためpairedではなく、24局/cellは既測noise floor以下である。normal carryを維持し、turn resetを採用しない。詳細は補足pack §20および `docs/evidence/v4-research-ensemble-reset-results-20260812.md`。

shadow-Cもidentity-onlyで凍結済みだが、medal 6件は同一generic local-eval policy SHAを共有するため、独立policy評価ではなくdeck-OOD診断に限定する。勝率、fault、速度、seat smokeは未実施である。
