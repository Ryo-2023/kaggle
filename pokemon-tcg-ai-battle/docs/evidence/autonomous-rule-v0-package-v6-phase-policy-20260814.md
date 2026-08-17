# 2026-08-14 — Rule v0 2-card package v6 と条件付きポリシーscreen

## 結論

提出互換P0（Rule v0＋root `deck.csv`）で、v5までとmultisetを分離した2-card coordinated package v6を評価した。weighted48とcommon24では recovery/reset package が一時的に positive だったが、seed-disjoint 384では差が **+0.2604pt（45/384 対44/384）**まで縮小したため candidate-only とし、768・longrun・promotion・training・submissionには進めない。

その後、deckを固定した交互最適化の policy-fixed-short 候補として、`energyAttached=true` かつ `turnActionCount>=2` の公開局面だけ ATTACK に +240 を加える研究用Rule v0 overlayをweighted48で測定した。candidate 4W-1D-43L、control 6W-0D-42L、差は **−3.125pt** であり、common24へ進めず即停止した。

## v6 coordinated package

親は root Rule v0＋root deck。候補は次の2件で、known vocabulary、60枚、root core、ACE、novel multisetを事前検証した。

- setup redundancy: `[1102,1142] → [1225,1121]`（Dusk Ball＋Fighting Gong → Hilda＋Ultra Ball）。candidate ID `84fe042c7779ee8c1dad98fc7b9f0f1a93197060cbe5417fab5739fc99a09420`、deck SHA `89a1d796a24994c80c26c5830eb0a829efc5be8a9f6fbc55b783e43734814989`、multiset SHA `b2a973f636d56ed12b19e14bd5b58f534e8ddf2018d5303a223e262706a3b8ad`。
- recovery/reset: `[1152,1182] → [1097,1213]`（Poké Pad＋Boss’s Orders → Night Stretcher＋Judge）。candidate ID `9f1ea0032b53e780729034fa20eddf58a6b6701c225e4e2e25180cf40310e080`、deck SHA `5d3a519b1c81c4cf9e7b68f2dd6a02ca9427862d24de8574d4629a4fd4064040`、multiset SHA `d1c136593aa58a401341210ce883a849c9346b7c4497f6ffe1f427648910cf36`。

ResourceGovernorはnormal、weighted/common24はworkers=12/recycle=16、authorityは全false、heldout training exposure=0。runtime smokeと全ledgerはfault0、両seat、同一strata/seed、unique GID gateを満たした。

| stage | parent | setup candidate | recovery candidate | 判定 |
|---|---:|---:|---:|---|
| weighted48（各48） | 3W-0D-45L | 6W-1D-41L（+7.8338pt weighted） | 6W-0D-42L（+6.8259pt weighted） | 両候補をcommon24へ |
| common24（各96） | 8W-0D-88L | 9W-0D-87L（+1.0417pt） | 11W-0D-85L（+3.1250pt） | recoveryのみ384へ |
| confirmation384（各384） | 44W-0D-340L | 未実施 | 45W-0D-339L（+0.2604pt） | candidate-only / STOP |

一次artifact:

- weighted root: `runs/final-sprint-autonomous/rule-v0-root-deck-package-v6-20260814/`
- weighted manifest `f79a02a25a8ee6b0104fe50240166e2d9c05c6158d32a98fb857d53e31dea308`
- weighted summary `858f05baac3be2d46600af3da95d5e9a3141aafab527bd3954ff51860ef30cc1`
- common24 root: `runs/final-sprint-autonomous/rule-v0-root-deck-package-v6-common24-20260814/`
- common24 manifest `d812208a7e607833ade12e8d383f0ecc5625a798ac31107bb5242f2e3247c0c2`
- common24 summary `2f952d9f78ec6c8f3816952d30fb8e3ccca68f5c7803c03dc1857763770fb81f`
- confirmation root: `runs/final-sprint-autonomous/rule-v0-root-deck-package-v6-confirmation384-20260814/`
- confirmation manifest `7b9b9c2765b72f785ca86069157fce2b56dc6da60d64772b6525b6e60b0e0167`
- confirmation summary `0717b5539e5b720758cc27070f55033150896b36cee7e8860565ff2c45d9b37a`

## 条件付きRule v0 policy screen

deck固定・policyだけを変える交互最適化の短期候補として、`MAIN_ONLY` の ATTACK overlayを作った。公開状態の `current.energyAttached is true` かつ `current.turnActionCount >= 2`、かつ必須MAIN選択である場合に限り、既存Rule v0のATTACK scoreへ+240する。それ以外、非MAIN、malformed、optional、illegal、例外はRule v0 exact fallbackとした。相手のprivate情報・teacher label・native behaviorは読んでいない。

- schema: `meta-specialist-rule-v0-phase-conditioned-policy-screen-v1-final`
- candidate policy: `rule-v0-phase-conditioned-attack-after-energy-v1`
- control: root Rule v0 exact
- root: `runs/final-sprint-autonomous/rule-v0-phase-conditioned-policy-screen-v1-20260814/`
- 48 games/arm、12 META_TRAIN IDs×両seat×2 repetition、workers=12/recycle=16
- candidate 4W-1D-43L（score 9.375%）、control 6W-0D-42L（12.5%）、差 −3.125pt
- 全96局DONE/fault0、両seat24/24、paired strata、GID/seed gate PASS
- authority: research-only、execute/training/promotion/submission/longrun=false
- manifest SHA `0d38bd78c439f3fa552befc3be0033afdfe54cf070156fc73efa2fbcbc6a30fe`
- weighted summary SHA `8398d423ff9cab8cbec115ff89c8b463863f2c598e5e7720af13afd80ea75ac8`
- summary MD SHA `0735b970c4e941b35ee49c775ddc682942f2fee075eefb87f22c4bb18cb496e6`
- runtime smoke SHA `63f491698baa17ac7bc19e65d7ca71783242c0192e3633e4d2683d72458a5b25`

負差のため同一policy surfaceのcommon24/384再測定は行わない。v6 packageの384差も小さく、現時点でpolicy phaseを長期化しない。次は既評価surfaceのblind retryではなく、新規deck packageまたは新しいpublic-state policy仮説を、smoke→workers12 weighted48で一度だけ評価する。

## 検証と境界

v6 runner/common24/confirmation wrapper、phase-conditioned overlay、candidate source、policy screen runnerは `py_compile` と focused tests を通過した。docs validator（13 canonical documents）と `git diff --check` もPASS。production `main.py`/`agents`、Champion、submission package、native pool、既存artifactは変更していない。Kaggle提出、commit、pushは未実施。`AGENT_INVALID`やfaultは勝率へ変換していない。
