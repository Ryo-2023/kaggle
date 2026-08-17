# self-owned failure-adapter routed source / direct-main repair and CEM (2026-08-15)

## 判定

`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。前回のself-owned failure-adapter routed sourceは、4親がsealed直下`main.py`形式だったため、生成wrapperが`payload/original_main.py`だけを探して8/8 faultとなった。これは性能結果ではなくingest contract defectである。入口形式をTDDで修正し、同じ4親を再封印したsourceは8/8 fault0でruntime promotionまで通過した。しかしP1固定CEMの独立positive／DEV転移は得られず、P1 centerを保持した。P1、root deck、BestKnown、Champion、production、submissionは不変である。

## 固定状態

- branch / HEAD: `feature/belief-guided-search` / `30cade0e5d349d6ea545f019fc411e9d53288f16`
- P1 policy SHA-256: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- root deck canonical SHA-256: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- self-owned adapter deck canonical SHA-256: `ed840b99364baa5b5cc03a3120e9d3c982d7c905e2ed8bea2b9e9d2017fa19b7`
- evaluator SHA-256: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- active heavy process: なし（run完了後に確認）

## 1. direct-main入口の修正

`src/mage_ptcg/opponent_ingest/routed_ensemble_meta_v1.py` のparent contractを次の2形式へ拡張した。

1. Kaggle wrapper形式: `main.py` + `payload/original_main.py`。従来どおりpayload入口を隔離importする。
2. self-owned sealed形式: `main.py`直下。親`main.py`を`parent_a/`／`parent_b/`へコピーし、親rootをimport rootとして隔離importする。

両形式ともentrypoint自体をstatic scanし、symlink、unsafe import、deck hash不一致をfail-closedにする。candidate/source hashへentrypoint形式も含め、単なる同一policyの名前替えを別identityとして再利用しない。追加したdirect-main regressionを含むfocused testは`6 passed`である。従来wrapper parentの5 testも同時に通過した。

旧 `runs/cg-selfowned-adapter-route-meta-20260815-a/` とそのfault smokeはquarantineし、削除・改変していない。`-b` は`runs/`全体scanを指定した途中rootであり、性能artifactとして扱わない。

## 2. source seal・smoke・promotion

対象parentは、同一root deckを持つ次の4 self-owned failure adapterである。

- `failure_adapter_public_counterpressure_v1_8e7feb2e236b`
- `failure_adapter_public_damaged_tempo_v1_da636bc0f4d4`
- `failure_adapter_public_finish_ko_v1_acbf6ca1067e`
- `failure_adapter_public_survival_retreat_v1_04160f4ad713`

最終generated rootは`runs/cg-selfowned-adapter-route-meta-20260815-c/`で、4候補をactor-visible routed ensembleへ封印した。

| artifact | SHA-256 / 結果 |
|---|---|
| generated pool | `b782c8466e0d3293cdd5a60f5a0b35492a55408a66da3098d44c8d16228ddfdf` |
| generated fresh meta | `335e36630ce6190f3804f4d696539300254dd051e8733a92c34930b6ab55f871` |
| generated split | `16d684f2152706e97a4c8e3e7770628ba86829bd89dc530b23579d85ffc4e8b2` |
| smoke summary | `e050a4b7ceb18389f25c1bd8a10fcbf22f6990edb493bd71a7c42d12e7736666` |
| promoted pool | `d3b0672ecf21ab505764e2aa5e5d4566c2af98c935a5f697d19b95eec2b36577` |
| promoted fresh meta | `3555e8743a3c4532a4d75f38fbdb217d2eea55dd11cbd0dfe5fabb12b1d17477` |
| rebound split | `c9b6c286ab21a676706f7676214e22b48a57a413940f01201b411091a18eb25a` |

P1両seat smoke（base seed `20260913`、各opponent/seat 1局）は`DONE=8/8`、fault `0`、draw `0`、P1 `4W-0D-4L`（50.0%）だった。これはruntime安全性のgateであり、性能改善の根拠ではない。promotion後もauthorityはtraining／promotion／longrun／submissionすべてfalseである。splitは`META_TRAIN=2`、`META_DEV=1`、`META_FINAL=1`で、FINALはCEMへ投入していない。

## 3. P1固定CEM

`runs/cg-selfowned-adapter-route-cem-20260815-c/`を次の条件で実行した。

- campaign seed: `20260924`
- search mode: `META_TRAIN_ALL`
- generation: `2`
- population／elite: `4／1`
- independent re-evaluation: 1 block、4 games/opponent/seat
- positive-delta gate: 有効
- 全screen／re-evaluation／DEV row: fault `0`
- manifest SHA: `6cdfe45df17b911c5b71dd6fad1f8ee15f3f735979f83e56b7a7b542ac6183e8`
- generation 0 results SHA: `1531f4068ea6745c42bbd0b0b740f68620dba7eec20012ecbf8d77a62ee4882e`
- generation 1 results SHA: `2662e68828db4c42ad67655271e0faa1ab9acae5f4e7c331d56dea4c9e4a706f`

generation 0のscreen上位 `cg-p1-cem-g00-c00-39c7de5282bc` は `5W-0D-3L`（objective `0.625`）だったが、独立再評価は候補 `6W-0D-10L`（`0.375`）対control `11W-0D-5L`（`0.6875`）、差 `-31.25pt`へ反転した。positive gateはcenterを更新せず、P1 centerを保持した。

generation 1ではcenterの独立再評価が候補 `10W-0D-6L` 対control `10W-0D-6L`（差 `0pt`）で、positive gateを満たさなかった。fresh DEVは候補 `8W-0D-8L` 対control `9W-0D-7L`、差 `-6.25pt`だった。よってこのsource compositionからP2/P3候補は得られず、META_FINALは未使用のまま保持した。

## 4. 解釈と次のゲート

direct-main対応により、self-owned sourceをrouted ensembleへ接続する再利用可能なingest方法は確立した。一方、同一P1-baseの4 failure adapterを公開状態で切り替えるだけでは、CEMの独立transferを作れなかった。したがって今回の結論は`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`であり、同じadapter親・同じroute recipe・同じCEM seedのblind retryはしない。

次は、未性能使用policy lineageまたは新規permission済みsourceを含む、相関を下げた複数runtime-safe familyの混合poolを新epochで生成する。`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を全て通過したcandidateだけを`cg_bestknown_loop_v1.py`へ接続する。

commit、push、Champion変更、Kaggle submissionは行っていない。
