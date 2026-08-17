# 公開kernel未使用meta epoch6d と c06 CEM（2026-08-16）

## 結論

epoch6d は、既存の性能使用済みepochとは別の公開kernel snapshot 8件を intake し、P1 の bounded smoke を fault なく通過させた。しかし、c06 近傍の CEM は独立 re-evaluation で control を上回らず、positive promotion gate を通過する候補は0件だった。従って center（c06）を保持し、`META_DEV`／`META_FINAL` は読まず、BestKnown・Champion・production・submission は変更していない。

判定は `SOURCE_GENERATION_PASS / CEM_NO_UPDATE / BESTKNOWN_UNCHANGED` である。

## 未使用source intake

- intake config: `configs/meta_specialist/cg_kaggle_kernel_meta_public_more_epoch6d_20260816.json`
- source epoch: `kaggle-public-20260816-fresh-epoch6d-diverse-agents`
- seed namespace: `kaggle-public-cg-seed-20260816-fresh-epoch6d-diverse-agents`
- intake root: `runs/cg-kaggle-kernel-meta-intake-public-more-epoch6d-20260816/`
- intake pool SHA: `210b53a5cac15da0e57186ebe6308b1acbe707a18f61f27cbfaf307f96b4c08d`
- intake fresh meta SHA: `21fa7a02bd40f185d5f10bfd95d7c9789436a3bfaef70813f3fc817c09f58355`
- accepted/rejected: `8 / 0`

採用した source は次の8件である。いずれも intake 前に既存 `opponents/` と既存 evidence の policy SHA identity check を通し、重複0件を確認した。

| candidate | 公開kernel |
|---|---|
| `kaggle_dicer992_archaludon_judge_20260816` | `dicer992/archaludon-judge-rule-based-agent` |
| `kaggle_naoto_mega_scrafty_counter_20260816` | `naoto714/en-mega-scrafty-ex-can-counter-damage-carry` |
| `kaggle_naoto_glaceon_exact6_20260816` | `naoto714/en-glaceon-exact-6-puzzle-vs-fast-meta` |
| `kaggle_naoto_iron_thorns_lock_20260816` | `naoto714/en-iron-thorns-ex-how-far-can-ability-lock-go` |
| `kaggle_naoto_archeops_draw_20260816` | `naoto714/en-archeops-draw-search-solrock-lunatone` |
| `kaggle_naoto_tr_moltres_removal_20260816` | `naoto714/english-tr-moltres-ex-can-removal-win` |
| `kaggle_maximim_ensemble_pimc_mcts_20260816` | `maximim/ptcg-ensemble-pimc-mcts-agent` |
| `kaggle_maximim_generic_heuristic_20260816` | `maximim/ptcg-generic-heuristic-baseline-agent` |

## Runtime smoke と promotion

P1 package `runs/cg-self-owned-cg-policy-family-v12-crossed-20260816/p1-core-control` を候補として、seed `202608971`、8 source × 2 seat × 2 games の32局を実行した。

- requested/completed: `32 / 32`
- status: 全局 `DONE`
- faults: `0`（fault rate `0.0`）
- wins/losses/draws: `8 / 24 / 0`
- score rate: `25.0%`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`

この smoke は性能採用ではなく runtime／合法性確認であり、入力 intake を変更せず、次の別artifactへ封印した。

- promoted root: `runs/cg-kaggle-kernel-meta-promoted-public-more-epoch6d-20260816/`
- promoted pool SHA: `a2d68d1565678d84f01ae814804e2b1a1b1985c82786aa01ac9692e209b87e59`
- promoted fresh meta SHA: `ba3a08e6e78bd73a96d3cf7030a85893c215b6a0fb0d9b5b92d8badfd01d1027`

## split

`cg_historical_split.json` は次の通り封印した。全8件は fresh epoch6d 内でのみ使用し、CEM 中の holdout 読み込みは禁止した。

- split SHA: `282057daff86fe5c4bca2ca272072968a76ac342c35f429d3e5a4ddb69373f32`
- `META_TRAIN`: dicer992 Archaludon、Naoto Mega Scrafty、Naoto Glaceon、Maximim ensemble（4件）
- `META_DEV`: Naoto Archeops、Maximim generic（2件）
- `META_FINAL`: Naoto Iron Thorns、Naoto Moltres（2件）
- `final_results_read_during_search`: `false`

## c06 CEM

実行root は `runs/cg-self-owned-cg-policy-cem-epoch6d-c06-g01-20260816/`。P1 source policy と self-owned scratch deckを固定し、c06初期config（SHA `5eaa501e4f9478fbc1b250b178794644903aab6967461aaa5e6908dedec197f6`）の近傍を探索した。

- campaign seed: `202608972`
- population / elite: `8 / 2`
- generations: `1`
- search: `META_TRAIN_ALL` の4 source
- screen: `144`局、`DONE 144`、fault `0`
- independent re-evaluation: `96`局、`DONE 96`、fault `0`
- parent policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- self-owned scratch deck SHA: `21620b5f30317f380c020f98672c524ba243b04f180df22830693e8f5acbaff2`
- control package policy SHA: `a52316249a6f5aa8bec19fd1a4fa904fcc684f5df4576e867bc5154ffae551d4`

screen 上位2候補は独立評価へ回したが、次の通り失敗した。

| candidate | screen delta | independent mean delta | independent min delta | opponent/seat safe |
|---|---:|---:|---:|---|
| `cg-p1-cem-g00-c00-5eaa501e4f94` | `+0.09375` | `-0.171875` | `-0.28125` | `false` |
| `cg-p1-cem-g00-c05-16cddad655e0` | `+0.06250` | `-0.093750` | `-0.31250` | `false` |

両候補とも repeat 間で安定した正差を示さず、risk-aware gate は不成立だった。結果の `new_center` は c06 と同一、`elites` は `incumbent-center` 2件である。screen 集計は `27W / 115L / 2D`、独立集計は `15W / 80L / 1D`（いずれも fault 0）だった。

## 境界と次の手

このepochで `META_DEV`／`META_FINAL` を読むことは、CEMの独立gateが不成立だったため行わない。epoch6dは性能使用済みとして台帳に記録し、blind retryもしない。

次は新しい source epoch の生成方法を変える。具体的には、同一作者のテーマ違いを増やすだけでなく、未使用の作者系譜・deck archetype・runtime strategy を事前に分散させ、source acquisition と performance holdout を別manifestで固定する。その新epochで c06 centerまたは明示的に設計した別parameter surfaceを再度TRAIN-only CEMへ渡す。

権限は `research_only`、`training_allowed=false`、`promotion_allowed=false`、`submission_allowed=false`、`longrun_allowed=false` のままである。BestKnown、Champion、production、submission、commit、push は変更していない。
