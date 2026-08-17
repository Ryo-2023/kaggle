# self-owned margin-gated public-state source / P1 CEM（2026-08-16）

## 判定

`SOURCE_GENERATION_PASS / RUNTIME_SMOKE_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`

新しい未使用meta source生成方法として、sealed cg-lethal P1へ「P1 scoreとの差が小さい合法選択だけに、actor-visibleな状態補正を加える」margin-gated rendererを追加した。公式カードCSVから生成したself-owned deckを6件、同一の新seed namespaceで作成し、runtime smoke後にpromote／split sealした。P1固定root-deck CEMは2世代を実行したが、独立positive、seat-safe、opponent×seat-safeを同時に満たす候補は0件だった。従ってcenter、BestKnown、Champion、production、submissionは変更していない。

## 出典と固定identity

- P1 parent policy: `runs/final-sprint-autonomous/cg-policy-screen-v1-retry-safe4-20260814/candidates/cg-lethal-target-v1/package/main.py`、SHA-256 `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- root deck `deck.csv` SHA-256: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- root-deck control package: `runs/cg-kaggle-kernel-meta-promoted-fresh-union4-rootdeck-v2-20260816/p1-root-control-v2`、control `main.py` SHA-256 `a05bf3dd311c543fd363f2d883abd30e9a3822b7d8df6a451a8b7705a3122b66`
- plan: `configs/meta_specialist/self_owned_cg_margin_gated_family_v1.json`、SHA-256 `46d473e44c19aee59687428f5022be6b97d58b72b9de3f0d49b186c5b31ee8dc`
- renderer: `src/mage_ptcg/meta_specialist/cg_p1_margin_gated_renderer_v1.py`
- generator: `scripts/generate_self_owned_cg_margin_gated_meta_v1.py`
- CEM core／runner: `src/mage_ptcg/meta_specialist/cg_margin_gated_cem_v1.py`、`scripts/run_cg_margin_gated_cem_v1.py`

`ono-`は外部作者名ではなく、ローカルrepoの識別子（`/home/bfe-lab-ono/...`、過去branch／commitの`agents/ono-cg-lethal-v1`）である。本sourceのpolicy／deckは上記SHAで一次artifactへbindしている。

## source epoch

staged rootは `runs/cg-self-owned-margin-gated-v1-20260816/`。公式 `data/raw/EN_Card_Data.csv` と、既存のcross-lineage deck specだけを使い、`runs` と `opponents` のcanonical deck hashを生成前に走査した。新しいsource epoch／seed namespaceは `self_owned_cg_margin_gated_family_v1_20260816`／`self-owned-cg-margin-gated-fresh-v1-20260816` であり、既存v16〜v19、action-conditioned v2、deck-conditioned adversarial v1のpoolを再利用していない。

6 variantsは次の通りである（各policyはP1 parentから新規renderし、各deckへbindした）。

| variant | policy SHA prefix | canonical deck SHA prefix |
|---|---:|---:|
| `dark-switch-01` | `66662e70843e` | `619a2d2f609e` |
| `fighting-evolve-03` | `4884c7056db7` | `f909f275965e` |
| `fire-lethal-00` | `4627b4ae0c19` | `080da390138e` |
| `lightning-prize-02` | `1b0ce0b6d97e` | `1671d37830aa` |
| `psychic-prize-05` | `47982b68b554` | `c8d5489cd074` |
| `water-switch-04` | `09b1dec3b814` | `210a36cc121f` |

生成manifest SHAは `6a87fc9b142a8e2bf0af0e4aa6898590ae2e1bd2c7796bf6762b0194bf520d5e`、staged pool SHAは `ff4b525c25cbb1a141a502f5c2f4494310c85de46efcb6581e2cbe299d043436`。promote後は pool `4cd6f198a15c4bd4f1121b5c72e782c9ac0bac759b712fb20d703eb8603e7489`、fresh meta `cb9da86d6762fbef87d877ced8691f4cb7d1fbd11819641685392c26f83670cb`、meta manifest `dbdeee18d06c74bd557c08267d0c5be45a5c62ce9dac27d4c657c006c96a91e9`、split `9d5e942a2bc85be4df155b809aaf1efd3f3da1e4289371ff7cb6db9a7e4353f5` となった。splitは `META_TRAIN=4 / META_DEV=1 / META_FINAL=1`、全行 `training_exposure=0`、`usage_boundary=local_eval_only` である。

## runtime smoke

`runs/cg-self-owned-margin-gated-v1-20260816/smoke-bounded-w1-4x/` で6 source × 4 opponent × 2 seat × 4 repetition = 192局を実行した。`DONE=192/192`、fault `0`、draw `0`、smoke summary SHA `6925f1895a143524152f5ec095ce932aeb0e336eecbd9274b7ed28fc26f55b1b`。集計の30W-162L（15.625%）はruntime gate付随値であり、BestKnown性能やpromotion根拠ではない。

## CEM

corrected campaign rootは `runs/cg-self-owned-margin-gated-cem-v1-corrected-20260816/`。campaign seed `2026081801`、population／elite `8／2`、2世代、screen `1 game/opponent/seat`、独立再評価 `2 repeats × 2 games/opponent/seat`、workers `4`、worker recycle `16`、META_TRAINのみを使用した。各candidate/controlは同一blockで同一opponent・seat・seed strataを持つ。strict gateは、各独立repeatのdeltaが全て正、fault 0、candidate seat gap ≤ `0.05`、opponent×seat gap ≤ `0.05` である。

| generation | screen上位 | 独立delta（top 2） | gate結果 |
|---:|---|---|---|
| 0 | c02 `+25.0pt`、c04 `+25.0pt` | c02 `−18.75/+6.25pt`、c04 `+6.25/−12.5pt` | seat／opponent×seat不安全、更新なし |
| 1 | c00 `+25.0pt`、c04 `+12.5pt` | c00 `+6.25/+37.5pt`、c04 `+6.25/0pt` | seat gap `25%/25%` 等、更新なし |

全screen／独立rowはfault 0だったが、accepted countはgen0／gen1とも `0`。centerは初期 `score_margin=6000`、その他7係数 `0` のまま、最終center SHAは初期と同一である。CEM manifest SHAは `e57466bd1ecf73d7aaf71bc8a50e234fb51aa0335eea21d1675d7966cad7ea6b`、generation results SHAは `2ac06a8ffbb39bb9093887fe4f541063b22a3d2b9480774d745f59e2f90dcc3c`／`6423cf610ac588f1c5b27ea7845bcecf97c0fc63766b687c8883e7d8719e6494` である。

初回fast run `runs/cg-self-owned-margin-gated-cem-v1-fast-20260816/` は、screen後の監査でscreen control集計がcandidate間で混ざる実装不備を発見したため、独立再評価途中で停止した。そこからの性能結果は採用せず、`block_id`でcandidate/controlを同一stratumへ限定する回帰テストを追加してcorrected runを再実行した。

## holdout／権限

候補がstrict gateを通過しなかったため `META_DEV` と `META_FINAL` は読み出していない。`cg_bestknown_loop_v1.py`、deck phase、Champion変更、production／submission package、commit、pushも未実行・不変である。今回のpoolとCEM候補は性能使用済みとしてexposure ledgerへ残し、blind retryのsourceには戻さない。

## 検証

- margin renderer／generator／CEM core／pair aggregation focused tests: `11 passed`
- renderer／generator／CEM modules: `compileall` PASS
- package verifier: 6/6 PASS
- runtime smoke: 192/192 DONE、fault 0
- CEM corrected: 2世代、全screen／独立row fault 0
