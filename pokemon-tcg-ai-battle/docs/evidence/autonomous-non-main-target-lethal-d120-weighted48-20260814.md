# Self-owned Rule v0 非MAIN target lethal overlay weighted48（2026-08-14）

## 結論

事前登録した `nonmain-target-lethal-d120-v1` は、Rule v0 の MAIN action score
（ATTACK / PLAY / EVOLVE / ATTACH / END）を変更せず、public な非MAIN target
option の `damage` / `hp` / `playerIndex` / `type` allowlistだけで lethal target
へ +120 を加える research-only overlay である。ResourceGovernor の safe admission
後、同一 META_TRAIN weighted48 strata の candidate/control を `workers=1`,
`worker_recycle_games=16` で実行した。

candidate は control を **−4.1667pt** 下回ったため、coverage gate・common24-
96・384・longrun へ昇格しない。この結果は target surface の局所NO-GOであり、
既存 BestKnown や他の未評価 deck/policy laneを変更しない。

## 実測

| arm | W/D/L | score rate | fault | seat 0 / seat 1 |
|---|---:|---:|---:|---:|
| control Rule v0 | 8/0/40 | 16.6667% | 0/48 | 4W / 4W |
| candidate lethal +120 | 6/0/42 | 12.5000% | 0/48 | 5W / 1W |

同一 strata の paired outcome は `control→candidate` 5、`candidate→control` 7、
双方勝ち1、双方負け35。candidate は両seat完了だが seat差が大きく、positive
point estimateでもない。candidate target override/selection coverageはこの
runnerのWDL artifactへは保存されておらず、coverage gateは未達（UNMEASURED）
として扱う。負のWDLだけで昇格不可なので、追加実験で補わない。

v3 rootの candidate 48 `AGENT_ERROR`（runner factoryがbound methodで
`__name__`設定に失敗）は性能証拠から除外した。plain function factoryへ修正後の
v4だけを採用する。

## 一次artifact

fresh root:
`runs/final-sprint-autonomous/nonmain-target-lethal-d120-weighted48-20260814-v4/`

| artifact | SHA-256 |
|---|---|
| `resource-warmup.json` | `44c40c4b3fb2946c2c042a2c9028ac0dc9a48bd81bb80448b4a865f6aba068ba` |
| `screen.json` | `7885b9c232a4787d703746b535b9c3ef0acba3d4d00c6412e6a442142f221bfd` |
| `screen.games.json` | `66cef3386ab8bb2435f29e7c0b42242e9c753820e34e88aa75f42014e679fd1c` |
| `evaluation/manifest.json` | `7491c2d8bda922415f1b15b84225a03bc0e1ea3aa5da0a84ef855e3f083cacae` |
| `evaluation/ledger.jsonl` | `ce952220b9ccc53be3012b4de8d44721f239d4689e54784c2a22faa441a532fe` |
| `evaluation/summary.json` | `47f1a2229bb936518c04c770737e5519f6f45e392303fe294dd5e679e22816c2` |
| `run-result.json` | `6c1a8faedef4e413b525214f9c7b43eba4c6c720e39c7f7c4f763a6bf3923e5d` |

Candidate policy SHA: `edb1ce1c1d3e6a551e416cdaf3c0f88dea0f9e6843f6841882e852d982d3727a`  
Evaluator SHA: `0cbac2789e08758d14783922c5c7145f25701a47d978b3d9df9d132aec4eed84`  
Resource warmup payload SHA: `12aa49c94a8d154aafada4319129237897f1e853ac79de1fa224b95cd62e92c2`

実装/テスト/evidence source SHA:

| source | SHA-256 |
|---|---|
| `src/mage_ptcg/meta_specialist/non_main_target_overlay_v1.py` | `0dc53feca37a2b46f9d7f64c954c456ab364fe39bf0e33392ecd42ac87759970` |
| `scripts/build_non_main_target_overlay_screen_v1.py` | `8d30ce2247516f684ee047b34fc4553e396cb4e3543b9cd451a5300cafc9f1ba` |
| `scripts/run_non_main_target_overlay_screen_v1.py` | `6e7753f5e0367e21f0b922d3a7db3d909ad15696679d6436d419c7d238c4f5f4` |
| `tests/meta_specialist/test_non_main_target_overlay_v1.py` | `30ddec184500e4493254568627f3d1015cda605dae6d439c04f0b6c6914f0e03` |

## 変更と検証

変更はresearch-only module / build CLI / run CLI / focused testsに限定した。
production `main.py`, `agents/rule_agent.py`, evaluatorは編集していない。

```text
TMPDIR=/tmp/luna-target-overlay-function2 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/pytest -q -s tests/meta_specialist/test_non_main_target_overlay_v1.py
6 passed in 0.28s

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m py_compile \
  src/mage_ptcg/meta_specialist/non_main_target_overlay_v1.py \
  scripts/build_non_main_target_overlay_screen_v1.py \
  scripts/run_non_main_target_overlay_screen_v1.py
exit=0
```

`git diff --check` と docs validator はこの evidence 追加後に実行する。

## 判定

`PROMOTION_STATUS=NOT_PROMOTABLE`  
`EXPLORATION_PRIORITY=STOP_LOCAL_NON_MAIN_TARGET_LETHAL_SURFACE`  
`COMMON24=NOT_STARTED`  
`LONGRUN=NO-GO`

全 authority は false。commit、push、submission、既存artifactの上書きは行って
いない。
