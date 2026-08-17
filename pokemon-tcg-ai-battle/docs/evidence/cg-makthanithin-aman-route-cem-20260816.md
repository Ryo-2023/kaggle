# cg Makthanithin × Aman actor-visible routed ensemble / P1 CEM（2026-08-16）

## 結論

未使用だった公開 Kaggle kernel `makthanithin/improved-probabilistic-agent` を新しい policy lineage として intake し、union5ですでに使用済みの Aman policy と組み合わせた actor-visible routed ensemble を生成した。runtime smoke は TRAIN 候補10件・40局を `DONE`・fault 0 で完了した。

この promoted poolに対する P1固定CEMは screen 416局、独立再評価256局を全て `DONE`・fault 0 で完了したが、独立候補は control を上回らず、seat-safe／opponent×seat-safeも満たさなかった。positive-delta／risk-aware gateは incumbent centerを保持した。未使用 DEV／FINALでのP1 baselineは32局・fault 0（4W-0D-28L、12.50%）だった。

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。P1 policy、root deck、BestKnown、Champion、production、submission、`opponents/`、commit、pushは変更していない。

## 1. provenance と権限境界

Makthanithin sourceは公開 notebookから取得した local-eval-only snapshotである。公開スコアは性能根拠に使わず、policy snapshotとCABT再現性だけを検査した。Amanは既存のunion5で性能使用済みなので、今回のpair全体を未使用系譜とは扱わない。

