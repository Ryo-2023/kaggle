# Native public-only B route blocker fix evidence

2026-08-13 の B route 契約修正証跡。これは実収集の性能結果ではなく、native
public-only self-rollout を実行する前の fail-closed dry-run contract の証跡である。

## 結論

I-1 authorization root-of-trust、I-2 pool manifest binding、I-3 public projection provenance、
I-4 complete common24 snapshot、repo-root containment/no-clobber は GREEN。current native の
実 permission と family-bound pool は未閉鎖のため、status は **COLLECTION NO-GO / 0局** を維持する。

## Artifacts / SHA

| artifact | SHA-256 |
|---|---|
| collector module | `674a1783052893a2a2edfb08b6309af825bfb6ad5b853101295663a343ce221d` |
| dry-run script | `a88187c6252b5f340d276e0895c8ebf6119d2d071336d93c08aae43af2ebb6fc` |
| focused tests | `596116309bd5e8870c948a8238da2eb4f3b70dd50154051be1ba82e9d34f55ba` |
| independent review | see `.superpowers/sdd/2026-08-13-native-preserving-meta-overfit/b-route-fix-review.md` |

## Gates

- caller-only `owned_policy` / fake permission / unbound training ownership: `BLOCKED`, ready false
- verified synthetic source + permission + projection + pool fixture: `DRY_RUN`, collection-ready true,
  evaluation-ready false, all authority false
- 95/96 or missing terminal marker/step discontinuity: rejected
- output outside repo root: rejected; existing destination bytes: preserved
- `--execute`: rejected; no evaluator, subprocess, trainer, CABT, or collection loop is imported/called
- focused B suite: 23 passed; nearby materializer suite: 32 passed

The fixture success above is not a permission grant for the current Tomato asset. No performance claim,
BestKnown change, submission package, or longrun was produced.
