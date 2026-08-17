# ChatGPT投入用補足コンテキストパック

## 対象範囲

これは、ユーザーが指定した「3:56の指示以降、現在までに実施した内容・実験結果・判断履歴を全てできるだけ詳細にまとめる」という依頼用の補足資料である。会話APIから3:56に対応する厳密なwall-clock timestampは取得できないため、3:56を作業系列の開始ラベルとして扱い、現在のworktree、artifact、SHA、実行ログから確認できる事実を記録する。

過去全体の詳細は、次の基礎packにある。本ファイルは基礎packの後に起きた内容と、3:56以降の判断を時系列に再整理したものなので、ChatGPTへは両方を渡すことを推奨する。

- 基礎pack: `docs/status/chatgpt_context_pack_2026-08-12.md`
- 本補足pack: `docs/status/chatgpt_context_pack_since_0356_2026-08-12.md`
- リポジトリroot: `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle`
- タイムゾーン: Asia/Tokyo
- 対象日: 2026-08-12

## 1. 最終目標と現状ラベル

最終目標は、Archaludonエージェントについてteacher、モデル、学習、評価、デッキを必要なら再設計し、現bestを十分な実戦評価で上回る候補を作り、seed・seat・opponentをまたいで安定する学習系を完成させること。その候補だけを長時間学習へ進める。

現在のラベルは次の通り。

| 軸 | 現状 |
|---|---|
| GPU/CUDA | 復旧済み。RTX PRO 5000 Blackwell、PyTorch 2.11.0+cu128、cuda:0が使用可能 |
| 実装 | V4、V5、strict、teacher、public OODの研究用境界とテストはかなり整備済み |
| offline NLL | 多くの短期armで低下するが、勝率とは一致しない |
| 実戦性能 | seed/seat/外部poolで反転・悪化が残り、安定改善は未達 |
| 直近の実験 | common public confidence/OOD maskのfixed-last 1 epoch pilotをseed0/1で完走し、fixed-sixを評価済み |
| 直近の判定 | candidate 22/48、Wave6 22/48で同点。seed0下振れ・seed1 seat1悪化のためpromotion gate不合格 |
| 現在の作業 | ChatGPT Proレビューに従う原因分解（評価noise、policy drift、teacher round-trip）を実施済み。shadow-C凍結と再帰/ensemble候補の準備中 |
| 長時間学習 | 不可。実戦gate未通過 |
| Champion変更 | 不可 |
| Kaggle提出 | 行っていない |

## 2. GPU障害の原因究明と復旧

### 2.1 ユーザー指示

以前はGPUが使えていたため、学習候補の探索より先に「使えない原因を最優先で特定して使えるようにする」よう指示された。その後WSL再起動、Windows再起動、av-suara停止を経て再確認した。

### 2.2 切り分けたもの

- WSL内 `nvidia-smi`
- Python `torch.cuda.is_available()`
- CUDA device名とversion
- V4 modelをCUDAへ転送できるか
- 1024²/2048² matmul smoke
- 実行中のGPU utilization、VRAM、CPU、RSS
- 同時CUDA process、残存kernel、NVML error

### 2.3 根因

根因はV4モデルが恒常的にVRAMへ収まらないことではなく、Windows host再起動が完全に反映される前のWSL GPU bridge/NVML状態と、残存CUDA process/kernel状態の不整合だった。実際のWindows再起動とWSL再起動後にGPUが復旧し、V4 smokeも通った。

詳細証拠:

- `docs/evidence/v4-gpu-access-recovery-20260812.md`
- `docs/evidence/v4-gpu-pilot-oom-20260812.md`

### 2.4 復旧後の実測

- GPU: `NVIDIA RTX PRO 5000 Blackwell`
- total memory: 48,935 MiB前後
- PyTorch: `2.11.0+cu128`
- `cuda:0`: 使用可能
- V4 model transfer: pass
- matmul smoke: pass
- 実学習VRAM: 約2.2〜2.7GiB
- 実学習GPU utilization: 約0〜16%
- 実学習CPU: 約100%

低いGPU utilizationはGPU故障ではなく、V4がrecord/prefix/GRU/TBPTTの小さい演算をPython再帰loopで処理しているため。VRAM拡張だけでは速度問題は解決しない。

## 3. 3:56以降に継続した監査と実験

### 3.1 strict disagreement

fresh strict-paired fixed-sixでcandidateは94/192、Wave6は93/192、差は+1勝/+0.52pt程度。以前に観測された+4.17ptは再現されなかった。

seed別・実装別の監査:

- seed0 eligible mass: 851 prefixes
- seed1 eligible mass: 985 prefixes
- selected target typeはほぼ13 ATTACKと14 END
- selected eligible prefixの`prefix_index`は全て0
- teacherはUniformLegalで全legal candidate logits=0、margin=0
- teacher targetへのagreementは戦略teacher品質の証拠ではない
- teacher-target-only filterは非対称で、student側の早すぎるEND等を漏らす
- complete-action log probability閾値-0.2はp約0.819以下であり緩い
- broad disagreementはprefix重複を含むので独立判断ミス率ではない

strict系列はbounded diagnosticとしては意味があるが、長時間学習へは進めない。

### 3.2 Rule v0 residual alpha=1

V4 semantic logitsへRule v0 main action type priorを加えるsession-level screenを行った。post-decode physical index置換ではなく、semantic logitsに加算して同じchosen actionをGRUへcommitする方式だった。

- PLAY 0.4、ATTACH 0.5、EVOLVE 0.6、ABILITY 0.3、ATTACK 0.2、END -1.0
- RETREAT type12とDISCARD type11は未定義で0のまま
- shadow-B alpha1: seed0 25/48、seed1 18/48、合計43/96
- shadow-B Wave6: seed0 29/48、seed1 27/48、合計56/96
- alpha0候補も51/96でWave6未満

alpha1はseed0/1とも不利なので、同じpriorを再sweepしない。

### 3.3 qualified teacher arms

R7 (`public_archaludon_cinderace_r7`)は96局62/96の強度を持つが、現manifestでは`local_eval_only`かつ`smoke_ok=false`。training-local permission対象ではないため使用禁止。

許可対象は`tomatomato_archaludon`、`lucifer19_battlecore`、`plamen06_steel`。

tomatomato 96局arm:

- 96/96 games、fault0、5,146 records
- snapshot index SHA `b5cc75c82ee321cb7841b99f80d49fd6759e56d060af435200239a45b36bc72f`
- fixed-six 24局/seedではseed0/1とも17/24、Wave6各11/24
- shadow-Bではseed0 26/48、seed1 24W/23L/1D、aggregate 50.5/96
- Wave6 shadow-B 56/96に対して-5.73pt
- pilkwangはseed0 0/8、seed1 2/8

lucifer19 48局arm:

- 48/48 games、fault0、40W/8L
- policy SHA `c4acf505565a078648844c47b865af3898d5fa75422c46a8762375dddff7f90c`
- collection SHA `1570bc1e2664fc6f60d126a6e0517cca1a2bca066976803ff954e6a6dfbe6424`
- snapshot index SHA `fca5b1d7c559d5cd6925dca4bd60c5b8e3a2ac80c949fafd6ed0cacc59bcbfd3`
- subject deck SHA `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`
- fixed-six candidate seed0 14/24、seed1 13/24
- Wave6 seed0 15/24、seed1 10/24
- aggregate candidate27/48対baseline25/48だがseed0悪化

### 3.4 outcome-weighted BC

旧trainerはepisode内のquality weight qが分子・分母で相殺されていた。旧trainer SHA `d115bd58767ca6ba45016806d5135e713b5c6e0a4a2a4ce96590b1290f307b91`。uniformとの差が数値誤差程度だったため、旧結果は性能結果ではなく実装診断とした。

修正版:

- trainer SHA `bbe8c151a78d36daeb0a7da995d54d65fef7c94892dec513d0d4610334fa4308`
- report SHA `03021ad432b7de828da1f4a4297f1c4421c7c658f3cc4931b6df22e8590aa589`
- seed0 NLL `0.5593681099→0.5019691924`
- seed1 NLL `0.5713402831→0.5183927419`
- fixed-six seed0 12/24、seed1 14/24
- Wave6 seed0 15/24、seed1 10/24
- seed0 seat1 4/12対baseline6/12

qualityは実効化されたがseed反転は残った。追加sweep停止。

### 3.5 V5 SetContext

V4 productionを変えずにV5 sidecarを作った。candidate set mean/count context、zero-init residual、STOPはV4 base global、V4 strict transfer、V5独自descriptor/loader/policy/trainer/evaluatorを実装した。

正式Wave6 base isolation:

- seed0 V5 12/24対Wave6 15/24
- seed1 V5 15/24対Wave6 10/24
- aggregate V5 27/48対Wave6 25/48
- seed0 seat0 5/12対Wave6 9/12
- fault0だが対応seed・seatgate不合格
- V5 implementation digest `8a6558579337447cc140ce98441e4bc90c55c26908eace502ab35655a475bfc4`

V5長時間化、shadow-B、head sweepは行わない。

## 4. public confidence/OOD系列

### 4.1 目的

weak matchupで学生が低confidenceまたは公開構造OODとなるprefixだけをloss-bearingにし、他のprefixは同じGRU episode contextとして通す。opponent ID、seat、非公開情報をruntime特徴へ入れず、training selectionの層別にのみ利用する。

### 4.2 replay preflight

対応Wave6 checkpointでseed0/1のsealed actor-visible transitionを再生した。

- 合計10,353 physical transitions
- 全体 margin median 1.9652
- weak cell margin median 1.8456
- weak cell entropy 0.4311
- weak cell target NLL 0.1254
- 100-transition behavior-logp replay誤差: mean `7.22e-08`、max `6.67e-07`
- target semantic class欠落: 0

exact full signatureはcross-seed交差がほぼなく、OODには粗い公開bucketを使用した。bucketはselection type/context、effective domain、prefix depth、STOP、option type set、公開entity/card-bag countを含む。

### 4.3 common reference bundle

- path `runs/meta-specialist-public-confidence-ood/reference-wave6-seed0-seed1-train-bundle-v1.json`
- artifact SHA `7dcf1cef7639a6a8b14ffcf6591bf3808fb7bed8edd85d82da369b3b6f511cda`
- ordered source-list SHA `b21c329a2ab599ba80e02294052a19ed4d770ac0a7335fc6197f33e3567206cb`
- 2 sources、7,570 transitions、16,043 prefixes、435 buckets、forced8,865
- source seed0 SHA `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce`
- source seed1 SHA `2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26`

bundleはopponent/seat/policy/game/component identityを出力しない。single-source、duplicate SHA、empty partition、canonical不一致はfail-closed。

### 4.4 fixed policy

manifest `configs/meta_specialist/public_confidence_ood_policy_v1.json`:

- file SHA `ae5396b19280049d9ceb3cea2b87ceeceaf8268a8fb747a3abfc9fb394cfd697`
- `min_normalized_surprisal=0.5`
- rare threshold `2`
- `focus_on_ood=true`
- `promotion_authority=false`
- `longrun_allowed=false`

forced effective domain 1、reference未登録、confidence不足、malformedはcontext-only。GRUは進め、loss weightは0。

### 4.5 common replay結果

| seed | train transitions/prefix | forced | non-forced | eligible prefix | eligible率(non-forced) | eligible transition | target missing | artifact SHA |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 0 | 3,678 / 7,784 | 4,318 | 3,466 | 395 | 11.396% | 345 | 0 | `00954fa622d2c1d749efaf3239fb3b9e30f8e01d12d16a70747e360ea12045a7` |
| 1 | 3,892 / 8,259 | 4,547 | 3,712 | 437 | 11.773% | 384 | 0 | `5974a7e715752691ff86ec5e5a1fae09b6db4411fe597224291a53107802dbe0` |

## 5. 初回public OOD実学習の失敗

### 5.1 固定条件

- seed0 Wave6 init checkpoint
- file SHA `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de`
- tensor SHA `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a`
- model vocab1267、hidden128、embedding64
- `cuda:0`
- torch threads2
- epoch1、patience0、lr1e-4、TBPTT8、clip1.0
- Rule v0 UniformLegal teacher、research-only
- output root `runs/meta-specialist-v4-public-confidence-ood-pilot-exec-20260812`

### 5.2 GPU観測

プロセスは約5分以上生存し、CPU約100%、GPU memory約2.7GBで動作した。OOMはなく、GPU復旧問題の再発でもない。

### 5.3 control完了

controlは1 epoch、74 optimizer updatesを完了。

- initial validation NLL `4.230611736653588`
- train NLL `2.8550402914103508`
- validation NLL `1.9670050386459597`
- mean preclip gradient norm `3.9766413102278837`
- train elapsed `150.52922673000285`秒
- best epoch 0、next epoch 1
- best file SHA `ced07914aab076a9b73d309bd42994105053f1eee8dcb341c0522357ab63d1d9`
- last file SHA `f1c9a416f2cbc24deaa6dd8f5d8c5c7fc6173c2fd6cd320d000983cab3f96fa2`

### 5.4 candidate失敗

candidate開始時に次のtraceで停止。

`execute_public_ood_pilot_seed_v1 → train_recurrent_bc_v4 → _train_epoch → sequence_weight <= 0 → ValueError("training sequence contains no post-burn-in decoder rows")`

根因は、eligible prefixが一つもないcontext-onlyゲームをcandidate sequenceとしてtrainerへ渡したこと。context-only行そのものは正しいが、全game context-onlyの独立sequenceはloss denominatorが0であり、現行trainerが拒否する。GPU、OOM、checkpoint SHA、teacher identityの問題ではない。

初回artifactは不変保存し、修正版で上書きしない。

## 6. 根因修正とテスト

### 6.1 修正

`build_masked_episode_material_v1`で、入力gameのmaskを先に検証し、eligible prefixが一つ以上あるgameだけをcontrol/candidate双方へmaterializeするよう変更した。

- selected game内のineligible prefixはweight0/context-onlyで保持
- all-context-only gameは両armから除外
- control/candidate selected game集合は一致
- transition/prefix topologyの意味は維持
- selected gameが0なら無言で学習せずfail-closed

### 6.2 最小再現test

追加fixture:

- eligible game mask `(True, False, False)`
- context-only game mask `(False, False, False)`

修正前は両gameがcandidate sequenceへ入り、本番と同じzero-loss問題を再現。修正後はcontext-only gameが除外され、eligible gameのweight `(1.0,0.0,0.0)`が保持される。

修正後:

- targeted two tests: `2 passed`
- executor + plan suite: `10 passed in 1.35s`
- py_compile: pass
- `git diff --check`: pass

### 6.3 修正版seed0 rerun

初回rootと別rootで再実行している。

