---
title: Strong Asset native pair ranking and BestKnown status (2026-08-12; superseded)
status: superseded-research-only
---

> この文書は2026-08-12時点のtop3 ranking履歴である。2026-08-13のplamen mutation common-protocol 4-block再評価と最終分類は、[common-protocol evidence](autonomous-deck-mutation-common-protocol-20260813.md)および[classification v3](autonomous-bestknown-classification-v3-20260813.md)を正とする。履歴値を最新のGlobalBestKnownやlongrun許可へ直接使わない。

# 結論

native pairを共通arenaで直接測る経路と、96局の全体screen、上位3 pairの各1536局確認が完了した。4 block合算では `tomatomato_archaludon` が首位だが、plamenとの差は5勝（0.326pt）、Luciferとの差は4勝（0.260pt）であり、CABTにseed setterがなくblock間game pairingもないため、厳密な一意のGlobalBestKnownではなく「現時点のEvaluationBestKnown候補」として凍結する。Champion変更、提出、training authorityの変更は行っていない。

## 一次96局ランキング

- 対象: smoke_ok=trueの96 assets（102件のうちslow 5件とR7 smoke=falseを別扱い）。
- 各asset: 24 reference opponents × 2 seats × 2 games = 96局。
- artifact: `runs/meta-specialist-asset-ranking-primary-fast96-20260812/asset_ranking.json`
- asset SHA: `7ad461caebd8bc8b21b1600f1719d8107f4654c0b2236c8ddcb57996f8b94b29`
- ledger SHA: `dc68512a72d57b804589692b2603f9b7fc872a61fc336d7ab93623641e57704a`
- manifest SHA: `161f18d0367d456b5a7cf1680d1d1a1ec619e9bbb82f984c0d1e6940c1269147`
- 9,216 requested / 9,207 DONE / 9 FAULT (全て medal_0019_df6f7443 の STEP_LIMIT)。faultは分母に残し、同assetをquarantine。

| rank | native pair | W/L/D/F | score |
|---:|---|---:|---:|
| 1 | plamen06_steel | 76/20/0/0 | 79.17% |
| 2 | tomatomato_archaludon | 73/23/0/0 | 76.04% |
| 3 | lucifer19_battlecore | 70/26/0/0 | 72.92% |

## top3 384局×2 block

Block1 artifact `runs/meta-specialist-asset-ranking-top3-confirm384-20260812/asset_ranking.json` SHA `58df60b5c3ace39fb827ede3adf229c2d3d626e14b9dd685dda0d18506f5690b`:

- tomato 279/384 = 72.656%; plamen 275/384 = 71.615%; Lucifer 266/384 = 69.271%; all fault0.

Block2 artifact `runs/meta-specialist-asset-ranking-top3-confirm384-block2-20260812/asset_ranking.json` SHA `776f499598d771af10bfcdec0b10e8578aa347d114b122099725c5ce38dc163e`:

- Lucifer 282/384 = 73.438%; tomato 273/384 = 71.094%; plamen 272/384 = 70.833%; all fault0.

合算768局:

| pair | wins/losses | score |
|---|---:|---:|
| tomatomato_archaludon | 552/216 | 71.875% |
| lucifer19_battlecore | 548/220 | 71.354% |
| plamen06_steel | 547/221 | 71.224% |

Block3 artifact `runs/meta-specialist-asset-ranking-top3-confirm384-block3-20260812/asset_ranking.json` SHA `e8ea484359d9085cdd2003c2877672f5245f9bb0fc8b1945148f141ab031acc7`:

- tomato 280/384 = 72.917%; plamen 278/384 = 72.396%; Lucifer 273/384 = 71.094%; all fault0.

Block4 artifact `runs/meta-specialist-asset-ranking-top3-confirm384-block4-20260812/asset_ranking.json` SHA `27d665871f2bad82dc9877a9dbd5fea51767caf9c5b28ad9b4804138fec01cc5`:

- Lucifer 282/384 = 73.438%; plamen 277/384 = 72.135%; tomato 275/384 = 71.615%; all fault0. Block4だけ明示的な `block_id=asset-ranking-top3-block4` を持つ。Block1–3は同じ既定block idを持つため、ledgerを結合するときはartifactとbase seedで再識別する。

