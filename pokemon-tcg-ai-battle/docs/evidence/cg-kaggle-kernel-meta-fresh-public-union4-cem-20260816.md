# 公開kernel fresh union4 / root deck固定 P1 CEM（2026-08-16）

## 結論

未性能使用の公開 Kaggle kernel snapshot を、既存の fail-closed intake で新たに取得・検査し、4件を個別 smoke から sealed union へ昇格した。root deckを現行 public root deck に固定した P1 policy CEM は、screen 72/72 row を `DONE`・fault0 で完走したが、8候補すべてが `seat_collapse=true`・`valid=false` となった。elite は空で `incumbent-center` を保持し、独立再評価、DEV／FINAL、deck phase、`cg_bestknown_loop_v1.py` は起動していない。

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。現行 P1、root deck、BestKnown、Champion、production、submission は不変である。今回の META_TRAIN は性能使用済みとして扱い、同じ union／候補の blind retry は行わない。

## source intake

Kaggle CLI で公開 kernel output をローカルへ取得し、tar SHA と source identity を固定した。各 config の `research_only=true`、authority 全 false、training exposure 0 を維持した。

| source id | 公開 kernel | intake 結果 | bounded smoke |
|---|---|---|---|
| `kaggle_sgzk001_engineering_agent_20260816` | `sgzk001/imitation-is-capped-engineering-is-not-agent` | accepted | 2/2 DONE、fault0、0W-2L |
| `kaggle_sushanth_alakazam_heuristic_20260816` | `sushanthtiruvaipati/ptcg-alakazam-heuristic-agent` | accepted | 2/2 DONE、fault0、0W-2L |
| `kaggle_prvsiyan_static_tusk_v24_20260816` | `prvsiyan/ptcg-ai-battle-static-deck-tusk-1208-v24` | accepted | 2/2 DONE、fault0、0W-2L |
| `kaggle_sushanth_lightning_ismcts_20260816` | `sushanthtiruvaipati/lightning-manectric-ismcts-agent` | accepted | 2/2 DONE、fault0、2W-0L |

次の source は fail-closed で除外した。

- `avikdas567/ptcg-ai-strategy-agentic-optimization-framework`: `invalid_ace_spec_count`
- `sushanth/grimmsnarlex`、`sushanth/mega-emboar`: `invalid_ace_spec_count`
- `sushanth/dragapult-ex-spread-damage-agent-v2`、`sushanth/palafin-ex-hero...`: `invalid_ace_spec_count`
- mktdev Lucario、Prvsiyan visible router、および既に採用済み snapshot: `source_identity_reused`／`artifact_identity_reused`

intake config と report は次の通りである。

- `configs/meta_specialist/cg_kaggle_kernel_meta_public_fresh_avikdas_sgzk_20260816.json`（SHA `c5874d01b4de97b7deccc50681fdbeddc4f68c7df69384599dcbcaff9dea51d7`）、report SHA `83633d0e49d639e5be94b006daec1d838ee04094c30c89fe4cc0d2853917a5ee`
- `configs/meta_specialist/cg_kaggle_kernel_meta_public_fresh_sgzk_sushanth_20260816.json`（SHA `fa0ede07e830197c5f9a53557069078dcae5f46551e6852250b886f06da1f493`）、report SHA `881bece6b2d0398553952ac9a80f092c871c511f2324cd8fa60f1234361ffbcc`
- `configs/meta_specialist/cg_kaggle_kernel_meta_public_fresh_mktdev_prvsiyan_20260816.json`（SHA `07e6e4137136fc7ea3b7db193575d55b9042059f307aedc924eed37b0d85c06c`）、report SHA `78266e086850fe881aa37153679c8f6f7c17c18acd3e344adc1e26cb83a9220c`
- `configs/meta_specialist/cg_kaggle_kernel_meta_public_fresh_sushanth_v2_20260816.json`（SHA `5445d89fbb2c9825aacb18a67c00ba6fbdeb2edbd97ec956d14f62bb6bc9b6ef`）、report SHA `1f55c59821475a34ef605be8f5cb5e0900e216d410bc4dadf3e951e32da6baca`

