---
title: R7 smoke-false native pair evaluation diagnostic (2026-08-12)
status: research-only
---

# 結論

current poolで`smoke_ok=false`かつ`usage_boundary=local_eval_only`の`public_archaludon_cinderace_r7`を、training/submissionへ転用せず、性能診断だけ96局実行した。96/96 DONE、fault0、68W/28L、70.8333%だった。tomato/Lucifer/plamenの1536局native rankingとは局数が異なるため、GlobalBestKnownの確定には使わない。R7は引き続きtraining・promotion・submission対象外である。

## 条件

- asset: `public_archaludon_cinderace_r7`
- policy SHA: `c08588467c3faa2cbc748703acc8e7099c6362c32747c84cb2cec8131d6a4ca3`
- deck raw/canonical SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- pool manifest SHA: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- reference: broad config 24 opponents、両seat、各2局、96局
- block id: `asset-ranking-r7-diagnostic`
- base seed: 9,500,000
- evaluator SHA: `ae476cc72ac4efcf080dff118b1c4ef15268edf8e1d22b9b04cb432d48f9a797`

## 結果

| 指標 | 値 |
|---|---:|
| requested / completed | 96 / 96 |
| W/D/L/F | 68 / 0 / 28 / 0 |
| score | 70.8333% |
| seat0 | 39/48 = 81.25% |
| seat1 | 29/48 = 60.4167% |
| runtime total | 36.065925 sec |

一次artifact:

- `runs/meta-specialist-asset-ranking-r7-diagnostic-20260812/asset_ranking.json` SHA `7787f191ffdfd559d26a29b8365974c7e384a21950e5d8068aef2bd1137785ac`
- ledger SHA `62b04763bd95c6f35b3b26799f3ad974414a98536b03eecb423f962c98c08b25`
- summary SHA `e40b02ce05108fed7170f95cc4ed8b6d16452e21ede7a4a82c09b92b4cb08209`
- manifest SHA `78f1883704741054176aa3de7f2c11b45b031ee7c93190f01ffff89cc5e896cf`

## 解釈

R7は96局時点ではfast96のtomato 76.04%より低く、top3 pooled1536のtomato72.07%・Lucifer71.81%・plamen71.74%と直接比較できない。smoke falseの原因とcurrent pool identity mismatchを解消しない限り、R7をBestKnown・TrainingEligibleBestKnown・SubmissionEligibleBestKnownへ入れない。今回のartifactは「local performance ceilingの診断」としてのみ参照する。
