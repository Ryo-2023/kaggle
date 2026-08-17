# V4 wave2 Archaludon: fixed held-out 評価プロトコル（2026-08-10）

## 結論

wave2 が生成する **新 source-closure の checkpoint だけ**を、Archaludon の正しい subject deck、固定6 opponent、両seatで評価する。最短で比較可能な手順は、`seed 0/1` の各24局development screenを先に完走させ、両方が fault 0 なら同じ2 checkpointとV2 baselineを各96局で再測定する流れである。

過去の V4 checkpoint は、runtime projection と multi-select timeout の修正前に作られたものを含み、現在の V4 strict loader が implementation source closure の不一致として拒否する。このため score の良否にかかわらず、wave2 と同じ evaluation arm に再利用してはならない。

## 評価対象・比較固定条件

| arm | checkpoint | subject deck | 実装ローダー |
| --- | --- | --- | --- |
| V4 wave2 seed 0 | `runs/meta-specialist-v4-performance-wave2/archaludon-64ep-4epoch-checkpoints/seed-0/best-recurrent-bc-v4.pt` | `opponents/public_archaludon_cinderace_r7/deck.csv` | V4 file SHA-256 + tensor-state SHA-256 + implementation/callable closure strict load |
| V4 wave2 seed 1 | `runs/meta-specialist-v4-performance-wave2/archaludon-64ep-4epoch-checkpoints/seed-1/best-recurrent-bc-v4.pt` | 同上 | 同上 |
| V2 baseline | `runs/from-worktree/meta-specialist-canonical/meta-specialist-bc-distill/v2smoke-archaludon/checkpoints/checkpoint-6518c148e3ac5849e0ded4cd6d45a11cc5314a716e97fe000f2853799fdcd45e.pt` | 同上 | production actor-pool strict V2 load |

全 arm で次を一致させる。固定 opponent の順序は `EVAL_HELD_OUT_V1` の6件であり、任意の相手差替えはできない。

- `--opponent-count 6`
- `--games-per-seat 2`（development: 24局）または `8`（confirmation: 96局）
- `--base-seed 9600000`（development）または `9700000`（confirmation）
- `--max-steps 2000`
- 両 seat（各 opponent / seat / rep を全て実行）

runner は fault を requested-game 分母に 0 点として残し、1件でも `comparison_status="invalid_faults"` にする。比較表に採用できるのは `comparison_status="valid"`、`faults=0`、`games_played=requested_games` の arm だけである。JSON には score だけでなく seat別・opponent別 W-D-L-F、fault reason、checkpoint digest、経過時間が保存される。

## 実行順

### 0. wave2 artifact の完了確認

wave2 report が `seed_results` に seed 0 と seed 1 を持ち、双方の `best_checkpoint_path` が上表の regular file を指すことを確認する。学習の report に記録された `best_checkpoint_file_sha256` / `best_checkpoint_tensor_state_sha256` と評価 JSON の digest が一致しなければ評価を破棄する。

### 1. development screen: V4 2 seed + V2 baseline、各24局

以下を順に実行する。各 command は strict binding も実行するので、checkpoint の source closure drift があれば CABT game を始める前に非0終了する。その場合は checkpoint を修復・再発行するまで後段へ進まない。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/measure_v4_checkpoint_strength.py \
  --checkpoint runs/meta-specialist-v4-performance-wave2/archaludon-64ep-4epoch-checkpoints/seed-0/best-recurrent-bc-v4.pt \
  --subject-deck-csv opponents/public_archaludon_cinderace_r7/deck.csv \
  --subject-archetype-id archaludon --opponent-count 6 --games-per-seat 2 \
  --base-seed 9600000 --max-steps 2000 \
  --output runs/meta-specialist-strength/v4-wave2-archaludon-seed0-dev24.json

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/measure_v4_checkpoint_strength.py \
  --checkpoint runs/meta-specialist-v4-performance-wave2/archaludon-64ep-4epoch-checkpoints/seed-1/best-recurrent-bc-v4.pt \
  --subject-deck-csv opponents/public_archaludon_cinderace_r7/deck.csv \
  --subject-archetype-id archaludon --opponent-count 6 --games-per-seat 2 \
  --base-seed 9600000 --max-steps 2000 \
  --output runs/meta-specialist-strength/v4-wave2-archaludon-seed1-dev24.json

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/measure_v2_checkpoint_strength_fixed.py \
  --checkpoint runs/from-worktree/meta-specialist-canonical/meta-specialist-bc-distill/v2smoke-archaludon/checkpoints/checkpoint-6518c148e3ac5849e0ded4cd6d45a11cc5314a716e97fe000f2853799fdcd45e.pt \
  --subject-deck-csv opponents/public_archaludon_cinderace_r7/deck.csv \
  --subject-archetype-id archaludon --opponent-count 6 --games-per-seat 2 \
  --base-seed 9600000 --max-steps 2000 \
  --output runs/meta-specialist-strength/v2-wave2-archaludon-baseline-dev24.json