個別 smoke の実行根は `runs/cg-kaggle-kernel-meta-smoke-fresh-{sgzk,sushanth-alakazam,static-tusk,sushanth-lightning}-20260816/`。これは runtime gate であり、勝率の昇格証拠ではない。

## sealed union と split

4個の promoted root を `scripts/merge_historical_meta_smoke_v1.py` で統合した正本は、次である。

- root: `runs/cg-kaggle-kernel-meta-promoted-fresh-union4-rootdeck-v2-20260816/`
- pool SHA: `f82aedcadce8a807bcbbcc3821e2b9fb7180dc6be0bc44da5d3fb9d9b8682e72`
- fresh meta SHA: `28be3f56df6d6326dce656ff463f466a952fd746f176238cc6048d5ad5ed41b5`
- meta manifest SHA: `851c10a78c74c08d3febf2fd72e0c7bb775dc52ec8d0bd1a58f064132590b85f`
- split SHA: `5c078f66e566726627be0036aceb761657cdaf30c75a71dce6f256d676f781a9`
- merge report: `runs/cg-kaggle-kernel-meta-promoted-fresh-union4-rootdeck-v2-20260816/merge_report.json`

性能探索への exposure を分離するため、split は `META_TRAIN=2`、`META_DEV=1`、`META_FINAL=1` とした。CEM は Prvsiyan static tusk と SGZK engineering agent の2件だけを読んだ。Alakazam は DEV、Lightning は FINAL として保全し、CEM中は未読である。

root deck control は現行 `deck.csv`（SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）を使い、P1 parent policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` から deck-bound control を生成した。control package の patched policy SHA は `a05bf3dd311c543fd363f2d883abd30e9a3822b7d8df6a451a8b7705a3122b66` である。

## P1 CEM

実行根は `runs/cg-p1-cem-fresh-public-union4-rootdeck-v3-20260816/`。source package は immutable P1 parent の root-deck control、candidate/control package は root deck-bound package とし、deckを探索中に変えていない。

```text
campaign seed       202608961
population / elite  8 / 2
generation          1
search mode         META_TRAIN_ALL
screen              72 games
independent reeval  実施なし（valid elite 0件）
evaluator SHA        b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08
```

screen は8 candidate＋controlを、2 opponent × 2 seat × 2 gamesで評価した。全72 rowが `DONE`・fault0・draw0（wins 6、losses 66）だったが、candidateは全て `seat_collapse=true`、`valid=false` であった。candidate別の delta は `0` または `−12.5pt` で、最も良い候補も control 同率に留まった。elite selection は `screen_valid_candidates_below_elite_count_preserve_center`、`elites=[]` である。

- campaign manifest SHA: `1d38702e82373f67d2bea27a019bd5ff23d3414f125beebaaab41545f5cd2753`
- evaluation summary SHA: `0d8e8edd7269f0f33c237b409311f6a381c05b66328b0cf37bb2dcb6ca8f3230`
- generation results SHA: `a101673e17e3a059344051d697fb051e029dc85e77f73b732092f3c57dcc4ac6`

この結果は、public source の intake／runtime 接続が機能することは確認した一方、少数 source の META_TRAIN だけでは seat-balanced な policy improvement を選べないことを示す。DEV／FINAL は未使用のまま残しており、候補が gate 外のため読出していない。

## 固定状態と次の条件

- P1 policy、root deck、BestKnown、Champion、production、submission は不変。
- `cg_bestknown_loop_v1.py` は未起動。policy→deck→policy loopへは接続していない。
- commit、push、Kaggle提出は未実施。
- 今回の union の META_TRAIN と CEM candidate は性能使用済み。同じ source／seed／候補の blind retry はしない。
- 次の優先課題は、source identity／policy SHA の exposure ledger を守りながら、相関の低い別公開 policy lineage または新しい self-owned surface を、runtime smoke と性能 holdout を分離して生成すること。全ゲート（`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`）通過候補だけを BestKnown loop に渡す。

