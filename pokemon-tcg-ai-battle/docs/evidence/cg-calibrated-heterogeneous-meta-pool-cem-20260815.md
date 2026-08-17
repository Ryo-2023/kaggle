# Calibrated heterogeneous meta pool / P1 CEM evidence（2026-08-15）

## 結論

`TRAIN_ONLY_DIFFICULTY_CALIBRATED_HETEROGENEOUS_POOL_V1` は、新しい meta source の生成方法として、source identity の未使用確認、異なる5 familyの pool 化、通常 interpreter の fault-free smoke、promotion、fresh batch 検証まで完了した。ただし、昇格済み pool を使った P1 CEM は独立 positive／seat-safe の条件を満たさず、P1 center を保持した。P1 policy、root deck、BestKnown、Champion、production、submission は不変である。

## Source 生成

- 実装: `src/mage_ptcg/opponent_ingest/calibrated_meta_pool_v1.py`
- CLI: `scripts/build_calibrated_meta_pool_v1.py`
- source kind: `internal_calibrated_heterogeneous_panel`
- recipe: `TRAIN_ONLY_DIFFICULTY_CALIBRATED_HETEROGENEOUS_POOL_V1`
- source family: `rocket_dispatch_classifier_v1`、`rocket_dispatch_confidence_v1`、`rocket_specialist_route_v1`、`rocket_theta_behavior_v2`、`waterbox_runtime_safe_v1`
- metal familyは smoke 中に parent timeout を含んだため、最終 pool から除外した。
- calibration は各候補を TRAIN-only ledger の2局（両 seat）で評価した。勝率は0、0.5、1.0に量子化される粗い難易度校正であり、性能の証明には使っていない。
- 既使用 audit は campaign の `search_refs`、pool rows、CABT ledger の opponent ID／policy SHAを集計した。選択前の audit は `used_ids=205`、`used_policy_sha256=190`。fresh gate は12候補すべて `unused_before_run=true` として通過した。

generated root `runs/cg-calibrated-heterogeneous-meta-20260815-c/`:

- pool SHA: `07d18f75a787bdcaddaa5c7c1adfdcad49bef7039d5629981fe82ec7032ca564`
- fresh SHA: `3e8818b72c30da9bc12b310b71bcd88e9ae1e0e4143249c6ab648cb5d67889cd`
- 初期 split SHA: `aa4749d903d4e77fa8dc8b0dfab95a24c8e2b0efc703bd37deee8afe50958f60`

## Smoke / promotion / split

P1 package SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` を12候補へ両 seat各1局、timeout 120秒で接続した。24/24 `DONE`、fault 0、draw 0、P1 は5W-0D-19L（20.8333%）だった。これは runtime safety の確認であり、候補の採用根拠ではない。

promoted root `runs/cg-calibrated-heterogeneous-promoted-20260815-c/`:

- pool SHA: `b5e4417d38855f8821baf7ef1d494aff5075ac88310ee4a8a89734306dfea095`
- fresh SHA: `32879d9ecb13ea25962368124469693dad9f150cdd226ce5ea1af2fb872f7297`
- meta manifest SHA: `8ca1457feacc1880e8cde10d1c2e2e316a74b987ff6706e0611ac9c9bb043a59`
- rebound split SHA: `d6d3b05c4f574e434dfb8ed50b12ea2af2b65844ae49587fc3af7fb12d7c4383`
- smoke summary SHA: `b8dcbb52386e2a065887e9b355e69b2936c064846fd6f2e567e7abe5cd825b58`
- split: `META_TRAIN=10 / META_DEV=1 / META_FINAL=1`

`cg_weekend_split_v1.py` は repo-relative source を既定とする一方、sealed split 隣接の source manifestも hash検証するように修正した。追加した回帰を含む split／calibrated pool tests は PASS である。

全12候補を runtime smoke したため、META_FINAL は CEM の性能選定には投入していないが、smoke-untouched holdoutではない。この制約を次の source設計で引き継ぐ。

## P1 CEM

campaign root: `runs/cg-calibrated-heterogeneous-cem-20260815-c/`

- campaign seed: `20260858`
- mode: `META_TRAIN_ALL`（10 refs、DEV/FINALを探索へ含めない）
- generation: 1（population 4、elite 1）
- screen: 各 candidate/control とも10 opponent × 2 seat × 2 games = 40局
- independent re-evaluation: screen elite 1候補とcontrol、2 blocks、各40局相当
- flags: `positive_delta_gate`、`risk_aware_update`、`reeval_for_update`
- screen: 200/200 `DONE`、fault 0
- independent: 80/80 `DONE`、fault 0
- CEM manifest SHA: `73dd78257eab0a334fe9fc33740f6e7af5292d10e75914ed86c69dd096ccadb7`
- generation results SHA: `e6ef01472f392a508cbc5243f69759ebc04751aec22cf7bef949b081e1cd56dc`
- re-evaluation summary SHA: `34f6f9714227fdaf3bafecfa9268c05096a1d5cdbe8526d561c7419a0e4dfb3f`

screen の candidate/control は次の通り（各40局、scoreは勝率）:

| candidate | candidate | control | delta |
|---|---:|---:|---:|
| g00-c00 | 5/40 = 12.5% | 8/40 = 20.0% | -7.5pt |
| g00-c01 | 4/40 = 10.0% | 8/40 = 20.0% | -10.0pt |
| g00-c02 | 5/40 = 12.5% | 8/40 = 20.0% | -7.5pt |
| g00-c03（screen elite） | 6/40 = 15.0% | 8/40 = 20.0% | -5.0pt |

screen elite `cg-p1-cem-g00-c03-fd74f19c63b0` は独立 re-evaluation で candidate 0/40、control 7/40となった。2 blockとも candidate 0勝で、control は5勝／2勝、fault 0だった。従って independent positive gate と risk-aware gate は不通過で、`elites=["incumbent-center"]`、`champion_changed=false`、new center は初期 identity configのままである。

## 判定と次の行動

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。この pool は「異なる familyを混ぜた source を安全に sealし、CEMへ接続できる」ことを示したが、性能改善の evidence ではない。2局 calibration、24局 smoke、40局 screen、80局 independent はいずれも native性能や提出勝率の証明ではない。

次は同じ5 family・同じ calibration poolの blind retryをしない。新しい sourceでは、runtime smoke用の候補と性能 holdoutを最初から分離し、相関の低い未性能使用 policy lineage または新規 permission済み sourceを含める。`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` を通過した候補だけを `cg_bestknown_loop_v1.py` の `P1 → policy CEM → fresh validation → deck → policy` へ接続する。

