# V4 Held-out Checkpoint Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** closed V4 checkpoint を固定 6 held-out opponent へ両 seat で実測し、digest-bound かつ fault fail-closed な JSON を出力する。

**Architecture:** 新規 script は既存 `measure_opponent_strength.py` の CABT game 経路を薄く再利用し、V4 の strict loader を actor-pool factory へ束縛する。test は実 runner の imports を差し替えて、JSON 契約を動作レベルで固定する。

**Tech Stack:** Python 3、pytest、PyTorch V4 checkpoint、CABT `scripts.test_sim.run_match`。

## Global Constraints

- 対象 opponent は `EVAL_HELD_OUT_V1` と一致する固定 6 ID とし、CLI 指定で変更しない。
- checkpoint の file SHA-256 と tensor-state SHA-256 を必須の output provenance とする。
- fault は score 分母から外すが、1 件でもあれば比較 status は invalid とする。
- 既存 `scripts/measure_opponent_strength.py` は変更しない。
- commit、push、Kaggle 提出を行わない。

---

### Task 1: JSON runner contract

**Files:**
- Create: `scripts/measure_v4_checkpoint_strength.py`
- Test: `tests/meta_specialist/test_measure_v4_checkpoint_strength.py`

**Interfaces:**
- Consumes: `EVAL_HELD_OUT_V1`、`_build_neural_agent_policy_factory_v4`、`run_match`。
- Produces: `main(argv: list[str] | None = None) -> int`、JSON report。

- [x] **Step 1: Write the failing test**

```python
def test_runner_uses_the_fixed_pool_and_marks_a_fault_invalid(tmp_path):
    report = _run_with_one_done_and_one_fault(tmp_path)
    assert report["opponent_ids"] == list(EVAL_HELD_OUT_V1)
    assert report["comparison_status"] == "invalid_faults"
```

- [x] **Step 2: Run test to verify it fails**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src pytest tests/meta_specialist/test_measure_v4_checkpoint_strength.py -q`

Expected: FAIL because the module does not exist.

- [x] **Step 3: Write minimal implementation**

```python
def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    checkpoint = _load_bound_v4_checkpoint(args.checkpoint)
    payload = _play_fixed_held_out_games(checkpoint, args)
    _write_json(args.output, payload)
    return 0
```

- [x] **Step 4: Run test to verify it passes**

Run: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src pytest tests/meta_specialist/test_measure_v4_checkpoint_strength.py -q`

Expected: PASS.

### Task 2: actual V4 screen and evidence

**Files:**
- Create: `docs/evidence/v4-heldout-checkpoint-evaluation-20260810.md`
- Create: `runs/meta-specialist-strength/v4-heldout-*.json` (git-ignored actual artifact)

**Interfaces:**
- Consumes: Task 1 runner and available Alakazam/Archaludon V4 epoch-1 checkpoint(s).
- Produces: reproducible command, exact artifact paths, and no unsupported strength claim.

- [x] **Step 1: Run two opponents in both seats for each available V4 lane**

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/measure_v4_checkpoint_strength.py --checkpoint <checkpoint> \
  --subject-deck-csv <deck.csv> --subject-archetype-id <lane> \
  --games-per-seat 1 --max-steps 2000 --output <artifact.json>
```

- [x] **Step 2: Record only observed status and the matched/ unmatched conditions of any v2 artifact**

```markdown
少数局の接続 screen は強さや優位性の根拠ではない。fault が 1 件でもあれば比較無効である。
```
