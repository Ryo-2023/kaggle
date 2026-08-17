# Actor-visible routed ensemble meta source / CEM（2026-08-15）

## 判定

`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。新しいsource-generation recipeは合法性・静的安全性・runtime smoke・CEM接続まで通過したが、独立positive、seat-safe、opponent×seat-safeを同時に満たすcandidateは0件だった。P1 policy、root deck、BestKnown、Champion、production、submissionは不変である。

## 目的とrecipe

既存cross-lineageと同一P1-base adapterのblind retryを避けるため、未CEMの公開kernel parent policy v4／v7／v9を2つずつ組み合わせ、turn、yourIndex、active／benchのcard ID、stadium、selection contextだけから決定的に親policy A/Bを選ぶ `ACTOR_VISIBLE_ROUTED_ENSEMBLE_V1` を実装した。

- 実装: `src/mage_ptcg/opponent_ingest/routed_ensemble_meta_v1.py`
- seal CLI: `scripts/generate_routed_ensemble_meta_v1.py`
- split rebind: `scripts/rebind_routed_ensemble_split_v1.py`
- focused tests: `tests/test_routed_ensemble_meta_v1.py`
- source boundary: `local_eval_only`
- private fields used: none
- expert/action labels、future RNG、network access: none

親payloadは候補ごとに`parent_a/`と`parent_b/`へ隔離コピーし、wrapper SHA、parent policy SHA、deck parent canonical SHA、routing recipe、freshness evidenceを封印した。生成poolは初期`smoke_ok=false`であり、smoke promotion後にsplitを再bindした。

## source artifact

| candidate | routing | policy A | policy B | deck parent | generated policy SHA | canonical deck SHA |
|---|---|---|---|---|---|---|
| `routed_board_4v_ebf0fd6aeeca` | `OPPONENT_BOARD_HASH_V1` | Koushikrudra v4 | Raunak v7 | Koushikrudra v4 | `53ab75b04526e3dd645d89f24292b29e4a6055f569e309929a9cd4df3bea87cd` | `ff107989f334ddf6d62186b3791bbb5846fc5045796ae020bb2059cf436eedeb` |
| `routed_context_v4_59c9a370d3b0` | `CONTEXT_TURN_HASH_V1` | Raunak v7 | Koushikrudra v4 | Prvsiyan v9 | `f508b9d4896d06dd9688eda11e281ba8954746d795ebdf9e8517a54025a04049` | `ff107989f334ddf6d62186b3791bbb5846fc5045796ae020bb2059cf436eedeb` |
| `routed_hash_rv_c18e048fdd29` | `PUBLIC_HASH_V1` | Raunak v7 | Prvsiyan v9 | Raunak v7 | `e196159111b1ec8c4b585ff1356b184d69e6aa48233f423b53ffe50208b3ff12` | `e656740ab5d19a958fe1a2d05ca05d49bea09b273a5cb593de5e1d4d9cbb8340` |
| `routed_turn_vr_c17268571e9a` | `TURN_PARITY_V1` | Prvsiyan v9 | Raunak v7 | Prvsiyan v9 | `af230046c5b6504fed9481518f60ab20049d97c73247ed1c68c7c1a61814a4e6` | `ff107989f334ddf6d62186b3791bbb5846fc5045796ae020bb2059cf436eedeb` |

Generated root: `runs/cg-routed-ensemble-meta-20260815-a/`

- pool SHA: `aae831cd7c12904499e097e4d9e729dccd4470442f7133b30255fede0e79b403`
- fresh meta SHA: `6e058fdea6a90fb0807dc046d2d1df9d629c09aeb4c0cfcd95528c7f088846d7`
- meta SHA: `e9e2fc0c89336bb94c1746bf5e745210209a9d252d5816c65a02337234005ce1`
- pre-smoke split SHA: `49a8121e26e230cc8b1f50cee72a75324ddc00a7054be73c106e7a2dedc66750`

Promoted root: `runs/cg-routed-ensemble-promoted-20260815-a/`

- pool SHA: `e9aa6b129964e41afb6125311db891efaddd0d3e80af8ab61d94a08127218d93`
- fresh meta SHA: `b167f3f1b4581aa9161dda153d2f4eeb98266bc4df676941d5bff17ef4f522c1`
- rebound meta SHA: `e9e2fc0c89336bb94c1746bf5e745210209a9d252d5816c65a02337234005ce1`
- rebound split SHA: `ff22d2efe41bda990456a8ec7c9680bb83bf61b116fa5520692a4800bc4f66e5`
- split: TRAIN=`routed_board_4v`, `routed_context_v4`; DEV=`routed_hash_rv`; FINAL=`routed_turn_vr`

## Runtime smoke

`runs/cg-routed-ensemble-smoke-20260815-a/` はP1＋4 references、両seat各1局、seed `20261001`、8局で実行した。

- requested/completed: `8/8`
- status: `DONE=8/8`
- faults: `0`
- draws: `0`
- P1 outcome: `4W-0D-4L`
- smoke summary SHA: `45b93ebb336b777584bfb1b8d4784ccb9c7adb4e6932a017455678725aa46d6e`

このsmokeはruntime safety確認だけに使い、CEMのcandidate選抜・DEV判定・FINAL判定へ勝敗を投入していない。ただし4件すべてをruntime smokeしたため、FINALは「性能未使用」ではあるが、smoke-untouched holdoutではない。この制約を次epochの設計へ引き継ぐ。

## P1固定CEM

Campaign root: `runs/cg-routed-ensemble-cem-20260815-a/`

- source package: P1 `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- pool SHA: `e9aa6b129964e41afb6125311db891efaddd0d3e80af8ab61d94a08127218d93`
- split SHA: `ff22d2efe41bda990456a8ec7c9680bb83bf61b116fa5520692a4800bc4f66e5`
- campaign seed: `20261002`
- generations: `2`
- population／elite: `8／2`
- search: `META_TRAIN_ALL`（2 refs、各seat 2局）
- independent re-evaluation: 2 blocks、各seat 2局
- positive delta gate: enabled
- risk-aware update: enabled
- total: screen `72+72`、independent `48+48`、DEV `32`、合計`272` rows
- manifest SHA: `812977267d9d728938623bba7850bc30035f52074e591ec11da402ba5609a4e1`
- gen0 results SHA: `742bd4fc7f4a923a4acb4d68844b1390da55a08ac544c4a2bed18917a8778c0c`
- gen1 results SHA: `0d3e43a461e38f73b9aeed03febd9311eea7a28fea3bba31f4f41a2bde625a1f`

