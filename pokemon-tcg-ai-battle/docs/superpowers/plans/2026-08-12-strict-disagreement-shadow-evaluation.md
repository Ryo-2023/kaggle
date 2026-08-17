# Strict Disagreement Arm と Shadow 評価 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wave6 seed1を起点に、teacher/student disagreementを明示的に抽出する短期armを作り、selection biasを避けたshadow poolと同一条件の評価ゲートを固定する。

**Architecture:** 既存のV4 screen、DAgger relabel、recurrent BC trainer、固定評価runnerを再利用する。新しい処理はstrict target抽出とshadow manifestに限定し、既存Wave3/Wave6 artifactを上書きしない。strict armはresearch-onlyで、評価はdevelopment fixed-sixと別のshadow cohortを分けて出力する。

**Tech Stack:** Python 3、PyTorch、既存V4 actor/runtime、既存recurrent BC、pytest、SHA-256、JSON/JSONL。

## Global Constraints

- `promotion_authority=false` とし、Champion変更を行わない。
- private/hidden state、deck order、serial locatorを新規artifactへ保存しない。
- checkpoint、deck、opponent、screen、transitions、protocol、実装closureのSHAを記録する。
- 既存Wave3/Wave6 rootを上書きせず、新しいtimestamped output rootを使う。
- 既存fixed-sixはdevelopment poolと明記し、shadow poolは候補選択後にfreezeする。
- strict targetはepisode/component単位で分割し、train/validation leakageを防ぐ。
- fault、non-DONE、invalid legal domainを学習・評価の成功例に含めない。
- commit、push、Kaggle提出、長時間学習、RL longrunは行わない。

---

### Task 1: Strict disagreement target の設計・抽出

**Files:**
- Create or modify: `scripts/run_meta_specialist_v4_dagger_screen.py`（既存APIを壊さない最小追加）
- Create: `scripts/build_meta_specialist_v4_strict_targets.py`
- Create: `tests/meta_specialist/test_strict_targets_v4.py`
- Create: `runs/meta-specialist-v4-strict-disagreement-20260812/strict_target_manifest.json`

**Interfaces:**
- 入力: Wave6 seed1 checkpoint、sealed screen transitions、Rule teacher factory、opponent/seat scope。
- 出力: `strict_target_manifest.json` と対象episode/component ID一覧。各対象は disagreement、teacher action type、student probability、source SHAを持つ。
- 抽出条件: 完全action disagreementを必須とし、teacher targetがlegalで、student probabilityが設定閾値以下のものを優先する。閾値とtop-kはmanifestへ記録する。

- [ ] screen/relabelの既存schemaとaction-key境界を確認する。
- [ ] 失敗テストで、同一action・非合法teacher action・private field混入・component重複を拒否する。
- [ ] deterministicなpriority排序とtrain/validation component分割を実装する。
- [ ] source checkpoint、screen、transitions、protocol、実装SHAをmanifestへ保存する。
- [ ] focused pytestと`python -m py_compile`を実行する。

### Task 2: Untouched shadow pool のfreeze

**Files:**
- Create: `runs/meta-specialist-v4-shadow-pool-20260812/shadow_pool_manifest.json`
- Create: `docs/evidence/v4-shadow-pool-freeze-20260812.md`

**Interfaces:**
- 入力: current opponent pool manifest、opponent lineage、fixed-six除外集合。
- 出力: identity-closedなshadow cohort、選定基準、全deck/policy/canonical SHA、source manifest SHA、freeze timestamp。
- 候補不足時は`status=insufficient_candidates`とし、無理にsealed評価と呼ばない。

- [ ] fixed-sixを完全一致で除外する。
- [ ] candidateのdeck/policy/canonical hashとsource lineageを検証する。
- [ ] deterministic selection ruleと未検証事項をmanifest/evidenceへ記録する。
- [ ] candidate数と現行評価runnerの互換性を確認する。

### Task 3: Strict arm の短期学習・評価

**Files:**
- Create: `runs/meta-specialist-v4-strict-disagreement-20260812/`
- Create: `docs/evidence/v4-strict-disagreement-short-arm-20260812.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`

**Interfaces:**
- strict target manifestを既存V4 BC runnerへ入力し、2 seed、同一deck、同一protocol、固定seed設定で短期学習する。
- evaluationはdevelopment fixed-sixとshadow cohortを別JSONへ出力する。
- 集計は合計勝率だけでなく、seed、seat、opponent、target/guardrail、fault、worst-case harmを含む。

- [ ] 新しいoutput rootへscreen、mixed dataset、seed0/seed1 checkpoint/reportをatomicに保存する。
- [ ] strict armのimitation metricsとtarget effective massを記録する。
- [ ] まず短期評価を実行し、faultやlegal failureがあればfail-closedする。
- [ ] fixed-sixとshadowの結果を混ぜず、Wave6 current evaluatorと比較する。
- [ ] `git diff --check`、docs validation、関連pytestを通す。

### Task 4: Gate判定と引き継ぎ

**Files:**
- Modify: `docs/evidence/v4-strict-disagreement-short-arm-20260812.md`
- Modify: `docs/status/current_status.md`
- Modify: `docs/status/handoff.md`

- [ ] +5ptを実務filterとして扱い、統計的証明とは呼ばない。
- [ ] target改善、guardrail非悪化、seed consistency、shadow generalizationを分離して判定する。
- [ ] 不通過なら長時間化せず、失敗armを保持したまま次の仮説を明記する。
- [ ] 通過してもpromotion gateを別途満たすまでChampionを変更しない。
