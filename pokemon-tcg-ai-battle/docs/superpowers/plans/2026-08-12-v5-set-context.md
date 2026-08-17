# V5 SetContext sidecar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** V4を変更せず、V4 baseから明示的に転送できる研究専用V5 SetContext sidecar model/loaderを実装し、恒等性・候補集合対称性・mask・strict拒否・provenanceをfocused testsで保証する。

**Architecture:** 新規`neural_model_v5.py`に`SpecialistModelV5`とV5専用save/load/transfer APIを置く。V4 baseのsemantic logitsへcandidate mean/count contextから作る候補別residualを加える。STOPはV4 base global tokenから算出する。新規`test_neural_model_v5.py`はV4 test fixtureを再利用し、V4のソース・loader・checkpointを編集しない。

**Tech Stack:** Python 3、PyTorch、pytest、既存`representation_v4`/`neural_model_v4`。

## Global Constraints

- 既存dirty差分を上書き・整形・削除しない。
- `neural_model_v4.py`、`neural_policy_v4.py`、V4 trainer、actor pool、提出経路は変更しない。
- V5は研究専用であり、性能pilot、長時間学習、Kaggle提出は実行しない。
- loaderはV4 strict loaderでbaseを検証してからV5へ転送する。`strict=False`とunknown-key ignoreは禁止する。
- TDD順序は、テスト追加→RED実行→最小実装→GREEN実行→focused py_compileである。

---

## Task 1: 設計と契約の固定

**Files:** `docs/superpowers/specs/2026-08-12-v5-set-context-design.md`, `docs/superpowers/plans/2026-08-12-v5-set-context.md`

- [x] SetContextの不変条件、STOP境界、mask、loader provenance、非対象を設計書に記載する。
- [x] TDD cycle、変更禁止範囲、検証コマンドを本計画へ記載する。

## Task 2: REDテストを先に追加する

**Files:** `tests/meta_specialist/test_neural_model_v5.py`

- [x] V4 tiny model/checkpoint fixtureを作り、V5 transfer APIが未実装で失敗することを確認する。
- [x] zero-init transferでV4 semantic logits/STOPと一致する契約を書く。
- [x] candidate permutation equivariance、STOP不変、duplicate-mask、N=0/N=1を検証する契約を書く。
- [x] V5 headを非ゼロ化してもSTOPがbaseのままである契約を書く。
- [x] V4↔V5 strict rejectionとmanifest provenance、SHA改ざん拒否を書く。
- [x] REDコマンド: `PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_neural_model_v5.py`（未実装importでcollection error）。

## Task 3: V5 model最小実装

**Files:** `src/mage_ptcg/meta_specialist/neural_model_v5.py`

- [x] V4 modelを明示的に転送可能なV5 subclassとして定義し、V5 marker/head configを追加する。
- [x] valid candidate mean/count contextとcandidate residual headを追加する。
- [x] headの最終層をzero-initし、semantic logitsだけへresidualを加える。
- [x] STOP計算と返却global tokenをV4 baseに固定する。
- [x] duplicate-maskをpoolとlogitの両方へ適用し、空集合をゼロcontextとして扱う。

## Task 4: V5 manifest/loader/transfer

**Files:** `src/mage_ptcg/meta_specialist/neural_model_v5.py`

- [x] V4 strict loaderを呼び出すtransfer helperを実装する。
- [x] V4 state allowlist、allowlist SHA、base file/tensor SHA、head schema、V5 tensor SHAをdescriptorへ保存する。
- [x] atomic save、immutable snapshot hash、weights-only load、exact key/config/digest validationを実装する。
- [x] V4 artifactをV5 loaderが拒否し、V5 artifactをV4 loaderが拒否することを確認する。

## Task 5: GREENと静的検証

**Files:** `src/mage_ptcg/meta_specialist/neural_model_v5.py`, `tests/meta_specialist/test_neural_model_v5.py`

- [x] REDで失敗したfocused testsがGREENになることを確認する。
- [x] `PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_neural_model_v5.py`を実行する（6 passed）。
- [x] `PYTHONPATH=.:src .venv/bin/python -m py_compile src/mage_ptcg/meta_specialist/neural_model_v5.py tests/meta_specialist/test_neural_model_v5.py`を実行する。
- [x] `git diff --check`と`git status --short`でV4既存差分を監査する。

## Task 6: policy/trainer接続の研究専用実装

- [x] `neural_policy_v4.py`、`recurrent_bc_v4.py`、`actor_pool_v1.py`のV5接続点を読み取り確認した。
- [x] V4 exact-type/closed behavior kindを避ける新規 `neural_policy_v5.py` と `recurrent_bc_v5.py` を追加した。V4 production filesは未変更。
- [x] public actor-visible入力、fresh policy、one-GRU-per-decision、commit/abort/reset、V4 STOP semantics、base provenanceをfocused testsで固定した。

