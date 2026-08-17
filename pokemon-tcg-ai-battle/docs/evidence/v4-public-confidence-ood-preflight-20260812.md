# V4 公開 confidence / OOD preflight（2026-08-12）

## 結論

Wave6 の sealed actor trajectory と対応する V4 checkpoint だけから、非公開情報・相手 ID・seat をモデル特徴へ渡さずに、semantic legal-domain、top1-top2 margin、entropy、target surprisal、公開構造 bucket を再計算できることを確認した。弱 cell では全体より margin が小さく、entropy と target NLL が高い方向の記述的シグナルが出た。ただしこれは action の因果価値でも residual の性能証拠でもない。次の学習へ進む前に、OOD bucket と confidence threshold を勝率を見ずに一度固定する必要がある。

保存済み transition に logits は含まれていないため、以下の margin / entropy / model-NLL は、保存済み actor-visible `model_input` / `prefix_steps.step_input` を対応する Wave6 checkpoint へ再入力して得た V4 semantic logits である。checkpoint の GRU hidden は `game_id` の境界だけで reset し、runtime 特徴や学習入力へ `game_id`、`opponent_id`、`seat`、`opponent_instance_id` を含めていない。

## 入力 identity

| seed | screen | transitions | transitions / prefix rows | checkpoint file SHA | tensor SHA |
|---|---|---:|---:|---|---|
| seed0 | `runs/meta-specialist-v4-archaludon-dagger-wave6-screen-v2/screen.json` | `screen.transitions.jsonl` | 4,763 / 10,094 | `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de` | `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a` |
| seed1 | `runs/meta-specialist-v4-archaludon-dagger-wave6-screen-seed1-v2/screen.json` | `screen.transitions.jsonl` | 5,590 / 11,841 | `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6` | `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a` |