- rerun root: `runs/meta-specialist-v4-public-confidence-ood-pilot-exec-rerun-20260812`
- seed0、同じbinding/common bundle/policy/teacher/model条件
- 起動時プロセス例PID `446965`（PIDは永続identityではない）
- GPU memory約2.2GB、GPU utilizationは低いがCPU再帰loopが進行

この補足pack作成時点では、修正版seed0の最終reportはまだ確認中である。完了後にmask mass、selected games、control/candidate NLL、optimizer updates、checkpoint SHAを追記する。

### 6.4 修正版seed0の完了結果

修正版seed0は両armとも正常完走した。output rootは `runs/meta-specialist-v4-public-confidence-ood-pilot-exec-rerun-20260812/seed-0/`、report SHAは `69079b399cf7f1c979ca300a9e223b9e0a242a2afb0d2d5abd5cd761bbf85629`。

mask materialization:

| partition | materialized transitions | prefixes | eligible | context-only | effective loss mass | optimizer updates |
|---|---:|---:|---:|---:|---:|---:|
| train | 3,552 | 7,515 | 395 | 7,120 | 395.0 | 68 |
| validation | 1,077 | 2,291 | 127 | 2,164 | 127.0 | — |

all-context-only gameを除外したため、common replayの全train 3,678 transitions / 7,784 prefixesよりmaterialized数は減っているが、eligible prefix数395は不変。selected game内のeligible外prefixはcontext-onlyとして残っている。control/candidateのselected sequence topologyは別SHAで管理し、同じselected game集合・同じteacher targetを使う。

control:

- initial validation NLL `4.20920850694899`
- last validation NLL `1.9791238543714986`
- elapsed `147.81667667500005`秒
- optimizer updates `68`
- best checkpoint file SHA `37789504dc72da9cadce844d15f12e3425bb768c387838ff1fc26b61b9e01f54`
- best tensor SHA `f3f29d27f81fa070052d5c5f42bd541f0a34483f5ec44447ee78f985aceb589c`
- last checkpoint file SHA `8e86524ba6ff70c820efc4518717d3c25dabbf17439e3661eadc1aed17645b8e`
- train sequence SHA `f7b3a3dc778cf921d9c90184ce72c0e24109b5bd9525f003906d8f0e13e586e1`
- validation sequence SHA `e781075e067f97aaab4254ac2b2aff7ae28a9936a5814b3ad1f99bda080f424f`

candidate:

- initial validation NLL `2.9896370932064227`
- last validation NLL `2.280730761257948`
- elapsed `92.96026462400187`秒
- optimizer updates `68`
- best checkpoint file SHA `081e60caa1fb59ff577e5761a01fc17666e780c18978c8a3b9329193e263a0e9`
- best tensor SHA `f08982fd812518eadf771afac61eb5a48163004e45c1073746502a7521c07002`
- last checkpoint file SHA `ba1ec26fa4ca5e6ea1a693851bbb9d259f3832c52da8a0495aee805d90619747`
- train sequence SHA `69833c415ff56f93c97f984dc9069135c931ca7dc6dbacb868c5418d337ea338`
- validation sequence SHA `1b3dfcaaca341df15549fda454d0ffc5862e662263d16207b90cc294ab84f0ac`

この時点で確認できるのは、mask semanticsを実trainerへ正しく接続でき、zero-loss sequence errorを解消したことだけである。candidateのNLLがcontrolより高いこと、両者の初期NLLが異なることは、同じWave6 initをロードしつつ、teacher target/mask sequenceやloss denominatorが異なるためである。実戦性能の結論はfixed-sixとseed1評価後に行う。

## 7. 現在のseed binding

### seed0

- transitions SHA `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce`
- screen SHA `9ad78bf31f41d307916b25d238544ea5060e0df9a8e16b5ca72a8e3977fc00e3`
- init file SHA `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de`
- init tensor SHA `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a`

### seed1

- transitions SHA `2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26`
- screen SHA `aed7438628ffdcb4a4d0c11a844c6ddea4a75017be44912180e3f6dc90abf1f1`
- init file SHA `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6`
- init tensor SHA `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a`

### deck identity

- root `deck.csv`: Mega Lucario/Hariyama系、SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- Archaludon subject deck: SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- lucifer subject deck: SHA `fbe6ab59992260b0d6774abed19469be315521b5ed0546de8c20f329607693e6`

root提出deckとArchaludon学習/評価deckは同一ではないので、subject結果をKaggle提出性能へ転記しない。

## 8. ChatGPTへ依頼する分析事項

1. V4でoffline NLLは下がるのにCABT勝率がseed/seatで反転する主因を、data seed、GRU trajectory amplification、semantic alias、teacher quality、deck mismatch、CABT noise、optimizer calibrationの順に評価する。
2. public OODのsurprisal 0.5、rare2、公開bucket、context-only semanticsが因果的改善へ向かうか、単なるstudent confidence filteringになっていないか分析する。
3. eligibleが一つ以上ある完全episodeだけをmaterializeし、ゲーム内eligible外をcontext-onlyに残す修正のselection biasを評価する。
4. 全context-only gameを除外する代わりに、trainer側でlossなしsequenceを許容する仕様の方が良いか、またはbase corpusとの混合が必要か判断する。
5. UniformLegal teacherでの実学習を、性能teacherではなくmask/regularization controlと扱う妥当性を確認する。
6. V5 SetContextを再開するなら、どの追加evidence（candidate permutation、seed、seat、shadow、domain interaction）が最低限必要か判断する。
7. qualified teacher 3件、public-only search/Q、weak residual、deck optimizationの次の優先順位を、失敗系列を踏まえて選ぶ。
8. fixed-six 24局/seedからshadow-Bへ進む数理的・実務的gateを設計する。
9. 長時間学習開始条件を「両seed・両seat・fault0・外部pool・worst-case degradation・action metric・Rule v0比較」で具体化する。
10. 修正版pilotがnegativeなら、どの情報を保存してpublic OOD系列を打ち切るべきか決める。

## 9. 再現コマンド

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_run_meta_specialist_v4_public_confidence_ood_pilot_exec.py \
  tests/meta_specialist/test_run_meta_specialist_v4_public_confidence_ood_pilot.py
```

期待値は修正後`10 passed`。

GPU確認:

```bash
nvidia-smi
PYTHONPATH=.:src .venv/bin/python - <<'PY'
import torch
print(torch.__version__)
print(torch.cuda.is_available())
print(torch.cuda.get_device_name(0) if torch.cuda.is_available() else None)
PY
```

common bundle/policy SHAは本文§4と§5に固定した値と比較する。既存output rootを上書きせず、seed0/1、control/candidate、初回失敗/re-runを別identityで管理する。

## 10. 判定に使ってはいけない解釈

- GPUが使えることを性能改善と呼ばない。
- NLL低下を勝率改善と呼ばない。
- fixed-six合計だけでseed再現と呼ばない。
- same checkpointを別poolで測った結果を独立学習再現と呼ばない。
- UniformLegal agreementを強teacher品質と呼ばない。
- alpha1のnegative shadow結果を再sweepの理由にしない。
- R7のlocal_eval_only artifactをtraining-local teacherに使わない。
- root提出deckとArchaludon subject deckを同一性能identityにしない。
- context-only rowをloss-bearingと誤って数えない。
- context-onlyだけの独立sequenceを、現行trainerへそのまま渡さない。
- 修正版seed0だけでpromotion/longrunへ進まない。

## 11. 現在の残課題

1. 修正版seed0 rerunのreportとcheckpointを取得。
2. seed1を同じfixed-last条件で実行。
3. control/candidate/Wave6を同一Archaludon subject deckでfixed-six評価。
4. CABT engine RNGがpairedでないことを明記した上で、seed・seat・opponent層別に比較。
5. gate不合格ならshadow-B・longrunを行わずpublic OOD系列を終了。
6. gate合格時だけshadow-Bを追加し、Rule v0との距離も測る。
7. current status、handoff、evidence、基礎packへ最終結果とSHAを追記。
8. ChatGPT分析を受け、teacher/model/search/deckの次目的を一つに絞る。

## 12. 終了条件

この作業系列は、実装が存在するだけでは完了しない。少なくとも次が必要。

- 修正版候補が両training seedで対応Wave6以上
- 両seatが非悪化
- fault0
- fixed-sixだけでなく事前凍結shadow-Bでも悪化しない
- target/non-target action metricが許容範囲
- checkpoint、dataset、teacher、deck、protocol、evaluatorのSHAが一意に保存
- candidate/controlのloss maskとtopologyが説明可能
- Rule v0、current Wave6、candidateの直接比較
- 長時間学習を開始するbudget、停止条件、resume条件が固定

これらが揃うまでは、目標は未達であり、goalをcompleteにはしない。

## 13. 3:56以降の最終更新 — seed1完了とfixed-six評価

この節は、前節作成後に完了したseed1実行、control/candidate/Wave6の同一条件評価、チェックポイント形式の境界問題、最終ゲート判定を追加する。ここまでの実験はすべて研究用で、`promotion_authority=false`、`longrun_allowed=false`、Champion変更なし、Kaggle提出なしである。

### 13.1 seed1修正版rerun

出力rootは `runs/meta-specialist-v4-public-confidence-ood-pilot-exec-rerun-20260812/seed-1/`、pilot reportは `seed-1/pilot-report.json`、report SHA-256は `c722e97afde21d1e075128f18f66ecd9b98aaed167ed68c03bf0495cb6f673e1` である。schemaは `meta-specialist-v4-public-confidence-ood-pilot-execution-v1`、statusは `RESEARCH_ONLY_COMPLETE`、CABT評価開始フラグはfalseである。

seed1のmask materializationは次の通り。

| partition | materialized transitions | prefixes | eligible | context-only | effective loss mass | optimizer updates |
|---|---:|---:|---:|---:|---:|---:|
| train | 3,832 | 8,129 | 437 | 7,692 | 437.0 | 66 |
| validation | 1,698 | 3,582 | 221 | 3,361 | 221.0 | — |

common replayのseed1 trainは3,892 transitions / 8,259 prefixesだった。all-context-only gameをcontrol/candidate双方の独立sequenceから除外したためmaterialized数は減っているが、eligible prefix mass 437は変わっていない。eligibleを一つ以上持つgame内のeligible外prefixはすべてGRU context-onlyとして残り、loss weightは0である。

seed1 control arm:

- 初期 validation complete-action NLL: `4.571262818764461`
- 最終 validation complete-action NLL: `2.1247019862187906`
- elapsed: `162.34669538399612`秒
- optimizer updates: `66`
- best checkpoint file SHA: `2d5c144fd96c1726ccb45691376c2981ef937d5fb39e2eb8f886dc903ae730d3`
- best tensor-state SHA: `fde74b5790f1cc10a229f67d7a41597947c6f224e55f80a7bac8172a87aba849`
- last resume payload SHA: `2a665b43c03874f6b8c123e0dbc7a755f23480a663d3bc1211b19de5a796383b`
- train sequence SHA: `fbe7200404cebda70e8356dba8cdaf6bf90d3950804f3f608072a7575a16e67c`
- validation sequence SHA: `d27dc73cb7bfe3e9d116dfc31669f9bc7538b133c64a531489269f628e9b9ebf`

seed1 candidate arm:

- 初期 validation complete-action NLL: `3.1239145635940866`
- 最終 validation complete-action NLL: `2.5465142668220047`
- elapsed: `106.64896614899772`秒
- optimizer updates: `66`
- best checkpoint file SHA: `9d09e0b4b76430232f179bcedb8e9efcf23e5d2b9b0e8b1e5e5e74ae4a436ec7`
- best tensor-state SHA: `2ea9bdd6028e8b66d3c71592d732ffca3a48aa999c9790ddfdbc279ee5b249c6`
- last resume payload SHA: `e225fe1217530c220af9876a39b72c0ed5c0db8cf20442ba432ad6348801f0af`
- train sequence SHA: `58455e47d02ba18f297f4f879cd7213d8948d1432147690c06d643ecb5959`
- validation sequence SHA: `55954ff447b3b8ea7a39dae7b314ff19b02f81f01c1ebe0c554af42cb56efddf`

seed0/seed1とも、学習は1 epoch固定、lr `1e-4`、TBPTT 8、gradient clip 1.0、`torch_threads=2`、Wave6対応checkpointから開始した。NLL低下は実trainer接続とoffline objectiveの進行を示すが、勝率改善を意味しない。特にcontrolの初期NLLとcandidateの初期NLLは、同じ初期tensorからロードしていても、sequence topology・teacher relabel・loss denominatorが異なるため直接比較しない。

### 13.2 evaluatorがlast resume payloadを拒否した問題

最初に各armの `last-recurrent-bc-v4.pt` を既存 `scripts/measure_v4_checkpoint_strength.py` へ渡したところ、4回とも `ValueError: checkpoint has no readable closed V4 tensor-state descriptor` でゲーム開始前に停止した。これはGPU、CABT、OOM、deck、protocol、SHA mismatchではない。`last-recurrent-bc-v4.pt` は再開用schema `meta-specialist-recurrent-bc-v4-epoch-resume-v1` のpayloadで、`model_state` と optimizer/historyを含むが、closed V4 descriptorを持たない。一方 `best-recurrent-bc-v4.pt` はevaluatorが要求するdescriptorを持つ。

この問題を隠れたcheckpoint差として扱わないため、V4 private helper `_tensor_state_sha256_v4` で各best checkpointのstate dictと各last payload内の`model_state`を再計算し、次の一致を確認した。

| arm | best tensor SHA | last payload内model_state SHA | 一致 |
|---|---|---|---|
| seed0 control | `f3f29d27f81fa070052d5c5f42bd541f0a34483f5ec44447ee78f985aceb589c` | 同じ | True |
| seed0 candidate | `f08982fd812518eadf771afac61eb5a48163004e45c1073746502a7521c07002` | 同じ | True |
| seed1 control | `fde74b5790f1cc10a229f67d7a41597947c6f224e55f80a7bac8172a87aba849` | 同じ | True |
| seed1 candidate | `2ea9bdd6028e8b66d3c71592d732ffca3a48aa999c9790ddfdbc279ee5b249c6` | 同じ | True |

今回はepochs=1、best epoch=0、best stateとfinal/last model stateが完全一致しているため、evaluatorにはdescriptor付きのbest checkpointを渡した。文書上はこれを「fixed-final = best（tensor state equalityを検証済み）」と明記し、lastを黙って評価したとは扱わない。将来のfixed-budget runnerでは、resume payloadと評価用closed checkpointを同一実行内で明示的に分ける契約を追加すべきである。

### 13.3 fixed-six評価条件

評価出力rootは `runs/meta-specialist-v4-public-confidence-ood-pilot-rerun-eval-20260812/`。全armを同じ条件で別々に実行した。CABT engine RNGにはseed setter/APIがなく、同一base seedはpaired common-random-numberを保証しないため、game-level paired/McNemarとは呼ばない。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/measure_v4_checkpoint_strength.py \
  --subject-deck-csv opponents/tomatomato_archaludon/deck.csv \
  --subject-archetype-id archaludon \
  --opponent-count 6 --games-per-seat 2 --base-seed 10100000 \
  --max-steps 2000 --output <new-output> \
  --checkpoint <best-recurrent-bc-v4.pt>
```

