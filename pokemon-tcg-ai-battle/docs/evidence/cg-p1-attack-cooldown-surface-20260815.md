# P1 attack-cooldown surface screen — 2026-08-15

## 結論

P1 `cg-lethal-target-v1`＋root deckを固定し、Mega Lucarioの`Mega Brave`（attack 983）がvisible activeをKOできない一方、`Aura Jab`（attack 982）が合法で、discardにFighting Energyがあり、benchに未充電のFighting系targetがある局面だけへ`+12000`するhash-bound overlayをscreenした。candidateは`19W-1D-76L/96`、P1 controlは`17W-0D-79L/96`で、score差は`+2.6042pt`だった。

ただしcandidateのseat rateは`12/48=25.0000%`と`7/48=14.5833%`（gap `10.4167%`）で、事前のseat-safe gate（≤5%）を満たさない。全192局は`DONE`・fault 0であるが、独立384局確認へ進めずSTOPとした。さらにlocal poolのpublic・`smoke_ok=true` 70件は全て既存artifactへ出現済みで、fresh-unused metaは0件である。したがってv6をP2/P3、BestKnown、Champion、production、submissionへ昇格しない。

## Identity

- P1 base policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- candidate policy SHA: `27daaf3a3cee887e2f3aa5046826202c5f1ece5c11d0eac06d0c40690ebc0079`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- screen root: `runs/final-sprint-autonomous/cg-p1-attack-cooldown-surface-screen-20260815/`
- summary SHA: `c7feff1225d823e792280e9941677b5181b6f29ad59d5c2e3adc38f5ac388f00`
- manifest SHA: `724c6ec9c4a13a95e6bf26881f0e4ad0f80fc76a3e4691ecb9b18bb1541d6150`
- manifest-complete SHA: `797574ed516ae2f26d1aa732592a307b3fa31cd597c27c0268494b8a2d84f43e`
- ledger SHA: `ccf5be1193b7ba0a96a606367f9e1252eeda529dc80f8be3410692870bfb4f88`

## Policy surface

`src/mage_ptcg/meta_specialist/cg_p1_policy_candidate_v6.py` はP1 source SHAを検証してから、次を全て満たすmain-phase attack選択だけを変更する。

1. `Aura Jab`（982）と`Mega Brave`（983）がともにlegal option。
2. visible opponent activeのHPが正で、983のdamageがHP未満（983でKOできない）。
3. own discardにFighting Energyが1枚以上ある。
4. own benchに`RIOLU`、`MEGA_LUCARIO`、`MAKUHITA`、`HARIYAMA`のいずれかがあり、そのenergy countが0。

この条件以外はP1 exact scoreへ戻す。private hand/prize/deck、opponent policy identity、teacher/native actionは参照しない。packageはresearch-onlyで、authorityはtraining/promotion/submission/longrun全てfalseである。

## Paired screen

実行条件は `performance_first_broad_pool_v1`、24 public opponent、両seat、各opponent×seat×repetition 2、base seed `49910000`、workers 12、worker recycle 16である。candidate/controlは96局ずつでpair key＋seat strataを共有した。

| arm | seat 0 | seat 1 | total |
|---|---:|---:|---:|
| candidate | 12W-1D-35L / 48 | 7W-0D-41L / 48 | **19W-1D-76L / 96** |
| P1 control | 10W-0D-38L / 48 | 7W-0D-41L / 48 | **17W-0D-79L / 96** |

- candidate score rate: `19.5/96 = 20.3125%`
- control score rate: `17/96 = 17.7083%`
- candidate-control delta: **`+2.6042pt`**
- candidate seat gap: **`10.4167%`**
- requested/completed: `192/192`
- status: `DONE=192`, fault `0`, draw `1`（candidate）

seat gapがgate外のため、screen陽性だけを理由に独立384へ進めない。既評価broad24の再利用metaであるため、仮にseat-safeでもfresh-unused昇格根拠にはならない。

## 判定と次の条件

- v6: `candidate-only / STOP_SEAT_UNSAFE_REUSED_META`
- P1＋root deck: BestKnown、Champion、productionとして不変
- P2/P3、CEM update、deck mutation、training、longrun、Kaggle提出: 未実施
- fresh・unused・smoke-ready public meta: `0件`（public smoke-ready 70/70が既存artifactに出現）

同じv6候補のblind retryは行わない。再開する場合は、真に未使用のmeta sourceが固定されるか、v6と重ならない新しいactor-visible surfaceをscreen→独立複数block→fresh DEV/FINALの順で事前登録する。

## Verification

```text
TMPDIR=/tmp PYTHONPATH=.:src pytest -q \
  tests/meta_specialist/test_cg_p1_policy_candidate_v6.py \
  tests/meta_specialist/test_run_cg_p1_policy_candidate_v6_screen_v1.py
  5 passed

python -m py_compile \
  src/mage_ptcg/meta_specialist/cg_p1_policy_candidate_v6.py \
  scripts/run_cg_p1_policy_candidate_v6_screen_v1.py
  PASS
```

既存production package、Champion、commit、push、Kaggle外部送信は変更していない。
