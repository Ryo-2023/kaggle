# V4 DAgger screen の decoding mode 監査（2026-08-12）

## 結論

Wave6 の Archaludon screen transition は、確認できる生成経路では **greedy decode** で選ばれている。`sample` を指定した screen job は見つからず、V4 screen job builder は全 job に `decoding_mode="greedy"` と `sampling_seed=0` を固定している。

したがって、現在の on-policy screen は「policy 分布から action を抽出した sampled rollout」ではなく、各公開 decision の logits に対する deterministic argmax（同点時は canonical byte 順の tie-break）である。記録された `behavior_log_probability` は、その greedy で実際に選んだ action を、同じ unperturbed logits で評価した log-softmax であり、sampled behavior の importance ratio としては扱えない。

これは、ChatGPT Pro レビューの「behavior が greedy なら、現在の signed loss は通常の REINFORCE/AWR estimator ではない」という指摘を支持する。現在の signed residual は、policy-gradient と呼ばず、**greedy behavior に対する outcome-signed self-imitation / signed behavior fitting** として解釈すべきである。

## コード根拠

### Screen job の固定値

`scripts/run_meta_specialist_v4_dagger_screen.py` の `build_dagger_jobs_v4` は、各 opponent・seat・repetition の job を次のように生成する。

```python
behavior_kind="neural_specialist_v4",
decoding_mode="greedy",
sampling_seed=0,
```

該当箇所は同ファイルの現在の 153–160 行（監査時 source SHA-256: `4e19cadcb23037efb8a6f0754ee6fc99205a641cd6d1b50ea9b8581b22cdb32e`）。screen runner は `ActorJobConfigV1` をこの job のまま `run_one_actor_game_v1` へ渡し、途中で decoding mode を変更しない。

### ActorPool の greedy / sample 分岐

`src/mage_ptcg/meta_specialist/actor_pool_v1.py` は `decoding_mode` を `{"greedy", "sample"}` に限定する。`sample` の場合だけ `_NeuralSamplingSessionV1` が base logits へ seeded Gumbel noise を加える（同ファイル 553–580 行、602–627 行）。一方、V4 factory は `decoding_mode == "greedy"` なら `SpecialistNeuralPolicyV4Factory` をそのまま返し、sample wrapper を通らない（同 682–716 行）。

監査時 actor pool source SHA-256: `b60ab5be3fede6b13b26533b65b96b6762e9f84f317e9103abc97ae8ac11092a`。なお、このファイルには別作業の未コミット差分があるため、今後変更した場合は SHA と行番号を再確認すること。

### Runtime の action 選択

`src/mage_ptcg/meta_specialist/runtime.py` の decision transaction は常に `greedy_decode_runtime_action_v2` を呼ぶ（現在 776–780 行）。ここでの `greedy` は、policy が返した logits をそのまま使う通常経路である。`src/mage_ptcg/meta_specialist/runtime_actions_v2.py` の decoder（現在 798–831 行）は、各 step の semantic class と STOP の score の最大値を選び、同点時は canonical byte 順で決定する。

`sample_runtime_action_v2`（同 892–932 行）は別 API として存在するが、今回の `ActorPoolV1` V4 screen 経路からは呼ばれない。sample job の場合のみ actor-pool 側が Gumbel-max を行い、その結果を上記 greedy decoder に渡す設計である。今回の job は sample ではない。

### Log probability の意味

greedy action の commit 後、runtime は同じ policy session を用いて `runtime_semantic_complete_action_log_probability_v2` を計算する（`runtime.py` 現在 780 行）。これは選択された complete semantic action の各 prefix と最終 STOP の log-softmax 合計である。action の選択自体は確率分布からの乱数抽出ではないため、これを「sampled action の behavior probability」と解釈して policy-gradient の likelihood-ratio estimator に流用してはいけない。

## 実 artifact の確認

screen JSONL は `decoding_mode` を transition ごとに再掲する schema ではない。そのため、artifact payload 単独で mode を再構成することはできず、以下の **source-bound evidence** として扱う。将来の screen runner では screen manifest 自体へ `decoding_mode` と `sampling_seed` を保存すると、artifact 単独でも監査可能になる。