screen は両方とも `VALID`、96/96 games、fault 0。subject deck は Archaludon lane の `opponents/tomatomato_archaludon/deck.csv`、SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` である。root の `deck.csv`（Mega Lucario/Hariyama 系）とは別 identity であり、本資料の数値を提出性能へ転記しない。

## 再計算した公開指標

`allowed_semantic_classes` は STOP を含まないため、実効 domain は `len(allowed_semantic_classes) + int(stop_available)` とした。forced row（実効 domain 1）は confidence/OOD の学習 mass からは除外できるが、GRU context からは除外しない。prefix step は transition 内の全 step を再生し、以下の表は各 physical transition の先頭 prefix（10,353 rows）を主集計にした。

| 集計 | rows | domain median | margin median | entropy median | target NLL median | normalized NLL median | STOP available |
|---|---:|---:|---:|---:|---:|---:|---:|
| 全体 | 10,353 | 4 | 1.9652 | 0.3956 | 0.1114 | 0.0736 | 9.35% |
| weak cell（ozawa / skarin / sue） | 4,847 | 4 | 1.8456 | 0.4311 | 0.1254 | 0.0804 | 9.49% |
| その他 | 5,506 | 4 | 2.0540 | 0.3608 | 0.0988 | 0.0667 | 9.23% |
| outcome=loss（事後記述のみ） | 4,400 | 4 | 1.9367 | 0.4090 | 0.1172 | 0.0805 | 9.23% |
| outcome=win（事後記述のみ） | 5,953 | 4 | 1.9963 | 0.3865 | 0.1064 | 0.0676 | 9.44% |
| train partition | 7,570 | 4 | 1.9876 | 0.3773 | 0.1055 | 0.0704 | 9.33% |
| validation partition | 2,783 | 4 | 1.9141 | 0.4384 | 0.1278 | 0.0791 | 9.41% |

seed 別では、seed0 の全 transition が margin median 1.9916、entropy median 0.3710、target NLL median 0.1006、seed1 がそれぞれ 1.9497、0.4174、0.1225 だった。weak cell だけを見ると seed0 は margin 1.7963 / entropy 0.4432 / NLL 0.1282、seed1 は 1.8993 / 0.4218 / 0.1232 である。

保存された behavior log-probability と再生した V4 logits の target log-probability は、固定 100 transition smoke で mean absolute error `7.22e-08`、最大 `6.67e-07`（forced/non-forced を含む）だった。全量 replay でも target class 欠落は 0 件だった。従って、confidence 指標は redacted `RuntimeDecisionTrace` の推測ではなく、sealed actor-visible payload と対応 checkpoint の組から計算できる。

## OOD 候補の評価

完全な `{model_input, step_input}` signature は seed0 で約 10,000 種、seed1 で約 11,747 種、cross-seed の交差は 2 件だけだった。この exact signature を OOD 判定へ使うとほぼ全件が未知になるため不適切である。

公開構造を粗く固定した bucket の候補は次の通りである。

`(selection_type, selection_context, effective-domain bin, prefix-depth bin, stop_available, allowed option_type set, pokemon entity-count bin, public card-bag mask-count bin)`

実装した `public_confidence_ood_v1.py` の bucket は、entity count と全 card-bag mask count を固定 bin にまとめる仕様である。この仕様で train prefix を数えると seed0 は 371 種、seed1 は 375 種、交差 311 種、Jaccard 約 0.715 となった。exact signature より安定しているが、まだ同じscreen由来の診断値であり、最終 reference ではない。より細かくbagごとに分けるbucketはcross-seedの新規率が上がるため採用しない。

### 固定reference artifact（診断用）

参照集合を再現可能にするため、`scripts/build_public_confidence_reference.py` を追加し、Wave6 seed0 screenの `partition=train` のみから histogram を生成した。出力は `runs/meta-specialist-public-confidence-ood/reference-seed0-train-v1.json`、artifact SHA `f96062c741f55aa7382e393d5e119b68e6b3c1635df8612b8d0c299f5303b096` である。入力source SHAは `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce`、3,678 transitions、7,784 prefix rows、371 buckets、forced prefix 4,318、rare threshold 2である。JSONには `uses_opponent_id=false`、`uses_seat=false`、`uses_policy_identity=false`、`uses_hidden_fields=false` を保存している。

再現コマンド:

```bash
PYTHONPATH=.:src .venv/bin/python scripts/build_public_confidence_reference.py \
  --transitions runs/meta-specialist-v4-archaludon-dagger-wave6-screen-v2/screen.transitions.jsonl \
  --partition train --rare-count-threshold 2 \
  --output runs/meta-specialist-public-confidence-ood/reference-seed0-train-v1.json
