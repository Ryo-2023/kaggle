# cg behavior-family source generation m–p — 2026-08-15

## 結論

Starmie専用だったvisible-state priority変換をAlakazam、Psychic、Comfey/Hydreigonへ広げ、4 variantずつを新規policy SHAとしてsealした。全epochでruntime faultは0へ抑えられたが、独立CEM／FINALで安定してP1を上回る候補は得られなかった。behavior-family generatorは「meta sourceを生成してCEMへ接続する方法」として機能した一方、現時点でBestKnown更新の根拠にはならない。

## 生成したsource family

- Alakazam epoch m: `runs/cg-alakazam-behavior-family-meta-20260815-m/`、Abra/Dunsparce/Fezandipiti/Poffin priorityの4 variant。
- Psychic epoch n: `runs/cg-psychic-behavior-family-meta-20260815-n/`、Zacian/Xerneas/Lillie/Cheren draw priorityの4 variant。
- Hydreigon/Comfey epoch o: `runs/cg-hydreigon-comfey-behavior-family-meta-20260815-o/`、deckout aggressive/conservative、Comfey setup、Litwick setupの4 variant。
- Hydreigon/Comfey epoch p: 別historical baseから同系統4 variantを再生成した `runs/cg-hydreigon-comfey-behavior-family-meta-20260815-p/`。

各variantはvisible-state-onlyの固定変換、static findings 0、`local_eval_only`であり、相手の非公開情報・将来乱数・外部入力を追加していない。生成実装は `src/mage_ptcg/opponent_ingest/behavior_family_meta_v1.py`、CLIは `scripts/generate_starmie_behavior_family_meta_v1.py` に集約した。

## CEM / fresh結果

各campaignはP1 control固定、population 8、elite 2、2世代、screen＋独立re-evaluation＋fresh DEVを実行し、全heavy blockがDONE・fault0だった。

| epoch | CEM要点 | fresh FINAL | 判定 |
|---|---|---|---|
| m Alakazam | gen1 screen positiveが独立で負差へ反転。center DEVは見かけの正差だがseat差が大きい | 13W-0D-19L 対 P1 14W-0D-18L、−3.125pt、seat gap18.75% | `NOT_PROMOTABLE` |
| n Psychic | screen上位は独立gate不成立、center保持 | このepochのderived `cheren_draw_first`をk candidate-03でcross-source確認し、+4.6875ptだがseat gap9.375% | `NOT_PROMOTABLE` |
| o Comfey | center保持、DEVはsource間の振れを示した | k candidate-03は+9.375ptだがseat gap6.25% | `NOT_PROMOTABLE` |
| p Comfey（別base） | candidate-03は独立で+18.75pt、centerはseat-safe | FINALは19W-0D-13L 対 P1 21W-0D-11L、−6.25pt、seat gap6.25% | `NOT_PROMOTABLE` |

主要artifact SHAは次の通りである。

- m CEM manifest: `ef2d1e07f16ffdeb415fcb2174a5976c462239493897c757854779eca39c870a`
- m FINAL summary / manifest: `8e00815b6e0fb529a836d5f20dd00daec9c9a432bb974f81399b8c938e8a22b5` / `f922a86db5d7640d0afc051a7e8c1af0dcf9adb90eb4ef2eb4b6f8bda09e4388`
- n CEM manifest: `911ff05c28a07c2eb90418361d109272cb4e6e1e1c79cde8cf75c2119ef8f10a`
- o CEM manifest: `1d4ef95c284f7fd06ed2e8b7c942801e3604f91daa2bd8e2e320296069db56ee`
- p CEM manifest: `fbbb986dd89865bf1cb915c03f001c765134c50c1b238f8dafae4839567db226`
- p FINAL summary / manifest: `f3e1846ddb29aab8adffcbd8d7ef5eb1cc9321c37cde202e9bc19b54f46c02bd` / `a0419cbbd2716404ad7b4c3cdd770183e2af132fae02e9f01bd8e0d812016cfa`

## 研究判断

同一sourceのpriority variantを増やすだけでは、screen上の差分をfresh sourceへ安定転移できない。今後は候補数を増やすblind retryより、(a) behavior family間の相関をmanifestへ明示、(b) source familyごとに複数FINALを先に確保、(c) seat gapをCEM更新時から目的関数へ入れる、の順でsource-generation方法を改良する。正差・seat-safe・fault0が揃うまではP1をresearch parentとして維持する。