固定条件:

- subject deck: `opponents/tomatomato_archaludon/deck.csv`
- subject deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- opponents: `kiyotah_lucario`, `sue124_alakazam`, `skarin_dragapult`, `ozawa_crustle_v2`, `nihei_megalopunny`, `yaroslav_crustleaware_lucario`
- 6 opponents × 2 seats × 2 games = 24 games/arm
- protocol SHA: `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`
- evaluator implementation SHA: `6298bfe03697609141c19f2520290c602fe4a4e3c2b23f16bb2267f29c56a835`
- faults: 全arm 0

### 13.4 固定六結果

| training seed | arm | wins / 24 | seat0 | seat1 | fault | JSON SHA |
|---:|---|---:|---:|---:|---:|---|
| 0 | matched control（同じselected games、全row supervision） | 2/24 | 2/12 | 0/12 | 0 | `cdf55d537062e5f2aaefc38d06e65847776030915b1ed11bc90cf93b9ecc3b0c` |
| 0 | public-OOD candidate（eligible-only supervision） | 10/24 | 5/12 | 5/12 | 0 | `ebd724463132790de6241fb5fa564c6ddd790f58fe5dfcca021bdb97326a6b1d` |
| 0 | Wave6 baseline | 11/24 | 6/12 | 5/12 | 0 | `e446f5155455f574b3fb78d96ae4c3297a6367b7d34b97313f4b2e202c4c0cb2` |
| 1 | matched control（同じselected games、全row supervision） | 7/24 | 3/12 | 4/12 | 0 | `7af6aa0277d2d55e353cd25e254e906c3a3450e4b8c8ddb21af04700f5c6418e5` |
| 1 | public-OOD candidate（eligible-only supervision） | 12/24 | 8/12 | 4/12 | 0 | `460d10011e40b4afc7dd9e59632b69622b412a6b26c4082aa0d921fd5fabc29a` |
| 1 | Wave6 baseline | 11/24 | 5/12 | 6/12 | 0 | `11e96260c2c8613a3797a4adaef23f4af497c690ab280010d73d21a5c5296390` |

aggregate:

- public-OOD candidate: `22/48 = 45.83%`
- Wave6 baseline: `22/48 = 45.83%`
- matched control: `9/48 = 18.75%`
- candidate − matched control: `+13 wins / +27.08 points`
- candidate − Wave6 aggregate: `0 wins / 0.00 points`

candidateのper-opponent winsは次の通り。

| seed | kiyotah | nihei | ozawa | skarin | sue | yaroslav |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 3/4 | 0/4 | 2/4 | 2/4 | 1/4 | 2/4 |
| 1 | 1/4 | 3/4 | 3/4 | 2/4 | 1/4 | 2/4 |

candidateはmatched controlより大きく改善したが、controlもall-context-only gameを除外したselected topologyであり、Wave6 full corpusそのものではない。したがって`+13 wins`をpublic OOD mask単独の因果効果とは呼ばない。Wave6との比較ではaggregateが同点で、seed0が1勝下回り、seed1はseat0で+3勝だがseat1で-2勝である。事前ゲートは「対応seed以上、両seat非悪化、fault0」であるため、candidateは不合格である。

### 13.5 最終判定

今回のpilotで確定したこと:

1. GPUは正常で、public OOD maskをV4 trainerへ接続し、両seed・control/candidateを完走できる。
2. all-context-only gameを独立sequenceに渡すとzero-lossでtrainerが停止する。eligible gameだけをmaterializeし、内部のeligible外prefixをcontext-onlyで残す修正は必要だった。
3. public-OOD candidateはmatched selected-topology controlを大きく上回ったが、control設計とepisode selectionを含む差であり、mask単独の証明ではない。
4. Wave6とのaggregateは22/48対22/48で同点、seed0/seat1の非悪化条件を満たさない。
5. よってpublic OOD系列はfixed-six promotion gate不合格。shadow-B、追加threshold sweep、長時間学習、V5再sweep、Champion変更、Kaggle提出へ進まない。

### 13.6 現在の全体状況と目安

実装・証跡・GPU復旧は順調に進んでいる。特に、V5 sidecar、common reference、public-only privacy boundary、mask denominator、executorのzero-loss境界、seed別provenanceまで閉じた。一方、性能目標は未達で、複数系列（strict disagreement、qualified teacher BC、outcome weighting、V5 SetContext、public OOD）が「offline/NLLまたは片seedの改善はあるが、両seed・両seat・外部poolで安定しない」状態にある。したがって現状は実装面では前進、性能面では行き詰まり気味、という二軸判定が正確である。

次に必要なのは、今回の失敗をさらに同じ閾値・epoch・weightで反復することではない。ChatGPTへ本packを渡し、次の一目的を選んだ後、以下の時間感覚で再開する。

- ChatGPT context packを読ませて次の目的を絞る: 30分〜数時間（外部モデルの応答時間を除く）
- CPUで次objectiveの契約・データ被覆・privacy/seed bindingを閉じる: 半日〜1日
- 2-seed fixed-six bounded pilot: 実行自体は各seed数分〜十数分、準備・検証込みで半日〜1日
- shadow-B confirmation: gate通過時のみ追加。少なくとも半日、局数を増やすなら数時間〜1日
- 長時間学習開始まで: 次objectiveが一度でgateを通る前提でも1〜3日、失敗時は別objectiveごとに同程度。現時点で「何時間後にChampion更新」とは見積もらない。

現在の安全な目標は、`Wave6を両seed・両seatで上回る、fault0・外部pool・action metricを伴う候補を一つ作り、初めて長時間学習を許可する`こと。今回のpublic OOD candidateはこの目標の診断材料を増やしたが、達成候補ではない。

## 14. ChatGPTへ追加で依頼する最終分析

今回のseed1/fixed-six結果を踏まえ、次を特に分析してほしい。

- candidate 22/48 = Wave6 22/48 だが seed0/seed1・seat0/seat1が反転する原因を、selected-game topology、mask density、teacher relabel、GRU trajectory、CABT noiseの順に分解する。
- matched control 9/48が極端に弱いことから、controlが本当に適切な対照か、all-context-only除外と同一selected-game集合がcontrolを不利にしていないかを検証する。
- `eligible prefix = 395/437`、`effective mass`、context-only保持の意味を、episode-level selection biasとtransition-level supervised massに分けて評価する。
- best checkpointをfixed-finalとして評価したこと、resume payloadがevaluator schemaを満たさなかったことが、今後の実験identity設計へ与える影響を整理する。
- public OOD系列を完全に打ち切るか、次回は「同一full base corpusでmaskだけを変えるcontrol」にするかを決める。勝率を見た後のthreshold変更はしない。
- qualified teacher（permission済み3件）、public-only search/Q（target生成未実装）、弱相手public residual、V5 SetContext、deck identity一致のどれを次の唯一のbounded objectiveにするか選ぶ。

## 15. ChatGPT Proレビュー結果の保存と実行方針

ユーザーが別のChatGPT Proへ渡して得たレビュー（添付 `pasted-text.txt`、2026-08-12受領）を、現在の証拠へ照合した。レビューの中心結論は、GPU不足・V4容量不足・単純なデータ量不足ではなく、次の共通原因仮説である。

> 非value-basedなteacher/action hard targetを、小規模episodeへ適用し、Wave6全体をrecurrent policyとしてfine-tuneしているため、offline NLLは下がるが、初期の小さなaction差がGRU履歴を通じて増幅され、opponent・seat・training seedごとに実戦性能が反転する。

この仮説は確定事実ではないが、次の独立系列が同じ形を繰り返すことと整合する。

| arm/変更 | offline | 実戦上の観測 |
|---|---|---|
| UniformLegal strict | NLL改善 | fixed-six +0.52pt程度、seed反転 |
| tomatomato 24局 | NLL改善 | fixed-sixのみ改善、shadow-B悪化 |
| tomatomato 96局 | NLL改善 | fixed-six 17/24×2、shadow-B -5.73pt |
| empty selection扱い | NLL改善 | seed0 8/24、seed1 18/24 |
| action-balanced | NLL改善 | 両seed10/24、Wave6未満 |
| lucifer19 | NLL改善 | aggregate +2勝、seed0悪化 |
| corrected outcome weight | 重み実効化・NLL改善 | aggregate +1勝、seed0悪化 |
| V5 SetContext | NLL改善 | aggregate +2勝、seed0悪化 |
| Rule prior alpha=1 | — | shadow-B 43/96、alpha=0 51/96で不利 |
| public OOD mask | trainer接続成功 | candidate 22/48、Wave6 22/48、gate不合格 |

この反証の積み重ねから、次を禁止する。

- teacherをさらにtomatomato/lucifer/UniformLegalへ差し替えて同じV4全体fine-tuneを続ける。
- 192/384局へ単純にteacher collectionを増やすだけの試行。
- epoch、learning rate、action weight、STOP thresholdを勝率を見ながら局所sweepする。
- Rule prior alpha=1を再利用する。
- public OODのthreshold/rare/epochを結果後に変えて再試行する。
- Wave6 baseを破壊する長時間学習、Champion変更、Kaggle提出。

レビューが提案する優先順は、以下の実作業へ落とし込む。

### 15.1 まず原因分解

1. **評価ノイズ監査**: Wave6 seed0/1、tomatomato 96 candidate seed0/1など既存checkpointを異なるbase-seed blockで固定six 96局程度×3回（または現実的なbounded回数）再評価する。同一checkpoint内のvariance、opponent/seat variance、training seed差を分離する。CABT `libcg.so`はseed setterを持たず、`std::random_device` / `std::shuffle`を使うため、真のgame pairingができない場合は独立層化評価と明記する。
2. **policy drift監査**: 全candidateを同じsealed actor-visible replayへ入力し、Wave6との差をtop1/root action change、complete-action KL/JS、STOP/ATTACK/END/RETREAT、domain size、episode first divergence、hidden norm/cosine、module parameter delta、seed0/1相互JSで測る。
3. **recurrence ablation**: 同じWave6 checkpointを通常carry、complete-actionごとreset、turn開始時resetで評価し、GRU trajectory amplificationを切り分ける。resetが同等以上ならGRU拡大/TBPTT延長を止め、短期memory・明示履歴を検討する。
4. **teacher projection round-trip**: qualified teacherのphysical action→semantic target→V4 decoder→physical legal actionを全recordで監査する。semantic equivalence、root/continuation、ordered/unordered prefix、alias multiplicity、empty selection、STOP、RETREAT、duplicate key、stale/missing endpoint、min/maxを個別集計する。不一致があればteacher品質以前にconverterを修正する。

### 15.2 次の性能arm

原因分解後の本命は `frozen Wave6 + zero-init residual` とする。

```text
frozen Wave6 semantic logits + bounded residual logits = final logits
```

- Wave6 backbone/GRU/headはfreezeし、residual headだけを学習可能にする。
- residualはzero-init、logit magnitude上限、residual L2、malformed/OOD時residual=0を固定する。
- broad base replay 80〜90%をanchorとし、teacher/outcome/search rowsは10〜20%程度へ限定する。
- selected target CE + Wave6 policy KL anchor + residual L2を使う。
- full-model fine-tune、residual-only、residual+KLを同一data/updateでmatched比較する。

補助armとして、一度だけuniform logit ensembleを測る。各モデルは独立hidden stateを持ち、同じcomplete actionを全modelへcommitする。まずWave6 seed0+seed1、次にWave6＋qualified candidatesを比較する。logit scaleはsealed validationの事前固定temperatureまたはmean-center/std normalizationを使い、勝率後のweight sweepはしない。

### 15.3 value/searchへの段階移行

hard action labelの代替として、既存trajectoryからcross-fitted Monte Carlo valueを作る。episodeをtrain/value foldへ分離し、terminal outcomeへの`V(public state)`をbootstrappingなしで学習し、held-out foldで`return - V(state)`をadvantageとする。`clip(exp(beta*advantage), 0, w_max)`をfrozen residualへ適用し、単純episode outcome weightingをcontrolにする。criticやAWR単体をpromotion根拠にはしない。

本命のpublic-belief searchは、actor-visible stateからbelief determinizationを作り、各legal root actionを分岐し、Rule v0またはqualified policyでrollout、平均Qと標準誤差を得る。最初から全状態を処理せず、100〜300 rootで現在logitとのranking disagreementとQ再現性を確認する。native Kinoshita/cg searchはhidden state要求・block/SIGSEGV・binary identity問題があるため、opaque APIを無理に再利用しない。

### 15.4 deck identityとshadow-C

