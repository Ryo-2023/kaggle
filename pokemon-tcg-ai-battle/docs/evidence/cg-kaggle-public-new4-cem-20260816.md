# cg 公開kernel new4 source / P1 CEM（2026-08-16）

## 結論

未性能使用の公開 Kaggle kernel snapshot を追加取得し、runtime-safe な4 source poolを封印した。P1固定CEMを2通り実行したが、独立再評価・seat-safe・risk-aware gateを満たす候補はなく、DEVで確認したtop candidateもP1 controlと同値だった。判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED` である。P1 policy、root deck、BestKnown、Champion、production、submission、`opponents/`、commit、pushは変更していない。

## source provenance と隔離

新規sourceは公開ページから取得したコードを実行せず、submission outputの静的payloadをmaterializeした。全sourceは `local_eval_only`、research-onlyであり、training／promotion／submission authorityはfalseである。

| source | 公開ページ | identity / 結果 |
|---|---|---|
| Jazi Archaludon v28 | [jazivxt/archaludon-metal-gpu-v28-agents-only](https://www.kaggle.com/code/jazivxt/archaludon-metal-gpu-v28-agents-only) | dynamic helper importだけを明示importへ置換した staged main。raw main `085f399dadf5e15d0e89c13ad4288e22a727514a5b95f538877eb804f970962e`、staged main `78a30b3032764e2377b68c5146a7fa39c65aa03313c1c8a785e9b5d46c542b03`、pool policy `ffe82ab856292777147ade2cfd3e715e7c29b21d3776fae76b0bf49abf5bd0dd`、canonical deck `e615614b792208bad0e0fc33714c18f02f0f2b6b985fd7659d003289afd79552`。 |
| Kaiwalya payload B | [kaiwalyaatulraut/pok-mon-ai-battle-challenge-simulation-solution](https://www.kaggle.com/code/kaiwalyaatulraut/pok-mon-ai-battle-challenge-simulation-solution) | 公開JSON payload B（Alakazam/Dunsparce complement）を実行せず抽出。raw main `46aae79654eca7d91e9a3c840d92e38d3ac6271b052379df43dc630163f68225`、pool policy `fbf696c88036148f72d6eee8b85f9950d729c7a248ffc3acb9a5e3bcf18a71e9`、canonical deck `e3dfa3c46108328863788a6a01f9c62195dbccf15cbcf91234df5156bb363a12`。 |
| Yaminh v3 staged | [yaminh/the-pokemon-company-ai-challenge-v3](https://www.kaggle.com/code/yaminh/the-pokemon-company-ai-challenge-v3) | raw policyはisolated evaluatorで `__file__` がなく `/kaggle_simulations/agent/deck.csv` を探して2/2 fault。公開policy/deck bytesを変えず、embedded exact deck fallbackだけを追加した staged policy。raw main `e6fe1018e4eb65e3277d83f2e656e934a0bcc9718514f8f577223d7ec53116b3`、staged main `a1ee55fb473ee4cd64e5c99e048380a7975e0f148f3511a0f27fa73fa2f9e9ca`、pool policy `cc62c8709b2be10ef272813791732a818999af96f9f1d61fe60f267b0cc4210f`、canonical deck `242860e528fb748d122ce8ac4d551236728cb9e545bf069039d7d148a7d6381d`。 |
| Jazi `main_rank1.py` snapshot | 同上 | 公開archive内のstandalone `main_rank1.py` とembedded `DECK`を別snapshotとしてmaterialize。raw main `73996da97cc5d88a6b4131995c91b870a09e29ffcf7ff2b4ae4151a25507da01`、tar `a2cf97b6b3053681020f4ceedefc311e919df6000efcd87932dc2f2bb920403e`、pool policy `b56331905b06215108b89aac2387d8059a94462e74dac18951990ae05674a706`、canonical deck `ed840b99364baa5b5cc03a3120e9d3c982d7c905e2ed8bea2b9e9d2017fa19b7`。 |

raw Yaminhのfaultはquarantineし、staged Yaminhを2/2 `DONE`・fault0で再確認した。Jazi rank1はP1対4局を全て `DONE`・fault0（全てP1勝ち）で確認した。なお `main_rank1.py` の公開docstringは `aristophanivan/improved-probabilistic-agent` をsourceとして明記しており、独立作者系譜とは扱わず、distinct snapshotとしてのみ使った。

## sealed pool と split

sealed rootは `runs/cg-kaggle-kernel-meta-promoted-public-new4-20260816/`。

- pool manifest SHA: `0c734ad4802b00605cda9a8d77215a5e6dfdbb94ed0f569254286be5cfc4574c`
- fresh meta SHA: `d7a28d33e7aa6e07dafd0cf4f76e2e894f441c3655b3093448a839f7ca954f07`
- meta manifest SHA: `2faf259f965011dea4fc17b047870f1c0d890cb162bcd4807ebe8d64dc426c73`
- historical split SHA: `d6783a320a631ccbf978bbba7cf04696248f8c03b3d69f7178aac5774cc1d81b`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`

