---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-14
---

# Rule v0 priority ATTACK-first research candidate

## 結論

提出互換の root deck を固定したまま、Rule v0 の research-only policy copy
`ATTACK → PLAY → ATTACH → EVOLVE → ABILITY → END` を alternating runtime へ接続した。
初回96局では candidate 13/96 対 control 9/96（+4.1667pt）だったが、candidate の seat差が
6.25ptで昇格ゲートを満たさなかった。別seedで一度だけ確認したところ candidate 12/96 対
control 13/96（−1.0417pt）へ反転したため、384/768/longrunへ進めず、candidate-only
hard-negative として停止する。

## 固定条件

- subject deck: root `deck.csv` SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- control policy: root `main.py` SHA `806284f8f03d974fdb8e8dd6020c1e6dd25d7936430119e8c2b8baa1d973eef7`
- candidate policy source SHA `8f2c6f5bbfb8014f16526e70022fdf246feac4a8172a45af101ac2294acaf042`
- broad24 pool, both seats, 2 repetitions, same candidate/control strata
- workers `12`, worker recycle `16`, fresh roots, fault-inclusive denominator
- authority: execute/training/promotion/submission/longrun all `false`

## 実測

| stage | candidate | control | delta | seat | 判定 |
|---|---:|---:|---:|---|---|
| first 96 (`base_seed=23600000`) | 13-0-83-0 (13.5417%) | 9-0-87-0 (9.3750%) | +4.1667pt | candidate 16.667/10.417%, gap 6.25pt | NOT_PROMOTABLE |
| confirmation 96 (`base_seed=23600100`) | 12-0-84-0 (12.5000%) | 13-0-83-0 (13.5417%) | −1.0417pt | candidate 14.583/10.417% | STOP |

両stageとも全192局 `DONE`, `fault=0`, `draw=0`。初回の局所positiveは別seedで再現せず、同候補の384確認は起動していない。

## 成果物

- materializer: `scripts/build_rule_v0_priority_attack_first_v1.py` — SHA `b59617f4f88dc95f53445d6c71cd031f5c598347c531e7c26b2bbce049f7da6a`
- focused tests: `tests/meta_specialist/test_rule_v0_priority_attack_first_v1.py` — SHA `b5bfd137482a16fb12ca99f2ec5e6eb01684a645ea0be9e17defdd01397403bb`
- first stage root: `runs/final-sprint-autonomous/rule-v0-priority-attack-first-96-20260814/evaluation/`
  - manifest SHA `40cd17f79a5bb8167ab2e8ce42888753066d3b0c9d900b398751128a0272a7f6`
  - summary SHA `244874d7b4327b62890933b2f6e10d1ade9ece54c55cc039b042a63a35fc77e1`
  - evaluator ledger SHA `8f73826b50c8d61f5b58998921fcacdc6b3279c3557bf704e61e51e452f13bb0`
- confirmation root: `runs/final-sprint-autonomous/rule-v0-priority-attack-first-96-confirmation-20260814/evaluation/`
  - manifest SHA `989f28bb6225ef32a8fe65ef532a81cc0806eeddcfab4b290bb295f2dc9c59bc`
  - summary SHA `fb34d284b76dc611b75c3c3a8a333e7440ecfea3864205db8e05efccf166b581`
  - evaluator ledger SHA `8085dbfb34e3ef3b705c1ae095eb0bac663d0ed4e942ac5ea09c9006158794e2`

## 検証と境界

focused pytest 2件、py_compile、`scripts/docs/validate_docs.py`、`git diff --check` を実行し、いずれもPASSした。
生成 source は production `main.py`/`agents/rule_agent.py` を編集せず、native behavior label、teacher label、private state、training、promotion、submission は使用していない。