root `deck.csv`はMega Lucario/Hariyama系 SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、現在のArchaludon subject deckはSHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`で別物である。root deck + Rule v0、Archaludon + Wave6/ensemble、qualified teacher deck/policy、既存最強agent/deckを12〜20 opponent・両seatでbroad arena比較し、最終性能laneを先に決める。Archaludonの結果をroot提出性能へ転記しない。

shadow-Bはtomatomato arms、Rule prior、複数candidateに使われたため、`development-external`でありuntouchedではない。frozen residual/ensemble/AWR/searchを作る前に、opponent/deck/policy content hashの非重複を満たすshadow-Cをfreezeし、候補選択完了までshadow-C勝率を参照しない。

### 15.5 長時間学習gate

以下をすべて満たす候補だけlongrunを許可する。

- 同一checkpoint反復評価varianceを上回る改善。
- 2 training seedまたはensembleでdevelopment +3pt程度以上。
- untouched shadow-Cの平均差が正。
- broad 12〜20 opponentでmeta-weighted expected win/Eloが+3pt程度以上。
- 片seatが-5pt超で崩れない。
- fault0、Rule v0/current submission bestとの直接比較済み。
- 外部320〜640局程度の評価記録。

longrun後も25/50/75/100% checkpointを出し、各段階96〜192局の独立評価を行う。2回連続でbaseを下回ったら停止する。このgateは従来の「全相手非悪化」より期待値を重視するが、seed・seat・fault・privacy・合法性は緩めない。

## 16. レビュー受領後の実行状態

レビュー指示を受け、次の3つを独立サブエージェントへ割り当てた。

- evaluator noise / CABT seed setter / recurrence ablation接続監査
- policy drift replay指標の研究専用script/test
- qualified teacher projection round-trip監査

主担当はGPU、共通artifact、評価race、integration、ChatGPT packを管理する。いずれも既存V4 production、actor pool、Rule v0、提出経路を直接変更せず、専用script/test/evidenceだけで進める。作業結果はこの節の後続追補へ追加する。

## 17. ChatGPT Proレビュー後の第一回監査結果

### 17.1 policy drift smoke

研究専用の `scripts/audit_v4_policy_drift_v1.py` と `tests/meta_specialist/test_policy_drift_audit_v1.py` をTDDで追加し、Wave6 seed0/1とpublic-OOD candidate seed0/1を同一sealed actor-visible replayへ入力した。全体manifestは `docs/evidence/v4-policy-drift-audit-input-20260812.json`、詳細は `docs/evidence/v4-policy-drift-audit-20260812.md`、smoke outputは `runs/meta-specialist-v4-policy-drift-audit-smoke-20260812.json`（SHA `36a33542ebd219ce54134a8b17019ab00abe37508817c9e6b1ad53d4e90b4b17`）である。focused testsは5 passed、CABT/学習/longrun/提出は起動していない。

8 complete episodes（train 4 / validation 4、source records 217/285、policy rows 400）のbounded smoke結果:

| baseline → candidate | top1 change | root change | mean JS | hidden cosine |
|---|---:|---:|---:|---:|
| Wave6 seed0 → public OOD seed0 | 11.75% | 12.39% | 0.04262 | 0.9542 |
| Wave6 seed1 → public OOD seed1 | 9.25% | 10.14% | 0.01807 | 0.9742 |
| Wave6 seed0 → Wave6 seed1 | 9.00% | — | 0.02459 | -0.0038 |

domain 7/8/9-16でpublic OODのchange率がdomain 2より高い傾向（seed0で13.64%/13.33%/10.00%対3.67%、seed1で22.73%/26.67%/10.00%対3.67%）も観測した。ただし400 policy rowsのbounded smokeであり、勝率との因果やcatastrophic forgettingの確定証拠ではない。Wave6 seed間hidden cosineは初期化・軌跡の座標差を含むため、絶対値だけで解釈しない。次はfull common manifestまたは固定1000行程度のreplayで再集計する。

### 17.2 evaluator noise / recurrence監査

`docs/evidence/v4-eval-noise-recurrence-audit-20260812.md` に、CABT seed setterとrecurrent ablation接続点の再監査を保存した。結論は、`run_match(seed=...)` のseedはagent factoryへしか渡らず、CABT `BattleStart`へ伝播しない。native `libcg.so`公開symbolにsetterはなく、`std::random_device`/`std::shuffle`を使用する。同一deck・同一deterministic agent・同一seed `123456`を同一processで5回実行しても、replay digestとepisode stepsは全て不一致（108/83/122/152/96）だった。現行評価はgame-level pairedではなく、block×opponent×seatの独立層化評価である。McNemar、paired bootstrap、同一base seedを共通乱数とする解釈は禁止する。

V4 normal carryは、complete actionのprefix中はhiddenを固定し、commit後だけ次hiddenを保存する。action resetはpolicy recurrent stateだけをbegin_decision前にresetし、turn resetはactor-visible turn変更時だけstate-only resetする必要がある。`MetaSpecialistRuntime.reset()`をturn内に呼ぶとregistered/terminal/trace stateまで消えるため使用禁止である。2局接続smoke（Wave6 seed0、先頭opponent、各seat1）はnormal 0/2、action reset 0/2、turn reset 2/2、全fault0だったが、engine pairing不能のため性能証拠ではない。

本格noise/ablationはまだ未実行。推奨はWave6 seed0/1のnormal carryを96局/block×3 block（合計576局）で先に測り、次にaction reset・turn resetを同条件で比較すること。既存evaluatorの96局blockは約117〜120秒/arm実測、Wave6 2 seed×3 blockは約12分、3 mode全体は約36分が目安である。開始前にshadow-Cをfreezeし、評価outputを全て新規pathへ保存する。

### 17.3 teacher round-trip監査の進捗

`scripts/audit_teacher_projection_roundtrip_v1.py` と専用testを追加し、許可済み `tomatomato_archaludon` 24/96局および `lucifer19_battlecore` 48局の全9,322 recordsを実行した。9,322/9,322 recordがphysical teacher action→semantic target→V4 shared decoder→physical legal actionのround-tripにPASSし、semantic/legal mismatchは0だった。内訳はempty selection 124、END 191、RETREAT 148、duplicate semantic groups 6,593（4,438 records、最大alias11）、selected alias rows 3,128、physical exact 8,186、unordered reorder 243、semantic同一のdeterministic alias substitution 1,136。ordered実recordは0でfixtureのみPASSした。詳細は `docs/evidence/v4-teacher-projection-roundtrip-20260812.md`、JSON SHA `303fd26a6a08082f2865182782f7cfc41710f7e4861ae86c8bbfd8c7fe511d4c`。ordered/soft-mass rows未観測は残課題だが、現record範囲のconverter破損は主因と確認されなかった。

### 17.4 第一回の原因順位更新

現時点の証拠から、性能停滞の仮説順位を次のように更新する。

1. non-value hard targetを用いたfull recurrent fine-tuneと、少数の初期action差のtrajectory amplification。
2. CABT engine RNGを固定できないことによる評価noise（24局screenではseed差と分離不能）。
3. teacher physical→semantic projectionのsemantic alias/ordered coverage不足。ただしtomatomato-24のround-trip自体は全件PASSで、converter破損が主因と確定したわけではない。
4. selected game topology・mask density・control denominatorのselection bias。
5. V4 representation capacity不足。policy drift smokeでは広いdomainで変化率が高いが、容量不足単独の証拠ではない。

従って、次の性能実験はfrozen Wave6 residual（anchor KL + residual L2）またはuniform logit ensembleのどちらか一つを、noise floor/recurrence監査後に選ぶ。V4全体fine-tune、teacher追加収集、threshold/weight/epoch sweepは停止する。

## 18. Wave6同一checkpoint評価noiseの実測

ChatGPT Proレビューに従い、Wave6 seed0/1の同一closed checkpointを、同じsubject deck・同じheld-out six・96局/block・異なるbase-seed blockで3回ずつ評価した。詳細は `docs/evidence/v4-eval-noise-results-20260812.md`。CABT engine seed setterは存在しないため、全blockは独立・層化評価であり、paired testではない。

| checkpoint | block score | mean | sample SD | range |
|---|---|---:|---:|---:|
| Wave6 seed0 | 44/96, 49/96, 46/96 | 48.26% | 2.62pt | 5.21pt |
| Wave6 seed1 | 42/96, 46/96, 56/96 | 50.00% | 7.51pt | 14.58pt |

Wave6 seed0のblock JSON SHAは `503c1d9562becbbdc15d231291793b61555f7aa55a18531524cd034e46859675`、`3778244ae0ee08a7c2f1ecac714d4443178450cb40b6956d8aeb46da985a2505`、`c99236a570321921187eb85e534c17c612eb99082b683e9271db5f31372e1055`。seed1は `c2ad9bffa4db6fd13393eb4f26f06ded2e1212b14b71b2da62087ae902b61fe8`、`b99e336108cb3af92ca3d10adae6f84e7e8235b5a3662e54379c2f9d73eb57be`、`3f6621d3fabf6870fff67ee433878318f02a8fc6d7a9fb6e8889985a29c72c7a`。全fault0。

この結果により、24局screenの1〜数勝差やaggregate +1〜2勝は、同一checkpointの評価noise floorを下回る可能性が高い。seed1のblock rangeが14.58ptと大きいため、training seed差とevaluation noiseはまだ完全には分離できない。次の候補は、反復block SDを上回る改善幅を示す必要がある。tomatomato-96 candidateの反復、action/turn resetの本格ablationは未実施である。

## 19. shadow-Cの凍結

ChatGPT Proレビューの「shadow-Bは既に候補選択へ使われたためuntouchedではない。次の本命候補の学習前にshadow-Cをfreezeする」という指示に従い、勝率を参照せずにidentityだけを固定した。artifactは `runs/meta-specialist-v4-shadow-pool-20260812-c/shadow_pool_manifest.json`、SHA `52acf95a05b5b4d592fb6a2f9788051a1caedf3c0003c322cf55b09af5d84014`、詳細は `docs/evidence/v4-shadow-c-freeze-20260812.md` である。

対象は `medal_0001_77a53ffc`、`medal_0004_01501d64`、`medal_0006_07bedfff`、`medal_0010_4bf59ca5`、`medal_0015_5e60b8c7`、`medal_0016_706fa912` の6 deck identity。fixed-six、shadow-A、shadow-Bとのcanonical deck SHAは重複せず、shadow-C内deck SHAも6/6一意、freeze時点のV4 artifact ID参照もない。全candidateの`main.py`は同一generic local-eval policy SHA `6336b4d54e63c5da780860b95565e1b6b99b68926b5610995fc8b83ca62f7f10`を共有するため、独立6 policy cohortではなく、deck-identity shadowとして扱う。CABT勝率、再smoke、fault、速度、seat評価は未実施であり、候補選択後までmanifestを外部評価へ使わない。専用pytestは2 passed、py_compile、docs validator、diff-checkはPASS。

## 20. 次候補の研究専用ensemble境界

Wave6全体を再学習する前に、Wave6 seed0/seed1等の凍結policyを独立hiddenで保持し、semantic logitsをdecoder前で一様平均する研究専用adapter `src/mage_ptcg/meta_specialist/research_logit_ensemble_v1.py`（SHA `4f93716278215c2fbfcb079b800b0ba23bb04f09d121cbdf483877ce5fa296db`）とfocused test（SHA `a1b46d541c9326d51d655d7a680e9711d10f464ef6e9233661f77dfbd63ab7d3`）を追加した。normal carry、complete-action reset、turn-change resetを明示的に分離し、同じsemantic complete actionを全memberへcommitする契約をテストで固定した。関連focused suiteは10 passedで、V4 production/runtimeを変更していない。

このadapterはまだ性能候補ではない。Wave6 seed0+seed1接続とnormal/action/turn resetの小block診断は完了した。勝率を見た後のmember weight調整はしない。ensemble結果がpositiveでも、shadow-Cは同一generic policy共有のdeck-OOD診断であることを維持する。

### 20.1 ensemble/resetの実測結果

研究専用evaluator `scripts/measure_v4_research_ensemble_strength.py` を接続し、同一subject deck、held-out six、両seat、2 games/cell、base seed `10100000`、fault denominator維持で実測した。全artifactは`research_only=true`、`promotion_authority=false`、`longrun_allowed=false`である。

| arm | wins/24 | seat0 | seat1 | fault | artifact SHA |
|---|---:|---:|---:|---:|---|
| Wave6 seed0 + seed1 uniform ensemble | 11 | 6/12 | 5/12 | 0 | `14a1b04dc04c37c549013c4177143c07ca9ae029494bcde876652f793ca6f2ac` |
| Wave6 seed0 duplicate-hidden normal | 12 | 7/12 | 5/12 | 0 | `10ae4e8415efd0ea0a419a2baf1388b755a90053b5de5a2c002a4ac5c964a884` |
| Wave6 seed0 duplicate-hidden action reset | 12 | 6/12 | 6/12 | 0 | `c714d9da547f27114aace112f7672b47d77ae5a4e2e9541c10c10a397fd10f8f` |
| Wave6 seed0 duplicate-hidden turn reset | 12 | 6/12 | 6/12 | 0 | `392fbfe15508a367329f495bd66edf60e845086c91ead06d58c981db95fdc150` |
| Wave6 seed1 duplicate-hidden normal | 15 | 8/12 | 7/12 | 0 | `b0fb7a34f0ae2fde54668eee1a2c5670bade292e2bea6d929857a68dddbe1d2a` |
| Wave6 seed1 duplicate-hidden action reset | 14 | 9/12 | 5/12 | 0 | `046a1cd1cb9419c8d13c2ba49bdbe358aea4591a2e6ac4c5a462085c41b5f3d5` |
| Wave6 seed1 duplicate-hidden turn reset | 11 | 4/12 | 7/12 | 0 | `510fafda8e3f4783aedaa38b9fcab0d99aa463b92ffe7ffad4ba5d026aa49377` |

seed0では3 modeが同率、seed1ではnormal > action > turnとなった。engine RNGはpaired不可で、各cell 24局は既測noise floor以下のため因果差を確定できない。ただしreset modeがnormal carryを安定して上回る材料はなく、turn resetはseed1で悪化したため既定契約を変更しない。Wave6 seed0+seed1 ensembleも11/24で、同block Wave6単体と同等であり改善候補とは扱わない。詳細は `docs/evidence/v4-research-ensemble-reset-results-20260812.md`。

## 21. ChatGPT Proレビューに基づく frozen Wave6 residual sidecar

full V4 fine-tuneで小さなlogit差がGRU軌跡へ増幅される仮説を検証する前段として、Wave6 backboneを凍結し、研究専用のzero-init bounded residualだけを外付けできる最小契約を実装した。成果物は次の通りである。

- module: `src/mage_ptcg/meta_specialist/frozen_residual_v1.py`（SHA `f00152efff832e60194fe98526fb76ceb04696afc98d246057df9fb83d8306a5`）
- focused test: `tests/meta_specialist/test_frozen_residual_v1.py`（SHA `86e6c20758a4d0573c72a94135b3a114daed1e7ddcd80c43e2d932db3127e8a7`）
- evidence: `docs/evidence/v4-frozen-residual-sidecar-20260812.md`（SHA `3fbabf0c90dfebdae9ea9df5e02bac2946c212e43faa88fd8f590a441254eea9`）

sidecarはWave6 policyを所有・更新せず、既存policy sessionの外側でsemantic/STOP logitsへ加算する。最終Linearのweight/biasをゼロ初期化し、`max_abs_residual * tanh(raw)`で残差を`[-max_abs_residual,+max_abs_residual]`へ制限する。base logitsは`detach()`され、lossでもbaseをdetachするため、optimizerがWave6 encoder/GRU/headへ戻る経路はない。初期状態は厳密にalpha=0で、V4出力を変えない。

残差適用条件は、actor-visible V1のcanonical `model_input + step_input`から作ったcontext SHAとsemantic canonical-byte由来のaction SHAがmanifestのknown集合へ一致する場合だけである。unknown context、unknown action、型違い、feature幅不一致、STOP availabilityとbase STOP tensorの不整合は、semantic/STOPともbaseのdetached exact pass-throughへfail-closedする。contextにはopponent ID、seat、policy identity、physical serial、local action ID、private fieldを入れない。

loss helperは`CrossEntropy(detach(base)+residual,target) + anchor_kl_weight * KL(base || adjusted) + residual_l2_weight * mean(residual^2)`を返す。semantic domainのarity、STOP可否、既存semantic decoder、alias dispatcher、legality判定、GRU hidden所有権は変更しない。wrapperは`base_session.logits → sidecar adjust → existing decoder → base_session.commit`の順序を保ち、complete semantic actionのcommitをbaseへ一度だけ委譲する。

focused testは`5 passed`。確認項目はzero-init、残差bound、base no-grad、anchor KL/L2、known/OOD/malformed pass-through、semantic/STOP arity、decoder/GRU commit委譲、bad topology/weight rejectionである。py_compile、git diff --check、docs validator（`Validated 13 canonical documents.`）もPASSした。V4 production、actor_pool、trainer、CABT evaluatorは一切変更していない。

接続対象として固定済みのWave6 checkpoint provenanceは次の通りである（今回sidecarへは未ロード・未学習）。

| seed | checkpoint file SHA | tensor-state SHA |
|---:|---|---|
| 0 | `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de` | `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a` |
| 1 | `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6` | `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a` |

実学習前に、seed0/seed1を混ぜないknown context/action manifest、source JSONL/partition SHA、subject deck・opponent/protocol SHA、`max_abs_residual`、anchor KL/L2 weights、optimizer budget、`promotion_authority=false`、`longrun_allowed=false`を固定する。順序は、(1) residual trainerのeffective denominator/maskテスト、(2) seed対応tiny overfit、(3) fixed-six 24局/seed smoke、(4)同一checkpointの96局noise-aware block、(5)noise floorを超えた場合だけshadow-C、である。見積りは接続から最短1〜3日程度であり、現時点で長時間学習・Champion変更・提出を許可するものではない。

このsidecarは「性能改善候補の完成」ではなく、「Wave6を壊さず残差仮説を検証できる契約の完成」である。既測noise（seed0 SD 2.62pt、seed1 SD 7.51pt）を上回る再現性、両seed・両seat・fault0、Rule v0/現best、shadow-Cでの外部改善が揃わない限り採用しない。teacher追加収集、V4 full fine-tune、threshold/weight/epoch sweep、longrunは引き続き停止する。

## 22. frozen residual 実学習前preflight

sidecarを実データへ接続する前に、seed0/seed1のWave6 provenance、known public context/action domain、effective loss denominator、tiny-overfit実行権限を一つのdiagnostic-only contractへ閉じた。成果物は次の通りである。

- schema module: `src/mage_ptcg/meta_specialist/frozen_residual_preflight_v1.py`（SHA `feb72b61e89040598c9f53bb053e4442b6a1bde215e7100991aa1dc6cdb7dd85`）
- builder: `scripts/build_frozen_residual_preflight_manifest_v1.py`（SHA `f94a001a45160e7a23490df2a9dc203b08cc3a65cf4e9c5080dce9c35bb8d830`）
- dry-run runner: `scripts/run_frozen_residual_tiny_overfit_v1.py`（SHA `beb44f667d7a498699e52d7845043454239ca584d33894d45869c4ebe649511e`）
- evidence: `docs/evidence/v4-frozen-residual-preflight-20260812.md`（SHA `043fd224...`）
- manifest: `runs/meta-specialist-frozen-residual-preflight-20260812/known-context-action-manifest-v1.json`（SHA `7b79b57436c3d1029abf35e9895045b4f546ff1aea0db706c948c4f4aaec6689`）
- dry-run descriptor SHA: `5a85d98d58db39666e8ccb5342bf73f6e68304236e69542cca6a53e1f31b8f8d`

実データはWave6対応のseed0/seed1 `screen.transitions.jsonl` train partitionから抽出した。seed0は3,678 transitions、7,784 prefix rows、7,706 unique context IDs、1,001 unique action keys、seed1は3,892 transitions、8,259 prefix rows、8,191 unique context IDs、1,064 unique action keysである。action keyにはSTOP keyを含む。manifestはseed順 `(0,1)`、subject deck SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`、checkpoint file/tensor SHA、screen/transitions SHAを保存し、`verify_files=True`で全ローカルsource hash一致を確認した。seed transition source SHAは互いに異なり、seed domainを混ぜない。

