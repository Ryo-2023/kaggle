---
title: O5 Versioned Benchmark & Evaluation Runner v1
date: 2026-07-21
base_commit: 514a56a6f74b592b9a8401870d378cb0bfc482b1
status: implementation-complete-real-cabt-executed-population-blocked
---

# O5 Versioned Benchmark & Evaluation Runner v1

## 結論

既存の O5 Registry Foundation（`o5_registry.py`）と O5 Activation & Archetype Opponent Factory（`o5_activation.py`）はすでに canonical へ統合済みであり、`BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION`（active exact Deck 0、Team permission manifest 0）を維持している。本 Slice はこれらを再実装せず、(1) run 識別・再現性フィールドを持つ Versioned Benchmark manifest envelope、(2) `sets.safety` の未実装だった4種の fault-injection opponent、(3) それらを resumable に実行し Wilson CI で集計する Evaluation Runner、(4) official cabt loader だけを使う CLI を追加した。`current_meta` と archetype 系 `adversarial` は、人間による Rules attestation と Team permission manifest が未受領のため引き続き 0 試合・blocked のままであり、これは捏造していない。その代わり、Core Regression と Safety（fault-injection agent family）については実 cabt で合計 **96 試合**（decided games 48）を実行し、結果を本証跡に記録した。

## 実装

- `src/mage_ptcg/competition_intelligence/o5_benchmark.py`: `o5_activation.build_benchmark_manifest()` が返す population-gated `sets`/`status` をそのまま利用し、`benchmark_id`／`benchmark_version`／`seed_set`／`game_count`／`commit`／`config_hash`／`manifest_hash` 等の run 識別・再現性フィールドを追加した `VersionedBenchmarkManifest`。同一入力は同一 `manifest_hash`、異なる入力は異なる hash になることをテストで確認した。
- `src/mage_ptcg/competition_intelligence/o5_adversarial_agents.py`: `sets.safety` のラベル（`exception_agent`／`slow_agent`／`invalid_artifact`／`unknown_selection`）に対応する実装。`select is None` は `main._deck_supplier` と同じ「deck 提出コール」であり 60 枚デッキを返す必要がある——この点を誤って `[]` を返していたバグを実 cabt smoke で発見し修正した（詳細は「発見した実装バグ」節）。
- `src/mage_ptcg/competition_intelligence/o5_evaluation.py`: 既存の `league.actual_runner.run_actual_league`（resumable、seat-swap、schedule 済み）を opponent member × seed ごとに呼び出す薄い orchestrator。scheduling／resume／seat-swap ロジックは再実装していない。crash/invalid/timeout で勝者が決まらない試合は `decided_games`（= wins+losses+draws）に含めず、`win_rate` と `wilson_ci_95` の分母を統一した。
- `scripts/run_o5_benchmark.py`: 既存 `scripts/run_actual_league.py` と同じ配線パターン（factory 注入、official cabt loader のみ使用、champion/challenger seat 入替）に従う CLI。`--dry-run` は `run_match` を一切呼ばず manifest だけを書き出す。

## 発見した実装バグ（実 cabt smoke で検出・修正）

初回の実 cabt smoke（2 games × 6 member）で `slow_agent`／`exception_agent`／`invalid_artifact`／`unknown_selection` の全メンバーが turn 1 で `AGENT_INVALID` になった。`delay_seconds=0.0`（実質 no-op）でも同様に失敗したため timeout 仮説を棄却し、捕捉した実際の observation を調べたところ `select` が `None`（deck 提出コール）であり、`main.make_deterministic_agent`／`_deck_supplier` は同じ局面で 60 枚デッキを返すのに対し、4 種の adversarial agent はいずれも `[]` を返していたことが原因と判明した。修正後、`random_legal`／`slow_agent` は `invalid_actions=0` で正しく完走し、`exception_agent` は `crashes`、`invalid_artifact`／`unknown_selection` は `invalid_actions` として意図どおり分類されることを実測で確認した。

## 実行結果とGate

### Benchmark 定義