```

このreferenceはWave6 screenと同じ on-policy 分布から作った研究用固定値であり、shadow-Bのuntouched testやpromotion evidenceではない。この節の作成時点では confidence threshold `0.5` はコード上の暫定値だったが、後続のpolicy manifestで `0.5` / rare `2` を固定済みである。source欠落・SHA不一致・privacy flag違反時はV4 unchangedへfail-closedとする。

### public replay runner による eligible mass の確認

再現可能な集計器 `scripts/measure_public_confidence_ood.py` を追加し、上記reference、対応seed0 Wave6 checkpoint、train partitionをCPU・torch threads=2で再生した。出力は `runs/meta-specialist-public-confidence-ood/replay-seed0-train-v1.json`（artifact SHA `d9fa79d0f5b03e24ea77850a0e2a358718710365a29418536b37148df402d844`）、checkpoint descriptor SHA `486918dc4e6d62bfa66f925a14a1133ec654304bb1db6e39a51974159e6c9051` である。eligible transitionは405/3,678、target欠落0。runnerは `--reference-source-sha256` でfrozen referenceのsourceを明示的に固定し、replay対象のtransition sourceとは別にできる。

3,678 transitions / 7,784 prefix rowsのうち forced domain 4,318、non-forced 3,466、eligible 458（全体5.88%、non-forced 13.21%）だった。reasonは forced 4,318、below focus 3,008、high normalized surprisal 322、rare public bucket 136。target欠落は0、mean normalized surprisal（non-forced）は0.18845、policyは `min_normalized_surprisal=0.5`、`rare_count_threshold=2`、`focus_on_ood=true`、margin上限なしである。従って現暫定設定はepisode全体をほぼ採用する状態ではないが、これは同じscreen train分布上の診断値であり、性能改善の証拠ではない。

同じseed0 train referenceを固定したまま、別sourceの分布も再生した。seed1 train（対応seed1 checkpoint、3,892 transitions / 8,259 prefix rows）は forced 4,547、non-forced 3,712、eligible 629（全体7.62%、non-forced 16.95%、eligible transition 569）、target欠落0だった。reasonは forced 4,547、below focus 3,083、high normalized surprisal 367、rare public bucket 161、unseen public bucket 101。replay artifactは `runs/meta-specialist-public-confidence-ood/replay-seed1-train-vs-seed0-reference-v1.json`（SHA `9b2838a87371e23fba8a46ae4933c9d874025b59c9fae3ebcbaa53c074a11973`）。seed0 validation（1,085 transitions / 2,310 prefix rows）は forced 1,278、non-forced 1,032、eligible 172（全体7.45%、non-forced 16.67%、eligible transition 157）、target欠落0で、artifact SHAは `ccfe576be1e0e0f9221e982bc7db54c6e0995427f41468e9a08e8256543bd1e4` である。

seed1/validationでeligible率が約13.2%から約16.7〜17.0%へ上がるが、episode-levelでほぼ全件をloss対象にする状態ではない。これはpublic bucketとconfidenceの分布差を示す診断であり、seed間の学習再現やCABT性能改善を示さない。thresholdをこの結果で調整せず、後続manifestへ固定した `0.5` / rare `2` のままeligible外のloss denominatorが本当に除外されることを先に検証する。

再現コマンド:

```bash
PYTHONPATH=.:src .venv/bin/python scripts/measure_public_confidence_ood.py \
  --transitions runs/meta-specialist-v4-archaludon-dagger-wave6-screen-v2/screen.transitions.jsonl \
  --partition train \
  --checkpoint runs/meta-specialist-v4-archaludon-longrun-wave6-current/archaludon-training-checkpoints/seed-0/best-recurrent-bc-v4.pt \
  --checkpoint-file-sha256 9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de \
  --checkpoint-tensor-state-sha256 36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a \
  --reference runs/meta-specialist-public-confidence-ood/reference-seed0-train-v1.json \
  --reference-source-sha256 2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce \
  --card-vocabulary-size 1267 --hidden-dim 128 --embedding-dim 64 \
  --device cpu --output runs/meta-specialist-public-confidence-ood/replay-seed0-train-v1.json