| generation | screen top | independent repeats | gate | result |
|---|---:|---:|---|---|
| 0 | `+12.50pt` | candidate-04 `+6.25pt / 0pt`、candidate-07 `+18.75pt / +12.50pt` | seat-safe=false、opponent×seat-safe=false | center保持 |
| 1 | `+25.00pt` | candidate-05 `−12.50pt / −12.50pt`、candidate-07 `−12.50pt / −12.50pt` | positive delta gate不成立 | center保持 |

gen1のfresh DEV（CEM選抜後、FINAL未使用）はcenter同士でcandidate `2W-0D-14L` 対 control `5W-0D-11L`、差`−18.75pt`、candidate seat rates `0.00／0.25`であった。新candidateは独立gateを通過していないため、FINAL performance confirmationと`cg_bestknown_loop_v1.py`接続は実行していない。

Campaign manifestは`COMPLETE`、`champion_changed=false`、`submission_sent=false`。P1、root deck、BestKnown、Champion、production、submission、`opponents/`は不変である。

## 検証と次の判断

- focused routed/cross-lineage tests: `5 passed`
- routed generator／CLI／rebind `py_compile`: PASS
- source freshness batch／split verification: PASS
- active heavy process: なし（完了後確認）
- commit／push／Kaggle提出: 未実施

このepochは、routed ensembleが「新しいsource生成方法として実行可能」だが「性能更新を生む証拠はない」ことを示した。同じ親集合・同じrouting recipeのblind retryは行わず、次は相関の低い新parent source、またはsmoke exposureと性能holdoutを完全に分離できる混合poolを優先する。`cg_bestknown_loop_v1.py`は候補昇格が得られるまで再開しない。

## Semantic routing epoch b / wrapper failure diagnosis

同じv4／v7／v9 parentを使うが、blindなhash／turn routeではなく、公開状態の意味的分岐を試すbounded epochを別artifactとしてsealした。追加recipeは `OPPONENT_DAMAGE_SWITCH_V1`、`OPPONENT_BOARD_SIZE_SWITCH_V1`、`CONTEXT_THREAT_SWITCH_V1` である。generated rootは `runs/cg-routed-ensemble-meta-20260815-b/`（pool SHA `c55a86d948f761c7bf2bbb89d957911b60f56296e9430303de33c87aa62c704d`、fresh SHA `3bd123260d9cef3db031189c1dace46da66202cf1ab1866bc95bea9c0d1ec3a6`、pre-smoke split SHA `0137decdb51547454c6ef103a05faffa8a8ed5c2ceafba2b04e88c5528af574d`）。

P1両seat smokeは8局を要求したが、`DONE=2/8`、`AGENT_ERROR=6/8`、fault rate `0.75` で昇格不可となった。CABT公開状態を実観測して最小再現した結果、empty `bench` のlistが `or ()` によりtupleへ変換され、`active(list) + bench(tuple)` が `TypeError` になるwrapper実装バグだった。親policy、P1、deck、engineの不具合ではなく、b artifactは改変せず保存した。

## Corrected semantic routing epoch c-fix / CEM（最新）