| source | 公開ページ | 固定情報 |
|---|---|---|
| Makthanithin | [makthanithin/improved-probabilistic-agent](https://www.kaggle.com/code/makthanithin/improved-probabilistic-agent) | Probabilistic Expectimax policy。raw main SHA `a81eab3eb761af95da2ddf70a67d6078897a2cd698dae4a7b6ea92de070fad2b`、staged main SHA `cdcf8329f5c091f994584ff5f987dd2de1e615679e838ecb74470f9cf2f89b04`、staged tar SHA `d4a8c5a9f6e11a11d0e6ac76f997420d269380c03582bf4a2dc0800297c90ddc` |
| Aman | [aman5153684/a-crustle-aware-fighting-agent](https://www.kaggle.com/code/aman5153684/a-crustle-aware-fighting-agent) | union5で既使用の policy parent。root deckと同じ deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` |

Makthanithinのstageでは、import-timeの `deck.csv` 書込みを1行だけ除去し、policy logicとdeck bytesは変更していない。network、subprocess、dynamic import、追加 filesystem writeの静的検査は空集合だった。intakeは `accepted=1 / rejected=0`、authorityは全てfalseである。`ono-` はこの公開sourceの作者名ではなく、self-owned package branch `agents/ono-cg-lethal-v1`／commit author `bfe-lab-ono`に由来するローカル識別子である。

## 2. routed ensemble と split

generatorは `ACTOR_VISIBLE_ROUTED_ENSEMBLE_V1` を使い、公開 `turn`、visible active／bench、stadium、selection context、相手の公開damage等だけで親policyを切り替えた。相手の非公開情報、future RNG、expert/action labelは使っていない。6 routing recipe（`PUBLIC_HASH_V1`、`TURN_PARITY_V1`、`OPPONENT_BOARD_HASH_V1`、`OPPONENT_DAMAGE_SWITCH_V1`、`CONTEXT_THREAT_SWITCH_V1`、`CONTEXT_TURN_HASH_V1`）をMak→Aman／Aman→Makの両方向へ適用し、12候補を生成した。

generated rootは `runs/cg-makthanithin-aman-route-meta-20260816/`、promoted rootは `runs/cg-makthanithin-aman-route-promoted-20260816/`。

- generated pool / fresh / meta / split SHA: `0d76435e7054010ce69be3783df85fd8ec90f8a3237f73fbf3ba531f9e59cb8f` / `2f2f74627d2d5b3e934aba7db4317b48bc812d5ca9909555e25c2e64c08f9bb1` / `67d3e96e568030de16d6782c7d628b0cfb4523a82bd6d7ceea399814557b3f54` / `b24099b0fb6c9bb0aa7113c54c33dc4beece24c09e43a4bb7a766727e54f1024`
- promoted pool / fresh / meta / split SHA: `287c78324a869e7724f8d6eedbfeb4317ab868a4c5a85de7ad945d380249cd80` / `c911d8e14b027a025329c314ab5376ab05f30174b3666701fad867344dfdacbb` / `e4992f55667b68e107d3619592b5dd2b0493034202b5afc62ac7adb64fc8dccc` / `bdf7dd77ae1a18d420145144ac2b0f632982c7bf60aaebb2ad1747201a3fe564`
- rebound split: `META_TRAIN=8`、`META_DEV=1`、`META_FINAL=1`。全 row は `training_exposure=0`、`usage_boundary=local_eval_only`。

TRAIN-only smokeは10 ref、各 opponent×seat 2局、合計40局。`DONE=40/40`、fault 0、2W-0D-38L、score rate 5.00%だった。promotionはsmoke済み10件だけを対象にし、DEV／FINALの性能結果はCEMへ投入していない。

## 3. P1固定 CEM

実験 rootは `runs/cg-p1-cem-makthanithin-aman-route-20260816-g01/`。P1 policy SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHAは `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`。campaign seed `202608170`、population／elite `12／3`、initial scale `0.20`、`META_TRAIN_ALL`、独立 re-evaluation 2 block、各 opponent×seat 2局、positive-delta gate、risk-aware updateを使用した。

| stage | 局数 | 結果 |
|---|---:|---|
| screen | 416 | `DONE`、fault 0 |
| independent re-evaluation | 256 | `DONE`、fault 0、16W-1D-239L |

独立再評価まで到達した3候補は次のとおり。いずれも独立平均が負で、seat-safe／opponent×seat-safeではない。

| candidate | screen Δ | independent repeat Δ | mean / worst Δ | 判定 |
|---|---:|---:|---:|---|
| `cg-p1-cem-g00-c01-1fceda07ea45` | 0.00pt | −3.125 / −15.625pt | −9.375 / −15.625pt | invalid |
| `cg-p1-cem-g00-c06-4bceae9d078c` | −3.125pt | −3.125 / −1.5625pt | −2.34375 / −3.125pt | seat-unsafe |
| `cg-p1-cem-g00-c07-d5f88e3bde06` | −3.125pt | −3.125 / −9.375pt | −6.25 / −9.375pt | seat-unsafe |

結果の `elite_selection` は `risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`。CEM center、P1 package、BestKnownは不変である。

## 4. 未使用 holdout baseline

rebound後の `META_DEV`（`routed_routed_mak_aman_damage_ec72caedd11a`）と `META_FINAL`（`routed_routed_mak_aman_hash_e451582127dd`）を、CEM選定後にP1 controlだけで各 opponent×seat 8局測定した。artifactは `runs/cg-makthanithin-aman-route-heldout-p1-baseline-20260816/`、合計32局、`DONE=32/32`、fault 0、4W-0D-28L、score rate 12.50%である。この測定は新候補の選定には使っていない。

## 5. 再現コマンドと次の再開条件

主要な再現コマンドは次のとおり。

```bash
PYTHONPATH=src:. python scripts/run_cg_p1_cem_v1.py \
  --output runs/cg-p1-cem-makthanithin-aman-route-20260816-g01 \
  --split runs/cg-makthanithin-aman-route-promoted-20260816/cg_historical_split.json \
  --source-package runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1 \
  --control-package runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1 \
  --pool-root runs/cg-makthanithin-aman-route-promoted-20260816 \
  --generations 1 --all-train-refs --reeval-for-update \
  --reeval-repeats 2 --reeval-games-per-opponent-seat 2 \
  --positive-delta-gate --risk-aware-update \
  --initial-scale-fraction 0.20 --campaign-seed 202608170 \
  --population-size 12 --elite-count 3 --execute
```

今回のMakthanithin×Aman route pairはCEM性能使用済みであるため、同じpairのblind retryは行わない。次は未性能使用policy lineageを含む新しい混合pool、または性能用holdoutを最初から分離した新source recipeを作り、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL → cg_bestknown_loop_v1.py` の順に進める。
