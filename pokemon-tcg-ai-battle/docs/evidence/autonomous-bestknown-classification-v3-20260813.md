---
title: Strong Asset BestKnown classification v3
date: 2026-08-13
status: research-only
promotion_authority: false
---

# 結論

24-opponent common protocolへ揃えた結果、現時点の `EvaluationBestKnown` と
`BestKnownArchaludon` は `tomatomato_archaludon` native pair の暫定首位とする。
plamen mutation candidateは23-opponentの4 blockでは親nativeを+2.0041pt上回ったが、
共通24-opponent protocolの4 block/1536では71.5495%で、tomato nativeのpooled1536
72.0703%を下回った。候補は有望な deck mutation signal だが、GlobalBestKnownを更新しない。

## 区分

| 区分 | 判定 | 根拠 | authority |
|---|---|---|---|
| EvaluationBestKnown | tomato native provisional | common native protocol 1536局、1107/1536=72.0703%、fault0 | promotion不可 |
| TrainingEligibleBestKnown | tomato primary / Lucifer control | current sealed snapshot + permission-filtered META_TRAIN | behavior/submission不可 |
| SubmissionEligibleBestKnown | Rule v0 + root deck | archive clean-room 2局 PASS、SHA `da4bbe9d...` | 現production anchor |
| BestKnownArchaludon | tomato native provisional | tomato pair自身がArchaludon/Cinderace native leader | promotion不可 |
| GlobalBestKnown | unresolved | slow5/R7/permission/common protocol gaps | 未確定 |

## common-protocol mutation結果

新規 runner `scripts/run_deck_mutation_common_protocol_v1.py`（SHA
`82c9caa21c4401996cdc691c2e6807c37140c4041a96c349bb5a42bfbd616ace`）で、candidate
deckとparent nativeを24 opponent、両seat、各8局の同一構造へ載せた。

| block | candidate | parent native | delta |
|---:|---:|---:|---:|
| 1 | 277/384=72.1354% | 268/384=69.7917% | +2.3438pt |
| 2 | 274/384=71.3542% | 279/384=72.6563% | −1.3021pt |
| 3 | 288/384=75.0000% | 260/384=67.7083% | +7.2917pt |
| 4 | 260/384=67.7083% | 282/384=73.4375% | −5.7292pt |
| pooled 1536 | 1099/1536=71.5495% | 1089/1536=70.8984% | +0.6510pt |

summary SHAはblock1 `86992be532a77d5d2b0396c7199ca78d49a119804b7b56932db8e65c6c626f1d`、
block2 `6a2109b1c8921cf65626da42f9e0a8295fe588fe24f3fc92e9086401f0983e87`、block3
`c104d040da4e1205a3e6451545fb3dfdfda8d8072333eb4bc9acb21540feccc6`、block4
`e8e3078209944540a4b3080055ddd60d765bc66c78be7aaa9ac30bec7b7a9b09`。全3,072 rowは
DONE/fault0であるが、block2/4反転とcandidateのtomato未達を理由に昇格させない。

## 権限・longrun

candidate manifest SHA `67f63c1caadf1b478cd3865dda8193f778e4c1fc4e5d0903776b23a0e982690b`、
meta manifest SHA `e430f1284e587e7f301f9e29abe377faad79ff5120a39c42b7b2f6a5223dd2ae`、schedule
SHA `9db6e1d9ea3a6913f2080ac5ca4f08b748ed9fa1469cfd0a7a74d0253cb16b6a`。pool nativeと
mutation candidateはlocal_eval_onlyで、candidateのtraining/promotion/submission/longrun
authorityはfalse。tomato/Luciferはsealed training snapshotを持つが、元native agentを
behavior sourceとして使える権限とは別である。tomato gateは既存 score-bias armでBLOCKED、
mutation candidateはpackage closure・rollback/resume lineage・clean META_DEV evidenceが
不足している。従って `LONGRUN_STARTED` は未成立で、AWR/value学習や提出を開始しない。

機械可読の分類一次artifactは
`docs/evidence/autonomous-bestknown-classification-v3-20260813.json`（SHAは同ファイルの
SHA-256）である。
