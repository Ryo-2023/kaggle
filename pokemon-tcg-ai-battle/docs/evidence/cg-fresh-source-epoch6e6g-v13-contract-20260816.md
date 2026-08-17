# Fresh source epoch6e/6f/6g と self-owned v13 CEM contract stop（2026-08-16）

## 結論

公開kernelからの追加探索は、既存artifactを再利用せずに安全に採用できる新規 source を2件（epoch6g）得た。一方、epoch6e/6f は全候補を重複・不正・entrypoint不足で fail-closed した。公式カードデータだけから生成した self-owned policy/deck family v13 は4件を新規に封印し、P1 runtime smoke は16/16 `DONE`・fault 0で通過した。

v13 を P1 CEMへ接続した最初の実行は、CABTを起動する前の static smoke で停止した。原因は CEM の古い materializer が、`p1-source-core` の immutable P1 policyをそのまま候補へコピーし、同じ `deck.csv`（c06）を持つ現行 control の `ROOT_DECK` fallbackへ再束縛していなかったことにある。既存の self-owned package materializerにはこの再束縛機能があるため、迂回や static gate 無効化は行わない。

判定は `SOURCE_GENERATION_PASS / CEM_BLOCKED_BY_DECK_FALLBACK_CONTRACT / BESTKNOWN_UNCHANGED`。BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py`接続は不変である。

## 公開kernel intake epoch6e/6f/6g

### epoch6e

- config: `configs/meta_specialist/cg_kaggle_kernel_meta_public_more_epoch6e_20260816.json`
- root: `runs/cg-kaggle-kernel-meta-intake-public-more-epoch6e-20260816/`
- result: `BLOCKED_NO_SAFE_CANDIDATES`
- accepted/rejected: `0 / 8`
- Faheem、Pllinas（2件）、Raunak、Jazivxt（2件）、Naoto は source identity／artifact identity reuse、または dynamic executionで除外した。

### epoch6f

- config: `configs/meta_specialist/cg_kaggle_kernel_meta_public_more_epoch6f_20260816.json`
- root: `runs/cg-kaggle-kernel-meta-intake-public-more-epoch6f-20260816/`
- result: `BLOCKED_NO_SAFE_CANDIDATES`
- accepted/rejected: `0 / 8`
- Avik は `invalid_ace_spec_count`、Sushanth系は source/artifact reuseまたは違法ACE、Mega Venusaur は `missing_agent_entrypoint`で除外した。

### epoch6g

新規作者系譜から提出payloadを含む2件を直接受理した。

| candidate | source | submission SHA |
|---|---|---|
| `kaggle_tetsutani_grimmsnarl_20260816` | `tetsutani/grimmsnarl-ex-damage-transfer-control` | `04f9779b77d17417570189d06a1b7ff5b0016797639a2a45f4b53bc02e945712` |
| `kaggle_samrishb_unified_framework_20260816` | `samrishb/unified-ptcg-framework-v2` | `8054a991f2190f5a0d414dfdc7a7cf2427e2bbeb6be9939736e79e4929c8712b` |

- config: `configs/meta_specialist/cg_kaggle_kernel_meta_public_more_epoch6g_20260816.json`
- intake root: `runs/cg-kaggle-kernel-meta-intake-public-more-epoch6g-20260816/`
- result: `SEALED`, accepted/rejected `2 / 0`
- pool SHA: `8dd9ceb8aa43058da20d6a21b18b15d2b787fdbc878586a967c823559aa96a9d`
- fresh meta SHA: `e1c0e6e11a36d1898dc69a2856833bcc60d3bceed870d1e14a97e2bc07ce1797`

Avik と Mega Emboar を legalizer で補正する別系譜も2件封印したが、P1 smokeは8局すべて `AGENT_INVALID` だった。原因は一引数 public agent に deck 注入用 `entrypoint_adapter`が無かったこと。adapterを追加した retry は同一policyの artifact identity reuse で正しく停止した。したがって、この系譜を性能metaへ昇格しない。

## self-owned fresh source v13

公式 `data/raw/EN_Card_Data.csv` と既存 self-owned generatorから、4 deck recipe × 4 policy variantを生成した。公開deckのコピーではなく、deck seed／ordinal、policy parameter、generator lineageをhashで束ねた research-only sourceである。

- plan: `configs/meta_specialist/self_owned_cg_policy_family_v13_fresh_source.json`
- root: `runs/cg-self-owned-cg-policy-family-v13-fresh-source-20260816/`
- source/deck/policy count: `4 / 4 / 4`
- batch manifest SHA: `8401fab70d7c2ace34e1e75f285f71f39dd25186e747ccdb964c05d86b82364`
- staged pool SHA: `950de680e6496c16010381ee5b203dd138587d38b74cc6216dc48939dd00cd38`
- promoted pool SHA: `7ad55492b60622c5271999b4944a3fb91ded28198d9da66dfbf77160468d39a9`
- promoted fresh meta SHA: `d0f158f01926acab0c8ba34842acf0bf70da738930d9fa486eae918e60390549`
- promoted meta manifest SHA: `02feb58669de34c4f9c7030438043ded63447625e915e9d53fc1a38305b9033f`
- split SHA: `c6773e48f9031426b2395503b9ee53eee498eb2d92b158951843c104899ab9b5`
- split: `META_TRAIN=2 / META_DEV=1 / META_FINAL=1`
- P1 smoke: seed `202608975`、16局、`DONE 16/16`、fault `0`、`5W-11L-0D`

4 promoted packageは `ROOT_DECK` と自身の `deck.csv` が一致することを静的に確認した。これは v13 source package側の問題ではない。

## v13 CEM contract stop

実行rootは `runs/cg-self-owned-cg-policy-cem-v13-fresh-source-20260816/`。設定は campaign seed `202608976`、population／elite `4 / 2`、1 generation、`META_TRAIN_ALL`、独立re-evaluation 2回、positive delta gate、risk-aware updateである。

実行は `generation-0000/candidates/candidate-00` の生成直後に static smoke で停止し、CABTは0局である。

- candidate deck SHA: `21620b5f30317f380c020f98672c524ba243b04f180df22830693e8f5acbaff2`
- control deck SHA: `21620b5f30317f380c020f98672c524ba243b04f180df22830693e8f5acbaff2`
- candidate initial fallback: immutable `p1-source-core`の旧ROOT_DECK
- control initial fallback: c06 `p1-core-control`のROOT_DECK
- stop: `ValueError: candidate failed the P1 deck/fallback contract`
- stop record: `runs/cg-self-owned-cg-policy-cem-v13-fresh-source-20260816/stop.json`
- `champion_changed=false`, `submission_sent=false`, `research_only=true`

ここで static smoke を無効化したり、deck SHAだけを見て続行したりしてはいけない。実CABTは package の `deck.csv` と agent の初期 `select=None` fallbackを同じdeckとして扱う必要があるためである。

## deck/fallback binding修正後のv13 CEM retry2

既存 `materialize_self_owned_cg_parameterized_package_v1`をCEM候補生成へ接続し、self-owned control packageのdeckを候補の`ROOT_DECK`へ再束縛した。contract-only candidateは60枚の`ROOT_DECK == deck.csv`、static smoke PASS、P1 parent SHA保持を確認した。

本実験は `runs/cg-self-owned-cg-policy-cem-v13-fresh-source-20260816-retry2/` で、未使用 campaign seed `202608977`、population／elite `4 / 2`、1 generation、`META_TRAIN_ALL`、独立re-evaluation 2回、positive delta gate、risk-aware updateを使用した。

- screen: `40 / 40 DONE`、fault `0`、`20W-20L-0D`
- independent re-evaluation: `24 / 24 DONE`、fault `0`、`10W-14L-0D`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- parent policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- control policy SHA: `a52316249a6f5aa8bec19fd1a4fa904fcc684f5df4576e867bc5154ffae551d4`
- deck SHA: `21620b5f30317f380c020f98672c524ba243b04f180df22830693e8f5acbaff2`

screen上位は `cg-p1-cem-g00-c03-f15db4b8d1e7` の `+62.5pt`、`c00`／`c01`の`+25.0pt`、`c02`の`+12.5pt`だった。しかし独立再評価へ進んだc03は平均 `−37.5pt`、minimum `−50.0pt`、c00は平均 `−25.0pt`、minimum `−50.0pt`であり、positive gateは不成立だった。selectionは `risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`、elitesは`incumbent-center`×2、`new_center`はc06と同一である。

この結果はdeck/fallback contractを修復できたことの証拠であり、性能改善の証拠ではない。`META_DEV`／`META_FINAL`は読まず、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py`接続は変更していない。

## 次の再開条件

次はv13の同一seed／候補をblind retryせず、別の未使用source epochまたは別policy surfaceを生成し、同じdeck/fallback contract、独立seed、positive gateを維持してCEMへ渡す。candidate manifestの探索用hash、self-owned package manifest、static smoke、no-clobberは継続する。

epoch6g／v13の同一seed・同一metaのblind retry、DEV／FINALの後追い読み込み、BestKnown昇格は行わない。

権限は `research_only`、`training_allowed=false`、`promotion_allowed=false`、`submission_allowed=false`、`longrun_allowed=false` のままである。
