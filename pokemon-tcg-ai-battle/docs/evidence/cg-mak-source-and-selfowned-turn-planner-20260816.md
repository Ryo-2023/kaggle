# Mak public source diagnostic and self-owned turn planner — 2026-08-16

## 結論

今回の結果は、新しい公開meta sourceを取得できたことと、そこからself-owned policyの性能改善を再現できたことを分けて扱う必要がある。Makthanithinの公開policyを隔離した診断候補は、同じMak/Aman routed panelでP1を大きく上回った。一方、公開コードを提出policyへコピーせず、P1から独立に実装したself-owned turn-planner surfaceは、その差を安定して再現しなかった。BestKnown、Champion、production、submissionは変更していない。

## Provenance

- 公開source: `makthanithin/improved-probabilistic-agent`
- raw public `main.py` SHA: `a81eab3eb761af95da2ddf70a67d6078897a2cd698dae4a7b6ea92de070fad2b`
- staged import-safe `main.py` SHA: `cdcf8329f5c091f994584ff5f987dd2de1e615679e838ecb74470f9cf2f89b04`
- staged tar SHA: `d4a8c5a9f6e11a11d0e6ac76f997420d269380c03582bf4a2dc0800297c90ddc`
- public sourceのruntime smokeはfault 0だったが、公開sourceはlocal-eval-onlyであり、self-owned提出policyのlineageではない。
- raw sourceはimport時の`deck.csv`書き込みを含んでいたため、診断用staged sourceではその副作用だけを除去した。policy logicのコピーを提出物へ昇格していない。

## Runtime contract finding

Mak sourceの`SEARCH_ALGO`は`search_begin(obs, your_deck=yd)`を呼ぶが、現在の`cg.api.search_begin`は全hidden-zone引数を要求する。この呼び出しは実行時に例外となり、source側の`except`から`AdvancedPolicy`へ戻る。したがって今回の強い差分を「search APIが効いた」とは解釈しない。直接診断候補では全64行が`DONE`・fault 0であり、探索経路の実効性ではなく、heuristic側の差分を含む結果として扱う。

## Direct public-policy diagnostic

Artifact: `runs/cg-mak-direct-vs-p1-diagnostic-20260816-v3/`

Mak/Aman routed panel（8 refs、candidate/control各32局、両seat、2 repetitions）で、同じroot deckを使ってMak sourceとP1を比較した。

| policy | W-D-L | objective | fault | seat rate | 判定 |
|---|---:|---:|---:|---:|---|
| Mak staged public source | 12-0-20 | 37.50% | 0 | 37.5% / 37.5% | 診断上の強い差分 |
| self-owned P1 control | 0-0-32 | 0.00% | 0 | 0.0% / 0.0% | control |

差分は`+37.50pt`だが、panel自体がMak/Aman-derivedであり、fresh holdoutではない。public sourceの成績をBestKnown、training target、提出policyへ使っていない。

## Self-owned policy surfaces

`src/mage_ptcg/meta_specialist/cg_p1_turn_planner_v1.py` を追加し、P1の各option独立scoreに対して、公開状態だけから「次の合法attackを作るattach/evolve/retreat/switch」を条件付きで優先する6 knob surfaceを実装した。search API、`search_begin_input`、logs、future RNG、相手private zoneは参照しない。candidate packageは次の隔離artifactとして生成した。

- package: `runs/cg-p1-turn-planner-diagnostic-20260816/default/package/`
- policy SHA: `075dc82bed4565c68c8f2a6b96eefc2b92ef3d8a7f17ecd61aa89ea2380fccbf`
- config SHA: `8738648e01f6c88f4df1d2b9a5cc177560f39d8704a9e7c551d3d9850f491527`
- parent P1 SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- focused tests: `4 passed`

同じMak/Aman-derived panelでは candidate `3W-0D-29L`、P1 `2W-0D-30L`で差`+3.125pt`だったが、candidate seat gapは`6.25%`でrisk-aware 5% gate外である。既存broad META_TRAIN（12 refs、candidate/control各48局）では candidate `5W-0D-43L`、P1 `6W-0D-42L`、差`−2.6662pt`、candidate seat gap`12.5%`だった。よって転移・seat-safe・freshnessを満たさず、P2／BestKnownへ昇格しない。

## 判定と次のゲート

判定は `NEW_PUBLIC_SOURCE_OBSERVED / SELF_OWNED_TRANSFER_NOT_PROMOTABLE`。Mak sourceは新しいmeta source候補として履歴化するが、同じMak/Aman panelのblind retryはしない。turn-planner surfaceもbroad panelで負差となったため、重いCEMへ直ちに接続しない。

次に必要なのは、runtime smoke用sourceと性能holdoutを分離した新しいpermission済みpolicy lineage、または複数の独立familyから生成した相関の低いmeta sourceである。全ゲート `legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` を通過するまで、`cg_bestknown_loop_v1.py`、deck phase、Champion変更、commit、push、Kaggle提出は行わない。

現行BestKnownはself-owned P1＋root deckのまま。P1 policy SHAは`1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHAは`2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`である。
