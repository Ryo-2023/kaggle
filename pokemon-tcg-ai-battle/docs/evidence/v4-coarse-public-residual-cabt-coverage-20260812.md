# Coarse public residual CABT coverage smoke (2026-08-12)

## 判定

fixed reference bundleから作った zero-residual coarse table を、V4 Wave6
base policy sessionへ研究専用 factory として接続し、seed0/seed1それぞれ
6 opponents × 2 seats × 2 games = 24局を実行した。両seedとも fault 0、
しかし zero-init のため nonzero residual と top-1 change は 0 だった。
この smoke の目的は勝率候補の比較ではなく、exact context SHA gateから
coarse public bucket gateへ置換したとき、実戦局面でgateが発火可能かを確認
することである。

## 固定 identity

* subject deck: `opponents/tomatomato_archaludon/deck.csv`
* subject deck SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
* reference bundle: `runs/meta-specialist-public-bucket-reference-bundle-20260812/train-bundle.json`
* bundle SHA: `7dcf1cef7639a6a8b14ffcf6591bf3808fb7bed8edd85d82da369b3b6f511cda`
* ordered source-list SHA: `b21c329a2ab599ba80e02294052a19ed4d770ac0a7335fc6197f33e3567206cb`
* bundle sources: Wave6 seed0/seed1 train screen JSONL、2 sources、435 buckets、16,043 train prefixes
* zero table: `runs/meta-specialist-public-bucket-reference-bundle-20260812/zero-table.json`
* zero table SHA: `3d2c06c55a42c3a221eefcf518ef111aac44c9f986961ee3e817de02ea983480`
* preflight SHA: `7b79b57436c3d1029abf35e9895045b4f546ff1aea0db706c948c4f4aaec6689`
* protocol SHA: `0f98f6996e960f1a179cf7e8c767e20150e5b1622ef4e74159bf5a61804492ba`

CABT engineはseed setterを持たないため、評価は
`independent_stratified_not_game_paired`であり、同じbase seedでもpaired
gameやMcNemar統計とは呼ばない。

## 結果

| seed | games | wins-losses | fault | total decisions | known bucket | applied slots / valid slots | nonzero | top1 change | OOD pass-through |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 0 | 24 | 12–12 | 0 | 1,627 | 1,614 / 1,627 = 99.2010% | 4,945 / 6,436 = 76.8334% | 0 | 0 | 13 / 1,627 = 0.7990% |
| 1 | 24 | 12–12 | 0 | 1,705 | 1,692 / 1,705 = 99.2375% | 5,870 / 7,369 = 79.6580% | 0 | 0 | 13 / 1,705 = 0.7625% |

scoreは各seed 12/24 (50.0%)だが、同一条件のWave6対照を同一CABT RNG
blockでゲーム単位にpairできないこと、またzero tableでは行動が変化しない
ことから性能改善・悪化の証拠ではない。重要な実測は、coarse bucketでは
約99.2%のdecisionが既知、約76.8–79.7%のlegal slotが事前登録tableへ到達
した点であり、exact contextの既存coverage（約0.89%、slot適用約0.44%）
より実戦適用条件が大幅に広いことを示す。

## 実行 artifacts

* seed0: `runs/meta-specialist-coarse-public-residual-cabt-20260812/seed-0/fixed-six-24-zero.json`
  * SHA: `847e31eb009fde8e52bc99298b8d0e8d48c2ed2d7046497f1796d873b6df7a91`
* seed1: `runs/meta-specialist-coarse-public-residual-cabt-20260812/seed-1/fixed-six-24-zero.json`
  * SHA: `d38bb6be60c99f5a95520e7a88676421371708c330d19b986e44dda9c2b1918f`

runnerは `scripts/run_coarse_public_residual_cabt_eval_v1.py`、factoryは
`src/mage_ptcg/meta_specialist/coarse_public_residual_factory_v1.py` で、
production V4 actor_pool / decoder / recurrent commitを変更していない。
出力は `performance_evidence=false`、`coverage_evidence=true`、
`promotion_authority=false`、`training_permitted=false`、
`longrun_allowed=false` を固定する。

## 次の判断

coarse gateのcoverage preconditionは満たしたので、次はこの同じgate/identity
へ、seed別にmaterializeした public-state value target と
`record_normalized` / `episode_normalized` complete-action trainerを接続
する。zero tableの勝率を根拠にarm選択はしない。nonzero residualを作った
後もまず24局/seedをcoverage・fault・top1 changeのsmokeとし、両seed・両seat
で事前登録した変化率/安全条件を満たす場合だけ、Wave6同時評価96局×3
independent blocksへ進める。
