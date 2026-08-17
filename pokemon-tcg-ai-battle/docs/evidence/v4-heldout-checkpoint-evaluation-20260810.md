# V4 checkpoint の固定 held-out screen（2026-08-10）

## 結論

closed V4 checkpoint を固定 held-out pool の先頭 2 opponent に対して両 seat で実行する runner を追加し、Alakazam / Archaludon の epoch-1 seed 0 checkpoint は各 4 局を fault 0 で完走した。両 screen は 0勝0分4敗だが、各 4 局だけの接続確認であり、強さ、v2 に対する退化、checkpoint の採否を示す根拠にはならない。

## runner 契約

`scripts/measure_v4_checkpoint_strength.py` は `scripts/make_medal_opponents.py` の `EVAL_HELD_OUT_V1` を固定 canonical pool とする。任意の opponent ID は指定できず、短い screen は `--opponent-count 1..6` でこの固定順序の prefix だけを選ぶ。

subject checkpoint は absolute path、実測 file SHA-256、descriptor の tensor-state SHA-256 を JSON へ必ず書く。V4 subject は `ActorJobConfigV1(behavior_kind="neural_specialist_v4")` と `_build_neural_agent_policy_factory_v4` から既存 `runtime.make_agent` へ接続し、file/tensor hash を strict loader に渡す。

fault は requested-game score 分母に 0 点として残り、`faults`、`fault_reasons`、seat / opponent 別 W-D-L-F にも記録する。fault が一件でもあれば `comparison_status="invalid_faults"` であり、score の比較を禁止する。

## 実測 screen

固定 pool prefix は `kiyotah_lucario`、`sue124_alakazam`、games-per-seat は 1、base seed は `9300000`、max steps は 2000 である。

| lane | V4 checkpoint file SHA-256 | tensor-state SHA-256 | W-D-L | faults | status | artifact |
| --- | --- | --- | ---: | ---: | --- | --- |
| Alakazam seed 0 | `cb0301cb4e598fa4674e03ed612e2e46246edfe5ea33330649ffd1e0f70d6406` | `20ea6d3c18bcd0a97202a7b25d61d2c7b7d7ff2770ec9e7d2a131eb740ba113c` | 0-0-4 | 0 | valid | `runs/meta-specialist-strength/v4-heldout-alakazam-epoch1-seed0-4.json` |
| Archaludon seed 0 | `2b47cada4643722c094bfe2d6b70fa9274320cbd9301dd99ff6a7b34ab97a88f` | `26517486da86de6ab2b84d4a047df735bd3349300ae5a0e20c727ccdf972ed7f` | 0-0-4 | 0 | valid | `runs/meta-specialist-strength/v4-heldout-archaludon-epoch1-seed0-4.json` |

Alakazam は `runs/from-worktree/meta-specialist-canonical/materialized-decks/alakazam-p1-deck-5187ac6faa5d66f0f3b7.csv`、Archaludon は現行 CABT legality を通る `opponents/public_archaludon_cinderace_r7/deck.csv` を使用した。試行した materialized Archaludon deck (`deck-cef18…`) は対戦前に CABT legality evidence 契約で拒否され、checkpoint / policy は起動されなかったため結果 artifact を作らず、screen の fault としても数えていない。

## 既存 v2 artifact との関係

`runs/meta-specialist-strength/perf-sprint-connect-alakazam-4.json` と `perf-sprint-connect-archaludon-4.json` は同じ 2 opponent・両 seat・4 局を保存し、各 1-0-3、fault 0 を記録している。ただし既存 JSON は base seed と max steps を保存していない。今回の V4 screen は base seed `9300000` と max steps `2000` を保存しているが、実行条件の完全同一性を検証できない。

従って、この表は artifact の存在確認に限る。V4 0-4 と v2 1-3 から相対的な強さ、非悪化、又は採用可否を結論してはならない。24 games/arm の比較は、同一 fixed pool、同一 games-per-seat、同一 base seed、同一 max steps、各 arm fault 0 を満たす新しい artifact に限って評価する。

## 再現

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/measure_v4_checkpoint_strength.py \
  --checkpoint runs/meta-specialist-v4-bc-pilot/alakazam-epoch1-checkpoints/seed-0/best-recurrent-bc-v4.pt \
  --subject-deck-csv runs/from-worktree/meta-specialist-canonical/materialized-decks/alakazam-p1-deck-5187ac6faa5d66f0f3b7.csv \
  --subject-archetype-id alakazam --opponent-count 2 --games-per-seat 1 \
  --base-seed 9300000 --max-steps 2000 \
  --output runs/meta-specialist-strength/v4-heldout-alakazam-epoch1-seed0-4.json
```

## 検証

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python -m pytest \
  tests/meta_specialist/test_measure_v4_checkpoint_strength.py -q
```

結果: `3 passed`。
