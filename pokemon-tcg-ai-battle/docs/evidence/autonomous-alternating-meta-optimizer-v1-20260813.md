# Autonomous Meta-Fine-Tuning Task 4: alternating deck/policy optimizer contract

作成日: 2026-08-13 (JST)

## 判定

研究専用の `alternating_meta_optimizer_v1` を追加した。これは CABT、学習、提出を
起動する runner ではなく、deck/policy の交互最適化を安全に実行するための状態・SHA・
checkpoint 契約である。production agent、既存V4、`main.py`、`deck.csv`、evaluator、
Champion は変更していない。

## 契約

### 固定タイムスケール

`CandidateStateV1.phase` は次の2値だけを許す。

- `POLICY_FIXED_SHORT`: policy config SHAを固定し、deck候補を短周期で比較する。
- `DECK_FIXED_LONG`: deck SHAを固定し、policy config候補を長周期で比較する。

固定側のSHAを変更する更新は fail-closed で拒否する。各状態には次を必ず保存する。

- candidate ID
- deck SHA
- policy config SHA
- immutable meta manifest SHA
- immutable META schedule SHA
- native BestKnown の pair / deck / policy / evaluator SHA
- 現在の 96 / 384 / 768 / 1536 局 stage

### Native baseline

`NativeBaselineArmV1` は任意化されておらず、全候補状態・successive-halving入力で
必須である。候補スコアだけを渡すランキングは許さず、`native_baseline_score` も必須に
した。これにより目的関数が「Rule v0より強いか」へ退行せず、native BestKnownとの
差分として記録される。

### Successive halving

評価ステージは次の列に限定する。

```text
96 -> 384 -> 768 -> 1536
```

`promote_successive_halving_v1` は同一 stage・同一 native baseline の候補だけを受け、
score降順（同点はcandidate ID）で上位半数を次 stageへ進める。次のstageを飛ばす、
順序を戻す、candidate/nativeスコアを省略する、native armを揃えない入力は拒否する。

### Meta manifest / schedule binding

初期化時に `meta_distribution_v1.load_meta_distribution_manifest_v1(...,
verify_sources=True)` を通し、manifest本体・source artifact・scheduleをSHA固定する。
resume、checkpoint、rollback のたびに再ハッシュし、次を拒否する。

- manifest / source artifactの変更
- scheduleの変更
- research-onlyでないmanifest
- training / promotion / submission authorityがtrueのmanifest
- 候補stateとsealed configのSHA不一致

deck mutation候補を渡す場合は既存 `DeckMutationCandidateV1` を要求し、候補の exact
multiset SHAとstateのdeck SHAを一致させる。また deck mutation側の authorityが全て
falseであることを再確認する。

### Checkpoint / resume / rollback

- checkpoint descriptorとstate journalを別々に原子的に公開する。
- checkpoint artifact、candidate state、manifest、scheduleのSHAをdescriptorへ保存する。
- resumeは全てのSHAを再検証する。
- rollbackは過去に公開されたdescriptorだけを対象とし、active checkpointを原子的に
  切り替える。
- `progress_summary.json` は現在stage、phase、state SHA、active checkpoint SHA、
  restart契約を記録する。

### Authority

`ResearchAuthorityV1` の execute / training / promotion / submission / longrun は
全てfalse固定で、trueを構築しようとすると例外になる。`execute=True` の initialize、
resume、execute APIは fail-closed で拒否する。dry-runではユーザー指定のcallbackを
呼ばないため、CABTや学習プロセスが暗黙に開始されない。

## 追加ファイルとSHA

- `src/mage_ptcg/meta_specialist/alternating_meta_optimizer_v1.py`
- `tests/meta_specialist/test_alternating_meta_optimizer_v1.py`
- `docs/evidence/autonomous-alternating-meta-optimizer-v1-20260813.md`

```text
15687de5f271e3323297464b33add70a7c250308812eaccff1050b70384b1d47  src/mage_ptcg/meta_specialist/alternating_meta_optimizer_v1.py
e67686e7548a820a756286c269c2d46585dfcbe65a7e8f2c9fc6e4bd296d1f11  tests/meta_specialist/test_alternating_meta_optimizer_v1.py
```

## 検証

```bash
PYTHONPATH=src pytest -q -s \
  tests/meta_specialist/test_alternating_meta_optimizer_v1.py \
  tests/meta_specialist/test_deck_mutation_v1.py \
  tests/meta_specialist/test_joint_optimization_v1.py
```

期待結果（2026-08-13）:

```text
26 passed in 0.67s
```

この検証には、固定タイムスケール、native baseline必須、96→384→768→1536、SHA改ざん
検出、atomic checkpoint、resume、rollback、authority fail-closed、deck mutation exact
SHA bindingを含む。実CABT、学習、長時間runner、提出は実行していない。
