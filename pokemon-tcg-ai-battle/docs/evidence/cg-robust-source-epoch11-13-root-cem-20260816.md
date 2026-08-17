# robust source epoch11–13 と root-deck P1 CEM（2026-08-16）

## 結論

epoch11–13で生成・独立検証した4件の self-owned robust sourceを、root deck固定の正規 P1 CEMへ接続した。screen 72局、独立再評価 48局の計120局はすべて `DONE`・fault 0だったが、独立 risk-aware／seat-safe／opponent×seat-safe gateを満たす候補はなかった。P1 center、root deck、BestKnown、Champion、production、submissionは不変である。

## static smoke停止の切り分け

先行の self-owned kieran deck policy CEM試行は、CABT開始前に停止した。candidateは kieran deck（deck file SHA `c82f8ccda501d9396e0eca9f6f7e0d8aebdeeefbd0f0bde631c5231158d6e2fd`）へ再束縛されていた一方、controlは P1 root deck（SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）のままだったため、`agent({"select": None})` fallbackが一致せず、既存の `P1 deck/fallback contract` が fail-closed した。

これは static smoke の実装不備ではない。既存回帰テストの deck-bound 経路は 2/2 PASSし、候補とcontrolが同一deckならfallback契約が通る。停止artifactは [`runs/cg-self-owned-near-root-policy-cem-20260816-kieran-epoch11-13/`](../../runs/cg-self-owned-near-root-policy-cem-20260816-kieran-epoch11-13/) の `stop.json` と部分 candidateである。契約を緩めず、self-owned deck policy CEMは同一deckに再束縛した source／control／splitを別epochで封印してから再開する。

## source pool

- pool: `runs/cg-robust-source-weekend-pool-20260816-epoch11-13-v1/pool/`
- pool manifest SHA: `0191e4cd4bbd481abdfe95ea84310562dd43c57ba375e902b1e11f3527c06ed7`
- split SHA: `17150416b386fb70a1b370265f7fe9e892957af8838690db3dbf3621ab9c5ed3`
- `META_TRAIN`: `robust-source-g00-c07-3586f707ac25`, `robust-source-g00-c01-1e80a5cf6dad`
- `META_DEV`: `robust-source-g00-c06-d0c73349f28c`
- `META_FINAL`: `robust-source-g00-c09-403fab8ac2aa`

source CEMで生成した候補を epoch11 high-precision、epoch12 c07-centered、epoch13 c01-centeredから集約した。DEV／FINALは今回の P1 CEM では未読である。

## root-deck P1 CEM

- campaign: `runs/cg-p1-cem-robust-source-epoch11-13-root-20260816/`
- seed: `2026090412`
- configuration: population／elite `8／2`、1 generation、`META_TRAIN_ALL`、独立2 block、各2 games／opponent／seat、positive-delta／risk-aware gate
- screen: 72/72 `DONE`、fault 0
- independent re-evaluation: 48/48 `DONE`、fault 0
- campaign manifest SHA: `1c96441cf7d55e194b1fe35a72b14b2c902f9dcc915d75997ce7c054f1dc98e9`
- generation manifest SHA: `a31f8f75f1aa152fdb0d292c02a452fc598a5de94dbbc7f08ff5c10f7dcc303f`
- results SHA: `aace24041382031e503f8645813f5fc8a206162c07470a602cc689a916c9cd73`
- screen summary SHA: `64764c49cd2a52bffd511b7558bd5c3000ea3a70389b65d579038e7bd85be53e`
- re-evaluation summary SHA: `09331d1e6c08d1ef21bdcae7e3847e1be9c27beaad8f4221adeff02277789d00`

screen上位 c05（`cg-p1-cem-g00-c05-c0f4a513cccb`）は候補 `6W-0D-2L` 対 control `3W-0D-5L`（`+37.5pt`）だったが、独立2 blockは `+37.5pt / −12.5pt`、risk-aware mean／minimum deltaは `+12.5pt / −12.5pt`。seat-safe、opponent-seat-safeとも不成立である。c00もrisk-aware mean／minimum `+6.25pt / −12.5pt`かつ両安全条件不成立だった。選択は `risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`、elitesは `incumbent-center`×2である。

## 判定と次の条件

判定は `SOURCE_GENERATION_PASS / STATIC_CONTRACT_DIAGNOSTIC / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。epoch11–13 poolおよび今回の root-deck CEMは性能使用済みとして blind retryしない。次は (1) source identity／generator lineageの相関を下げた新しいpermission済み meta source、または (2) self-owned deckとP1 controlを同一deckへ束ねた別splitを生成する。その後、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を満たした候補だけを `cg_bestknown_loop_v1.py` へ渡す。

commit、push、Champion変更、production変更、Kaggle提出は行っていない。
