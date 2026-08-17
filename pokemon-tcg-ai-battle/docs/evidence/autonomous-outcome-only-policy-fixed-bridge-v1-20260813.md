# Outcome-only policy-fixed bridge v1（2026-08-13）

## 結論

`POLICY_FIXED_SHORT` の research-only bridge を閉じた。これは A の outcome-only hard-negative schedule を厳密に再検証し、現行 Rule v0 を control、bounded `PLAY=-2` action overlay を candidate として、同一の META_TRAIN opponent・seat・seed・repetition strata を 96 局ずつ materialize するだけの計画 artifact である。`ready_for_evaluation=true` は schema/identity の閉包を示すが、`execution_allowed=false` のため本 artifact 生成では評価器を起動していない。

## 入力と境界

- schedule: `runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-schedule-v1-20260813/schedule.json`
- schedule semantic SHA: `f8bec57883ce60e50bb33de0b01939f85d0bceda9a7f09d021d411f82d07570b`
- schedule file SHA: `df9397e5e07f995ed41b000b8170a26b71f16ed429e9cfade57e36e949b4d3e9`
- input source: fault-free 96-game V4 WDL summary（META_TRAIN 20 opponent IDs、META_FINAL 4 IDs）。heldout exposure は 0、teacher/native action labels・private fields・training data は 0。
- opponent pool は現行 `opponents/pool_manifest.json` の `local_eval_only` assets のみ。synthetic opponent は拒否する。
- candidate surface は既存 research-only Rule v0 bounded action overlay だけで、legal action set は変更せず malformed/unsupported observation は既存 Rule v0 fallback に委ねる。
- `main.py`、`agents/rule_agent.py`、production evaluator、既存 performance artifact は変更していない。

## 生成 artifact

新規 run root は `runs/final-sprint-autonomous/v4-seed1-outcome-only-policy-fixed-short-bridge-v1-20260813/`。

| artifact | SHA-256 | 内容 |
|---|---|---|
| `manifest.json` | `654e4b980a1813215e12352eb6ebcb5bdd86819ee9f22d5a448783842a90490c` | strict bridge manifest |
| `manifest.games.json` | `86a826015810851169f5b3ca154effddc432db757cf38f66640532731727148f` | 96 control + 96 candidate game payload |
| manifest semantic `bridge_sha256` | `5439787b421f86822a66f5a1939b583931140305fbef62bc2e46c00e26dabfa6` | canonical identity |

`manifest` は control/candidate とも 96 slots、seat 0/1 は各 48、同じ 20 META_TRAIN IDs（quota=0 の `harukiharada_crustle` は slot なし）、schedule weight/quota sum は 1.0/96 である。authority は training/promotion/submission/external-execution/longrun の全て `false`、`research_only=true`、`execution_allowed=false` で封印している。

## 再現と検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/build_outcome_only_policy_fixed_bridge_v1.py \
  --repo-root . \
  --schedule runs/final-sprint-autonomous/v4-seed1-outcome-only-hard-negative-schedule-v1-20260813/schedule.json \
  --subject-deck deck.csv --candidate-id play-minus \
  --action-deltas-json '{"PLAY":-2}' \
  --output runs/final-sprint-autonomous/v4-seed1-outcome-only-policy-fixed-short-bridge-v1-20260813/manifest.json
```

既存出力への上書きは CLI が拒否する。生成後に strict reload を実行し、schedule/source/evaluator/deck/pool/config/candidate/control identity、candidate config、train/heldout IDs、slot strata を再導出して一致を確認した。

Focused TDD:

```text
tests/meta_specialist/test_outcome_only_policy_fixed_bridge_v1.py: 11 passed
```

追加回帰は deck/pool/candidate policy/train IDs/control identity の改ざんを semantic SHA 再計算後にも拒否する。`py_compile`、docs validator、`git diff --check` は完了済み。性能 run、CABT、training、promotion、submission、longrun は未起動である。

## 次の gate

この artifact は評価器へ渡せる入力計画だが、評価の昇格根拠ではない。次に起動する場合も同一 manifest の control/candidate pair を使用し、96 → 384 の段階 gate、fault 0、seat collapse なし、paired support と native baseline を確認する。META_FINAL を含む実行、manifest の手編集、権限の自己申告は拒否する。