| 項目 | 値 |
|---|---|
| `benchmark_id` / `benchmark_version` | `o5-benchmark-core-v1` / `1.0.0` |
| `deck_registry_version` | `o5-deck-archetype-registry-v1` |
| `policy_pack_version` | `o5-activation-opponent-factory-v1` |
| `cabt_version` | `1.32.0`（`kaggle_environments`） |
| `seed_set` | `[90000, 90001, 90002, 90003]` |
| `seat_swap_policy` | `ALWAYS_SWAP` |
| `game_count`（seed・memberあたり） | `4` |
| `commit` | `d5efd83c41c4922718715db39d348510d08b0c69`（実行時の feature branch HEAD） |
| `config_hash` | `a28a577fa7ca01a14fba0a4d276f3ccf8c5dd0317f11d1b4feb6ba9f94474cc5` |
| `manifest_hash` | `727db94758fd386994db2853d9e53f3c70a214a96e8f651f2508249e3cff9e52` |
| `status` | `BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION` |

manifest 全文は [o5-versioned-benchmark-evaluation-runner-v1.manifest.json](o5-versioned-benchmark-evaluation-runner-v1.manifest.json)、実行結果全文は [o5-versioned-benchmark-evaluation-runner-v1.json](o5-versioned-benchmark-evaluation-runner-v1.json) を正とする。

### Candidate / Baseline

Candidate は Rule Agent v0（`rule_v0`、Champion）。Baseline は `random_legal`。

### 試合数

| Set | Status | Games |
|---|---|---|
| `core_regression` | EXECUTED | 16（`random_legal`。`rule_v0` 自身は候補と一致するため自己対戦をskip） |
| `current_meta` | `BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION` | 0 |
| `adversarial`（archetype系） | `BENCHMARK_BLOCKED_INSUFFICIENT_ACTIVE_POPULATION` | 0 |
| `safety` | EXECUTED | 80（5 member × 16） |
| **合計** | | **96**（logical pair 48 = 6 member×seed組 × 2 pair） |

### Member別結果（Wilson 95% CI、decided_games を分母とする）

| Member | Games | Decided | Win | Loss | Win rate | Wilson 95% CI | Invalid | Crash | Timeout |
|---|---|---|---|---|---|---|---|---|---|
| `random_legal`（core_regression） | 16 | 16 | 10 | 6 | 62.5% | [0.386, 0.815] | 0 | 0 | 0 |
| `random_legal`（safety） | 16 | 16 | 10 | 6 | 62.5% | [0.386, 0.815] | 0 | 0 | 0 |
| `slow_agent` | 16 | 16 | 8 | 8 | 50.0% | [0.280, 0.720] | 0 | 0 | 0 |
| `exception_agent` | 16 | 0 | 0 | 0 | N/A | N/A | 0 | 16 | 0 |
| `invalid_artifact` | 16 | 0 | 0 | 0 | N/A | N/A | 16 | 0 | 0 |
| `unknown_selection` | 16 | 0 | 0 | 0 | N/A | N/A | 16 | 0 | 0 |
| **overall** | 96 | 48 | 28 | 20 | 58.3% | [0.443, 0.712] | 32 | 16 | 0 |

- `exception_agent`／`invalid_artifact`／`unknown_selection` は意図どおり fault を起こし、勝者が決まらないため `decided_games=0`（`win_rate`／CI を「0% 敗北」ではなく「未決着」として区別する）。
- Rule Agent v0（candidate）自身が invalid/crash を起こした試合は0件——`random_legal`／`slow_agent` の invalid/crash が常に0であるのに対し、`exception_agent`／`invalid_artifact`／`unknown_selection` の invalid/crash 件数はそれぞれの設計どおりの16件と厳密に一致することから、fault は常に opponent 側に起因すると推測できる（ただし現状の runner は match 単位の invalid/crash 件数のみを記録し、seat 別の直接attributionは持たない。厳密な per-seat 帰属は既知の限界として次節に記載）。
- `timeouts` は全 member で 0。追加の手動検証として `slow_agent` に `delay_seconds=3.0` を与えても `AGENT_TIMEOUT` は観測されず、20 手・26.7秒で `DONE` した。**観測事実**: このローカル `kaggle_environments==1.32.0` の cabt 実装には、少なくとも 3 秒/手の遅延では作動する actTimeout が確認できなかった（cabt が per-action timeout を持たない、または本評価で使い切れないほど大きい、のいずれかは未確認）。
- Seat 非対称性: `champion_player_0`（先手）16勝8敗、`champion_player_1`（後手）12勝12敗。試行数が少なく（decided 48）確定的な結論ではないが、観測事実として記録する。
- Reproducibility: 全 member で `reproducible=true`（記録試合数が期待試合数と一致）。同一 manifest・同一 output-dir での再実行は既存記録を再利用し、集計結果は完全に一致した（resume 確認）。

