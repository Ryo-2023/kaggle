# self-owned cross-lineage basket v18 / runtime fault diagnosis

## 結論

公式CSVから Fire/Charizard、Dark/Gengar、Lightning/Manectric、Grass/Venusaur の4 archetypeを生成し、P1 parameterized lineage 4件と independent-root lineage 4件を混成した8-source poolを作った。hash監査、promotion、初回runtime smokeは通過したが、P1固定CEMでGrass/Venusaurの2 sourceに `STEP_LIMIT` が再現し、fault-inclusive gateで fail-closed した。CEM結果は性能根拠として破棄し、BestKnown、P1 policy、root deck、Champion、production、submissionは変更していない。

## source生成

- P1 plan: [`self_owned_cg_policy_family_v18_cross_lineage_p1.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/configs/meta_specialist/self_owned_cg_policy_family_v18_cross_lineage_p1.json)、SHA `8dd7adc2e9d8fd670a96e4f9082f9065b4cd7703a886d7a741d5342c23bc3f71`
- independent plan: [`self_owned_cg_independent_policy_family_v18_cross_lineage.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/configs/meta_specialist/self_owned_cg_independent_policy_family_v18_cross_lineage.json)、SHA `44cf862ebf6ea434d05fc958fc0a93bd7ad249d21bcc5a227b8fd122e92df60a`
- mixed staged pool SHA `822dbf3c8af9f2fcf693e36fade9e20c1826ec8e90e5fbfafce639daf9c41d53`
- source/deck/policy hash collision with existing pool manifests: 0
- authority: all false、local evaluation only

## runtime gates

初回のCEM-role smokeは8 source、両seat各1局の16局で `16/16 DONE`・fault0（12W-4L）だった。しかしこの粒度ではGrass sourceの非終端を検出できなかった。

- initial smoke SHA `b9053762f2875281af7df154822b90635cf1883a4da6541ee1c82bbdf49ee202`
- promoted pool SHA `b497c89f0c887d449b9fe651ead5b5da501a3bdd65f93313313c9d2dc5907b52`
- `fresh_meta.json` SHA `14705c380d8a109b37ee66e5a16d64b40b678ea2e88d6e9372d101abadd299d0`
- split SHA `dac15378f9c145e976fedfa146e467bada78be837560eeb4f63a8631a41b1264`

追加診断としてGrass/Venusaur 2 sourceだけを各seat 4局、計16局で再実行したところ、12局DONE、4局STEP_LIMIT（fault率25%）だった。従って候補sourceとして runtime-safe ではない。

- diagnostic summary SHA `3d82486645ac3cdf3d09a01e3ddd8547ae52b84fcc25ea51b9ffd6fe78f9cdb0`
- fault source: `self-owned-cg-self-owned-cg-factorial-grass-balanced-v18-03-ea173d246c73`、`self-owned-cg-self-owned-cg-independent-factorial-grass-conservative-v18-07-6cb82c069c65`

## CEM結果

[`runs/cg-p1-cem-self-owned-cross-lineage-v18-20260816`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/cg-p1-cem-self-owned-cross-lineage-v18-20260816) は population／elite `8／2`、1世代、META_TRAIN 6 sourceで完走したが、screen 216局中17局が`STEP_LIMIT`。candidate/control双方にfaultが入り、positive delta gateは候補を空集合として、P1 centerを保持した。

- campaign manifest SHA `46266a63233716dd4dcbe365b9de35a8114cb59a9a30432dc944c4f04d1ea809`
- results SHA `9ec604f142736f16b43ce9b2b442b3f579684b9880ab584db60aea11bba4a7d6`
- `champion_changed=false`、elites `[]`、new centerはP1 identity

## 判定と変更した運用条件

判定は `SOURCE_GENERATION_PASS / INITIAL_SMOKE_INSUFFICIENT / CEM_FAULT_INCLUSIVE / RUNTIME_UNSAFE_GRASS_SOURCE / CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。v18 pool／seed／候補は性能使用済みとしてblind retryしない。

この失敗から、promotion前のruntime gateを「各source 1 game/seat fault0」から「各source 4 games/seat fault0」へ強化した。次のruntime-safe v19ではGrass/Venusaur sourceを除外し、P1系とindependent系を混成した6 sourceをこの強化gateで再検証する。
