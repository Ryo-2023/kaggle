---
project: MAGE-PTCG
evidence: o1-competition-intelligence-o1-6
as_of: 2026-07-18
scope: detached O1 Competition Intelligence worktree only
---

# O1-6 Meta／Surrogate Evidence

## 結果

stdlibのみで動く決定的baselineをsidecarへ追加した。Weighted Strategy Observationはsource／cutoff／permissionを持ち、prior、temporal input、source/duplicate/confidence weight、unknown mass、effective sample size、interval、source compositionをimmutable Meta Snapshotへ集約する。未知massを既知archetypeへ再配分しない。

Opponent Surrogateはactor-visible Stable ActionKeyと粗いcontextだけを使うsmoothed empirical policyである。Laplace smoothing、entropy floor、exact/reduced/generic fallback、heldout NLL/top-k/Brier/coverage/fallback評価を実装した。Student dataset／teacher／runtimeへの接続はない。

fixed/rolling benchmark manifestとnon-authoritative Promotion Reportも追加した。report decisionは`NO_DECISION`、`REVIEW_REQUIRED`、`INSUFFICIENT_EVIDENCE`のみで、Champion/default/Kaggle submissionを変更できない。

## One-shot cycle

`run-intelligence-cycle`はnormalize → analyze → Intelligence Snapshot → Meta Snapshot → surrogate → benchmark → non-authoritative reportをrun stateへ段階保存する。再実行は完了stageを再利用する。auto training、auto promotion、auto submitはすべてfalse固定である。

## 検証

```bash
PYTHONPATH=. pytest -q tests/competition_intelligence \
  tests/test_competition_intelligence_runtime_isolation.py \
  tests/test_competition_intelligence_cli_end_to_end.py
```

結果：274 passed。

```bash
env -u PYTHONPATH python3 -m compileall -q src/mage_ptcg/competition_intelligence
```

結果：成功。

live Kaggle access、large cabt tournament、Student retraining、Champion promotion、Kaggle submissionは実行していない。
