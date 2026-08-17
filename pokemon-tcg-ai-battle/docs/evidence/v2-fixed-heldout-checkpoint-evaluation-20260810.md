# V2 checkpoint の固定 held-out 比較 runner（2026-08-10）

## 結論

V4 medium checkpoint と公平に比較するため、runtime-compatible V2 checkpoint を固定6 opponent、両 seat、同じ seed schedule、同じ `max_steps` で測る専用 runner を追加した。これは評価実装と strict loader の検証であり、CABT の実測はまだ行っていない。

## runner 契約

`scripts/measure_v2_checkpoint_strength_fixed.py` は、V4 runner と同じ `scripts/make_medal_opponents.py:EVAL_HELD_OUT_V1` の順序を canonical held-out pool とする。任意の opponent ID は受け取らず、短い接続 screen は `--opponent-count 1..6` によるこの順序の prefix のみを許す。

subject は V2 の file SHA-256 を実測して記録する。ロードは payload を読むだけの経路ではなく、`ActorJobConfigV1(behavior_kind="neural_specialist")`、`_build_neural_agent_policy_factory_v1`、`runtime.make_agent` の production actor path を通る。この loader は live file hash と `expected_content_hash` を照合し、checkpoint metadata の topology を使う strict inference load を行う。

各 opponent / seat / rep に対して `seed = base_seed + game_index` を用いる。fault は requested-game 分母に0点として残し、全体・seat・opponent ごとの W-D-L-F と reason を JSON へ保存する。fault が一件でもあれば `comparison_status="invalid_faults"` とし、score を V4 との比較や採否の根拠にしない。

出力 schema は `meta-specialist-v2-fixed-heldout-checkpoint-strength-v1`。V4 の held-out runner と同じ比較に必要な、checkpoint absolute path / file SHA、fixed pool IDs、選択 prefix、games-per-seat、base seed、max steps、requested/played/fault games、W-D-L、score / Wilson interval、seat / opponent 内訳、elapsed seconds を含む。

## strict loader の確認

次の runtime-compatible V2 smoke checkpoint を、実際の `_v2_subject_factory` から production actor loader へ束縛して確認した。

| lane | checkpoint | deck | loader result |
| --- | --- | --- | --- |
| Alakazam | `checkpoint-7849171dc6e70336a0222e991831a7b1df978ba9ebd87324a7dd61e172d05e79.pt` | `opponents/nihei_alakazam/deck.csv` | strict actor binding passed |
| Archaludon | `checkpoint-6518c148e3ac5849e0ded4cd6d45a11cc5314a716e97fe000f2853799fdcd45e.pt` | `opponents/public_archaludon_cinderace_r7/deck.csv` | strict actor binding passed |

これは checkpoint が current runtime adapter で load 可能なことだけを示す。強さ・policy quality・V4に対する優劣を示すものではない。

## V4 と同条件で測るコマンド

GPU medium V4 の output artifact が確定した後、両 arm で `--opponent-count 6 --games-per-seat 2 --base-seed 9200000 --max-steps 2000` を揃えて実行する。例として Alakazam baseline は以下である。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/measure_v2_checkpoint_strength_fixed.py \
  --checkpoint runs/from-worktree/meta-specialist-canonical/meta-specialist-bc-distill/v2smoke-alakazam/checkpoints/checkpoint-7849171dc6e70336a0222e991831a7b1df978ba9ebd87324a7dd61e172d05e79.pt \
  --subject-deck-csv opponents/nihei_alakazam/deck.csv \
  --subject-archetype-id alakazam \
  --opponent-count 6 --games-per-seat 2 \
  --base-seed 9200000 --max-steps 2000 \
  --output runs/meta-specialist-strength/v2-fixed-heldout-alakazam-v2smoke-24.json
```

比較時は lane ごとに V2 / V4 の `opponent_ids`、`games_per_seat`、`base_seed`、`max_steps`、subject deck、そして両者の `comparison_status="valid"` を機械的に照合する。いずれかが異なれば score を比較しない。

## 検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest \
  tests/meta_specialist/test_measure_v2_checkpoint_strength_fixed.py -q
```

結果: `4 passed`。CABT 実ゲームはこの runner 作成では実行していない。