splitは候補選定とholdoutを分離した。

- `META_TRAIN`: Jazi Archaludon v28 staged、Kaiwalya payload B（2 refs）
- `META_DEV`: Yaminh v3 staged（1 ref）
- `META_FINAL`: Jazi `main_rank1.py` snapshot（1 ref）

全rowは `training_exposure=0`、`usage_boundary=local_eval_only`、`final_results_read_during_search=false`である。`META_FINAL`はCEM中に読んでいない。

## CEM results

### g02: `META_TRAIN_ALL`、低コスト診断

root `runs/cg-p1-cem-public-new4-20260816-g02/`、campaign seed `202608194`、population／elite `12／3`、2世代、screen 2 games/opponent×seat、independent re-evaluation 2回×1 game/opponent×seat、positive-delta gate、risk-aware updateを実行した。全208局（各世代104）を `DONE`・fault0で完了した。

2 games/seatでは全screen candidateがseat-collapseとなり、g00/g01ともcenter保持、DEV検証は発生しなかった。これはsource faultではなく、少数局でseat差が閾値を超えた診断結果である。

### g03: 6 games/opponent×seat相当のTRAIN block

root `runs/cg-p1-cem-public-new4-20260816-g03/`、campaign seed `202608195`、population／elite `8／2`、2世代、single TRAIN block（2 refs、6 games/opponent×seat）、independent re-evaluation 2回×2 games/opponent×seat、positive-delta gate、risk-aware updateを実行した。screen 432局、re-evaluation 96局、g01 DEV 32局の計560局を `DONE`・fault0で完了した。

- g00 screen top: candidate `5/24`、control `4/24`、`+4.1667pt`。しかし independent aggregateは candidate `2/16`、control `1/16`相当で、seat-collapse／repeat間反転が残った。
- g01 screen top: `+12.5pt`相当の候補はseat-collapse。validな候補の独立deltaは `−6.25pt`以下または0で、両世代とも `incumbent-center` を保持した。
- g01のP1 centerによる未使用DEV確認は candidate `4/16` 対 control `3/16`、fault0、差 `+6.25pt`、candidate seat rate `0.25/0.25`。これはP1 centerのDEV基準確認であり、CEM candidateの昇格根拠ではない。

### top candidate のfresh DEV diagnostic

g00 top candidate `cg-p1-cem-g00-c03-e4f3b46a61c5`（policy SHA `24f2d6b7…`）を、選定後に未使用 `META_DEV`（staged Yaminh）へ8 games/opponent×seatでpaired fresh validationした。32局は全て `DONE`・fault0で、candidate/controlとも `1/16`、`0pt`、同じseat collapse（`0.125/0.0`）だった。よって昇格根拠にはしない。

## BestKnown / provenance note

P1 policy SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck file SHAは `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` のままである。`ono-` は公開source名ではなく、self-owned package branch `agents/ono-cg-lethal-v1` と local Git identity `bfe-lab-ono` に由来する。`cg-lethal`のself-owned pair全体が公開sourceからのcopyだという意味ではない。公開root deckとのbyte/canonical一致がある場合も、policy lineageとpair provenanceを分けて記録する。

## 再現・次の条件

主要再現rootは以下である。

- `runs/cg-kaggle-kernel-meta-promoted-public-new4-20260816/`
- `runs/cg-p1-cem-public-new4-20260816-g02/`
- `runs/cg-p1-cem-public-new4-20260816-g03/`
- `runs/cg-p1-cem-public-new4-dev-validation-g00-c03-20260816-retry/`

次は同じ4-source poolをblind retryせず、runtime smoke候補と性能holdoutを分離した新source epochを作る。CEM candidateは `legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL → cg_bestknown_loop_v1.py` の順で扱う。BestKnown／Champion変更、deck phase、commit、push、Kaggle提出は行わない。