## 検証

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/competition_intelligence/ tests/test_run_o5_benchmark_cli.py
# 419 passed
```

実 cabt 実行（再現コマンド。`env -u PYTHONPATH` は既存の `docs/evidence/cabt-capability-recovery.md` の導線に従う。所要時間は初回実行で約30秒、resume時は約3秒）:

```text
env -u PYTHONPATH PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/run_o5_benchmark.py \
  --benchmark-id o5-benchmark-core-v1 --benchmark-version 1.0.0 \
  --candidate-agent-id rule_v0 --deck deck.csv \
  --seeds 90000 90001 90002 90003 --games-per-member 4 \
  --output-dir <output-dir> --max-steps 10000
```

## 情報境界

- 新規 opponent agent（`exception_agent`／`slow_agent`／`invalid_artifact`／`unknown_selection`）は actor-visible な `select.option`／`minCount`／`maxCount` だけを読み、相手の非公開情報を参照しない。`main.py` から到達不能（`competition_intelligence` パッケージ配下）。
- Rules attestation は `UNVERIFIED_RULES_CONSTRAINT`、Team permission manifest は 0 件のまま不変。`current_meta`／archetype 系 `adversarial` を許可なく active 化していない。
- Champion／submission default は Rule Agent v0 のまま。Kaggle 提出は未実施。

## 既知の限界

1. **archetype population は引き続き blocked**: 本 Slice は Core Regression と Safety（fault-injection agent family）のみを実行した。複数 Archetype に対する `current_meta` 評価は、人間による Rules attestation と Team permission manifest の受領まで実行できない（既存 O5 Activation Evidence と同じ制約）。
2. **cabt のシナリオレベル adversarial（setup事故、resource starvation 等)は未実装**: cabt に確認済みの seed 制御／局面注入 API がない（`ENGINE_SEED_SUPPORTED = False`）ため、エージェント挙動レベルの fault（exception／invalid／unknown selection）のみを実装した。未確認の engine 機能を前提にした仕組みは作らなかった。
3. **per-seat の invalid/crash 帰属が間接的**: `crashes`／`invalid_actions` は試合単位の集計であり、`league.actual_runner._summary()` の既存契約をそのまま再利用しているため、どちらの seat が原因かを直接記録していない。本証跡の「opponent 側に起因する」という記述は、`random_legal`／`slow_agent` の invalid/crash が常に0であることからの推測であり、直接測定ではない。
4. **actTimeout の実測範囲は限定的**: `slow_agent` の遅延は 0〜3 秒でしか検証していない。より長い遅延や `kaggle_environments` の異なるバージョンで挙動が変わる可能性がある。
5. **PilotProfile拡張（SETUP_FIRST／DISRUPTION_FIRST）は未対戦**: `EXPERIMENTAL_PILOTS` は `DEFAULT_PILOTS` に影響しない加算的追加として実装したが、archetype population が0のため `build_opponent_population` を通した実対戦検証はできていない。

## 残課題

人間による Rules attestation（`VERIFIED`）と artifact 単位の Team permission manifest を受領した後、`o5 acquire-environment-top-decks` で許可済み exact Deck を分類し、`current_meta` を非空にしたうえで本 Evaluation Runner を再実行することで、複数 Archetype に対する 100 logical-pair 以上の評価へ進められる。Promotion は非自動であり、Champion／default は Rule Agent v0 のままとする。