mask schemaではcontext-only rowもrecurrent contextを必ず進め、`supervision_weight=0`としてloss denominatorから除外する。eligible rowのみpositive weightを許可し、effective loss mass・loss-bearing row数・weighted loss sumを分離集計する。context-onlyへlossを掛ける、recurrent contextを飛ばす、seed0/seed1を混ぜる、unknown schemaを受け入れる場合はfail-closedする。

`run_frozen_residual_tiny_overfit_v1.py`はmanifestを読み、training/CABT/longrun/promotionが全てfalseのdescriptorだけを生成する。`--dry-run`なしの実行はexit code 2で拒否し、model load・optimizer作成・trainer呼び出しを行わない。descriptorはoptimizer updates 0、epochs 0、seed対応checkpoint provenanceを記録する。focused preflight/runner suiteは合計19 passed（拒否パスを含む）、py_compile、docs validator、git diff --checkもPASSした。

旧artifact `58733323...` はSTOP key追加前のbuilder結果であり、現在の正典ではない。現在のmanifestは「実学習許可」ではなく「実学習前のidentity/denominator閉鎖」である。次は、residual-only trainerのtiny overfitを明示的に実装・検証し、base freeze、mask denominator、checkpoint descriptor、seed対応を満たした場合だけfixed-sixへ進む。現時点で学習/CABT/longrunは未起動である。

## 23. frozen residual sidecar trainer と self-imitation tiny の実測

preflight後の最小実行として、Wave6 backboneを更新せずsidecarだけを最適化する研究専用trainerを追加した。既存の`recurrent_bc_v4.py`は変更せず、strictにhash検証した`SpecialistModelV4`を`eval()`・`torch.no_grad()`・全parameter `requires_grad=False`で保持し、`FrozenResidualSidecarV1`のparameterだけをSGDへ渡す構成である。record groupごとのforwardとhidden carryは維持し、同一physical action内のprefixは同じincoming recurrent tokenを共有する。`supervision_weight=0`のcontext-only rowはforward/contextには残すが、loss numerator・denominator・effective massから除外する。

### 23.1 成果物と現在SHA

実装後の現在ファイルSHAは次の通りである。過去のsection 21/22に記載された実装途中のSHAは履歴値であり、現在の正確な値はこの表を優先する。

| 種別 | path | SHA-256 |
|---|---|---|
| sidecar | `src/mage_ptcg/meta_specialist/frozen_residual_v1.py` | `61416783f70214ac63aac29b108b4ab4185826f5566c61a32b861a963b94ad5a` |
| trainer | `src/mage_ptcg/meta_specialist/frozen_residual_trainer_v1.py` | `b819e5438f4ddb3e9b188cabfae2c1466a20edc3678ba787279ba38c00e4c4be` |
| tiny runner | `scripts/run_frozen_residual_tiny_overfit_v1.py` | `e336eb9ac2b14685fea5867a6b53eb4bf80b712b78d3433a9dc0903629356dc5` |
| tiny evidence | `docs/evidence/v4-frozen-residual-tiny-integration-20260812.md` | `d8327755ae80d6cc91c626241fb06b250ae2b9b8c7e6ecf3df0d8a19580e2d04` |

trainerのcheckpoint descriptorには`target_kind`と`target_manifest_sha256`を必須化した。許可済みtarget kindは`self_imitation_rule_relabel_v1`と`signed_behavior_log_probability`だけで、未知のtargetやmanifest SHA欠落は拒否する。descriptorは`training_permitted=false`、`promotion_authority=false`、`longrun_allowed=false`を強制する。

### 23.2 seed別tiny結果

実行条件はseedごとに独立、sealed train transitionの最初のgameから最大64 prefix、CPU、learning rate 0.01、最大1 update、max residual 0.25である。targetは今回、Rule teacher relabelを用いたself-imitationであり、cross-fitted outcome targetではない。

| seed | rows | context-only | loss-bearing / denominator | effective mass | base tensor SHA不変 | sidecar tensor SHA |
|---:|---:|---:|---:|---:|---|---|
| 0 | 63 | 32 | 31 / 31 | 31.0 | `true` | `1071699f2ba06cadb3547ba1e2cce38f8ee517cb622261bf3c41850b7a290af7` |
| 1 | 50 | 30 | 20 / 20 | 20.0 | `true` | `f7de0e162a5068bce4d1e983bbda103ac9788bbcb7f1111199ac898a1bbfa569` |

seed0 reportは`runs/meta-specialist-frozen-residual-preflight-20260812/tiny-seed0/seed-0-tiny-report.json`、seed1 reportは同`tiny-seed1/seed-1-tiny-report.json`に保存した。両reportは`execution=EXECUTED_BOUNDED_RESEARCH_TINY`、`evidence_class=SELF_IMITATION_INTEGRATION_ONLY`、`performance_evidence=false`、対応seedのWave6 checkpoint file/tensor SHA、preflight manifest SHA、target kind/SHA、`base_checkpoint_sha256_unchanged=true`、`sidecar_base_checkpoint_binding_verified=true`を持つ。sidecar parameter countは1,889で、両seedともzero-initからtensor stateが変化した。

この結果が示すのは、sidecar-only optimizerがgradient/updateを受ける、baseが凍結される、context-only maskが分母へ混入しない、seed対応とdescriptor provenanceが閉じている、という実装接続だけである。1 game・1 update・最大64 prefixなので、lossやsidecar SHAを勝率・teacher quality・一般化・promotionの証拠として解釈してはならない。明示的な`--execute`が必要で、flagなしはexit code 2で拒否するfail-closed契約も確認した。

### 23.3 signed loss の追加と未接続部分

self-imitation hard lossとperformance targetを混同しないため、`frozen_residual_signed_behavior_loss_v1`を別APIとして追加した。入力はbase logits、residual logits、合法domain内target index、cross-fitted signed weightで、weightは有限値かつ`[-1,1]`に制限する。

```text
adjusted = detach(base_logits) + residual_logits
imitation = mean(-signed_weight * log_softmax(adjusted)[target_index])
anchor_kl = KL(softmax(detach(base_logits)) || softmax(adjusted))
residual_l2 = mean(residual_logits ** 2)
total = imitation + kl_weight * anchor_kl + l2_weight * residual_l2
```

正のweightは実行行動を強め、負のweightはその行動のlog probabilityを下げる方向へ働く。base logitsへ勾配は戻らず、非finite logits、可変domainの不整合、範囲外weightは拒否する。focused testで正負weight、anchor/L2、base detach、範囲外weight拒否を確認した。

ただし今回の実データtiny trainerは`target_kind=self_imitation_rule_relabel_v1`であり、`RecurrentBCStepV4.target_masses`をsoft distributionとして使う性能経路や、cross-fitted signed weightを各prefixへjoinしてsigned APIへ流す実データtrainerはまだ未接続である。残差成果物は「安全な研究契約とintegration probe」であって、性能候補checkpointではない。

## 24. cross-fitted Monte-Carlo outcome target の生成

hard teacher labelではなくsealed on-policy outcomeから残差の向きを作るため、seed別にcross-fitted Monte-Carlo target manifestを生成した。episode内returnは逆順の`G_t = reward_t + discount_t * G_(t+1)`、baselineはepisode SHAに基づくdeterministic 2-foldで当該foldを除いたepisode returnのglobal mean、signed weightは`clip(G_t - baseline, -1, 1)`である。opponent ID/seatはepisode topology確認にのみ使い、manifest・runtime feature・targetへ出力しない。teacher hard selection、self-imitation、counterfactual Qとは別物である。

| seed | train episodes | transitions | episode return（勝/負/0） | signed target（正/負/0） | manifest SHA-256 |
|---:|---:|---:|---:|---:|---|
| 0 | 74 | 3,678 | 36 / 38 / 0 | 2,162 / 1,516 / 0 | `9d1a793a79f47206c36dc7e748f527fff339d7192e12b0e0cbc7201ea9c006d0` |
| 1 | 69 | 3,892 | 36 / 33 / 0 | 2,177 / 1,715 / 0 | `4725d7e6741c51b48a4cb828070753790dc9cd16c771ecf783b316f2091bc2f5` |

schema/moduleは`src/mage_ptcg/meta_specialist/cross_fitted_outcome_residual_v1.py`、builderは`scripts/build_cross_fitted_outcome_residual_manifest_v1.py`、evidenceは`docs/evidence/v4-cross-fitted-mc-residual-targets-20260812.md`である。focused schema/builder testsは6 passed、実artifactのloader再読込・SHA再検証、py_compile、git diff --checkもPASSした。全artifactは`training_permitted=false`、`promotion_authority=false`、`longrun_allowed=false`である。

このtargetは「勝ち局の全行を一律に押す」従来のepisode outcome weightより、fold外の状態難易度を差し引いたsigned方向を持つ点が異なる。ただしon-policy実行行動のoutcomeから作ったtargetであり、counterfactual action value、teacher correctness、search Qを示さない。実性能へ進む前に、transition manifestとprefix rowの厳密join、legal semantic domain/STOPを含むtarget index検証、variable domainのpaddingなしloss、effective denominatorとnegative weightの正規化、sidecar base tensor不変、target kind/source SHAのdescriptor固定をTDDで閉じる必要がある。

## 25. 現時点の判断と次のゲート

ChatGPT Proレビューの仮説「non-value hard targetによる小規模full-model recurrent fine-tuneがWave6をdriftさせ、初期action差がGRU trajectoryへ増幅する」は、policy drift、seed反転、同一checkpoint評価noise、full-model候補の外部非再現性と整合する。ただし仮説であり、残差が改善することはまだ示されていない。レビュー指示に対して、GPU原因切り分け、評価noise/CABT seed setter監査、policy drift、teacher projection round-trip、recurrence/ensemble診断、shadow-C freeze、frozen residual sidecar/preflight、seed0/1 self-imitation tiny、cross-fitted MC target生成、そして本context pack更新までを完了した。

signed targetの実data joinとseed0/1 bounded tinyまでは完了したが、ここを終えただけでは性能候補・長時間学習とは呼ばない。

1. **完了**: signed cross-fitted targetをresidual-only trainerへ実データで接続し、target join、negative weight、mask/denominator、base不変を確認した。ただし最大2 episode・1 updateのintegration evidenceである。
2. residual sidecarのhash-bound policy factory/evaluatorを追加し、known context/action、nonzero residual、OOD pass-through coverageを保存する。
3. fixed-six 24局/seedでWave6とcontrolを同一条件で比較する。CABT seed setterがないため独立層化評価とし、paired/McNemarとは呼ばない。
4. 両seed・両seat・fault0・noise floor超過が揃った場合だけ、96局×複数block、Rule v0/current best、凍結済みshadow-C（generic policy共有のdeck-OOD診断）へ進む。