| artifact | schema / status | games | faults | transition rows | file SHA-256 |
|---|---|---:|---:|---:|---|
| `runs/meta-specialist-v4-archaludon-dagger-wave6-screen-v2/screen.json` | `meta-specialist-v4-dagger-screen-v2` / `VALID` | 96/96 | 0 | 4,763 | `9ad78bf31f41d307916b25d238544ea5060e0df9a8e16b5ca72a8e3977fc00e3` |
| `runs/meta-specialist-v4-archaludon-dagger-wave6-screen-v2/screen.transitions.jsonl` | `meta-specialist-v4-dagger-transition-v1` | — | — | 4,763 | `2d9892855350ac99a085eb616489e65e995415e987ce7c2470e20cc27e08b0ce` |
| `runs/meta-specialist-v4-archaludon-dagger-wave6-screen-seed1-v2/screen.json` | `meta-specialist-v4-dagger-screen-v2` / `VALID` | 96/96 | 0 | 5,590 | `aed7438628ffdcb4a4d0c11a844c6ddea4a75017be44912180e3f6dc90abf1f1` |
| `runs/meta-specialist-v4-archaludon-dagger-wave6-screen-seed1-v2/screen.transitions.jsonl` | `meta-specialist-v4-dagger-transition-v1` | — | — | 5,590 | `2e5438aec5e451d70c37593971b45965cd33950822423b1540fdaf56b3f27e26` |

JSONL の追加確認:

- 全行が `specialist-actor-trajectory-transition-v1` で、`behavior_log_probability` を持つ。
- seed0 は 3,678 train / 1,085 validation、seed1 は 3,892 train / 1,698 validation に分割される。
- JSONL payload には `sampling_mode` フィールドが存在しない。これは mode が不明という意味ではなく、screen manifest が runner の job binding を再掲していないという provenance 上の不足である。
- screen JSON に screen-runner の source commit / source SHA は保存されていない。したがって上記の artifact は、保存済みの checkpoint/deck/transition identity と現在の専用 runner の一致を前提にした **source-bound confirmation** であり、artifact bytes 単独による歴史的な mode attestation ではない。別 runner で生成・上書きされた可能性を排除するには、生成時の runner SHA または command manifest が必要である。

## 検証

専用の回帰 assertion を `tests/meta_specialist/test_run_meta_specialist_v4_dagger_screen.py` へ追加した。固定 screen job が全て greedy / sampling seed 0 であることを検査する。また、actor pool の sample mode が greedy mode と異なることを既存テストで確認した。

```text
TMPDIR=$PWD/.tmp-screen-audit \
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
.venv/bin/python -m pytest -q -s \
  tests/meta_specialist/test_run_meta_specialist_v4_dagger_screen.py::test_build_jobs_uses_fixed_order_both_seats_and_seed_progression \
  tests/meta_specialist/test_actor_pool_v1.py::test_run_one_actor_game_v1_neural_sample_mode_differs_from_greedy
```

結果: **2 passed**（1.49 秒）。追加 assertion を含む test source SHA-256: `c470fe76a66642435a84b8ad5226f5aa05671042bd6686e42fb435da6e9db3f2`。

## 判断への影響

1. 現行 screen 由来の `behavior_log_probability` を、sampled behavior の確率として使う説明は撤回する。
2. 現行 signed residual の目的は、greedy rollout の実行 action と episode outcome に基づく signed self-imitation であり、REINFORCE/AWR の unbiased estimator ではない。
3. それでも outcome-signed fitting の小規模診断を行うこと自体は可能だが、長時間学習を許可する根拠にはならない。greedy behavior の deterministic bias、global-mean baseline、prefix/episode weighting は別々に評価する必要がある。
4. 今回は production runtime、checkpoint、deck、CABT評価結果を変更していない。なお、screen manifest へ `decoding_mode`、`sampling_seed`、runner source SHA を保存することは、次回 screen からの provenance 改善として推奨する。
