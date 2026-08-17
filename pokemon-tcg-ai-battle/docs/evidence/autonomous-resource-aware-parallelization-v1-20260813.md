# Resource-Aware Parallelization v1（2026-08-13）

## 結論

研究用評価器へ接続できる、read-only の resource governor 契約を追加した。CPU、`/proc/meminfo` または optional `psutil`、現在プロセス RSS、swap、`nvidia-smi` の GPU と compute-process 状態を一つの immutable snapshot に束ね、メモリ状態を `normal` / `warning` / `critical` / `emergency` に分類する。推奨 worker 数は logical CPU・安全なメモリ余力・task cap・`max_workers` の最小値で決まり、GPU compute process が存在する場合の GPU-required admission は拒否する。

本変更は既存 production runner、parallel evaluator、既存 performance artifact、training、CABT、longrun、submission を変更・起動しない。governor は worker を spawn/kill せず、無関係 process へ signal を送らない。

## 実装範囲

- `src/mage_ptcg/meta_specialist/resource_governor_v1.py`
  - `ResourceBudget`、`ResourceSnapshot`、`ResourceDecision`、`ResourceGovernor` を提供する。
  - strict mapping reload、unknown/malformed field reject、optional `psutil` fallback、`nvidia-smi` 不在時の CPU-only 動作を実装した。
  - `ramp_workers=(1,2,4,8,12)`、`initial_workers=2`、`max_workers=12`、`recycle_games=16`、`gpu_max=1` を固定した。
  - safe free `10 GiB` / `20%`、critical free `6 GiB`、worker memory reservation `2 GiB` を固定した。
  - telemetry payload は canonical JSON の SHA-256 を含み、temporary file + fsync + exclusive `os.link` で no-clobber 公開する。公開先が先に作られた場合は `FileExistsError` とし、既存 bytes を変更しない。
- `configs/meta_specialist/resource_budget_v1.json`
  - 上記 budget の strict JSON source。
- `tests/meta_specialist/test_resource_governor_v1.py`
  - state classification、CPU/memory/task cap、GPU admission、ramp、strict reload、canonical hash、no-clobber を固定する。

## 状態と admission

`normal` は available memory が `max(10 GiB, total*20%)` 以上、`warning` はそれ未満かつ `6 GiB` 以上、`critical` は `6 GiB` 未満、`emergency` は memory telemetry 不明・非正値または source error とする。`critical` / `emergency` の推奨 worker は 0。`warning` は critical floor を残して計算し、CPU・memory・task cap の最小値を返す。GPU-required の場合は GPU 不在、compute process 検出、`gpu_max` 超過のいずれも 0 admission とする。

## TDD / 検証結果

実装前の RED:

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_resource_governor_v1.py
ModuleNotFoundError: No module named 'mage_ptcg.meta_specialist.resource_governor_v1'
```

GREEN focused suite:

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s tests/meta_specialist/test_resource_governor_v1.py
9 passed in 0.09s
```

静的確認:

```text
PYTHONPATH=.:src .venv/bin/python -m py_compile \
  src/mage_ptcg/meta_specialist/resource_governor_v1.py \
  tests/meta_specialist/test_resource_governor_v1.py
exit=0

git diff --check -- \
  src/mage_ptcg/meta_specialist/resource_governor_v1.py \
  configs/meta_specialist/resource_budget_v1.json \
  tests/meta_specialist/test_resource_governor_v1.py \
  docs/evidence/autonomous-resource-aware-parallelization-v1-20260813.md
exit=0
```

nearby regression は resource module が production runner を import しないことを確認するため、既存 progress/evaluator suites と合わせて実行した。

```text
PYTHONPATH=.:src .venv/bin/pytest -q -s \
  tests/meta_specialist/test_resource_governor_v1.py \
  tests/meta_specialist/test_progress_v1.py \
  tests/test_parallel_cabt_evaluator_v1.py
22 passed in 17.18s
```

## 現環境値（read-only probe）

一次 telemetry: `runs/final-sprint-autonomous/resource-governor-v1-20260813/telemetry.json`

| 項目 | 観測値 |
|---|---:|
| collected_at_utc | `2026-08-13T14:07:15Z` |
| logical CPUs | `28` |
| load1 | `0.14697265625` |
| memory total | `50510598144` bytes（約47.06 GiB） |
| memory available | `46799687680` bytes（約43.59 GiB） |
| memory free | `40154263552` bytes |
| process RSS | `21114880` bytes |
| swap total/free | `8589934592 / 8572272640` bytes |
| nvidia-smi | available |
| GPU count | `1` |
| GPU compute processes | `[]` |
| state | `normal` |
| recommended workers | `12` |
| GPU admission | `true` |
| process kills | `0` |

telemetry payload SHA は `5bcc48e4c82aeda10ad65f5550904c18fabb69d70eff3d13fdefdf1bb0279af6`、ファイル SHA は `34d71d2a4793d533d3389f17bc5c8b70344e77246320b57107b99eb6bc5d3a31` である。GPU 状態は観測時点のものであり、後続 run の予約・占有を意味しない。

## SHA / 再現コマンド

```text
resource_governor_v1.py  2aaa4ed01625361ead9a13c10d2ba1577b11185fbc2fbbd53e624f6b47bf9508
resource_budget_v1.json  e9e6f17d7b395d4973ca7bed8792d40c71367084dced6d3d740eaba62f743848
test_resource_governor_v1.py  6a2836d73bb5c684a97eef1eea633fd92b7bae5e991c2f8b1029d08b0f932086
```

live telemetry の再現:

```bash
PYTHONPATH=.:src .venv/bin/python - <<'PY'
from pathlib import Path
from mage_ptcg.meta_specialist.resource_governor_v1 import ResourceBudget, ResourceGovernor

budget = ResourceBudget.from_json("configs/meta_specialist/resource_budget_v1.json")
ResourceGovernor(budget).write_telemetry(
    Path("runs/final-sprint-autonomous/resource-governor-v1-20260813/telemetry.json")
)
PY
```

この出力先は既存の場合 no-clobber で失敗するため、再実行時は新しい research-only root を指定する。`nvidia-smi` が無い環境では CPU snapshot は継続し、GPU-required のみ拒否する。

## 制約と未実施事項

- governor は recommendation/admission 専用であり、worker poolへ自動接続していない。
- `nvidia-smi` の malformed output は GPU telemetry error として保持し、GPU admission を安全側へ倒す。CPU-only evaluator は継続可能である。
- RSS は現在プロセスのみで、子 worker の将来 RSS を予測しない。task ごとの実測に応じて `worker_memory_gib` を再校正する必要がある。
- resource snapshot から性能、勝率、BestKnown、training readiness、longrun GO を主張しない。

commit、push、remote branch、Champion変更、Kaggle submission は行っていない。
