# self-owned independent cross-element source v2 / P1 CEM

## 結論

公式カードCSVから別のdeck archetype（Fighting/Zygarde、Water/Starmie、Psychic/Gardevoir）を生成し、P1とは独立した root policy renderer で8 sourceを作った。hash衝突、sealed promotion、CEM-role runtime smokeは通過した。P1固定CEMでは c00/c05 が独立再評価で正の差分を再現したが、相手別 seat-safe gate を満たさず、`champion_changed=false` となった。BestKnown、P1 policy、root deck、Champion、production、submissionは変更していない。

## source生成と境界

- source epoch: `self_owned_independent_cross_element_v2_20260816`
- Fighting deck spec: [`self_owned_cg_deck_spec_v17_cross_element_fighting.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/configs/meta_specialist/self_owned_cg_deck_spec_v17_cross_element_fighting.json)、SHA-256 `5fc03d1df69307cdcde04227553fcd387499742499cd38a4e57efa3ef0e52243`
- Water deck spec: [`self_owned_cg_deck_spec_v17_cross_element_water.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/configs/meta_specialist/self_owned_cg_deck_spec_v17_cross_element_water.json)、SHA-256 `a767e9e14e92f4934964208106c72bd3600c1ba31288d573cacce5004d2e59f0`
- Psychic deck spec: [`self_owned_cg_deck_spec_v17_cross_element_psychic.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/configs/meta_specialist/self_owned_cg_deck_spec_v17_cross_element_psychic.json)、SHA-256 `f8aa1202d7430f3684e868daac22f84e3836bb194d05b444840d698487f7dbb9`
- factorial plan: [`self_owned_cg_independent_policy_family_v2_cross_element.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/configs/meta_specialist/self_owned_cg_independent_policy_family_v2_cross_element.json)、SHA-256 `9e39382fe379029ae7b79d4b3caeccf8c036a517288fd4f797137115a08b628a`
- factorial manifest SHA `00e3ea511c326e8440b40511c9c580053aec99d7bfebd0ca60e30ae038c092d1`
- staged batch manifest SHA `88a8668da014f665f949cf6b190125834d9b1b460d65701d65065591d6c0fc97`
- staged pool SHA `25404f4c4bbe140ca468722f0d8f245fd320809ae3a5b516e83af72b82d609dd`

8件すべてを既存 `runs/**/pool_manifest.json` と照合し、policy hash／canonical deck hashの履歴衝突は0件だった。sourceは公式CSVとローカルrendererだけから生成し、用途は local evaluation only、authorityは `training/promotion/submission/longrun=false` である。

## runtime smoke と promotion

sourceをsubjectにする向きではなく、CEMと同じ P1 subject 対 source opponent の履歴smokeを実行した。対象は8 source、両seat各1局の16局で、すべて `DONE`、fault 0、12W-4Lだった。

- smoke summary: [`smoke_summary.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/cg-self-owned-independent-cross-element-v2-20260816-historical-smoke-v2/smoke_summary.json)、SHA-256 `c18e88e26f38b8fc92127bfb0d5a71770f3ab52af783183bac66af0ed3fdc2ce`
- promoted pool: [`promoted`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/cg-self-owned-independent-cross-element-v2-20260816/promoted)
- promoted batch manifest SHA `7d6e1b4435da5a6c4a41b22ca70e9fa5cddbc3ab40cd123f6cf88c02ef14af3d`
- promoted pool SHA `325b0c33bec126928f588f04d15ce978b4db855489ca1f72f92f3f781d3e6aaa`
- `fresh_meta.json` SHA `0b2bf2fb491f46b53baac12999307b640bb9f2c0d1e4f08d3a2ee84bd5c37a64`
- `meta_manifest.json` SHA `55240fb8e36f5860e9fdd34e40c80fb4d2de69d3d11aab01eed0b2aa3ac224c8`

## split と P1固定CEM

splitは性能探索前に固定し、`META_TRAIN=4`（Zygarde balanced／lethal、Starmie lethal、Gardevoir ability）、`META_DEV=2`（Starmie reserve／search）、`META_FINAL=2`（Zygarde retreat／Gardevoir conservative）とした。DEV／FINALはCEMのsearch／update中に読んでいない。

- split: [`cg_historical_split.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/cg-self-owned-independent-cross-element-v2-20260816/promoted/cg_historical_split.json)、SHA-256 `bba92ad1cd182ee8c05cad2d5122b70c90432ca76464bead62344c2c5b2922ce`
- CEM root: [`cg-p1-cem-self-owned-independent-cross-element-v2-20260816`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/cg-p1-cem-self-owned-independent-cross-element-v2-20260816)
- campaign manifest SHA `9a032923c57ffc2a07de483823220debdb646812858928bf8eb22f2b4baa8266`
- generation manifest SHA `6b757ba313d80216abed304c6e085f28b07f922df55542502417ea67438b3f2b`
- results SHA `8b43db2b2021494c8d9432d11c2216c33b225ecec9a45f65fbdac2d69b99a1c5`
- population／elite `8／2`、1 generation、screen 144局、独立再評価 48局、全row `DONE`・fault 0

screenでは control 12W-4L（75.0%）に対して、上位 c00 は15W-1L（93.75%、delta +18.75pt）、c05は14W-2L（87.5%、delta +12.5pt）だった。独立再評価（各8局×2 repeat）では、c00の delta は `+50.0 / +50.0pt`、c05は `+37.5 / +37.5pt` で、いずれも positive delta 自体は再現した。

ただし相手別 seat gap gate は、c00／c05とも `opponent_seat_safe=false`、`seat_safe=false` だった。従って選抜は次世代内部の探索中心更新に留まり、campaign manifestの `champion_changed=false`、P1 center／BestKnownは保持した。DEV／FINALは採否判断の前に読む必要がないため未読のままとした。

## 判定と次の条件

判定は `SOURCE_GENERATION_PASS / HASH_COLLISION_PASS / PROMOTION_PASS / RUNTIME_SMOKE_PASS / CEM_POSITIVE_BUT_SEAT_UNSAFE / BESTKNOWN_UNCHANGED`。v17 source、seed、候補は性能使用済みとして同一poolのblind retryを行わない。

次は、今回の independent root renderer と3 archetypeの組み合わせをそのまま再試行せず、相手別 seat gapを評価設計に組み込んだ低相関 source（policy familyとdeck familyを同時に変更するか、source数を増やして候補選抜のnoiseを下げる）を新epochで生成する。P1→CEM→fresh DEV→fresh FINALの順序と、P1／root deckの不変条件は維持する。
