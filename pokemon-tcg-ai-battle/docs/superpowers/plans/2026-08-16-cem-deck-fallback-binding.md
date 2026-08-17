# CEM Deck/Fallback Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** CEMが生成する候補packageの`deck.csv`とagentの初期`select=None` fallbackを同一deckへ束ね、immutable P1 policy lineageを保ったままfresh self-owned sourceでCABTを再開できる状態にする。

**Architecture:** 既存の`materialize_self_owned_cg_parameterized_package_v1`をCEMの候補生成境界から再利用する。CEMはself-owned manifestを持つcontrol packageをdeck binding sourceとして明示的に渡し、生成後に既存CEM manifestをsidecarとして追加する。legacy control packageにself-owned manifestがない場合は従来materializerを維持し、static smokeが最終契約を検査する。

**Tech Stack:** Python 3、pytest、既存CG package materializer、`run_cg_static_smoke_v1.py`、hash-bound JSON manifests。

## Global Constraints

- BestKnown、Champion、production、submission、root `deck.csv`は変更しない。
- `BASE_SOURCE_SHA256`のimmutable P1 policy lineageを変更しない。
- 同一ファイルの同時編集を行わず、commit／push／Kaggle提出は行わない。
- CABTは候補のstatic smokeとdeck/fallback一致がPASSしてから起動する。
- `research_only=true`、authority全false、no-clobberを維持する。

---

### Task 1: CEM deck-bound materializationの失敗テスト

**Files:**
- Modify: `tests/meta_specialist/test_run_cg_p1_cem_v1.py`
- Read: `runs/cg-self-owned-cg-policy-family-v12-crossed-20260816/p1-source-core`
- Read: `runs/cg-self-owned-cg-policy-family-v12-crossed-20260816/p1-core-control`

**Interfaces:**
- Consumes: `scripts.run_cg_p1_cem_v1._materialize_cem_candidate(...)`。
- Produces: candidateのdeck bytes、manifest、`select=None` fallbackを検査する回帰テスト。

- [x] **Step 1: Write the failing test**

  `p1-source-core`をpolicy source、`p1-core-control`をdeck binding sourceとして候補を生成し、候補の`agent({"select": None})`がcontrolと一致し、候補deck SHAがcontrolと一致し、`root_deck_bound`を持つCEM manifestが出ることを実際のpackageで確認する。

- [x] **Step 2: Run test to verify it fails**

  Run: `PYTHONPATH=.:src .venv/bin/pytest -q tests/meta_specialist/test_run_cg_p1_cem_v1.py -k deck_bound`

  Expected: FAIL because `_materialize_cem_candidate` is not defined.

### Task 2: 既存self-owned materializerをCEMへ接続

**Files:**
- Modify: `scripts/run_cg_p1_cem_v1.py`

**Interfaces:**
- Consumes: Task 1の`_materialize_cem_candidate(source_package, output_package, config, candidate_id, deck_binding_package)`。
- Produces: self-owned control packageがある場合にdeck-bound candidateを作り、CEM manifestを追加する関数。

- [x] **Step 1: Write minimal implementation**

  self-owned package manifestがdeck binding sourceに存在する場合は`materialize_self_owned_cg_parameterized_package_v1`を呼び、targetの`deck.csv`とpatched `ROOT_DECK`を生成する。続けて`cg_p1_cem_candidate_manifest.json`へcandidate／config／policy／deck／parent／binding manifest SHAをcanonical JSONで記録する。manifestがないlegacy controlでは既存`materialize_parameterized_package`を呼ぶ。

- [x] **Step 2: Run focused test to verify it passes**

  Run: `PYTHONPATH=.:src .venv/bin/pytest -q tests/meta_specialist/test_run_cg_p1_cem_v1.py -k deck_bound`

  Expected: PASS;候補のfallbackとdeck SHAがcontrolと一致する。

### Task 3: CEM generation pathsへbindingを適用

**Files:**
- Modify: `scripts/run_cg_p1_cem_v1.py`
- Modify: `tests/meta_specialist/test_run_cg_p1_cem_v1.py`

**Interfaces:**
- Consumes: Task 2のmaterializer。
- Produces: generation candidateとodd-generation incumbentの両方で同じdeck bindingを使うCEM。

- [x] **Step 1: Add integration assertions**

  generation pathで`control_package`をdeck binding sourceとして渡すこと、legacy P1 packageでは従来fallbackを壊さないことをテストする。

- [x] **Step 2: Implement the two call-site changes**

  `run_generation`内のcandidate生成とincumbent生成を`_materialize_cem_candidate`へ置換し、control packageを明示的に渡す。CABT起動前の既存static smokeは維持する。

- [x] **Step 3: Run focused regression tests**

  Run: `PYTHONPATH=.:src .venv/bin/pytest -q tests/meta_specialist/test_run_cg_p1_cem_v1.py tests/meta_specialist/test_cg_p1_parameterization_v1.py tests/meta_specialist/test_self_owned_cg_parameterized_package_v1.py`

  Expected: PASS。

### Task 4: v13契約検証とfresh CEM再開

**Files:**
- Create: `runs/cg-self-owned-cg-policy-cem-v13-fresh-source-20260816-retry1/`（contract-only生成物）
- Create: `runs/cg-self-owned-cg-policy-cem-v13-fresh-source-20260816-retry2/`（CEM生成物）
- Modify: `docs/evidence/cg-fresh-source-epoch6e6g-v13-contract-20260816.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`
- Modify: `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md`

**Interfaces:**
- Consumes: v13 promoted pool／split、P1 source/control、Task 3のCEM。
- Produces: CABT前contract PASS、必要ならfresh TRAIN CEM結果。DEV／FINALはpositive独立gate成立時だけ読む。

- [x] **Step 1: Run static contract-only verification**

  v13 sourceとp1-core-controlを使ってcandidate-00を生成し、`ROOT_DECK == deck.csv`、static smoke PASS、manifest hashを確認する。失敗時はCABTを起動しない。

- [x] **Step 2: Run one bounded research-only CEM**

  v13 splitを使用し、campaign seedは未使用の`202608977`、population／elite `4／2`、1 generation、`META_TRAIN_ALL`、independent repeats 2、positive gate、risk-aware updateで `runs/cg-self-owned-cg-policy-cem-v13-fresh-source-20260816-retry2/`へ実行する。既存epoch6d／v13 seedを再利用しない。

- [x] **Step 3: Record evidence and validate docs**

  CEMのstatus、局数、fault、delta、gate結果、BestKnown不変をevidenceへ追記し、`PYTHONPATH=.:src .venv/bin/python scripts/docs/validate_docs.py`と`git diff --check`を実行する。