```

Development scoreは小標本の選抜材料であって性能主張ではない。両 V4 arm fault 0、baseline fault 0、かつ少なくとも一方のV4が V2 development scoreを上回る（同点は未達）場合に、次の96局へ進む。片方のみがこれを満たす場合でも両seedの再現性は未確認なので、その survivor を96局で特性確認してよいが、長時間学習の採用候補にはしない。

### 2. confirmation: V4 2 seed + V2 baseline、各96局

stage 1 の全 arm が fault 0 かつ進行条件を満たしたときのみ、別の base seed label で3 armを測る。V2を再測定することで、96局の score、seat別、opponent別を V4 と同じ command contract で比較できる。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/measure_v4_checkpoint_strength.py \
  --checkpoint runs/meta-specialist-v4-performance-wave2/archaludon-64ep-4epoch-checkpoints/seed-0/best-recurrent-bc-v4.pt \
  --subject-deck-csv opponents/public_archaludon_cinderace_r7/deck.csv \
  --subject-archetype-id archaludon --opponent-count 6 --games-per-seat 8 \
  --base-seed 9700000 --max-steps 2000 \
  --output runs/meta-specialist-strength/v4-wave2-archaludon-seed0-confirm96.json

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/measure_v4_checkpoint_strength.py \
  --checkpoint runs/meta-specialist-v4-performance-wave2/archaludon-64ep-4epoch-checkpoints/seed-1/best-recurrent-bc-v4.pt \
  --subject-deck-csv opponents/public_archaludon_cinderace_r7/deck.csv \
  --subject-archetype-id archaludon --opponent-count 6 --games-per-seat 8 \
  --base-seed 9700000 --max-steps 2000 \
  --output runs/meta-specialist-strength/v4-wave2-archaludon-seed1-confirm96.json

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/measure_v2_checkpoint_strength_fixed.py \
  --checkpoint runs/from-worktree/meta-specialist-canonical/meta-specialist-bc-distill/v2smoke-archaludon/checkpoints/checkpoint-6518c148e3ac5849e0ded4cd6d45a11cc5314a716e97fe000f2853799fdcd45e.pt \
  --subject-deck-csv opponents/public_archaludon_cinderace_r7/deck.csv \
  --subject-archetype-id archaludon --opponent-count 6 --games-per-seat 8 \
  --base-seed 9700000 --max-steps 2000 \
  --output runs/meta-specialist-strength/v2-wave2-archaludon-baseline-confirm96.json
```

## 採用判断

### 長時間学習へ進める最低条件

1. wave2 training JSON で両seedの validation complete-action NLL が initial より低下し、positive STOP の対象・評価 coverage が記録される。
2. 24局・96局とも評価対象の V4/V2 arm が全て fault 0、全requested game完了、strict checkpoint provenance が一致する。
3. 96局で **両 V4 seed** が V2 baseline score を上回り、V4の2 seed平均も V2を正方向で上回る。
4. 両seatが V2 対比で正方向（少なくとも同点でない）で、改善が単一 seat だけの有利さではない。
5. opponent別では6相手中4以上が V2以上、かつ悪化する相手が1相手に集中せず、score差が baselineの既知の不確実性に埋もれないことを確認する。

この条件は「正式promotion」ではなく、Archaludon の約1,000 update長時間BCを開始してよいという研究上の準備判定である。V2既存96局 artifact は 24/96（0.250、seat 0=0.292、seat 1=0.208、fault 0）であり、過去の旧V4 seed1 は37/96（0.385、両seat正方向）だった。ただし旧V4は現source closureで再ロードできないため、wave2の採否根拠に転用しない。

### 不採用・再調査条件

- fault、strict loader failure、または requested games 未完了が一つでもある: score比較を無効として runtime/source closure を先に修正する。
- 両seedの96局で V2以下: capacityだけを増やさず、sampling/update budget と static BC warmup を原因候補として再分析する。
- 一方だけが96局でV2を上回る: seed不安定として長時間化しない。強い方の特徴は保存するが、同一設定のthird seedまたはデータ/optimization変更を先に検証する。
- seat片側だけの改善、または2相手以下の改善に留まる: matchup/seat 偏りと扱い、長時間学習の根拠にしない。

### 反証上の注意

runner は policy/agent randomness を同じ `base_seed + game_index` で初期化するが、CABT engine RNG が完全なseed-attested paired comparisonを提供していない。したがって同じ base-seed label は command条件の整合を意味し、同一初期局面の完全ペアリングを意味しない。96局、両seat、固定6相手、2 seedを要求するのはこの未対化ばらつきを縮めるためであり、単一24局や旧artifactとの単純差分を性能証拠にしない。

## 所要時間見積り

既存の同一runner実測では、Archaludon V4 24局は29.7–30.6秒、V2 24局は20.3秒、V4 96局は115.8秒、V2 96局は92.5秒だった。wave2 V4にはより高速なruntime projection/batched decoderが入っているため、これより長くなる根拠はないが、保守的に以下を見込む。

| stage | 実行数 | 実測基準の合計 | 実運用見積り |
| --- | ---: | ---: | ---: |
| dev24 | V4×2 + V2×1 | 約81秒 | 2–4分 |
| confirm96 | V4×2 + V2×1 | 約324秒 | 6–10分 |
| 合計 | 360局 | 約405秒 | 8–14分 |

実行はCPU CABT workerを使う。GPUは学習に解放したままでよく、この評価自体にCUDAは不要である。

## 監査根拠

- `scripts/measure_v4_checkpoint_strength.py`: V4 strict file/tensor digest binding、fixed pool、両seat、fault fail-closed JSON。
- `scripts/measure_v2_checkpoint_strength_fixed.py`: 同条件の V2 production actor-pool loader とJSON schema。
- `runs/meta-specialist-strength/v2-confirm-archaludon-v2smoke-seed9500000-96.json`: V2 baselineの既存96局、fault 0。
- `runs/meta-specialist-strength/v4-confirm-archaludon-gpu-seed1-seed9500000-96.json`: source-closure修正前 V4の参考観測（再利用不可）。
- `docs/evidence/performance-first-sprint-20260810.md`: wave2 の目的と、long-runへ進める前に96局を要求する判断。