現時点で禁止する操作は、同じhard teacher labelの追加収集、V4全体fine-tune sweep、threshold/rare/epochの勝率後付け調整、Rule prior再試行、Champion変更、Kaggle提出、無条件longrunである。最終的なKaggle性能向上に対する進捗は**約60%（幅55〜65%）**と評価する。実装・監査・証跡整備は約85%だが、実戦性能検証は約25%、提出・長時間化は0%で、残りはtarget接続後の実戦結果に支配される。

## 26. cross-fitted target と sealed prefix のhash-bound materialization

signed targetを通常のhard/soft BCへ誤って渡さないため、別の研究専用join module `src/mage_ptcg/meta_specialist/cross_fitted_outcome_materializer_v1.py` と focused testを追加した。materializerは対応seedの preflight known-domain manifest、sealed `screen.transitions.jsonl`、cross-fitted outcome manifestを受け取り、source file SHA、manifest file SHA、source episode SHAを再計算する。episode ID、transition SHA/index、prefix数、chosen semantic/STOPと合法domain index、record topology、seed provenanceが一致しなければfail-closedする。

現在のmodule SHAは`f6854af5a8d795770826751260ff58fba158f08bead1768ebeb3d54bab7b05c5`、focused test SHAは`1d63a0e99f6b9107ae73bbfd251d2c09797a78889eb54f9b14c9d2498ef935c9`である。最終focused testは4 passed（関連materializer/manifest suite合計10 passed）、py_compileとgit diff --checkもPASSした。

返す`RecurrentBCSequenceV4`は、同じactor-visible stateとrecord/group順を保持するcontext carrierだが、全prefixの`supervision_weight=0`である。したがって通常V4 BC trainerへ渡してもlossを発生させない。signed targetは別型`AlignedSignedResidualPrefixV1`へ保持し、sequence index、step index、episode/transition/prefix index、legal target index、signed weight、target kindだけを持つ。opponent ID、seat、policy identityは返却型・summaryへ出さない。これは「joinが正しい」ことと「性能用optimizerが接続された」ことを明示的に分離するためである。

focused testは3 passed。実データの全train partitionをseed別にmaterializeした結果は次の通り。

| seed | sequence count | sequence/prefix rows | positive signed | zero signed | negative signed | source transition SHA | target manifest SHA |
|---:|---:|---:|---:|---:|---:|---|---|
| 0 | 74 | 7,784 | 4,601 | 0 | 3,183 | `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce` | `9d1a793a79f47206c36dc7e748f527fff339d7192e12b0e0cbc7201ea9c006d0` |
| 1 | 69 | 8,259 | 4,619 | 0 | 3,640 | `2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26` | `4725d7e6741c51b48a4cb828070753790dc9cd16c771ecf783b316f2091bc2f5` |

全7,784/8,259 prefixについてtarget indexは合法domain内で、STOP alignmentとtransition SHA/indexが一致した。materialization summaryは`training_permitted=false`、`promotion_authority=false`、`longrun_allowed=false`を保持する。実行はCPU上で、学習、optimizer、CABT、policy factory、longrunは起動していない。

bounded確認としてmax_episodes=2を別途実行し、seed0は2 sequences / 301 aligned prefixes、seed1は2 / 228を得た。全screenを読み直してsource hashとmanifest再計算を行うため、CPU上の所要時間はseedあたり概ね約1分だった。これは速度見積りであり、学習時間やGPU性能の証拠ではない。

次に必要なのは、このaligned prefixをbase logits再生へ一度だけ渡し、可変domainごとにsigned lossを計算するresidual-only trainerである。既存`frozen_residual_trainer_v1.py`はself-imitation hard `target_index`経路のため、今回のmaterializationをそのまま同trainerへ入れることは禁止する。

## 27. signed residual trainer のbounded API（実data学習前）

aligned targetを通常BCへ誤接続せず、negative signed weightをhard CEへ混ぜないため、新規研究専用 `src/mage_ptcg/meta_specialist/signed_residual_trainer_v1.py` を追加した。現在SHAは`2f46948b134d18b0e7837f0e83d0c17f8b2a98af81b994513a1611ab0c9f9502`、focused test SHAは`4bf12a24a725534bf520d9891e87323f1714a050cbe386190d322fedc97ab3b2`である。

trainerは`SignedOutcomeMaterializationV1`とfrozen Wave6 base、sidecar、seed known-domainだけを受け取る。各record groupについて`base_model.forward_record_group_v4`を一回だけ`no_grad`で実行し、全prefixをcontextとして通す。legal domainはprefixごとに可変のまま扱い、STOPがあればbase STOP logitとsidecar STOP residualを追加する。paddingやordinary hard CEは使わず、`frozen_residual_signed_behavior_loss_v1`へselected target indexとsigned weightを渡す。sidecarのみSGD更新し、base parameterは全て凍結・gradientなし、前後tensor SHAはprovenanceと一致しなければ失敗する。

loss normalizerは`abs(signed_weight)`の総和を事前固定の分母として使い、positive/negative/zero weightを別集計する。zero weight rowはcontext forwardには残るがsigned loss rowには数えない。結果descriptor/summaryはtarget kind、target manifest SHA、source transition SHA、source episode SHA、base checkpoint file/tensor SHA、positive/negative mass、zero rows、base unchanged、`training_permitted=false`、`promotion_authority=false`、`longrun_allowed=false`を保存する。

新規focused 2 tests、materializer/outcome/sidecarを合わせた関連suiteは16 passed、py_compileとgit diff --checkもPASSした。fixtureではrecord group一回呼出し、base state不変、sidecar-only更新、positive/negative normalizer、variable domain契約、target kind拒否を確認した。

ただし実Wave6 checkpointをhash-bound loadしてartifactへ書くrunner、実data少数episodeでのsigned update、residual policy factory、fixed-six runtime evaluatorはまだ未実装である。従ってこの節のGREENはAPI/fixture契約の証拠であり、実戦性能や学習許可ではない。次はまず実data 2 episode程度を別artifactへ保存する bounded tiny（性能証拠false）と、base/sidecar SHA・target manifest binding・coverageを確認する。

## 28. 実Wave6 signed residual tiny（seed0/1、性能証拠ではない）

fixture APIの次に、hash-bound preflight、対応seedのWave6 checkpoint、cross-fitted outcome manifestを実データで接続するrunner `scripts/run_signed_residual_tiny_v1.py` を追加した。実行には`--execute`と明示的な`--max-episodes`が必須で、未指定・無制限の学習はexit code 2で拒否する。今回の実行はseedごとに最大2 episode、最大1 optimizer update、CPU、CABT/production actor/evaluatorなしである。runner SHAは`4eeadc35d18f9acfa2812f71d49a115ce7a49f8d85ece7a0184f9f945f3c9bc7`、focused test SHAは`d59b4e0dcfbde9db147eb6b9caf8327effee5ccd0394087b33ae027dfa525780`。runner testは2 passed、signed trainer/materializer/outcomeを含む直近のtargeted suiteは12 passed、py_compile、docs validator、git diff --checkもPASSした。

| seed | sequences/prefix rows | signed loss rows | positive mass | negative mass | zero rows | signed loss | base tensor SHA不変 | sidecar artifact SHA |
|---:|---:|---:|---:|---:|---:|---:|---|---|
| 0 | 2 / 160 | 160 | 160.0 | 0.0 | 0 | `0.1939923994` | `true` | `e512024175133257ad2a4280d0b99ca6b8f0857a96c6f821368e7066695550fc` |
| 1 | 2 / 131 | 131 | 0.0 | 131.0 | 0 | `-0.1017357097` | `true` | `1af6823337d35a4b788d0cf83b509f6f578e6810f1c4b3c38d3485a7082c0d82` |

最終のhash-bound rerunは、重複出力を混同しないよう `runs/meta-specialist-signed-residual-tiny-20260812/seed-0/seed-0-signed-tiny-report.json` と `runs/meta-specialist-signed-residual-tiny-20260812/seed-1/seed-1-signed-tiny-report.json` を正本として扱う（先行出力 `runs/meta-specialist-frozen-residual-outcome-targets-20260812/tiny-seed{0,1}/` は同一入力・同一数値の退避複製）。最終report SHAはseed0 `43423e6a288f24b5eb8af9aee991f9d14b9bb5c9a71ff2c9d1ecf7331c3ec9d8`、seed1 `337da0c405ae36550ec0993278ac8632058d736a42d14cdf3d85d0155a139317`。sidecar file SHAはseed0 `e512024175133257ad2a4280d0b99ca6b8f0857a96c6f821368e7066695550fc`、seed1 `1af6823337d35a4b788d0cf83b509f6f578e6810f1c4b3c38d3485a7082c0d82`である。両reportで、preflight SHA、target manifest SHA、source transition/episode SHA、base file/tensor SHA before/after、sidecar tensor SHA、target kind、positive/negative/zero mass、`training_permitted=false`、`promotion_authority=false`、`longrun_allowed=false`、`performance_evidence=false`を確認した。report文字列にopponent/seatは含まれない。

seed0の2 episodeはpositive signed massのみ、seed1はnegative signed massのみになった。これはcross-fit targetの局所サンプル分布を示すだけで、性能差やtargetの妥当性を示さない。signed lossがseed1で負になることも、signed log-probability objectiveの定義上あり得るが、通常のNLL低下とは比較できない。今回のtinyは1 updateかつ2 episodeで、sidecar updateが実データに届き、baseを壊さないことを示すintegration evidenceに限定する。

次の未完了は、(1) coverageとresidual nonzero/OOD pass-throughを含むhash-bound policy factory、(2) sidecar artifactを読み込む研究専用evaluator、(3) Wave6対応seed0/1・fixed-six 24局/seedの比較である。ここが未完了の間はshadow-C、longrun、Champion変更、Kaggle提出を行わない。

## 30. sidecar artifact strict loader（factory/evaluator接続前）

実data tinyが生成したsidecarをseed対応Wave6 provenanceへ結び付ける研究専用loader `src/mage_ptcg/meta_specialist/frozen_residual_loader_v1.py` を追加した。loaderはregular-file/SHA、closed artifact schema、`target_kind=signed_behavior_log_probability`、base checkpoint file/tensor SHA、preflight seedのknown context/action、`training_permitted=false` / `promotion_authority=false` / `longrun_allowed=false`、strict state_dictを検証し、未知フィールド・別seed・SHA不一致はfail-closedで拒否する。production V4、actor_pool、CABTは変更していない。

loader SHAは `b0ddadb7cb79404b4e8abcdf55c4e88eb8549a99247b3ed851d9b47c04f558ae`、focused test SHAは `36a648c692e29c40849658c3c446a0ce9fabd389e013ca3c5178e345214e7b16`、10 passed。loader単体は性能評価ではなく、次のfresh-per-game factoryとcoverage付きfixed-six evaluatorの前提契約である。

## 31. fresh-per-game residual policy factory（評価器接続前）

loaderを使い、sidecarをfactory構築時に一度だけhash検証し、各`new_policy()`でfresh base policyへ`FrozenResidualPolicyV1`をwrapする研究専用factory `src/mage_ptcg/meta_specialist/frozen_residual_factory_v1.py` を追加した。descriptorにはsidecar artifact SHA、seed、base file/tensor SHA、known context/action count、全authority falseを保存する。production actor_pool/runtime/CABT evaluatorは変更していない。

factory SHAは `ff447a4104109073556c8d419054c8408c547b62758277368f5eb2d553e64bde`、focused test SHAは `acda323ef8a5268c09d5fc49d2b1958dfcc2f9f0df05e937ff91cd0d68b19098`、4 passed。fresh object、sidecar共有、descriptor、SHA/seed mismatch fail-closedを確認した。残りはcoverage telemetry付き研究evaluatorと、まだ起動していないfixed-six 24局/seedの実測である。

## 32. residual evaluatorのdry-run契約（CABT未起動）

factory/loaderを受ける研究専用 `scripts/measure_frozen_residual_strength_v1.py` とtestを追加した。現版はCABTをimport・起動せず、sidecar/preflight/deck SHA、seed、fixed-six 6相手×2 seat×`games_per_cell<=2`、factory identity、coverage schemaを検証して`DRY_RUN_NOT_EXECUTED` descriptorだけを書く。`--execute`は「未実装」としてfail-closedで拒否する。descriptorは`engine_seed_supported=false`、`pairing=independent_stratified_not_game_paired`、authority/training/longrun/performance全false、coverage全0を固定する。

script SHAは `6180956f709811dbcd0493ccd5141d25452c81d8d53a48bd1c1a75cd4421ae6b`、test SHAは `455f12b8bd0e954e3b58227ef2ece4a22d6f1268b1a7e158b93168c75c5ac2ef`、focused 2 passed。ここまでで「評価を誤って実行しない契約」は閉じたが、勝率・coverage実測はまだ0である。
## 33. ChatGPT Pro追補 — signed residualをCABTへ流す前の再監査

最新レビューを受け、signed residualをそのまま性能candidateとして扱うことを停止し、coverage・target semantics・normalization・behavior modeを先に監査した。結論は「frozen residualの器は動くが、現行v1の学習信号とruntime gateは性能実験として未完成」である。

### 33.1 behavior mode

`scripts/run_meta_specialist_v4_dagger_screen.py::build_dagger_jobs_v4` は全screen jobへ `decoding_mode="greedy"`、`sampling_seed=0` を固定する。ActorPoolはsample時のみGumbel wrapperを使い、runtime transactionは`greedy_decode_runtime_action_v2`でsemantic argmax/tie-breakを行う。従ってscreenのchosen actionはcategorical samplingではない。保存された`behavior_log_probability`はgreedyで選ばれたactionを同じlogitsで再評価した値であり、unbiased REINFORCE/AWRやimportance ratioには使えない。signed objectiveの正式名称は`greedy outcome-signed self-imitation / signed behavior fitting`とする。

専用evidenceは`docs/evidence/v4-screen-decoding-mode-audit-20260812.md`（SHA `d3e01529bbc751ff5c3db629bbb26dc7ec8b71221a2a7a5ec25b38bab12b5f7c`）。ただしscreen JSON/JSONL自身にはdecoding mode・sampling seed・runner source SHAが再掲されていないため、既存artifactについてはsource-bound confirmationである。次回screen manifestへこれらを保存する。

### 33.2 coverage telemetryの実装

`FrozenResidualSidecarV1`へ研究専用`ResidualCoverageSnapshotV1`とcounterを追加し、exact known context/action、residual applied/nonzero、top-1 flip、STOP、action type、OOD pass-through reason、residual magnitude p50/p95を測定できるようにした。factoryへsnapshot/resetを追加し、CABT evaluator `scripts/run_frozen_residual_cabt_eval_v1.py`は各opponent×seat×gameのdeltaとseed全体をJSONへ保存する。coarse public bucketはまだruntimeへ接続せず、`coarse_public_bucket_observed=false`を明示する。

