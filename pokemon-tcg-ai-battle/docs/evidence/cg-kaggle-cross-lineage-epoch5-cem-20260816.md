---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-16
---

# 公開kernel intake epoch5／cross-lineage meta／self-owned CEM

## 結論

公開kernelから安全に受理できた3 sourceを、policy親とdeck親の直積（同一親の組合せを除く6組）へ再構成し、P1 packageに対するruntime smoke、promotion、hash-bound split、self-owned deck-bound CEMまで接続できた。source generation／runtime gateは成功したが、CEM候補は独立seedで再現せず、BestKnown・Champion・production・submissionは変更していない。今回の判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED` である。

## 入力sourceと受理境界

Kaggle公開出力を `runs/cg-kaggle-kernel-discovery-20260816-next/` から再検査した。最初のepoch5 intakeは、filesystem write、dynamic execution、source identity reuse、違法deckをfail-closedで除外した。deck headerやimport-time writeだけを除去した staged variantは、変更内容を一次artifactへ記録し、競技ロジックを補っていない。

最終 intake `runs/cg-kaggle-kernel-meta-intake-public-fresh-epoch5d-20260816/` は3件を受理し、3件を除外した。

- 受理: `kaggle_prvsiyan_search_alakazam_v12_staged_20260816`（Prvsiyan）、`kaggle_sushanth_emboar_strategy_staged_20260816`（Sushanth Mega Emboar）、`kaggle_sushanth_zacian_run_staged_20260816`（Sushanth Zacian）。
- 除外: Samrish（`source_identity_reused`）、Siddharaj（静的検査の `dynamic_execution`）、Sushanth Greninja（`'Card'`／`invalid_deck`）。
- 最終 intake fresh SHA: `56d41011cb5d1ab1defe7ca5e96b716598a832c65af2631c9c31b3acf382b98f`。
- config SHA: `ec103980ee0b727120537847ef1035be3e74bc074f270ffa1fc7618ce3e516d9`。

受理sourceの staged tar SHAは、Prvsiyan `218e1532cd8e40fd63603759bffae55195d711fc2e1dba7578bd4e41104d1a21`、Sushanth Emboar `4bdfb4a4b8e0425a04fd77c4d83f884c8624257907e03739274adb0162bb60f8`、Sushanth Zacian `812c1e8ca8c9b79924c36b141ccd595d57c4d516e3dd757efb5e44bb3a026a1d` である。前者は import-time の `deck.csv` write 1行を除去し、後二者は `Card ID` headerだけを除去した。いずれも policyの意図を追加していない。

## smoke／promotion

受理3 sourceをP1 package（policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`）と両seat各2局で実行した。`runs/cg-kaggle-kernel-meta-smoke-public-fresh-epoch5d-p1-20260816/` は12/12 `DONE`、fault 0、5W-7L、score `0.4166667`、evaluator SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08` だった。

その後 `scripts/generate_cross_lineage_meta_v1.py` で、3 policy parent × 3 deck parentの非対角6組を生成した。再構成recipeは `CROSS_LINEAGE_POLICY_DECK_RECOMBINATION_V1`、cross pool SHA `9861f9c8ed8c35fa57ec7b2d1895ed1e02de1ba9792449d1cc359ae99b8bae18`、cross fresh SHA `23d15341ca149c0f06638bb1a9dfdc915fb60b7f65f230e71749a3d27d89369c`、meta manifest SHA `96fe602dc1206ef0a0b34de18066062ca9671867154552c0dad8a710bdede04b` である。

cross poolのP1 smoke `runs/cg-cross-lineage-smoke-public-fresh-epoch5-p1-20260816/` は24/24 `DONE`、fault 0、21W-3L、score `0.875`。smoke summary SHAは `c06c4b51b0d17d87671f28c235b38e72e69099eab5e731d427e62aa8301c2d79` である。promotion後のrootは `runs/cg-cross-lineage-meta-promoted-public-fresh-epoch5-p1-20260816/`、pool SHA `fa22538880d29ce7cd9e322991cf9a94d93e03b44d45acdfa4bc14a5f3244f08`、fresh SHA `9ca211e8a5f00460c79a96596e232d4e1e8c24cb26aa3397455dd3f5e22f3494`、meta manifest SHA `b83513fdb3b6bae89c88156e7c7a3f1dcbc746736b0743f8806bcff25f3fa052`、split SHA `cb55300b15dc8cf8c7d23521977705bb25570ffd3c0e386fd719bad827a3c844` である。splitは `META_TRAIN=4 / META_DEV=1 / META_FINAL=1`、全行 `training_exposure=0`、`local_eval_only`、両seat／fault-inclusiveである。

## self-owned CEM

`runs/cg-self-owned-cg-policy-cem-cross-lineage-epoch5-g01-20260816/` で、P1 sourceを不変のpolicy parentにし、同じdeck-bound package `p1-core-control`をself-owned deckとcontrolに使った。deck SHAは `21620b5f30317f380c020f98672c524ba243b04f180df22830693e8f5acbaff2`、control policy SHAは `a52316249a6f5aa8bec19fd1a4fa904fcc684f5df4576e867bc5154ffae551d4` である。

campaignは seed `202608965`、population／elite `8／2`、1世代、`META_TRAIN_ALL`、screen 2局／opponent／seat、独立2 block×2局／opponent／seat。screen 144局＋独立96局の全rowが `DONE`・fault 0だった。screen上位c01は15/16、controlは10/16、delta `+31.25pt`だったが、独立deltaは `+12.5pt` と `−6.25pt`、risk-aware min delta `−6.25pt`、seat／opponent-seat safe false。selectionは `risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`、eliteは `incumbent-center`×2、P1 centerは不変である。campaign manifest SHA `b443facf5256001a3dfaff5f1da27945c7a6b903d0f53b76ce46cf0a865accb5`、generation results SHA `279020c76ba76928090d1c30fd7ba4551709143ef239c6a49ec8631e35b2ff56`。

screen上位c01 (`config_sha256=8e170764febde51d32063c766cc1463446f954b60964760cec40aa3e84596394`) を、同じMETA_TRAIN・未使用seed `202608967`・8局／opponent／seatで追加確認した。`runs/cg-self-owned-cg-policy-cem-cross-lineage-epoch5-c01-confirm-train-retry-20260816/summary.json` の64局結果は候補48勝、control54勝、fault 0、objective `0.75` 対 `0.84375`、delta `−9.375pt`。screenの改善は再現せず、P2／BestKnown昇格条件を満たさない。

この確認はMETA_DEV／META_FINALを読んでいない。したがってDEV／FINALは未使用のまま保全し、`cg_bestknown_loop_v1.py`、deck phase、Champion、production、submissionは起動していない。

## 判定と次手

cross-lineageは「公開sourceのpolicy／deckを別々に再利用し、両方の親を同時に採用しない」meta生成方法として、静的検査、runtime smoke、promotion、split loader、CEMまで安全に接続できることを実証した。一方、3 sourceからの6組は親policy／deckの相関が高く、screen優位を独立seedで再現しなかった。したがって同じpool、同じc01、同じseedのblind retryは行わない。

次は、公開sourceの数を増やすだけでなく、policy SHA／deck SHA／generator lineageの相関を明示的に下げた新epoch（別作者か、公式カードDBから独立に生成した別policy renderer）を作り、先にsource smokeとexposure ledgerを固定する。`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を全通過した候補だけを `cg_bestknown_loop_v1.py` に渡す。

現行BestKnownは変更なしで、ラベルは「self-authored P1 policy＋common/public root deck」である。`ono-`は公開作者名ではなく、local Git identity／branch由来のローカル識別子である。
