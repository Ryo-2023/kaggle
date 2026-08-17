# Rocket Specialist Route Meta v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 受理済みRocket sourceの`_SPECIALIST_THETA` family routingだけをboundedに変更する新しいderived meta source familyを、fresh splitとP1 CEMへhash-boundで接続する。

**Architecture:** 専用transformerはASTで唯一の`_SPECIALIST_THETA`辞書を検証し、値Name参照のsource spanだけを置換する。既存のpool loader、freshness scan、historical split builder、CABT/CEM runnerを再利用する。既存theta numeric generatorやcurrent poolは変更しない。

**Constraints:** local_eval_only、authority全false、TRAIN-only smoke、DEV/FINALの段階ゲート、同一ファイルの同時編集禁止、commit/push/Kaggle提出/Champion変更なし。

## Task 1: 失敗するroute transformer契約テストを追加

**Files:**
- Create: `tests/test_rocket_specialist_route_meta_v1.py`
- Reference: `runs/cg-fresh-internal-meta-intake-20260815-f/internal_ozawa-rocket-rule_de797c3646e9/main.py`

- [ ] fixtureで唯一の`_SPECIALIST_THETA`、5 table Name、4 family keyを固定する。
- [ ]各variantが値参照だけを変え、`_TIER_A_TO_GROUP`と`_apply_theta` marker、deck/import/環境キーを保持することを確認する。
- [ ]未知variant、duplicate dict、missing key、non-Name value、no-opをfail-closedすることを確認する。

## Task 2: strict route transformerを実装

**Files:**
- Create: `src/mage_ptcg/opponent_ingest/rocket_specialist_route_meta_v1.py`
- Test: `tests/test_rocket_specialist_route_meta_v1.py`

- [ ] exact `_SPECIALIST_THETA` dictをASTで抽出し、キー／値Nameの許可集合を検証する。
- [ ] 12 variantのroute mapを宣言し、Name tokenのbyte spanのみ置換する。
- [ ] transformed sourceを再parseし、route mapが期待通りであることとSHA変化を検証する。
- [ ] focused testと`py_compile`を通す。

## Task 3: sealing、freshness、CLI、config

**Files:**
- Modify: `src/mage_ptcg/opponent_ingest/rocket_specialist_route_meta_v1.py`
- Create: `scripts/generate_rocket_specialist_route_meta_v1.py`
- Create: `configs/meta_specialist/cg_rocket_specialist_route_v1.json`
- Test: `tests/test_rocket_specialist_route_meta_v1.py`

- [ ] source note、deck SHA、static findings、current pool/artifact identityを検証する。
- [ ] 12 policyをno-clobberでsealし、TRAIN 8／DEV 2／FINAL 2を生成する。
- [ ] per-candidate evidence、pool/fresh/split/meta/intake reportとSHAを保存する。
- [ ] config validation、focused test、docs validationを通す。

## Task 4: TRAIN-only smokeとP1 CEM

**Runtime roots:** `runs/cg-rocket-specialist-route-meta-20260815-b/`, `runs/cg-rocket-specialist-route-smoke-20260815-b/`, `runs/cg-rocket-specialist-route-cem-20260815-b/`

- [ ] splitから8 TRAIN IDだけを明示し、両seat fault/illegal 0のsmokeを完走する。
- [ ] immutable P1 control、population 16、elite 2、独立re-eval 2回、positive-delta gate、risk-aware updateでgeneration 0を実行する。
- [ ]独立gate不合格ならgeneration 1／DEV／FINALを起動せず停止する。
- [ ] gate通過時のみDEV→FINALへ進み、BestKnown更新は別承認なしに行わない。

## Task 5: evidenceとChatGPT資料の更新

**Files:**
- Create: `docs/evidence/cg-rocket-specialist-route-meta-v1-20260815.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`
- Modify: `docs/status/chatgpt_context_pack_cg_bestknown_2026-08-15.md`

- [ ] source-generation結果とperformance promotionを分離して記録する。
- [ ] source/pool/fresh/split/meta/evaluator/CEM SHA、split exposure、失敗理由、次のkill条件を記録する。
- [ ] `git diff --check`、focused test、docs validation、active process確認を実施する。
