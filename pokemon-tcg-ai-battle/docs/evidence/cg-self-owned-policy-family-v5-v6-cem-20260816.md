# self-owned policy family v5 / cross-archetype v6 と CEM（2026-08-16）

## 結論

公式カードDBから新しい self-owned meta source の生成方法を2 epoch実行した。v5は同一 role-separated v4 deck family内で8種類の deck と P1 parameter overlayを生成し、v6は deck spec v2／v3／v4を混ぜた8種類の cross-archetype sourceを生成した。両epochとも legality、source seal、両seat smoke、CEM worker実行は fault 0 で完了したが、独立再評価または固定候補検証で性能差が再現しなかった。従って P1 center、現行 BestKnown、Champion、production、submissionは変更していない。

今回の実装上の成果は、CEM parentが候補 packageのnative `cg`をimportしてからspawn workerを起動することで発生していた `buffer full` 境界汚染を、static smoke専用subprocessへ隔離して解消したことである。新しいCEM epochは通常の12 worker設定で完走し、旧RUNNING artifactは性能結果として扱わない。

## 共通の実験契約

- P1 policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- P1/root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- evaluator SHA: `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- CEM: population `8`、elite `2`、1 generation、`META_TRAIN_ALL`、screen 2 games/opponent×seat、独立再評価2 block×2 games/opponent×seat、positive-delta gate、risk-aware update
- `META_DEV`／`META_FINAL`はCEM選定に投入していない。候補がgateを満たさなかったため、BestKnown loopとdeck phaseも起動していない。
- 生成sourceは公式 `data/raw/EN_Card_Data.csv` と versioned role specだけを生成入力にし、既存artifactはcanonical SHA collision監査だけに使った。authorityは全て `training=false / promotion=false / submission=false / longrun=false`。

## CEM static smokeのnative境界修正

旧 `scripts/run_cg_p1_cem_v1.py` の `_static_smoke` は親プロセスでcandidate packageをimportし、candidateの `cg.sim` import時に `GameInitialize()` を実行していた。その後 `spawn` evaluatorを起動すると、native global bufferの初期化状態が子workerへ影響し、12 worker実行で `buffer full. capacity:7` と `BrokenProcessPool` が発生した。

次の変更を行った。

- `scripts/run_cg_static_smoke_v1.py` を追加し、compile・package load・candidate/controlの契約比較を専用subprocess内だけで実行する。
- CEM parentの `_static_smoke` はこのhelperを `subprocess.run(..., start_new_session=True)` で起動し、reportのSHA、status、contractを検証する。parentはcandidate `cg`をimportしない。
- `tests/meta_specialist/test_run_cg_p1_cem_v1.py` に、parent `_load_candidate`を呼ばないこととsubprocess起動契約を固定する回帰を追加した。

TDDのRED確認後、次がPASSした。

```text
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 \
TMPDIR=/tmp/codex-test-cem-subproc PYTHONPATH=.:src .venv/bin/python -m pytest -q --capture=no \
tests/meta_specialist/test_run_cg_p1_cem_v1.py tests/meta_specialist/test_opponent_pool_v1.py
32 passed in 2.89s
```

v5/v6 CEMの通常12 worker実行でも、screen／再評価は全て `DONE`・fault 0で完了した。helper／runner／testのSHAはそれぞれ `852d2d4347c3c0fa8d78dd847450b6cb5bd0a934b3bf5bdcd71d82956fc8355f`／`ab107e6a65d2438634b0fab1421e9c9092acd99c81cb6ea9afb50e4b005dd8ed`／`30ed8c68d5de1ddfd4d16c3bc4a8d93aea154a31751c38c73f70b93d6fa91611`。

## v5: same-family self-owned source

### 生成とseal

- plan: `configs/meta_specialist/self_owned_cg_policy_family_v5.json`、SHA `77afc8e46b7d6ca3b0b15d5b3f9e647f3f7f4d587481d6833ce5fc450dd2e9fc`
- source epoch: `self_owned_official_card_data_policy_family_v5_20260816`
- 8 recipe: `self_owned_cg_deck_spec_v4.json`、seed `20260920..20260927`、ordinal `0..7`
- staged root: `runs/cg-self-owned-cg-policy-family-v5-20260816-retry1/`
- factorial manifest SHA `733c10ab099de020d4e174b3de5c649a4c7222f0ba4adbe1a87bb3685a07472c`
- promoted root: `runs/cg-self-owned-cg-policy-family-v5-20260816-promoted/`
- promoted pool／fresh／meta／split SHA: `509bb5b7b08a2af8b876fd4ed578c5ad64ca8a16ef6a7ddbdf5024ef2f7871a6`／`57a842a68aa3e7d0a500d68a253903b912c71d6103dd3c243a45381415f0621b`／`b8d263e22bf9b73925bf1ad4fd4becda14c1d44e08cf44066d132bd5ce755951`／`d7064695eeea863689c3c821fb0c69179dfa49e34a05a656e522fd8920931d`
- 8 sourceはdeck／policy SHAが相互にdistinct、`parent_deck=null`、`public_parent_read=false`。splitはTRAIN 4／DEV 2／FINAL 2。

P1両seat smoke `runs/cg-self-owned-cg-policy-family-v5-20260816-smoke/` は16/16 `DONE`・fault 0・11勝5敗（score `0.6875`、summary SHA `715a974c181b05edd4e082a984dd50d4bda2e3ba7f7818011f2f6b577b587aca`）だった。

### CEMと固定候補検証

CEM rootは `runs/cg-self-owned-cg-policy-family-v5-20260816-cem/`（manifest SHA `511571cab6daf34635bd7028c1cccc990b8eed111b54a2b7c172d7225509e8c2`、results SHA `c7c8b2bd103464d3df31b8cb7ce37d1a813864c2aa7273db029aec1b2eb6d94f`）。screen 144局、独立再評価96局の全240局が `DONE`・fault 0だった。

- screen上位 c07: `0.8125` 対 control `0.4375`、差 `+37.5pt`
- c07の独立2 block: 差 `[+31.25pt, 0pt]`、mean `+15.625pt`、min `0pt`
- c07は `seat_safe=false`、`opponent_seat_safe=false`。positive/risk-aware gateは不成立で、selectionは `incumbent-center`×2。

見かけのc07を fresh seedで固定検証した `runs/cg-self-owned-cg-policy-family-v5-20260816-c07-validation/`（manifest SHA `d0a27c243454f2e541c3d46a082660f0ee7411d5d9344dd2f534791c83087642`）は、全192局 `DONE`・fault 0だったが、candidate-control差は TRAIN `−4.6875pt`、DEV `−3.125pt`、FINAL `0pt`だった。従ってc07をP2／BestKnownへ昇格しない。

v5判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。v5 pool／候補のblind retryは行わない。

## v6: cross-archetype self-owned source

### collision quarantineとretry

v2／v3／v4 deck specを混ぜるplan `configs/meta_specialist/self_owned_cg_policy_family_v6_cross_archetype.json` を作成した。初回 `runs/cg-self-owned-cg-policy-family-v6-cross-archetype-20260816-retry1/` はv3 seed `20261003`／ordinal `3`でcanonical collisionに当たり、8 sourceを揃えられず `BLOCKED` になった。生成済み部分artifactは削除せずquarantineした。

v3 recipeだけseed `20261103`／ordinal `13`へ変更したretry2は、v2×3、v3×3、v4×2の8 sourceを生成した。

- plan SHA（retry2設定）: `未計測`（設定変更後に再計測する）
- staged root: `runs/cg-self-owned-cg-policy-family-v6-cross-archetype-20260816-retry2/`
- batch manifest SHA `cf39ac6794759085422f41833a7652ed94bdf41b4fd4fa4ee6f039add47d6dc1`
- promoted root: `runs/cg-self-owned-cg-policy-family-v6-cross-archetype-20260816-promoted/`
- plan SHA `84b8d67e158cb701df82df398ea1a6a73258837c416ea93a0c8c9d69a3f8cf56`
- promoted pool／fresh／meta／split SHA: `ca1c7c8124ffd3f40d88618b2b86b751423e732589e59e560a6aa4431740a0cd`／`d6ac59c615f06d438f9b0f5fb6ce5e01ecb4e1f1d380faf86f075faf3910c726`／`207a17049f71c482f5575c43fb31e9b41325436d2ae3556720c7338f2dd3ca24`／`11b41b9995b736e3ad7fd1074c2353cf78afd435e93eb8cfc845d6dc6928092b`
- splitはTRAIN 4／DEV 2／FINAL 2。

P1両seat smoke `runs/cg-self-owned-cg-policy-family-v6-cross-archetype-20260816-smoke/` は16/16 `DONE`・fault 0・9勝7敗（score `0.5625`）だった。

### CEM結果

CEM rootは `runs/cg-self-owned-cg-policy-family-v6-cross-archetype-20260816-cem/`。screen 144局、独立再評価96局の全240局が `DONE`・fault 0だった。

- screen上位 c03: `0.9375` 対 control `0.4375`、差 `+50.0pt`
- c03の独立差 `[−18.75pt, −12.5pt]`
- 次点c05の独立差 `[−25.0pt, −18.75pt]`
- 全候補でopponent/seat-safe gateを満たす独立positiveはなく、centerはP1のまま。

v6判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。DEV／FINAL、deck phase、`cg_bestknown_loop_v1.py`接続は未実施で、v6 poolのblind retryも行わない。

## BestKnown、self-owned境界、次の条件

現行BestKnownは不変で、正確なラベルは「self-authored P1 policy＋common/public root deck」である。v5/v6 sourceはself-owned deck＋P1-derived policyのmeta opponentであり、P1候補の提出deckをself-ownedへ置き換えたものではない。`ono-`は公開kernel作者名ではなく、local Git identity／branch／commit由来の識別子である。

次に進む条件は、同一poolのblind retryではなく、source generation recipeまたはpolicy lineageの相関を変えた新epochを作り、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL`を順に満たすことである。gateを満たす候補が出るまでBestKnown、Champion、production、submission、commit、pushは変更しない。