focused residual/runner/factory/loader suiteは32 passed、py_compile、git diff --checkを通過した。実装の完全SHAは以下を実ファイルから再計算し、短縮値は識別用にのみ使う。

### 33.3 measured fixed-six coverage

各seedの既存tiny sidecar（各2 episode/1 update）を、同じsubject deck、held-out 6 opponent、両seat、2 games/cellで再評価した。CABT engine seed setterは存在しないため独立層化評価である。

| seed | report | wins/24 | faults | total decisions | exact context | exact rate | residual slots / eligible slots | top-1 flips | OOD pass-through |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | `runs/meta-specialist-signed-residual-tiny-20260812/seed-0/fixed-six-24-coverage.json` | 10 | 0 | 1,346 | 12 | 0.8915% | 24 / 5,509 = 0.4357% | 0 | 1,334 / 1,346 = 99.1085% |
| 1 | `runs/meta-specialist-signed-residual-tiny-20260812/seed-1/fixed-six-24-coverage.json` | 9 | 0 | 1,358 | 12 | 0.8837% | 24 / 5,289 = 0.4538% | 0 | 1,346 / 1,358 = 99.1163% |

seed0 scoreは10/24（41.67%）、seed1は9/24（37.50%）、合計19/48（39.58%）だった。同じtiny sidecarのcoverageなし初回run（24/48）は別CABT RNG runであり、ゲーム単位の差を比較してはならない。重要な観測は勝率ではなく、exact gateがほぼ全decisionでunknown_contextとなり、残差が実質的に発火していないことである。nonzero residual slotsは両seed24だが、magnitudeは約`8.74e-7`、top-1 flipは0であり、action trajectoryを変えていない。

したがって、現行exact context SHA gateで「residualが効かなかった」とは判定しない。正しくは「新規CABT局面へほぼ適用されないため性能因果を測れなかった」である。これを理由にfull-screen signed residual学習やlongrunへ進めない。

### 33.4 target / normalization / featureの再分類

現行cross-fitted targetのbaselineはfold外episode returnのglobal meanであり、state value `V(s)`ではない。勝敗episodeの実行actionを一括して強弱付けするoutcome-signed self-imitationで、敗戦episode内の正しいaction・forced action・敗因actionを区別しない。性能本線ではactor-visible stateのみからcross-fitted `V_hat(s)`を回帰し、`A_t=G_t-V_hat_heldout(s_t)`へ置換する。

現行trainerはepisode/sequenceごとに`sum(abs(signed_weight))`で正規化するためepisode間は概ね等重みだが、episode内はprefix数とweightに比例する。multi-select prefixや長いgameを過重化し得る。次の比較は、(a) physical recordのcomplete-action log probabilityを1 sampleとする`record_normalized`、(b) episode全体の総abs advantageを固定する`episode_normalized`、(c) cross-fitted public-state value baseline、を事前固定した小さなarmに分ける。既存v1は診断controlとして保存し、改善結果を混ぜない。

sidecar inputはexact context SHAをfeatureとして使わず、現在もpublic state scalarのbounded digest、semantic action digest、domain/prefix/STOP情報を使う。一方、レビューが提案するfrozen recurrent token、candidate embedding、base logit/margin、coarse OOD embeddingはまだ接続していない。unknown bucketでzero pass-throughする現行仕様を保ち、coarse bucketをruntime gateへ接続する際は別schema・別artifact・zero-init parityを先に作る。

### 33.5 次のゲート

1. zero-init sidecarのWave6 action sequence parityを確認する。
2. exact gate coverageが約0.9%であることを踏まえ、pre-registered coarse public bucket gateのresearch-only adapterを作る（threshold/勝率後付け調整は禁止）。
3. record/episode-normalized lossを固定2 armで実装し、multi-prefix gradient massがrecord数へ依存しないことをsynthetic testで確認する。
4. state-valueを導入する場合は、episode/transition overlap 0のcross-fit、public-only input、value target/manifest SHA、`performance_evidence`/authority falseを閉じる。
5. coarse gate + normalized targetをseed0/1同一data・同一update budgetでtiny/fixed-six coverage smokeへ接続する。
6. coverageが実用的で、両seed・両seat・fault0・Wave6 noise floor超過が揃ったarmだけ96局×3 blocksへ進める。

現時点で禁止する操作は、current exact-gate signed residualのfull-screen学習、勝率を見たthreshold/weight/epoch sweep、shadow-C勝率、longrun、Champion変更、Kaggle提出である。

## 34. 最新状態の固定値（status/handoff更新後）

section 33のcoverage結果を正本として、`docs/status/current_status.md` と `docs/status/handoff.md` に同じ判定を追記した。現在の実装SHAは、作業木上の実ファイルから再計算する。coverage実測の総括は次の通りである。

| 指標 | seed0 | seed1 | 合計 |
|---|---:|---:|---:|
| games | 24 | 24 | 48 |
| wins | 10 | 9 | 19 |
| total decisions | 1,346 | 1,358 | 2,704 |
| exact context | 12 (0.8915%) | 12 (0.8837%) | 24 (0.8876%) |
| residual applied / eligible slots | 24 / 5,509 (0.4357%) | 24 / 5,289 (0.4538%) | 48 / 10,798 (0.4445%) |
| top-1 changes | 0 | 0 | 0 |
| OOD pass-through | 99.1085% | 99.1163% | 99.1124% |
| faults | 0 | 0 | 0 |

`known_public_bucket` / `coarse_public_bucket_observed` は現行runnerでは未接続であり、null/falseを出力する。したがってこの48局はcoverage診断であって、residualの勝率・一般化・promotion evidenceではない。既存の同局数runとはCABT RNGが独立で、game-level pairingは成立しない。

次の作業は、固定済みreference bundleとpublic bucket仕様を別artifactへbindしたcoarse gate adapterの契約実装である。既存V4、既存exact-gate sidecar、CABT本評価を直接変更せず、unknown/malformed pass-through、zero-init parity、bucket coverage、authority=falseをTDDで閉じる。record/episode normalizationとpublic-state value targetも同じく研究専用で、勝率を見た後付け探索は行わない。

このpackをChatGPTへ渡す際は、冒頭の要約だけでなく、section 33・34、`docs/evidence/v4-frozen-residual-coverage-audit-20260812.md`、`docs/evidence/v4-screen-decoding-mode-audit-20260812.md`、`docs/status/current_status.md`、`docs/status/handoff.md`を合わせて参照する。これらは「実装が進んだ」ことと「性能候補が成立した」ことを分離するための最新補助資料である。

## 35. coarse gate と normalization preflight の追加

速度優先の指示を受け、性能実験へ最短で進むための2つの研究専用契約を追加した。既存V4、exact-gate sidecar、CABT evaluator、学習本体は変更していない。

### coarse public bucket gate

新規 `src/mage_ptcg/meta_specialist/coarse_public_residual_gate_v1.py` は、`build_public_confidence_reference_bundle.py` が生成した2-source以上のtrain reference bundleをbundle SHA・ordered source-list SHA・bucket schema・privacy flagsへhash-bindする。runtime相当のadapterは、(1) actor-visible stateがreference既知bucket、(2) semantic action/STOPが合法かつ事前登録 residual entry、(3) residualがfiniteかつ固定bound内、の全条件を満たす場合だけdetached base logitsへ残差を加える。unknown bucket、malformed input、arity/STOP mismatchはbaseへexact pass-throughする。

adapterのcoverageはknown bucket、valid action slots、residual applied/nonzero、top-1 change、OOD reason、STOPを保存し、descriptorの`training_permitted`、`promotion_authority`、`longrun_allowed`、`performance_evidence`はfalse固定。focused testsは5 passed、module SHA `ffd3eb706d2c85fa6aeae11cd480f1d731b5925ea93a75535ab88ac9db57f849`、test SHA `01268f28052e20b14ead9c2d5fe3fea6ad61566ec90dc7f1531d1cc5c5c02746`、evidence `docs/evidence/v4-coarse-public-residual-gate-preflight-20260812.md`（SHAは実ファイルから再計算）である。これはgateの契約であり、residual tableを学習・評価した結果ではない。

### record/episode normalization

新規 `src/mage_ptcg/meta_specialist/signed_residual_normalization_v1.py` は、aligned prefix targetをphysical record単位へ集約する研究専用preflightである。`record_normalized`は同じrecordのprefix数に依存しない総abs contributionを作り、`episode_normalized`はepisodeごとの総abs massを1へ揃える。非連続prefix、recordのepisode跨ぎ、非有限/範囲外weight、未知modeはfail-closedする。focused testは3 passed、evidenceは `docs/evidence/v4-signed-residual-normalization-preflight-20260812.md`。

このmoduleは現行signed trainerへ未接続で、実data・CABT性能結果ではない。次の実験runnerではcomplete-action log probability（prefix logitsのsum）をrecord 1 sampleとして計算し、同一seed/data/update budgetの`record_normalized`対`episode_normalized`を固定比較する。state-value `V_hat(s)`はその後、public-only入力・episode/transition overlap 0・target manifest SHA・authority falseを満たす別armとして接続する。

## 36. 次に最短で行うこと

1. coarse gate adapterのzero-init/unknown pass-throughとfixed reference bundleのloadを確認（完了）。
2. record/episode normalizationのsynthetic invarianceを確認（完了）。
3. existing materializationからcomplete-action logitsを一度だけ集約するresearch-only trainerを追加し、base freeze・sidecar-only optimizer・normalizer・coverageを検証する。
4. coarse gate + normalized targetをseed0/1へ接続したtinyを、`performance_evidence=false`で生成する。
5. 24局/seedはcoverage/fault/runtime smokeとしてのみ実行し、known bucket/apply/top-1 changeが実用的でないarmを即終了する。
6. そこを通ったarmのみWave6同時評価96局×3 independent blocksへ進める。noise floorを超えなければ残差系列を終了し、public-belief root-action Q/search targetへ移る。

現時点で、exact-gate signed residualの勝率、coarse adapterの勝率、normalization preflightのlossを性能改善と呼ばない。commit、push、Champion変更、Kaggle提出、無条件longrunは行わない。

## 37. complete-action normalization trainer GREEN

既存materializationへ直接接続する前段として、研究専用の合成 complete-action trainer を追加した。`src/mage_ptcg/meta_specialist/coarse_record_residual_trainer_v1.py` は、physical record に属する複数 prefix の detached base logits と合法 semantic action domain を受け取り、record group 内の log probability を集約する。`record_normalized` は同じ record の prefix 数に依存しない総abs contribution、`episode_normalized` は episode ごとの総abs advantage mass 1 を作る。

新しい `CoarsePrefixLogitRowV1` は episode/record/prefix の整合、bucket SHA、sorted action SHA、target legality、finite base logits/weight を検証する。残差 table は zero-init、bounded `tanh`、base tensor 非更新、anchor KL、residual L2 の契約を持ち、unknown bucket/action、非有限値、prefix gap は fail-closed である。authority/training/longrun/performance は全 false。

合成 fixture の結果は、prefix 1件と4件で record mass が不変、episode-normalized の各 episode mass が1、1 update後の残差が上限以下、非有限/未知入力が拒否、の4項目すべてPASS（`4 passed`）。Evidenceは `docs/evidence/v4-coarse-complete-action-normalization-preflight-20260812.md`。

これは実Wave6 logits・coarse runtime gate・state-value `V_hat`・CABTへ未接続であり、性能結果ではない。次は同じ seed 対応 screen replayから record-group logits を一度だけ生成し、coarse bucket と `record_normalized`/`episode_normalized` を固定した性能false tinyへ接続する。その後、coverageが実用的な armのみ24局/seed smoke、さらに96局×3 independent blocksへ進める。

## 38. public-state value residual の実data bounded実験（2026-08-12）

速度優先の指示に従い、exact context/action gateのcoverage不足を解消するため、public-state value targetをcoarse public bucketへ落とし、Wave6 baseを凍結した残差だけをboundedに学習・評価した。詳細な正本は `docs/evidence/v4-public-state-value-residual-20260812.md` である。このsectionはChatGPTへ単独で渡しても、今回の最新実験の目的・設定・結果・判断が追えるように、artifact identityと数値を再掲する。

### 38.1 最終判断

結論は `RESEARCH_DIAGNOSTIC_ONLY / RESIDUAL_ARM_NOT_PROMOTABLE` である。row materialization、cross-fitted public target、coarse gate、record/episode normalization、base freeze、coverage telemetry、CABT fault処理は成立した。一方、lr=1000のcandidateは一部blockでcontrolを上回ったものの、seed1・block・seatで方向が反転し、両seed・両seat・noise-aware blockの安定gateを満たさなかった。seed0/seed1を一つの共有残差表へ混ぜてもseed1崩壊は解消しなかった。従ってlongrun、shadow-C勝率、Champion変更、Kaggle提出へは進めない。

### 38.2 固定条件と評価の限界

