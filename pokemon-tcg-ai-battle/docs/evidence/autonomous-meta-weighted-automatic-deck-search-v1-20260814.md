# META_TRAIN重み付き自動deck探索（2026-08-14）

## 結論

META_TRAIN選択分布のカード頻度を使う自動候補生成と、既存native arenaへのresearch-only接続を追加した。親Tomato deck＋4候補を同一seed strataで評価し、weighted48では3候補が陽性、common24では3候補すべて陽性だった。しかし384確認では、`1097→1086`置換が−0.2604pt、`1097→5`置換が+2.7344pt、`1122→3`置換が+0.2604ptで、+3pt昇格基準を満たさなかった。全1536行はDONE/fault0、seat/paired-strata/GID gateを通過したが、768/longrun/promotion/submissionへは進めない。全候補はcandidate-onlyである。

## 実測

親はTomato native policy/deck（usage=`local_eval_only`）。META_TRAIN上位12行の重みを使い、候補は既存`opponents/**/deck.csv`と`runs/final-sprint-autonomous/**/deck.csv`のmultisetを除外して自動生成した。

| arm | 置換 | weighted48 | common24/96 | confirmation384 |
|---|---|---:|---:|---:|
| parent | — | 0.6495 | 59/96 = 61.458% | 262/384 = 68.229% |
| `68dff323082a` | 1097→1086 | 0.7173（+6.780pt） | 73/96（+14.583pt） | 261/384（−0.260pt） |
| `f28e8df31dcd` | 1097→5 | 0.7010（+5.143pt） | 68/96（+9.375pt） | 272/384（+2.734pt） |
| `f9ce23526a87` | 1122→3 | 0.7623（+11.279pt） | 67/96（+8.333pt） | 263/384（+0.260pt） |

weighted48は親＋4候補の240局、common24は親＋3候補の384局、confirmationは親＋3候補の1536局である。全て`workers=12`。weighted/common24は`worker_recycle_games=16`、confirmationは`worker_recycle_games=64`。confirmationの初回wrapperは評価器ledgerを封印した後のsummary整形で停止したため、既存ledgerを再実行せずstrict finalizerで結果を再導出した（`performance_rerun=false`）。

## 実装・証跡

- module `src/mage_ptcg/meta_specialist/meta_weighted_deck_search_v1.py` SHA `ea0050fba77980a1a6596c23a634c7e133afeda123a803faeb732747f22ee1d1`
- weighted runner `scripts/run_meta_weighted_deck_search_v1.py` SHA `caff4a1f7e82edf32541e6896edbd5fe26cabc4ec0d293ef5b7f1ba28ea1cedd`
- common24 runner `scripts/run_meta_weighted_deck_search_common24_v1.py` SHA `56c756574719bef6b72dafb7d8eb45df5214fd7718cee1721432e9288cbea55e`
- confirmation/finalizer `scripts/run_meta_weighted_deck_search_confirmation384_v1.py` SHA `77c150cbdff825b283b98342e20df7ba8a4215d0d3372a243ced9e6c12ad496a`
- focused tests `tests/meta_specialist/test_meta_weighted_deck_search_v1.py` SHA `49d16b059fc74ac59cd4b7ccd716621f87ee9529e793c20cde011d08f7e48cd5`, runner test SHA `a265159d22805a04ae96d486d6415460c579d1ea2f7b4eb36e2df7fede64bc0a`
- weighted manifest/summary SHA `c5072a6486c41daa9de13f86cabe28f1d539cbe61cae4254e014b5fa467fa774` / `40e873f8f73243dd112df5f56231c6257c528198627b63a02c7e5c15e1260a77`
- common24 summary SHA `24a6875c8d35358e3312b0e3399504b139d1c7bad469cdb83016fc00aef73fbc`
- confirmation summary/ledger SHA `632959c637b2051229c71c1be4907d90d82ced438bd454e0214b61c8d841164a` / `7076dc8c4aec11a83d8df8e611e45eeab84986bb5610d3d88d317f44bfa4db7a`

## 検証と境界

focused tests `5 passed`、module/runner `py_compile` PASS、docs validator `Validated 13 canonical documents.`、`git diff --check` PASS。既存production `main.py`/`agents`、submission package、native permission、Championは変更していない。authorityは全行false。META_TRAIN重みは候補生成とevaluation weightingだけに使い、behavior/teacher/private情報を学習へ流していない。384の最大候補でも+3pt gate未達のため、同候補の768/longrunは起動しない。新規候補を追加する場合だけ、同じ自動生成→weighted48→common24→384の段階をfresh rootで再開する。
