# V4 runtime policy adapter evidence (2026-08-10)

## 結論

`SpecialistModelV4` の closed checkpoint を、既存 C1 v2 runtime の
`SpecialistDecisionPolicyV2` / `StepLogitPolicyFactory` として利用できる研究用
adapter を追加した。既存 runtime が semantic class の decode、STOP、
lexicographic local-alias dispatch を引き続き所有するため、adapter は
actor-visible v1 input から V4 relational state と class logits を供給するだけで
CABT index を直接扱わない。

## 実装境界

- 実装: `src/mage_ptcg/meta_specialist/neural_policy_v4.py`
- checkpoint load は `load_specialist_checkpoint_v4` を必ず経由し、外部から
  `expected_file_sha256` と `expected_tensor_state_sha256` の両方を要求する。
  ファイル SHA が一致した descriptor から topology を復元した後、既存 strict
  loader が implementation/live-callable/tensor state の binding を再検証する。
- 同じ complete action 内では GRU を一度だけ評価して、session 作成時の incoming
  hidden から recurrent token と next hidden を得る。各 prefix はその固定 token へ
  prefix/candidate head だけを適用し、runtime の `.commit()` 時にのみ next hidden を
  次 action へ反映する。`reset()` は episode/game state を消去する。
- `SpecialistNeuralPolicyV4Factory` は immutable loaded weights を共有しつつ、
  `runtime.make_agent` が要求する fresh per-game policy object を返す。

## 検証

実行コマンド:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src pytest tests/meta_specialist/test_neural_policy_v4.py -q
```

結果: `3 passed`。

- prefix が 2 回問い合わせられても hidden input が固定であり、commit 後の次 action
  だけがその hidden を使うことを確認。
- v4 checkpoint の file SHA と tensor-state SHA がともに必要であることを確認。
- fixture deck と actor-visible CABT selection callback を `runtime.make_agent` に
  通し、返却された index が既存 runtime により合法範囲・重複なしとなることを確認。

## Actor pool 接続と CABT smoke

`ActorJobConfigV1` へ既存 `neural_specialist`（v1 checkpoint）と区別される
`behavior_kind="neural_specialist_v4"` を追加した。この kind は次の三つを必須とし、
file SHA は `behavior_identity` とも一致しなければならない。

- `neural_checkpoint_path`
- `neural_checkpoint_file_sha256`
- `neural_checkpoint_tensor_state_sha256`

`scripts/run_meta_specialist_v4_actor_smoke.py` はこの binding を指定して
`run_one_actor_game_v1`（内部で `scripts.test_sim.run_match`）を一局だけ実行する。
以下は `/tmp` に生成した小型 V4 fixture checkpoint と既存 materialized Alakazam deck
を使った実行であり、Kaggle への送信は行っていない。

```text
status=completed, outcome=loss, winner=1, steps=92, transitions=18, fault=null
```

engine output directory: `/tmp/meta-specialist-v4-actor-smoke-verify/`

既存 v1 actor-pool regression も実行した。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  pytest tests/meta_specialist/test_actor_pool_v1.py tests/meta_specialist/test_actor_pool_v4.py -q
```

結果: pass（新規 V4 binding test を含む）。

強制 STOP では既存 runtime が policy logits を呼ばないため、V4 GRU state も advance
しない。この既存 runtime 契約を変えずに adapter を最小化した結果であり、forced-stop
を action recurrent state として扱う必要が生じた場合は runtime contract 側での設計判断を
要する。
