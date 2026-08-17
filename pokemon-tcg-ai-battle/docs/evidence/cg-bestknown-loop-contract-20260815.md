# cg BestKnown loop contract — 2026-08-15

## 結論

fresh・unused metaが供給された時点で、self-owned cgの研究parentを
`DECK_FIXED_LONG`（policy改善）→`POLICY_FIXED_SHORT`（deck改善）→policyへ
交互に更新できる、研究専用のbounded coordinatorを追加した。実CABTの起動と
candidate生成は既存runnerへ注入し、coordinator自身はCABT、training、Champion変更、
submissionを実行しない。

## Freshness gate

`build_fresh_meta_batch_v1` は、明示的なfresh-meta manifestとpool manifestをSHAで束縛し、
各referenceについて次を再計算する。

- `smoke_ok=true` かつ `usage_boundary=local_eval_only`
- pool上のpolicy SHAと実体の一致
- pool宣言値とディスク上のカード構成canonical deck SHAの一致
- `fresh=true`、`unused_before_run=true`、freshness証跡ファイルのSHA一致
- source epoch、seed namespace、seed plan SHAの固定と既消費seed namespaceとの非重複
- 呼び出し側の既消費IDとの交差がないこと

raw `deck.csv` SHAをcanonical deck identityとして受け入れないため、R7のような
identity不整合はCABT開始前にfail-closedする。

## BestKnown transition gate

`run_bestknown_loop_v1` は最大8サイクルに制限し、candidateを新しい研究parentへ
進める条件を `POSITIVE_CONTINUE`、fault 0、正のcandidate delta、candidate seat gap
5%以下に固定する。条件を満たさない候補は、faultなら`STOP_FAULT`、正値宣言とgateの
不一致なら`STOP_INVALID`、その他は`STOP_NOT_PROMOTABLE`としてincumbentを保持する。

各cycleはno-clobber checkpointへ候補・incumbent identity、summary、phase、fresh batch
ID、reference IDs、authority falseを保存する。既存P1／BestKnown／Championを変更せず、
次回呼び出しでは出力の`consumed_reference_ids`と`seed_namespace`をfreshness ledgerへ渡す設計である。

## Verification

```text
PYTHONPATH=.:src pytest -q -s \
  tests/meta_specialist/test_cg_bestknown_loop_v1.py \
  tests/meta_specialist/test_cg_alternating_runtime_v1.py \
  tests/meta_specialist/test_cg_population_loop_v1.py \
  tests/meta_specialist/test_run_cg_alternating_runtime_v1.py
14 passed

python -m py_compile src/mage_ptcg/meta_specialist/cg_bestknown_loop_v1.py \
  tests/meta_specialist/test_cg_bestknown_loop_v1.py
python scripts/docs/validate_docs.py
git diff --check
```

今回、fresh sourceが0件であるため、重いCABTは起動していない。P1＋root deck、
BestKnown、Champion、production、submission、pool manifestは不変である。