bのsealed artifactを上書きせず、active／benchをtuple正規化した修正版を新しい `c-fix` epochとして生成した。空bench回帰テストを追加し、focused routed／cross-lineage testsは `5 passed`、module／CLI／rebind `py_compile`もPASSである。修正版候補は次の4件で、全てpublic-state-only routingである。

| candidate | routing | policy SHA | canonical deck SHA |
|---|---|---|---|
| `routed_bench_49_fixed_48f3148716af` | `OPPONENT_BOARD_SIZE_SWITCH_V1` | `ba4d65d463dcf6edbcd35bea82a61c5c7947fd704f8e40a62a251bc93c7728b9` | `ff107989f334ddf6d62186b3791bbb5846fc5045796ae020bb2059cf436eedeb` |
| `routed_context_74_fixed_3379d4fac52a` | `CONTEXT_THREAT_SWITCH_V1` | `bc15738789ca014cc3f897f2b6420874d051fff13e5a4cc23d50dc81ae10d91b` | `ff107989f334ddf6d62186b3791bbb5846fc5045796ae020bb2059cf436eedeb` |
| `routed_damage_47_fixed_7f44cae46533` | `OPPONENT_DAMAGE_SWITCH_V1` | `d9c505eb15e33639ac18a714d93b69f6634f2113413404078d5d8abf6a0be528` | `e656740ab5d19a958fe1a2d05ca05d49bea09b273a5cb593de5e1d4d9cbb8340` |
| `routed_damage_79_fixed_8bfc5251f115` | `OPPONENT_DAMAGE_SWITCH_V1` | `d8aa4b6b698e388bbed0e6eaf5af1c76e41b799b1ec427057a38b217e0d6d708` | `ff107989f334ddf6d62186b3791bbb5846fc5045796ae020bb2059cf436eedeb` |

generated root `runs/cg-routed-ensemble-meta-20260815-c-fix/` はpool／fresh／pre-smoke split SHAがそれぞれ `487db2fd945096cddf990fa8bcce88c4ff781082e2b9381a30876723d7a1659b`／`48f8f8ff5783bf62417d7fcae8aabf3f3e54eabe09129561dc3462ad29ee065e`／`73aee9f4a2a2b8bc9d35320048b98391c935bcdfa2ba119f32935ba8ece17f6d`。修正版smoke `runs/cg-routed-ensemble-smoke-20260815-c-fix/` はbase seed `20261011`、P1両seat各1局の8局で `DONE=8/8`、fault `0`、draw `0`、P1 outcome `1W-0D-7L`（summary SHA `a9b73d98b3d3663bb43a0b5af5fc051621c456001e9aa97f75060bc027ce0a4b`）となった。smokeはruntime安全性だけに使い、性能選抜へ勝敗を投入していない。

promoted root `runs/cg-routed-ensemble-promoted-20260815-c-fix/` はpool SHA `8597484b9e85ab31834a0c322d0a334ecda0a44a2a6f14769296509eba9fc4bd`、fresh SHA `0a8c08237410a520566b781f09248a0aff9ebf240450eb61fdef2c0e4c0b69fc`、rebound meta SHA `4b1483bae3aa511a323eec4fea171bcd94b208b24ec2f72f99a694ec3945acae`、rebound split SHA `2dcb4a8690d44e4a511fab2cf2cfa6aae13c2c53e2d1983b20a8a42f6ed45081`。splitはTRAIN=`routed_bench_49_fixed`／`routed_context_74_fixed`、DEV=`routed_damage_47_fixed`、FINAL=`routed_damage_79_fixed`である。4件をruntime smokeしたため、FINALは性能未使用だがsmoke-untouchedではない。

修正版poolを `runs/cg-routed-ensemble-cem-20260815-c-fix/` へP1 control付きで接続した。campaign seed `20261012`、1世代、population／elite `8／2`、META_TRAIN 2件・各seat 2局、positive delta gate、risk-aware update、independent re-evaluation設定で、screen `72/72`を全て `DONE`・fault0で完了した。しかしscreen valid candidateは `0/8`（全候補がseat-collapseまたはinvalid）、elite空、center保持となり、独立re-evaluation、DEV、FINALは起動していない。campaign manifest SHAは `b1355246a039adb6c5c346a0f85ef8a6b787ac7885365af5ceeeb05e8bb0663d`、generation results SHAは `2ff68d09d8192a7529684d2606e017b1f0214cde1e6d68c23790f1724879262d`。

判定は `SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。意味的routingのwrapper契約は修正後にruntime gateを通ったが、今回のsource compositionは独立positive／seat-safe候補を生まなかった。P1 policy、root deck、BestKnown、Champion、production、submission、`opponents/`、commit、pushは不変であり、`cg_bestknown_loop_v1.py`への接続、deck phase、FINAL performance confirmationは未実行である。次は同じv4／v7／v9親の再組合せを繰り返さず、相関の低い新parentまたは新規permission済みsourceを獲得・生成する。
