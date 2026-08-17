# Self-owned failure-conditioned adapter source / CEM 証跡（2026-08-15）

## 結論

P1のactor-visible失敗面から、expert labelや相手の非公開情報を使わずに新しいself-owned opponent sourceを生成する方法を実装し、CABTへ接続した。sourceのlegality・static safety・bounded runtime smokeは通過したが、P1固定CEMでは独立positiveかつseat-safeな更新候補が得られず、`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL` と判定する。P1、root deck、BestKnown、Champion、production、submissionは変更していない。

今回のsourceは「P1の改善候補」ではなく、P1を評価するための新しいlocal-eval-only opponent群である。P1のterminal outcomeはtrigger familyを決める根拠として集約的に参照したが、action label、expert label、private opponent field、teacher datasetは生成物へ保存していない。

## 実装

- adapter本体: `src/mage_ptcg/opponent_ingest/self_owned_failure_adapter_v1.py`
- seal CLI: `scripts/generate_failure_adapter_meta_v1.py`
- smoke後のsplit再bind: `scripts/rebind_failure_adapter_split_v1.py`
- focused test: `tests/test_self_owned_failure_adapter_v1.py`
- recipe: `FAILURE_CONDITIONED_PUBLIC_COUNTERPRESSURE_V1`

各policyはsealed P1 scorerへ委譲し、次のいずれか1つの公開状態条件だけをbounded bonusとして追加する。

| variant | 公開状態条件 |
|---|---|
| `public_finish_ko_v1` | 相手activeを可視damageでKOでき、自己activeが極端に傷んでいないときATTACKを優先 |
| `public_survival_retreat_v1` | 自己activeのHP比が低く、energy付きbenchがあるときRETREATを優先 |
| `public_counterpressure_v1` | 自己activeが一定以上damageを受け、相手activeへ非致死ATTACKが可能なとき圧力を優先 |
| `public_damaged_tempo_v1` | 相手activeの可視damage比が高く、非致死ATTACKが可能なときtempoを優先 |

全候補は同じroot deckを使うが、policy wrapper SHAは4件すべて新規である。base P1 policyは性能使用済みであるため、freshnessは「親が未使用」ではなく、新しいpolicy/deck pair identityとruntime未使用で記録した。

## source seal と smoke

入力はP1 package（policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）。生成rootは `runs/cg-failure-adapter-meta-v1-20260815/` である。

- 4候補、exact 60、公式card ID、ACE SPEC exactly one、static findings 0。
- pool SHA: `fa01fb4882f6bbd4e9569a262430b8cdf4def47eef69421c68e374d2c58bfd28`
- fresh meta SHA: `9bc0213edadd941d9c348b9cc758bc8151b6862209b89a1215e2c24d0427ff80`
- intake split SHA: `f2e5f8b7370b8d0da8774c9c6291424a4f34fa1ae9002095faf09dc4fe15aec4`
- 初期poolは全件 `smoke_ok=false` とし、CEMへ直接渡していない。

P1をcandidate packageとして4候補へ両seat各1局のbounded smoke（base seed `20260895`、8局）を実行した。

- 8/8 completed、fault 0、draw 0、DONE 8。
- P1の結果は6 win / 2 loss（score rate 75%）。これはsourceの強さではなく、smoke plumbingの結果である。
- smoke summary SHA: `423f18280cb9f6c9edc452048ae07cb36278078586414ddcf8dc35fb0d71757d`

fault-free promotion後のrootは `runs/cg-failure-adapter-meta-promoted-v1-20260815/` である。

- promoted pool SHA: `369daf3ff9db77361734e52fb41dab9ec45daffd8f73e30c853882e9b6c91892`
- promoted fresh meta SHA: `723d0b6b97b36db992cf62f7d89ebd6de70380a2293fdfca1f40fc84f532c546`
- rebound split SHA: `f2bd6deadea48ab0e91e6aa642f135b2780a67f5ceedcc321a94c71a1146944a`
- `load_weekend_split` によるsource hash・smoke・P1 binding検証はPASS。

## P1固定CEM

`runs/cg-failure-adapter-cem-v1-20260815/` で、promoted poolをCEMのopponent sourceとして使用した。設定は次のとおりである。

- `META_TRAIN=2 / META_DEV=1 / META_FINAL=1`
- P1 source/control固定、population 12、elite 3、2世代
- campaign seed `20260901`
- initial scale fraction `0.2`
- `--all-train-refs`、screenは各opponent/seat 2局
- independent re-evaluation 2 block、各opponent/seat 1局
- `--positive-delta-gate`、`--risk-aware-update`
- evaluator SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`

評価artifactの完了数はgen0 `104 + 32`、gen1 `104 + 32 + 32`で、合計304/304、fault 0である。gen1 evaluationにはdraw 1件がある。

観測された選抜結果は次のとおりである。

- gen0 screen上位は見かけの`+50.00pt`（同一P1 centerのseed差を含む）。独立2 blockでは`+25.00pt / +25.00pt`だったが、同じcenterを含むためpolicy gainとは扱わない。
- gen0別候補はscreen `+37.50pt`から独立`−25.00pt / +25.00pt`へ反転し、別候補は独立`0pt / 0pt`だった。
- gen1の最高screen差は`+18.75pt`。独立2 blockでは各`+25.00pt`の候補もあったが、一方のblockでseat差・opponent×seat差が大きく、robust seat-safe gateを満たさない。
- 両世代ともeliteは`incumbent-center`のみで、P1 centerを保持した。
- gen1のMETA_DEVはcenter対controlが`0.5625 vs 0.6875`（差`−12.50pt`）、candidate seat rates `50.00% / 62.50%`、control `50.00% / 87.50%`であり、fresh validationとしても昇格不可。
- `META_FINAL`はCEM選抜・DEV判定中に読んでいない。

主要artifact SHA:

- CEM manifest: `48b7bb14fdecec57e5b6be0dd7b9a52922065fa613b0f046ec5a1a3c44bfd36e`
- gen0 results: `5c0636a7a851f5b9f2357722a4dbd684fd7febaa6ef0029c3d7a5c4612df4ae9`
- gen1 results: `f51ec416209602118718766f3c5a80361ff852ad1d699d5538863e29058420af`

## 判定と次の優先順位

今回のrecipeはsource取得・生成方法としては成立したが、同じP1 baseとroot deckに強く相関するため、BestKnown更新の性能証拠にはならなかった。旧cross-lineage pairや既評価public kernelのblind retryにも戻らない。

次の候補は、(1)未性能使用policy parentを含む新しいself-owned source、(2)複数runtime-safe behavior familyを混合したpool、(3)必要ならpermission済み外部kernelの新規取得である。いずれも `legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` の順に進め、通過候補だけを `cg_bestknown_loop_v1.py` の `P1 → policy CEM → fresh validation → deck → policy` へ接続する。

## 変更しなかったもの

P1 policy、root deck、BestKnown、Champion、production、submission、`opponents/`、commit、push、Kaggle submitは変更していない。全artifactはresearch-onlyで、training・longrun・submission authorityを持たない。
