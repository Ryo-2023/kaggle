# self-owned v14／v15 source生成とroot-deck CEM（2026-08-16）

## 結論

self-owned source生成と公開sourceの混成poolを、現行の正典である P1＋root deck に結び付けた。全CABT行は `DONE`・fault 0 だったが、v14／v15とも独立再評価の positive・seat-safe・opponent-safe gateを満たさなかった。したがって BestKnown、P1、Champion、production、submission、`cg_bestknown_loop_v1.py` 接続は変更しない。`META_DEV`／`META_FINAL`も未読のまま保全した。

## v14 behavior-spread source

公式 `data/raw/EN_Card_Data.csv` と既存の7つのdeck specから、8つのdeck recipe×policy variantを生成した。8 deck／8 policyは一意で、各 package は自身の `ROOT_DECK` と `deck.csv` が一致する。P1 smokeは seed `202608978`、32/32 `DONE`、fault 0（15W-17L）だった。

- promoted pool SHA: `01aa3179e1bb7e1a68a646b315574bda758b1afd876ff21ea0ab41c216758d3d`
- fresh meta SHA: `2c3bd8082a95eee45e2791293f6acd014c96959ce1bf433bdcdcfbfbe670b6eb`
- meta manifest SHA: `5ac26b79f05e18ae0e963b5c71fb7917f650443ed47a61a629fa28ff0480c1d2`
- split SHA: `06ba48f3db5075be5278088bd576f2bbd381bf49bf68c2871f6172854668efd0`
- CEM: `runs/cg-self-owned-cg-policy-cem-v14-behavior-spread-20260816/`
- screen: 216/216 `DONE`、109W-107L、fault 0
- independent: 72/72 `DONE`、38W-34L、fault 0

screen上位 c01 は独立平均 `−16.667pt`、minimum `−41.667pt`（反復 `+8.333pt / −41.667pt`）、c06 は平均 `0pt`、minimum `−25pt`（`+25pt / −25pt`）。両方とも seat／opponent-safe false で、positive gateによりcenterを保持した。

## v15 public-mix source

v15初回生成は `self_owned_cg_deck_spec_v1.json` のcanonical deckが既存public identityと衝突したため、candidate生成前に fail-closed で停止した。衝突を隠さず、spec v2・別seedへ置き換えた retry1だけを採用した。retry1は公式カードCSVから8 self-owned sourceをSTAGED生成し、8 deck／8 policy unique、`parent_deck=null`、`public_parent_read=false`、authority全falseである。

- plan SHA: `d8765e9221b3957853cc0bc29ab4852e48660f2793837fd1f02610de4c28f18f`
- factorial manifest SHA: `1cebb2f12b37f28366661cc5d335f8da91b50764392dad848bf059ff6020b09e`
- v15 promoted pool SHA: `906619e0d0335af52538113196f68fccf8b56524c61ef68a3ab0aca20cc07e02`
- v15 fresh meta SHA: `296f8a1c1cbb0bea641b53e300bc382d82e3eafe0347ffcb5a895ed3e1cb3795`
- v15 promotion smoke SHA: `9ae88036e6159ce2aee8e1cd50903a88e023048d48beb6d38cd5a43346584fa3`
- P1 smoke: seed `202608981`、32/32 `DONE`、fault 0、17W-15L

epoch6gの未使用public source 2件（`tetsutani/grimmsnarl-ex-damage-transfer-control`、`samrishb/unified-ptcg-framework-v2`）とv15をmergeし、10 sourceのroot-deck splitを別artifactとして作った。v15 self-owned 8件を `META_TRAIN`、public 2件を `META_DEV`／`META_FINAL`に置き、CEM選抜からpublic holdoutを分離した。

- merged root-deck pool SHA: `7b27d98dbb546d37eabc6869aeca88474da8d17e84bdce3e9d5d8a084ab7d58c`
- merged fresh meta SHA: `c40aa72dca9925f62857262f84b807685fc5f8322a0e185ce9f8f23334be2aa6`
- meta manifest SHA: `f6df1830fdb7c871ea6f65de0c211768c4514f37331eba731a196774e4ba7464`
- root-deck split SHA: `e25e01b5af15deef75fa20ff9bf84b2cf82dedbdebc373cc1018110ccd622cbf`
- P1/root source and control deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`

root-deck CEM `runs/cg-self-owned-public-mixed-cg-cem-v15-rootdeck-20260816/` は、P2 config `c83df4408b24` をcenterに、seed `202608982`、population／elite `8／2`、1 generation、`META_TRAIN_ALL`、独立再評価2回、`initial_scale_fraction=0.25`、positive／risk-aware gateで実行した。

- screen: 288/288 `DONE`、fault 0。上位は c05 `+12.5pt`、c06 `+9.375pt`、c01/c02 `+6.25pt`、c07 `+3.125pt`
- independent c05: mean `−3.125pt`、minimum `−6.25pt`、反復 `−6.25pt / 0pt`、seat／opponent-safe false
- independent c06: mean `−6.25pt`、minimum `−18.75pt`、反復 `−18.75pt / +6.25pt`、seat／opponent-safe false
- selection: `risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`
- new center: P2 configを保持、elitesは `incumbent-center`×2
- CEM manifest SHA: `284e38c7b3a54c1d20b40eded8ce9b4c652cb044e5c177b6a25e69815854453e`
- generation results SHA: `d556266f8eed5fae20f2d6f36a81029eb1c94e30a3d3186bf193279f1552e81f`

screenの一時的な上振れは独立再評価で再現せず、未使用DEV／FINALを読む条件を満たさなかった。判定は `SOURCE_GENERATION_PASS / ROOT_DECK_BOUND / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED` である。

## 契約修正と検証

self-owned batch promotion後にsmoke summaryがstaged pool SHAを保持し、promotionで更新されたpool SHAと不一致になる契約不整合を検出した。`src/mage_ptcg/opponent_ingest/self_owned_cg_meta_source_v1.py` はpromotion時にsmoke evidenceを新しいpromoted poolへ再束縛するよう最小修正した。`tests/test_self_owned_cg_meta_source_v1.py` にpool SHA一致の回帰を追加し、同テストは6 passed。既存CEM／parameterization／self-owned package suiteも再実行対象とする。

## 次の再開条件

v14／v15 source、P2 config、seed、候補は性能使用済みとしてblind retryしない。次は、同じdeck recipeの数を増やすだけではなく、(1) public／self-ownedのsource lineage相関を下げる、(2) holdoutを生成時点から分離する、(3) screen top候補へ独立seedを重点配分する、という新しいmeta生成・評価方法を別epochで設計してから再開する。positive・seat-safe・opponent-safeを満たすまではDEV／FINAL、deck phase、BestKnown昇格、提出を行わない。

全artifactはresearch-onlyであり、authorityはtraining／promotion／submissionともfalse。commit、push、Champion変更、Kaggle提出は行っていない。