4 blockをartifact単位で重複除外して合算した1536局結果（各pair 4×384、全4,608局、全fault0）は次のとおり。

| pair | wins/losses | score | seat0 / seat1 wins |
|---|---:|---:|---:|
| tomatomato_archaludon | 1107/429 | 72.070% | 561/768 / 546/768 |
| lucifer19_battlecore | 1103/433 | 71.810% | 554/768 / 549/768 |
| plamen06_steel | 1102/434 | 71.745% | 567/768 / 535/768 |

Block4の一次artifactは `runs/meta-specialist-asset-ranking-top3-confirm384-block4-20260812/` にあり、asset ranking SHAは上記、ledger SHA `84da3af844423958e4203675b4ee3988ebf8005138f707038aaf884aac454ed8`、summary SHA `4bc09a27dcb46ecb5225822500c3b4d4c909f57c8367e43e1f8815b938c384c7`、manifest SHA `4ec17e738cba550b9fc94948ac95e3b3ec667937f48877210476471421808f20` である。

## BestKnown classification (provisional)

- `EvaluationBestKnown`: 現時点の候補は `tomatomato_archaludon`（1536局72.070%）。R7は別診断96局を完走したが、slow 5件は未完了で、R7も局数非整合・smoke false/local_eval_onlyのためpool全102件を含むGlobal確定ではない。
- `TrainingEligibleBestKnown`: 現行permissionで新規収集可能なのはtomatomato/Lucifer等のqualified set。sealed再利用可能な比較資産はtomatomato/Lucifer。hard BC armは既に384局でWave6未満のため停止。
- `SubmissionEligibleBestKnown`: poolのlocal_eval_only assetはas-is提出不可。現時点の提出anchorは別途Rule v0 packageであり、native pool pairを提出候補とは扱わない。
- `BestKnownArchaludon`: native `tomatomato_archaludon`（1536局72.070%の暫定首位）。plamen/Luciferとの差が小さいため、AWR起点はtomato単独に固定せず、top3 tie cohortとして扱う。
- `GlobalBestKnown`: 未確定。slow 5件（`kinoshita_pimc_search`, `ozawa_metal_psychic_search`, `water_box_search`, `waterbox_search_v3`, `tientrum_alakazam_search`）は1局15秒fail-fastで240/240 fault、DONE=0となり、性能順位を得られなかった。R7は96局診断（68/28、fault0）を完走したが、tomato/Lucifer/plamenの1536局と局数が揃わず、smoke=false/local_eval_onlyのためGlobalには統合しない。slow5一次artifactは`runs/meta-specialist-asset-ranking-slow5-failfast1-20260812`、asset ranking SHA `eb14411fbc0ee71776498ec9a26341ac5692a16bf9732ac490e76cfd6864c201`。

## 未完了・次段

1. slow 5件は通常ランキングqueueから隔離し、1局/assetのfail-fast診断または静的runtime quarantineとして記録する。現状は2/240局で停止し、性能順位には入れていない。
2. R7 smoke=falseの診断結果（`runs/meta-specialist-asset-ranking-r7-diagnostic-20260812`）を保持し、training/submissionには使わない。slow5 fail-fast診断（`runs/meta-specialist-asset-ranking-slow5-failfast1-20260812`）は比較不能quarantineとし、hard native killなしに通常rankingへ戻さない。
3. 1536局結果は現時点のfreeze候補とし、tomato/Lucifer/plamenのtie cohortを共通BestKnown controlとして使う。4 blockの差は小さいため、次の改善はこの3 pairすべてを比較する。
4. BestKnown cohortから大量on-policy trajectoryを収集し、hard BCではなくactor-visible state value + AWR/filtered BCを新しいtarget authorityで実装する。既存Vhatは厳密public-onlyではないので、artifact上はactor-visible state valueと呼ぶ。
5. 必要ならpublic-only search/Qとdeck optimizationを交互に行い、毎回native BestKnown cohortを同一common arenaのcontrolにする。

評価器は`parallel-cabt-evaluator-v1`で、max in-flightをworker数に制限し、faultを分母へ含め、spawn/recycleを使用する。16 workerはCABT初期化競合で停滞したため、8 workerを採用した。slow search系は同じqueueへ混ぜない。