## Task 7: 研究専用V5 strength evaluator

**Files:** `scripts/measure_v5_set_context_checkpoint_strength.py`, `tests/meta_specialist/test_measure_v5_set_context_checkpoint_strength.py`

- [x] V4 evaluator/actor poolを編集せず、固定`EVAL_HELD_OUT_V1`・両seat・`games_per_seat`のrunnerを追加する。
- [x] `_build_actor_pool_deck_binding_v1`、V5 policy factory、`runtime.make_agent`を接続するlazy factoryを追加する。
- [x] V5 descriptor、base provenance SHA、transfer/head provenance、protocol/evaluator SHAをJSONへ保存する。
- [x] faultをrequested games分母へ含め、fault時のcomparison statusをinvalidにする。
- [x] RED（script未作成）→GREEN focused test 3 passed、py_compile、diff-checkを確認する。
- [x] V5 policy API接続後の実runner評価は親タスクの継続指示に基づき、adapter smokeまで実行した。

## Task 8: bounded 2-seed pilot と fixed-six gate

**Files:** `scripts/run_v5_set_context_pilot.py`, `docs/evidence/v5-set-context-pilot-20260812.md`

- [x] Wave6/V4 baseのseed対応strict transferを実行し、既存別lineage sidecarの再利用・上書きを拒否した。
- [x] zero-head V4/V5 parity、実CABT adapter smokeをseed別に確認した（fault0）。
- [x] Lucifer19 sealed snapshot、1 epoch、lr `1e-4`、TBPTT8、burn-in1、cuda:0、2 seedで学習した。
- [x] fixed-six 24局/seedを同一protocolで評価した。正式なWave6-base V5はseed0 12/24、seed1 15/24、Wave6 15/24・10/24で、seed0下振れのため事前ゲート不合格。Lucifer19-V4-baseの初回armは別診断として保持する。
- [x] V5長時間化、shadow-B、Champion変更、Kaggle提出へ進まないと判定し、evidence/current_status/handoff/ChatGPT context packへ記録した。

## Task 9: public residual/OOD preflight（pilot完了・fixed-six gate不合格）

V5正式isolationがseed0で対応Wave6を下回ったため、V5 headのsweepと同じV4 BCのweight/epoch探索は停止する。次工程は、public-only search/Qの実target生成（determinization・rollout・Q/visit未実装）より短い、weak-matchup public residual/OOD eligibilityの事前診断とする。

- [x] `screen.transitions.jsonl` の actor-visible `model_input` / `step_input` / V4 logits から domain size、top1-top2 margin、entropy、prefix長、STOP可否、normalized surprisalを固定特徴として抽出した（詳細は `docs/evidence/v4-public-confidence-ood-preflight-20260812.md`）。
- [x] opponent ID/seat/policy identity/hidden fieldをruntime featureへ入れないRED契約を書いた。`public_confidence_ood_v1.py` は actor-visible typed input のみを受け、effective domain と reference SHA fail-closed を含む6 focused testsを通過した。ID/seatは training component の選択と集計に限る。
- [x] Wave6 seed0 screenのtrain partitionから、source SHA・bucket schema・privacy flags・rare thresholdを固定する `build_public_confidence_reference.py` と2 focused testsを追加し、single-source diagnostic referenceを生成した。さらにseed0/seed1を固定順で束ねる `build_public_confidence_reference_bundle.py` と3 focused testsを追加し、2-seed共通reference bundle（artifact SHA `7dcf1cef...`、source-list SHA `b21c329a...`）を生成した。これは同一screen由来の診断用referenceであり、独立評価用ではない。
- [x] `measure_public_confidence_ood.py` を追加し、対応seed0 checkpointのtrain replayで forced/context-only、eligible mass、target欠落0、policy hash-bound provenanceを確認した。replay artifactは診断用で、性能証拠ではない。
- [x] seed0 train referenceを固定したままseed1 trainとseed0 validationを再生し、reference sourceとreplay sourceを分離したSHA-bound artifactを保存した。eligible率はnon-forcedで13.21% / 16.95% / 16.67%、全target欠落0である。
- [x] train側公開特徴分布だけでOOD thresholdとconfidence thresholdを勝率を見ずに一度固定した。`configs/meta_specialist/public_confidence_ood_policy_v1.json` に bucket仕様、rare `2`、confidence `0.5`、privacy境界、promotion/longrun禁止をhash-boundで記録した。未知・malformed・privacy欠落はV4 unchangedへfail-closedとする。
- [x] eligible外はGRU context-onlyへ通すが、`supervision_weight=0`としてloss denominator/effective massから除外する契約を確認した。`test_public_context_only_mask_is_excluded_from_trainer_denominator_and_gradient` で、masked sequenceがeligible行だけのNLL・勾配・parameter updateと一致することを確認した。実データへのoverlay接続はまだ行っていない。
- [x] 実screen overlay前の契約専用 `run_meta_specialist_v4_public_confidence_ood_bc.py` と9 parameterized/focused testsを追加した。common two-source bundleのSHA、closed privacy/authority、row topology、context-only mask、training入口fail-closedを検証する。runnerは学習/evalを接続していない。
- [x] public OOD maskをWave6対応seed0/1へ同一common bundle・同一policyで接続し、control/candidateを1 epoch・fixed-last相当で完走した。seed0 train eligible 395、seed1 train eligible 437、両seed target missing 0、fault0。
- [x] fixed-six 24局/seedでWave6 / matched control / public-OOD candidateを評価した。candidateはseed0 10/24、seed1 12/24、aggregate 22/48。Wave6は11/24、11/24、aggregate 22/48。seed0下振れとseed1 seat1悪化のため事前gate不合格とした。
- [x] aggregateのみ正、seed反転、seat崩壊を理由にpublic OOD系列を打ち切り、Rule v0 action-type alpha=1の再利用・threshold/rare/epoch後追いsweepを禁止した。

