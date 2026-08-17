# CG actor-visible action-level mixer meta source / P1 CEM（2026-08-15）

## 判定

`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。

新しいsource生成方法として、同一canonical deckを持つ複数のruntime-safe policy lineageへ同じ公開観測を渡し、各parentが返した合法なindex集合のどちらか一方だけを、公開状態の決定的なrecipeで選ぶaction-level mixerを追加した。parentのindexを合成・発明せず、相手のhand／prize／deck／discardなどprivate情報も読まない。4 lineage・4 recipeから12候補をsealし、24局の両seat runtime smokeをfault 0で完了、P1固定CEMへ接続したが、独立re-evaluationでrobust positiveを得られずP1 centerを保持した。

P1 policy、root deck、BestKnown、Champion、production、submission、`opponents/pool_manifest.json`、commit、pushは変更していない。DEV／FINALはCEM中に読んでおらず、`cg_bestknown_loop_v1.py`のheavy policy→deck→policy loopにも接続していない。

## 実装と境界

- 実装: `src/mage_ptcg/opponent_ingest/routed_ensemble_meta_v1.py`
- CLI: `scripts/generate_routed_ensemble_meta_v1.py`
- promotion helper: `scripts/promote_historical_meta_smoke_v1.py`
- tests: `tests/test_routed_ensemble_meta_v1.py`, `tests/test_promote_historical_meta_smoke_v1.py`
- derivation recipe: `ACTOR_VISIBLE_ACTION_LEVEL_MIX_V1`
- source kind: `internal_actor_visible_action_level_mixer`
- recipes: `ACTION_LEVEL_KO_MIX_V1`, `ACTION_LEVEL_TEMPO_MIX_V1`, `ACTION_LEVEL_SETUP_MIX_V1`, `ACTION_LEVEL_HASH_MIX_V1`
- runtime cost: 1 decisionあたり2 parent calls。parentが返す合法index集合の一方を選び、集合をmergeしない。
- 公開入力: `turn`, `yourIndex`, active／bench／stadiumのvisible IDs、visible HP／maxHP／appear flag、`selection_context`、公開option／attack metadata
- private入力: なし
- deck family: canonical deck SHA `ff107989f334ddf6d62186b3791bbb5846fc5045796ae020bb2059cf436eedeb`
- parent lineage: Koushik rear-card、Prvsiyan control、Prvsiyan Alakazam、Prvsiyan meta-router

既存の4候補版はTRAIN 2件しかなく、P1固定の小規模CEMで全候補が`seat_collapse=true`となった。この結果を盲目的に再試行せず、4 lineageのpair／recipeを変えた12候補版へ拡張した。

## Seal・smoke・promotion

12候補のgenerated rootは `runs/cg-action-level-mixer-meta-20260815-c/`。

| artifact | SHA-256 |
|---|---|
| generated pool | `c863bb81ba03360a2b82188eaada3dc1d66fc4e5cf80c4dda31a922dd60cb411` |
| generated fresh meta | `ddb1a661ad35f3ef496f572f4ebb73f74f6bef2a589f4ab91cbc50b1b0f61a28` |
| generated split | `dc6e8c6e5a12c6ecb968b91d6e4699f2081514e0715b6ff0f612a48f8a2d9f51` |
| generated meta manifest | `b123fa45a34eb69265c2bcbeb757ee29da5ec9f0286cad0437bc8a69f6bac443` |

全12 referenceを両seat各1局、base seed `20261103`でsmokeした。`DONE=24/24`、fault `0`、draw `0`、P1は`1W-0D-23L`（score `4.1667%`）だった。勝敗はplumbing確認専用であり、CEMの選定根拠にはしていない。smoke summary SHAは`8f88412da59af578986314aafbfa80cb5f555b36b6e04a5c8457ef387d3614ec`。

全件を別rootへpromotionし、rebindした promoted rootは `runs/cg-action-level-mixer-promoted-20260815-c/`。splitは`META_TRAIN=10 / META_DEV=1 / META_FINAL=1`で、fresh-metaは`build_fresh_meta_batch_v1(..., consumed_ids=())`を通過した。

| promoted artifact | SHA-256 |
|---|---|
| pool | `0563fea8a48f712819aa7577133614ea06f0994dbd28d034fbbc44588b2a2c70` |
| fresh meta | `0f9db8e11f09094f8ed77cb2de192b9b838960e5d644185985f49e7080a03db3` |
| rebound split | `c995d521939494cf1aecdc00a954544e886c2d62608bc80b302f1eb2fabe1b54` |
| meta manifest | `b123fa45a34eb69265c2bcbeb757ee29da5ec9f0286cad0437bc8a69f6bac443` |
| promotion report | `7e5d1f73656d1894b00d06c7f7e78aed39dfe7a117fe1472356ea7322a342415` |

smokeを全12件へ行ったため、FINALは「CEM性能選定未使用」だが「smoke-untouched」ではない。この制約を次epochへ持ち越す。

## P1固定CEM

最初の4候補版 `runs/cg-action-level-mixer-cem-20260815-a/` は、TRAIN 2件、population 4、1世代、screen 40局、fault 0だったが、全候補・controlが小標本の`seat_collapse=true`となり、valid elite 0、center保持となった。これはsourceを捨てる性能判定ではなく、評価分解能不足の診断である。

12候補版は `runs/cg-action-level-mixer-cem-20260815-c/` で実行した。

- P1 source/control: `cg-lethal-target-v1`、policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- campaign seed: `20260817`
- search: `META_TRAIN_ALL`、10 refs、各opponent／seat 2局
- population／elite: `4 / 1`
- generations: `1`
- independent re-evaluation: 2 blocks、各opponent／seat 1局
- gates: positive-delta、risk-aware lower-tail、seat／opponent×seat validity
- screen: `200/200 DONE`、fault `0`、12W-0D-188L
- re-evaluation: `80/80 DONE`、fault `0`
- DEV／FINAL: 未使用

screenではcandidate c03が`4W-0D-36L`（objective `10.0%`、control `0W-0D-40L`）で一時eliteになった。しかし独立2 blockはそれぞれcandidate `0W-0D-20L`／`1W-0D-19L`で、controlとの差は`−10.0pt`／`−5.0pt`、いずれもseat-collapseだった。positive gateにより`incumbent-center`を選択し、P1 centerを保持した。generation results SHAは`d0263387fd3fbdb9a7a8a7ef96f73f686d2f74691f41322f31b4156b1cbad67b`、campaign manifest SHAは`3df6dc086ec33bb5c3d97f439ba14f5081406fac7497d2377fcc4732207e1137`。

## 次のゲート

今回のsourceは、合法性・静的安全性・freshness・runtime smoke・CEM接続まで成立したが、独立positive／seat-safe／opponent×seat-safeを満たしていない。したがって同じ4 lineage・同じ4 recipeのblind retryはしない。次は、(1) 未性能使用policy lineageを含む相関管理済みの新しい混合pool、または (2) runtime smoke対象と性能holdoutを分離した新しいpermission済みsource を生成する。全ゲートを通過したcandidateだけを `cg_bestknown_loop_v1.py` の `P1 → policy CEM → fresh validation → deck → policy` へ渡す。

