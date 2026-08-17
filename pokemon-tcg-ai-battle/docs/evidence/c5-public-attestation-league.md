---
project: MAGE-PTCG
slice: C5
status: gate-a-contract-and-gate-b-runner-implemented
as_of: 2026-07-16
---

# C5 Public Attestation / League

## 結論

Actor-visible情報を一時利用する`ActorVisibleRedactedAttestation`を追加した。public traceにはactor hand、candidate card ID、card identity hash、condition resultを保存しない。TR-000010のbinding結果はprivate offline artifactだけに最小enumで保存する。Championとsubmission defaultはRule Agent v0のままである。

## Gate A — Actor-visible teacher binding

- ephemeral fields: actor hand card identity、candidate hand source identity、actor own active/benchのfield counts、selection contract。
- persisted private fields: teacher/canonical ID、candidate public ID、condition evaluated/result、binding status/reason enum、binder version、provenance category。
- persisted public fields: redacted candidate public IDとsemantic operationだけ。condition resultはpublic traceへ保存しない。
- TR-000010はLunatone/Solrock duplicateまたはthird Lucario-lineを一時評価する。public candidate IDが曖昧、sourceが不明、分類不能なら`TR000010_AMBIGUOUS`または`TR000010_INSUFFICIENT_ACTOR_VIEW`でfail closedする。

actual cabt applicationは **0 / NOT_RUN**。この環境に`kaggle_environments`が無く、official cabt traceを生成できないためである。これはbinding contractの失敗ではなく、`CAPABILITY_UNAVAILABLE`である。

## Gate B — Public trace / League runner

- public traceはself hand IDも含めず、legal optionからredacted ActionKey public IDとsemantic operationだけを保存する。
- `ActualLeagueConfig` / `run_actual_league`は20の偶数game、交互seat、deterministic seed schedule、atomic resume、W-L-D、invalid/crash/timeout/fallback、match latency p50/p95/maxをartifactへ保存する。
- public trace、private binding artifact、league result artifactは別ファイルであり、混在しない。

actual gamesは **0 / NOT_RUN**。公式cabt runtimeが無いため20-game smokeは実行していない。promotionは`NO_DECISION`である。

## 検証

```bash
python -m pytest tests/test_actor_visible_attestation.py tests/test_actual_league_runner.py \
  tests/test_cabt_trace.py tests/test_actionkey_adapter.py -q
# 65 passed, 3 skipped
```

privacy regressionはraw candidate card ID、actor own-hand ID、identity hash、private trace混入、actor B hand混入、ambiguous bindingを対象にする。binderはsubmission runtimeへ接続していない。

最終検証（2026-07-16）では、`python -m pytest -q`が552 passed、7 skippedで完走した。収集は559 tests／40 test filesで、collection errorとduplicate node IDは0件だった。focused verificationは89 passed、3 skipped、curation check、docs validation、Rule Agent v0 submission build／verify／clean-roomも通過した。

official cabt probeは`kaggle_environments`不在で`CAPABILITY_UNAVAILABLE`となり、actual artifactは生成されなかった。このためactual cabtは`NOT_RUN`、actual gamesは0、TR-000010 actual applicationsは0である。

## 残リスク

actual cabt runtimeとLucario/Hariyama decision traceが利用可能になるまで、TR-000010 actual applicationsは0のままである。利用可能時はprivate artifactとpublic traceを別pathへ指定して1-game traceを先に検証し、その後に20-game smokeを実行する。