## Task 10: ChatGPT Proレビュー反映 — 原因分解と次objective選定

public OODまでの結果は「NLL改善とfull-model recurrent fine-tuneが勝率へ安定転化しない」共通パターンを示す。次は同じteacher・weight・epochの探索を続けず、次の監査を独立に完了する。

- [x] evaluator reproducibility: Wave6 seed0/1の同一checkpointを96局×3 blockで反復し、within-checkpoint varianceとCABT seed setterの不存在を確認した。seed0は44/96,49/96,46/96（平均48.26%、SD2.62pt）、seed1は42/96,46/96,56/96（平均50.00%、SD7.51pt）。
- [x] policy drift: sealed actor-visible replay 400 rowsでWave6/public-OOD/seed間のtop1/root change、JS、hidden cosine、domain別傾向を計算した。bounded smokeのため因果・promotion証拠とは扱わない。
- [x] recurrence ablation: 同一checkpointを独立hiddenへ複製したresearch-only小block（24局/cell）でnormal/action/turnを比較した。seed0は全mode12/24、seed1は15/14/11で、fault0だがpaired不可・noise floor以下。normal carryを維持し、turn resetを採用しない。96局×3のconfirmatory ablationは未実施。
- [x] teacher projection round-trip: tomatomato-24/96とlucifer19-48の9322 recordsを監査し、9322/9322 PASS、semantic/legal mismatch 0を確認した。ordered/soft-mass実recordは未観測。
- [x] shadow-C: medal deck identity 6件をfixed-six/shadow-A/Bとdeck/policy SHA検査してfreezeした。ただし6件は同一generic policy SHAを共有するため、deck-OOD診断に限定する。

## Task 11: frozen Wave6 residual / ensemble / value signal（設計後に一つずつ）

- [ ] Wave6 backbone/GRU/headをfreezeし、zero-init residualだけを更新する研究専用sidecarをTDDで作る。broad anchor replay KL、residual L2、actor-visible OOD gate、malformed時residual=0を固定する。
- [x] uniform logit ensembleの研究専用adapterを、各model独立hidden・semantic logits/STOP平均・complete action全member commitとして実装し、focused testsをGREENにした。Wave6 seed0+seed1の24局診断は11/24、fault0で同blockのWave6単体と同等。weight sweep/longrunは未実施。
- [ ] cross-fitted Monte Carlo value/AWRを、episode outcomeのbootstrappedでないV(public state)とreturn−Vから作る。frozen residualのみを更新し、単純episode weightをcontrolとする。
- [ ] public-belief searchはdeterminization/rollout/Q/SE生成の100〜300 root prototypeまでとし、native opaque/unsafe searchを再利用しない。
- [ ] root deck（Mega Lucario/Hariyama）とArchaludon subject deckのidentity差を、broad deck-policy arenaで先に測定する。Archaludon結果をroot提出性能へ転記しない。

## Task 12: longrun gate（未達）

- [ ] 同一checkpoint反復評価の揺れを上回る改善。
- [ ] 2 training seedまたはuniform ensembleでdevelopment +3pt程度。
- [ ] untouched shadow-Cで平均差が正。
- [ ] broad 12〜20 opponentでmeta-weighted expected win/Eloが+3pt程度。
- [ ] 片seatが-5pt超で崩れない、fault0、Rule v0/current submission bestと直接比較済み。
- [ ] 外部320〜640局程度を記録する。

gateを満たすまではlongrun、Champion変更、Kaggle提出を行わない。longrunを許可した場合も25/50/75/100% checkpointごとに96〜192局評価し、2回連続でbaseを下回れば停止する。