```

この cross-seed 集計は threshold の最終設定ではない。実 pilot では、対戦結果を参照しない frozen base-corpus を reference にし、bucket 仕様・source SHA・rare 閾値を manifest に固定する。reference artifact が欠落・改変・privacy 欠落の場合は fail-closed（V4 unchanged）とする。

## 解釈と次の gate

weak cell で margin 低下・entropy/NLL 上昇が見えるため、公開 confidence/OOD eligibility を試す情報量はある。しかし win 側は平均遷移長も長く、loss/win 表の差は局面・seat・相手・ゲーム長の交絡を含む。これを「ATTACK を増やせば勝てる」「END が原因」と解釈しない。

次の bounded pilot は以下で固定する。

1. public bucket と confidence threshold を勝率未参照で一度だけ freeze する。
2. opponent ID / seat は train sample または component 選択の層別にだけ使う。runtime policy、checkpoint、public feature には入れない。
3. eligible 外 row は hidden context を通すが `supervision_weight=0` とし、loss denominator / effective loss mass へ入れない。
4. zero-init public residual または loss-only overlay を対応 Wave6 seed0/1 から同一固定 budget で学習し、alpha=0 の同一 base corpus control と fixed-six 24 games/seed で比較する。
5. 対応 seed 以上、両 seat 非悪化、fault 0、target/non-target metric の大幅悪化なしを同時に満たした場合だけ shadow-B へ進む。aggregate のみ正、seed 反転、seat 崩壊、OOD 欠落なら residual 系列を終了する。

## 再現境界

この資料は read-only replay と診断証跡であり、production V4、V5 sidecar、Rule v0、Champion、deck、Kaggle package を変更していない。次の実装では、まず次の RED 契約を追加する。

- `allowed=[]` かつ `stop_available=true` の effective domain は 1 とする。
- replay log-probability error は固定 fixture で `1e-5` 未満にする。
- forced row は confidence/OOD loss mass から除外するが、recurrent context は保持する。
- `opponent_id`、`seat`、`opponent_instance_id`を変更しても public bucket/score は不変にする。
- reference artifact SHA が無い場合は OOD を有効化せず V4 unchanged とする。

なお、Rule v0 の単純 action-type alpha=1 は別実験で shadow-B 43/96（alpha=0 相当 51/96）へ悪化しているため、この preflight の後に同じ prior を再 sweep しない。

## 共通reference bundleと事前登録したpolicy

seed0 self-referenceを2-seed pilotのtraining permitへ流用しないため、Wave6 seed0/seed1のtrain sourceを固定順（seed0→seed1）で束ねる `scripts/build_public_confidence_reference_bundle.py` を追加した。bundle artifactは `runs/meta-specialist-public-confidence-ood/reference-wave6-seed0-seed1-train-bundle-v1.json`、SHA `7dcf1cef7639a6a8b14ffcf6591bf3808fb7bed8edd85d82da369b3b6f511cda`、ordered source-list SHA `b21c329a2ab599ba80e02294052a19ed4d770ac0a7335fc6197f33e3567206cb` である。2 sources、7,570 transitions、16,043 prefix rows、435 buckets、forced 8,865を含む。source full-file SHAは seed0 `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce`、seed1 `2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26`。出力はbucket histogram・counts・source SHA・privacy flagsだけで、opponent/seat/policy/game/component identityは含まない。bundle builder focused testsは3 passed（single-source回帰と合わせて5 passed）。

共通bundleを参照するpolicy manifest `configs/meta_specialist/public_confidence_ood_policy_v1.json` のSHAは `ae5396b19280049d9ceb3cea2b87ceeceaf8268a8fb747a3abfc9fb394cfd697`。`min_normalized_surprisal=0.5`、rare threshold `2`、`promotion_authority=false`、`longrun_allowed=false` を固定した。

## 事前登録したpolicyとloss-mask意味論

replay結果を見て閾値を調整することを防ぐため、`configs/meta_specialist/public_confidence_ood_policy_v1.json` を固定した。manifest file SHAは `4289716b87427ea33f6d691817eda7f474d2dcc32b16593a34c674f914e36ee3`。reference artifact/source SHAは `f96062c741f55aa7382e393d5e119b68e6b3c1635df8612b8d0c299f5303b096` / `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce` と一致し、`min_normalized_surprisal=0.5`、rare threshold `2`、`focus_on_ood=true` を事前条件にした。

mask semanticsは、forced effective-domain 1 および reference未登録・confidence不足・malformed等のineligibleを `context_only` とし、GRU stateは通常どおり進めるが、supervision weightは0、loss numerator/denominatorから除外する。eligible non-forcedのみ `loss_bearing` とする。runtimeは opponent ID、seat、policy identity、hidden fieldを受け取らず、training componentの層別選択に限ってIDを使える。manifestは診断専用で `promotion_authority=false`、`longrun_allowed=false`、`status=pre_registered_diagnostic_policy_not_yet_connected_to_training` である。したがって、現在のeligible massは学習改善の証拠ではなく、次に実trainerへ接続するときの契約値である。

V4実trainerへの最小契約回帰も追加した。`tests/meta_specialist/test_recurrent_bc_v4.py::test_public_context_only_mask_is_excluded_from_trainer_denominator_and_gradient` は、eligible外stepを `supervision_weight=0` でGRU contextへ通した場合、eligible stepだけを含むsequenceとNLL・gradient・parameter updateが一致することを確認する。これによりcontext-only rowがloss denominatorへ混入しないことをfixture上で検証したが、実screen overlayをtrainerへ接続したわけではない。実データへの接続、2-seed学習、CABT評価は未実施である。

## 契約専用mask runner（学習未接続）

実screenを誤ってそのまま学習へ流さないため、`scripts/run_meta_specialist_v4_public_confidence_ood_bc.py` を契約層だけのresearch-only moduleとして追加した。sealed actor-visible rowを同じ順序で保持し、同一group/recordの行を落とさず、public scoreから `supervision_weight` を生成する。eligible外はGRU context-only、loss weight 0である。opponent ID、seat、policy identity、hidden fieldをAPIへ受け付けず、manifestのpromotion authority/longrun/privacy/loss semanticsとcommon reference bundleのartifact/source-list/source hashesをclosed schemaで検証する。

runnerは `train=True`、`training_requested=True`、CLI実行を明示的に拒否する。8 focused testsがrow topology、episode_start、hidden-context保持、closed mapping、single-source拒否、bundle SHA、training fail-closedを確認した。実screen overlayの学習、checkpoint生成、CABT評価は未実施であり、次のpilot許可条件ではない。

## 2026-08-12 追補 — 2-source共通bundleでの両seed再生

seed0 self-referenceを使った旧診断値と、2-source共通bundleを使う新診断値を混同しない。`scripts/measure_public_confidence_ood.py` を対応するWave6 checkpointへ再入力し、両seedとも同じbundle artifact SHA `7dcf1cef7639a6a8b14ffcf6591bf3808fb7bed8edd85d82da369b3b6f511cda`、ordered source-list SHA `b21c329a2ab599ba80e02294052a19ed4d770ac0a7335fc6197f33e3567206cb` を指定した。

| replay | transitions / prefixes | forced | non-forced | eligible prefixes | eligible rate (non-forced) | eligible transitions | target missing | artifact SHA |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| Wave6 seed0 train | 3,678 / 7,784 | 4,318 | 3,466 | 395 | 11.396% | 345 | 0 | `00954fa622d2c1d749efaf3239fb3b9e30f8e01d12d16a70747e360ea12045a7` |
| Wave6 seed1 train | 3,892 / 8,259 | 4,547 | 3,712 | 437 | 11.773% | 384 | 0 | `5974a7e715752691ff86ec5e5a1fae09b6db4411fe597224291a53107802dbe0` |

seed0 replayのcheckpoint descriptor SHAは `486918dc4e6d62bfa66f925a14a1133ec654304bb1db6e39a51974159e6c9051`、seed1は `6c6a5903c9efd19d2e73fe0c26d307f9b1eb692a9b2a2c6ecce05a703e770bf9` である。両方ともtarget欠落0、privacy flagsは全てfalse、policyは `min_normalized_surprisal=0.5`・rare threshold `2`・margin上限なしで同一である。common bundleでのeligible massは旧seed0-reference再生の458/629から減少したが、これはreference bucketの定義を変更したのではなく、seed0/seed1を事前に束ねたfrozen referenceへ切り替えた結果である。

この再生はmask候補とSHA整合性の診断であって、勝率改善・teacher品質・promotionの証拠ではない。次段の学習では、両seedで同じpolicy manifestとbundleを使い、eligible外をcontext-only（loss denominator外）として保持する。mask生成・train/eval・checkpoint選択を一つの実行identityへ束ね、best epochを固定budgetの成功証拠に流用しないことを必須とする。

## 2026-08-12 追補 — 実trainer接続の初回失敗と修正

common bundleのmaskを実trainerへ接続する研究専用executorをseed0で実行した。GPUは正常（RTX PRO 5000、約2.2〜2.7GiB使用）で、control armは1 epoch・74 updates完了したが、candidate armは `ValueError: training sequence contains no post-burn-in decoder rows` で停止した。initial validation NLLは `4.230611736653588`、control train NLLは `2.8550402914103508`、control validation NLLは `1.9670050386459597`、mean preclip gradient normは `3.9766413102278837`。OOM、CUDA availability、checkpoint SHA mismatchではない。

根因は、eligible prefixが一つもない完全context-only gameをcandidateの独立trainer sequenceへ渡していたこと。V4 trainerはweight0 rowをforward/contextには使うが、sequence内に一つもloss-bearing rowがない場合はzero denominatorを拒否する。修正では、eligible prefixを一つ以上持つgameだけをcontrol/candidate双方へmaterializeし、selected game内のeligible外prefixは従来通り`supervision_weight=0`で保持する。all-context-only gameは独立sequenceとして両armから除外し、selected game集合とtopologyを一致させる。

修正前にeligible gameとall-context-only gameのfixtureで本番と同じ失敗を再現した。修正後は、context-only game除外と既存full-episode topologyのtestがpassし、executor/plan suiteは`10 passed in 1.35s`、py_compile、diff-checkもpassした。初回output root `runs/meta-specialist-v4-public-confidence-ood-pilot-exec-20260812/`は不変保存し、修正版は `runs/meta-specialist-v4-public-confidence-ood-pilot-exec-rerun-20260812/`でseed0から再実行中である。これはmask設計の性能改善証拠ではなく、実trainer接続契約の修正証跡である。

### 修正版seed0完了

修正版seed0はcontrol/candidateの両armが1 epoch・68 optimizer updates完了した。report SHAは`69079b399cf7f1c979ca300a9e223b9e0a242a2afb0d2d5abd5cd761bbf85629`。

| partition | transitions | prefixes | eligible | context-only | effective loss mass |
|---|---:|---:|---:|---:|---:|
| train | 3,552 | 7,515 | 395 | 7,120 | 395.0 |
| validation | 1,077 | 2,291 | 127 | 2,164 | 127.0 |

all-context-only gameの除外でmaterialized数はcommon replay全体より減ったが、eligible massは395で不変。selected game内のeligible外prefixはweight0/context-onlyで保持している。

controlはinitial validation NLL `4.20920850694899`、last `1.9791238543714986`、elapsed 147.8167秒。best checkpoint file/tensor SHAは`37789504dc72da9cadce844d15f12e3425bb768c387838ff1fc26b61b9e01f54` / `f3f29d27f81fa070052d5c5f42bd541f0a34483f5ec44447ee78f985aceb589c`。

candidateはinitial validation NLL `2.9896370932064227`、last `2.280730761257948`、elapsed 92.9603秒。best checkpoint file/tensor SHAは`081e60caa1fb59ff577e5761a01fc17666e780c18978c8a3b9329193e263a0e9` / `f08982fd812518eadf771afac61eb5a48163004e45c1073746502a7521c07002`。

両armの学習完走は実trainerのmask接続を示すが、CABT勝率改善を示さない。seed1とfixed-six評価前にpromotion判断をしてはいけない。

## 修正版seed1完了とfixed-six最終評価

seed1 rerunは `runs/meta-specialist-v4-public-confidence-ood-pilot-exec-rerun-20260812/seed-1/` で完了した。pilot report SHAは `c722e97afde21d1e075128f18f66ecd9b98aaed167ed68c03bf0495cb6f673e1`。trainは3,832 transitions / 8,129 prefixes、eligible 437、context-only 7,692、effective loss mass 437、66 updates。validationは1,698 / 3,582、eligible 221、context-only 3,361、effective mass 221。control NLLは `4.571262818764461→2.1247019862187906`、candidate NLLは `3.1239145635940866→2.5465142668220047`。candidate best file/tensor SHAは `9d09e0b4b76430232f179bcedb8e9efcf23e5d2b9b0e8b1e5e5e74ae4a436ec7` / `2ea9bdd6028e8b66d3c71592d732ffca3a48aa999c9790ddfdbc279ee5b249c6`、controlは `2d5c144fd96c1726ccb45691376c2981ef937d5fb39e2eb8f886dc903ae730d3` / `fde74b5790f1cc10a229f67d7a41597947c6f224e55f80a7bac8172a87aba849` である。

### resume payloadと評価用checkpointの境界

既存evaluatorへ各armの`last-recurrent-bc-v4.pt`を渡すと、resume schemaにclosed V4 tensor descriptorがないため `checkpoint has no readable closed V4 tensor-state descriptor` で停止した。best checkpointのstate dictとlast payloadの`model_state`をV4 tensor hash helperで再計算したところ、seed0/1のcontrol/candidate全4 armで完全一致した。epochs=1・best epoch=0なので、今回の評価はdescriptor付きbestを`fixed-final`として使った。これは評価結果を捨てる理由ではないが、今後はexecutorがresume payloadと評価用closed checkpointを別々に出力し、evaluatorが拒否する形式を事前testで閉じるべきである。

### fixed-six protocolと結果

評価rootは `runs/meta-specialist-v4-public-confidence-ood-pilot-rerun-eval-20260812/`。subject deck SHAは `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`、held-out opponentは6件、2 games/opponent×seat、base seed `10100000`、protocol SHA `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`、evaluator SHA `6298bfe03697609141c19f2520290c602fe4a4e3c2b23f16bb2267f29c56a835`、全fault0である。CABT engineはcommon-random-number pairingを保証しないため、同一base seedでもpaired statistical testとは呼ばない。

| seed | arm | wins/24 | seat0 | seat1 | evaluation JSON SHA |
|---:|---|---:|---:|---:|---|
| 0 | matched control | 2 | 2/12 | 0/12 | `cdf55d537062e5f2aaefc38d06e65847776030915b1ed11bc90cf93b9ecc3b0c` |
| 0 | public-OOD candidate | 10 | 5/12 | 5/12 | `ebd724463132790de6241fb5fa564c6ddd790f58fe5dfcca021bdb97326a6b1d` |
| 0 | Wave6 baseline | 11 | 6/12 | 5/12 | `e446f5155455f574b3fb78d96ae4c3297a6367b7d34b97313f4b2e202c4c0cb2` |
| 1 | matched control | 7 | 3/12 | 4/12 | `7af6aa0277d2d55e353cd25e254e906c3a3450e4b8c8ddb21af04700f5c6418e5` |
| 1 | public-OOD candidate | 12 | 8/12 | 4/12 | `460d10011e40b4afc7dd9e59632b69622b412a6b26c4082aa0d921fd5fabc29a` |
| 1 | Wave6 baseline | 11 | 5/12 | 6/12 | `11e96260c2c8613a3797a4adaef23f4af497c690ab280010d73d21a5c5296390` |

aggregateはcandidate `22/48`、Wave6 `22/48`、control `9/48`。candidateはcontrolより+13勝だが、両者はall-context-only gameを除外した同一selected topologyであり、mask単独の因果効果ではない。Wave6との比較はaggregate同点で、candidate seed0が1勝下、seed1 seat1が4/12対6/12で悪化した。従って「対応seed以上・両seat非悪化・fault0」の事前gateは不合格である。

## 最終診断

このpilotは、GPU障害ではなくtrainer materialization bugを修正し、public eligibility maskを両seedの実学習へ接続できるところまで進んだ。一方、NLL低下とcontrol比改善は、Wave6に対する安定した実戦改善へ転化しなかった。public-OOD系列はこのfixed-six gateで打ち切り、shadow-B、threshold後追い調整、長時間学習、Champion変更、Kaggle提出を行わない。次objectiveはChatGPT Proレビューとcontext packの分析後に一つだけ選ぶ。
