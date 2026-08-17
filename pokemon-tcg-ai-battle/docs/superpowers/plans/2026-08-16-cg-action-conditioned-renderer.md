# CG Action-Conditioned Renderer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** 公開状態と合法action familyの交互作用を持つ研究専用CG policy rendererを生成し、未使用metaへ接続可能なfault0 source poolと固定deck CEM bridgeを作る。

**Architecture:** P1の固定packageを検証して新しい12係数overlayをrenderし、self-owned deck packageへ再束縛する。専用generatorは公式カードCSV＋deck specから6 sourceを生成し、promotion前の4 games/source/seat smokeを通過したものだけをpromoteする。候補席はnative `cg/`同梱generator package、opponent席は軽量promoted poolへ分離し、raw P1 same-deck controlで固定deck CEMを行う。BestKnownやproductionには自動反映しない。

**Tech Stack:** Python標準ライブラリ、既存`self_owned_cg_package_v1` verifier、既存deck generator、CABT historical smoke runner。

## Global Constraints

- hidden opponent hand/deck/prize identity、teacher label、native actionは使用しない。
- candidateは`research_only=true`、training/promotion/submission authorityはfalse。
- 同一sourceの4局未満smokeをpromotion根拠にしない。
- 既存root `deck.csv`、P1 package、BestKnown、Champion、production、submission、commit、pushは変更しない。

### Task 1: Renderer contract

**Files:**
- Create: `src/mage_ptcg/meta_specialist/cg_p1_action_conditioned_renderer_v1.py`
- Test: `tests/meta_specialist/test_cg_p1_action_conditioned_renderer_v1.py`

- [x] configの境界、hash、candidate ID、hidden-field拒否、source compile、agent末尾を失敗テスト先行で固定する。
- [x] P1 source SHAを検証し、action family×公開state bucketのoverlayをrenderする。
- [x] self-owned deck packageへ`ROOT_DECK`を再束縛し、manifest／sidecarを生成する。

### Task 2: Source generator

**Files:**
- Create: `scripts/generate_self_owned_cg_action_conditioned_meta_v1.py`
- Create: `configs/meta_specialist/self_owned_cg_action_conditioned_family_v1.json`
- Test: `tests/meta_specialist/test_generate_self_owned_cg_action_conditioned_meta_v1.py`

- [x] official CSV＋deck specだけからplanを読み、6 distinct packageをstageする（v1/v2）。
- [x] existing pool／public canonical hashと衝突したらfail-closedする。
- [x] source／deck／policy／generation manifestのhashをbatch manifestへ固定する。

### Task 3: Runtime gate

**Files:**
- Generate only: `runs/cg-self-owned-action-conditioned-v1-20260816/`
- Create: `docs/evidence/cg-self-owned-action-conditioned-v1-20260816.md`

- [x] source package compile/static smokeを通す。
- [x] v2の6 sourceを各seat 4局、計48局でhistorical smokeし、fault0でpromoteした。
- [x] splitをTRAIN/DEV/FINALへ分離し、screen runnerでsplitを明示指定する。

### Task 4: Candidate runtime and CEM bridge

**Files:**
- Create: `src/mage_ptcg/meta_specialist/cg_action_conditioned_cem_v1.py`
- Create: `scripts/run_self_owned_cg_action_conditioned_cem_v1.py`
- Create: `tests/meta_specialist/test_cg_action_conditioned_cem_v1.py`

- [x] promoted sourceの省略runtimeをcandidate席へ渡さないfail-closed境界を追加する。
- [x] raw P1 same-deck controlを使うCEM bridgeを実装する。
- [x] population/elite `6/2`の1世代をtrain→DEV→FINALへ接続し、fault0とseat gateを記録した。

### Task 5: Handoff

**Files:**
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`
- Modify: `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md`

- [x] source smoke、hash audit、runtime boundary、raw-control CEMの結果をevidenceへ記録する。
- [x] `python scripts/docs/validate_docs.py` と `git diff --check` を実行する。
- [x] fault0でもseat-safeでなければ性能改善を主張せず、次のfresh source epochを明記する。