- subject deck: `opponents/tomatomato_archaludon/deck.csv`、SHA `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- held-out pool: `kiyotah_lucario`, `sue124_alakazam`, `skarin_dragapult`, `ozawa_crustle_v2`, `nihei_megalopunny`, `yaroslav_crustleaware_lucario`
- protocol SHA: `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`
- public reference bundle SHA: `7dcf1cef7639a6a8b14ffcf6591bf3808fb7bed8edd85d82da369b3b6f511cda`
- ordered source-list SHA: `b21c329a2ab599ba80e02294052a19ed4d770ac0a7335fc6197f33e3567206cb`
- preflight manifest SHA: `7b79b57436c3d1029abf35e9895045b4f546ff1aea0db706c948c4f4aaec6689`
- CABT engine seed setter: `false`; engine RNGは`random_device`/`shuffle`由来で、同じbase seedを渡してもgame-level common random numberは作れない。
- 従ってすべての勝率差は`independent_stratified_not_game_paired`であり、paired/McNemar/paired bootstrapの証拠として扱わない。
- 24局/seedはcoverage/fault smoke、96局/seed/blockはnoise-aware diagnosticであり、両方ともpromotion gateを自動通過させない。

### 38.3 public-state value target

`cross_fitted_public_state_value_v1.py` と builder を追加した。episode terminal rewardから逆順return `G_t = reward_t + discount_t * G_(t+1)`を計算し、同一episodeを含まないfold外のpublic bucket平均を`V_hat`として、`clip(G_t - V_hat, -1, 1)`をsigned residual targetにした。bucketがfold外に無い場合はfold外global transition-return meanへfallbackする。これはteacher hard labelでもcounterfactual Qでもなく、public-only on-policy outcomeの方向付き残差である。screen chosen actionはgreedyなので、REINFORCE/AWR sampled-behavior estimatorとは呼ばない。

| seed | episodes | transitions | public-bucket source | global fallback | target artifact | SHA |
|---:|---:|---:|---:|---:|---|---|
| 0 | 74 | 3,678 | 3,500 | 178 | `runs/meta-specialist-public-state-value-20260812/seed-0-public-state-value-v1.json` | `15809fb7fe3e473a7d3c37c223c1d803bd5feeab87bc6ccb27942963d86872ce` |
| 1 | 69 | 3,892 | 3,707 | 185 | `runs/meta-specialist-public-state-value-20260812/seed-1-public-state-value-v1.json` | `e31a2ed1e3c4949eb043b5f7e5e9671fe3560de00213420db7335dbd30cd906` |

### 38.4 sealed replay row materialization

対応seedのWave6 checkpointをstrict loadし、`representation_v4_from_step_input_v1`と`forward_record_group_v4`でtransitionを時系列再生した。record内prefixは共通incoming recurrent token、record間だけhidden carryとし、semantic action keyはcanonical sort後にlogit列とtarget indexを整合させた。seed間でrecord IDが再利用されるため内部group keyは`(episode_id, record_id)`へ修正した。

| seed | episodes | transitions | prefix rows | public buckets | row artifact SHA |
|---:|---:|---:|---:|---:|---|
| 0 | 74 | 3,678 | 7,784 | 371 | `07ff84efb01cc70ceeac8f42f32ef14a827c950cfdbd5c4f349d855ddf56bc26` |
| 1 | 69 | 3,892 | 8,259 | 375 | `f210883d51d33009c31e4ca4d1ace648895023e5e2f8470ec84628238bc16b80` |

各rowはepisode/record/prefix、public bucket、sorted legal action keys、detached base logits、legal target index、signed weight、target/value/source/checkpoint SHAを持ち、STOP availabilityとlegal domainを再検証する。opponent ID/seat/private stateはtarget featureへ入れず、authorityは全てfalseである。

### 38.5 coarse gate zero-init smoke

`coarse_public_residual_gate_v1.py` は、reference bundle既知bucket、valid semantic action、finiteかつ事前bound内residualの三条件のみで加算し、unknown/malformedはbase exact pass-throughする。semantic decode、physical alias、legality、STOP、GRU commitはbase V4へ委譲する。zero table SHAは `3d2c06c55a42c3a221eefcf518ef111aac44c9f986961ee3e817de02ea983480`。

| seed | score | decisions | known bucket | applied slots | nonzero | top1 changes | OOD pass-through |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 12/24 | 1,627 | 1,614 (99.2010%) | 4,945/6,436 (76.8334%) | 0 | 0 | 13 (0.7990%) |
| 1 | 12/24 | 1,705 | 1,692 (99.2375%) | 5,870/7,369 (79.6580%) | 0 | 0 | 13 (0.7625%) |

zero-initの勝率はbaseの独立CABT noiseであり、coarse gate性能の根拠ではない。ただしexact context/action gateの約1%未満coverage問題は、coarse bucketならruntime適用可能なcoverageへ改善できることを示した。

### 38.6 normalization trainer と lr=0.1 診断

`coarse_record_residual_trainer_v1.py` はphysical recordのprefixをcomplete-action groupとしてまとめる。`record_normalized`はprefix数に依存しないrecord mass、`episode_normalized`はepisode総abs mass=1とし、base logitsをdetach、coarse tableだけをSGD更新する。anchor KLはfloat32丸めによる微小負値を`clamp_min(0)`してvalidatorを安定化した。focused trainer testは4 passed。

lr=0.1、3 updates、record/episodeの4 armはmax residualが約`8.0e-6〜1.47e-5`に留まり、seed0 10/24または12/24、seed1 11/24または13/24だった。全armでtop1 change=0なので、これらのscore差は行動変更を伴わないCABT RNGであり性能証拠には採用しない。

### 38.7 episode-normalized lr=1000

top1変更が起こるかだけを一度診断するため、episode-normalized、3 updates、lr=1000、max residual=0.25へ固定した。これは勝率を見てthresholdを反復したものではない。

| seed | table SHA | max residual | 24局 | top1 changes | block1 96局 | block2 96局 |
|---:|---|---:|---:|---:|---:|---:|
| 0 | `c7428bbf52185939d3180de4c96c485f53c324213cf809be19044ea43f3bda20` | 0.13545 | 14/24 | 6 | 49/96 | 56/96 |
| 1 | `a86b31d5d302a809e6552a50ff6f717228a761f9eb7228783b034f08bc6b5b25` | 0.10785 | 10/24 | 3 | 51/96 | 42/96 |

24局coverageはseed0 known98.50%、applied74.45%、nonzero94.52%、OOD1.50%、top1=6、seed1 known99.36%、applied76.68%、nonzero94.93%、OOD0.64%、top1=3だった。残差自体は実際にsemantic decisionへ到達する。

noise-aware block1（base seed 10100000）は、seed0 candidate49/96 vs zero42/96、seed1 candidate51/96 vs zero45/96で、合計100/192 vs 87/192（+13勝、+6.77pt）だった。しかしopponentごとの寄与はseed間で一致せず、pairedではない。

block2（base seed 10101000）は、seed0 candidate56/96 vs zero43/96、seed1 candidate42/96 vs zero49/96で、合計98/192 vs 92/192（+6勝、+3.13pt）だった。seed1が明確に逆方向で、seat1は17/48と両seat非悪化gateを失敗した。block1+2合算はcandidate198/384 (51.56%) vs control179/384 (46.61%)、差+19勝/+4.95ptだが、独立評価かつseed/block反転のため因果改善の確定証拠ではない。

### 38.8 seed共有残差

seed0/1 rowsをnamespace付きで混ぜ、episode-normalized、3 updates、lr=1000、max residual=0.25の共有tableを作った。table SHAは `048cd017139d55a06f67b468537da4f1cec7f4ebfb8635e706a4541c3f9df15d`、16,043 prefix/7,570 records、max residual0.0788811。base seed10102000の96局/seedはseed0 50/96（seat0 32/48、seat1 18/48）、seed1 38/96（seat0 14/48、seat1 24/48）で、seed1 seat0が崩れた。共有表にするだけでは再現性問題を解けない。

### 38.9 変更・検証・残課題

追加／修正は研究専用に限定した。

- public value target/materializer、coarse public gate、coarse record trainer、seed別/shared residual trainer、research CABT parserを追加。
- record groupingを`(episode_id, record_id)`へ修正し、anchor KL/diagnosticの丸めをclamp。
- V4 production model/policy、通常V4 trainer、actor_pool、既存evaluator、submission/Championは変更していない。
- trainer/gate/public-target/normalization focused suites、py_compile、`git diff --check`、docs validatorを最終編集後に再実行する。
- 既知の失敗run（seed1 block2で誤table pathを渡したSHA mismatch）はfail-closedで採用せず、正しいzero controlを再実行した。

現時点の次の性能主線は一つに絞る必要がある。候補は qualified teacherのsoft target、public-only advantageのaction-conditioned化、またはpublic-only search/Qだが、今回の残差でlr/epoch/thresholdを追加探索しない。shadow-C勝率、longrun、Champion変更、Kaggle提出は、別候補が両seed・両seat・fault0・noise-aware gateを満たすまで禁止する。

## 40. Strong Asset Fine-Tuning主線の追加実施と384局確認（2026-08-12）

### 40.1 方針転換

ChatGPT Proレビューを受け、性能主線を「Rule v0由来の学習」「UniformLegal strict disagreement」「public confidence exact-hash residual」の継続から、`deck + agent`を一体のstrong assetとして扱うFine-Tuningへ切り替えた。Rule v0は現行提出の合法性・fallback基準として保持し、性能teacherとは扱わない。BestKnownはpair identity（deck hash、policy hash、source/permission、同一common arena）で管理し、local-eval-onlyとtraining-local permissionを混同しないことを固定した。

### 40.2 Strong Asset Census / readiness

`docs/evidence/strong-asset-census-20260812.json`/`.md`でpool 102 assets（public71/internal31、policy unique58、declared deck unique77、raw deck unique79、smoke101/1）を棚卸しした。training-localとして明示的に使える候補は現行証跡では`tomatomato_archaludon`と`lucifer19_battlecore`の2つで、それ以外の大半はlocal_eval_onlyであり、teacher trainingへ無断転用不可。R7 `public_archaludon_cinderace_r7`のfixed-six 62/96は測定上強いが、smoke=false/local_eval_onlyのためBestKnown・training・promotion・submitには使わない。

`docs/evidence/strong-asset-finetune-readiness-20260812.md`は、qualified hard teacher collectionは可能だが、external `agent(obs)`はbehavior probability/logitsを返さないためAWR ratioやV4 DAgger logitsへ直結しないこと、AWR/CRR helperは重み計算のみでtrainer未接続、ActorPool subject kindもrule/neural/V4に限定されることを記録した。最短のbounded pathとして、qualified pairから新規collection→sealed split→1 epoch outcome-weighted BC→同じdeck/common poolで広域確認を選んだ。

### 40.3 Lucifer collection / BC

`lucifer19_battlecore`をtraining-local qualified pairとして固定し、新規96局を収集した。結果は96/96 complete、fault0、records5,102、72W/24L、seat48/48。sealed splitはtrain3,601/dev748/test753。teacher manifest SHAは`d25d1d4f0cdc51207e9269d510310981039f3ebefd570f3c33ccc1c1a7023d84`、snapshot index SHAは`ea5275370d17bcc520d31aec3302ea0be054520eb92811cd5af2cdac54005ba4`、snapshot shard SHAは`2372381b53b659cc8262f4d98152a240cdde85a20048a891133255da57904135`、dataset JSONL SHAは`dd4aafe98838da2d43e493e555cc56c5c5c244bb4f1687e60c815c0219fe11b9`。

Wave6対応seed0/1からV4を1 epoch、lr=1e-4、TBPTT8、burn-in1、outcome weight（win=1/draw=2/3/loss=1/3）、66 updates/seedで学習した。validation NLLはseed0 `0.542242→0.480128`、seed1 `0.569239→0.517547`。これはoffline NLLであり性能証拠ではない。BC report SHAは`8e08c375263cb13c58fd6209a98f7b0ec96194c063cea5ded7f6c8467b905e09`。

### 40.4 96局から384局への検証

同じLucifer deck、24 opponent、両seat、engine seed setterなしの独立層化arenaで、先行96局はWave6 s0 54/96→BC s0 59/96、Wave6 s1 51/96→BC s1 54/96だった。しかし384局を一括投入した初回はqueued future誤timeoutとspawn競合が発生したため不採用とした。研究CLIへ`--timeout-seconds`を追加し、96局×4 blockを1 armずつ再実行した。全採用blockは96/96 DONE、fault0。

| arm | W-D-L / 384 | score | seat0 / seat1 | 差 |
|---|---:|---:|---:|---:|
| Lucifer BC seed0 | 211-1-172 | 55.08% | 111/192, 100/192 | Wave6 s0より-17勝/-4.30pt |
| Wave6 baseline seed0 | 228-0-156 | 59.38% | 121/192, 107/192 | control |
| Lucifer BC seed1 | 229-0-155 | 59.64% | 117/192, 112/192 | Wave6 s1より-8勝/-2.08pt |
| Wave6 baseline seed1 | 237-0-147 | 61.72% | 113/192, 124/192 | control |

96局時点の+5/+3勝は384局で消失し、両seedともcandidateがbaselineを下回った。seed1はseat0で+4勝でもseat1で-12勝となり、seat非悪化gateに失敗した。4 block全てfault0なので、不合格理由はruntimeではなく性能・seed・seat再現性である。採用artifactは`runs/meta-specialist-performance-sprint-v1/lucifer19-{bc,wave6}-seed{0,1}-broad-384-b{0,1,2,3}`にあり、各summary/ledger SHAは`docs/evidence/performance-first-strong-asset-arena-20260812.md`へ完全列挙した。

### 40.5 最終判断

判定は`STRONG_ASSET_BC_NOT_PROMOTABLE`。同一hard-label snapshotのepoch/fraction/action-weight/threshold追加sweep、longrun、BestKnown更新、shadow-C勝率、Champion変更、Kaggle提出は禁止する。現行Rule v0提出物は変更しない。次に再開する場合は同型BCの延長ではなく、qualified soft/action-probability target、public-state action-conditioned advantage、public-only search/Qのいずれか一つを仕様・target authority・評価gateまで閉じてから新規対照を作る。

### 40.6 作業状態

- 実装・監査・証跡：約92%
- Strong Asset短期性能確認：384局/armまで完了
- promotion / longrun / Champion変更 / Kaggle提出：0%
- 未完了の384局一括runは不採用artifactとして保持し、勝率表から除外
- 変更済みの正典：`docs/evidence/performance-first-strong-asset-arena-20260812.md`、`docs/status/current_status.md`、`docs/status/handoff.md`

## 41. 最新Final Sprint補足への参照

Strong Asset native ranking、BestKnown分類、AWR runner実装、target aggregation修正、tomato all-row/filtered training、broad96/broad384 arena、Wave6同条件control、SHA、GO/NO-GO、slow5/R7の未完了条件は、重複を避けるため最新packへ集約した。

詳細正本: `docs/status/chatgpt_context_pack_final_sprint_2026-08-12.md`

要点だけを再掲する。

- native top3 pooled1536: tomato `1107/1536=72.0703%`、Lucifer `1103/1536=71.8099%`、plamen `1102/1536=71.7448%`、全fault0。tomatoは暫定EvaluationBestKnownだが差は4〜5勝でnear-tie。
- GlobalBestKnownはslow5が1局15秒fail-fastで240/240 fault（DONE=0、性能順位なし）、R7が96局の診断のみ（smoke=false/local_eval_only、1536局との局数非整合）であるため未確定。slow5 artifact SHA `eb14411fbc0ee71776498ec9a26341ac5692a16bf9732ac490e76cfd6864c201`、R7診断は68/28、fault0、asset SHA `7787f191ffdfd559d26a29b8365974c7e384a21950e5d8068aef2bd1137785ac`。
- tomato all-row AWRはoffline NLLをseed0 `0.593270→0.517162`、seed1 `0.585023→0.520517`へ下げたが、broad384はseed0 `222/384`、seed1 `216/384`。同条件Wave6は`199/384`、`237/384`でseed反転、native tomatoには未達。
- filtered AWRは24局でseed0 `6/24`、seed1 `10/24`のためbroadへ延長しない。
- AWR、Lucifer hard BC、residual/coarse residualはいずれもBestKnown超え候補ではなく、longrun/Champion/Kaggle提出へ進めない。
- native poolのSubmissionEligibleは0件。現行package anchorはpool外Rule v0であり、性能BestKnownとは別管理する。
