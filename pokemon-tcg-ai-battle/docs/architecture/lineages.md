# 系統マップ — 現行系と旧 R2D3/PSRO 系

`src/mage_ptcg/` の 24 パッケージがどの系統に属し、何から使われているかを一覧にする。
「どのコードが今生きているのか」を判断するための入口であり、設計仕様は書かない。

分類は推測ではなく、**実際の import 関係**から機械的に判定した（2026-08-06 時点、
branch `feature/meta-specialist-canonical`）。

## 3 つの系統

| 系統 | 意味 |
|---|---|
| **提出** | `main.py` が実際に import する。Kaggle へ出るコード |
| **現行** | archaludon meta-specialist の学習系。いま開発している |
| **旧 R2D3/PSRO** | 以前の学習系。現行系からは使われていない |
| **Optional** | AGENTS.md が O1〜O3 と定める任意レーン。critical path 外 |

## パッケージ一覧

| パッケージ | files | 系統 | 使う側 |
|---|---:|---|---|
| `knowledge` | 6 | **提出 + 現行** | `main.py` 3 箇所、`meta_specialist` 4 箇所 |
| `solver` | 4 | **提出** | `main.py`（`bounded_search` / `reliability` / `transition` を再export） |
| `student` | 7 | **提出** | `main.py`（`RuntimeStudentPolicy`） |
| `meta_specialist` | 59 | **現行** | 学習パイプラインの本体 |
| `decision_state` / `exact_file` / `deck_io` | 各1 | 現行 | `meta_specialist` の基盤 |
| `offline_scaleup` | 8 | 現行（一部） | `meta_specialist` 2 箇所 |
| `contracts` | 2 | 共有 | 型定義 |
| `policy_learning` | 38 | **旧 R2D3/PSRO** | 現行系・提出のいずれからも参照なし |
| `continuous_league` | 24 | **旧 R2D3/PSRO** | 下記の例外を除き現行系から参照なし |
| `bootstrap_champion` | 10 | 旧 R2D3/PSRO | 同上 |
| `competition_intelligence` | 56 | Optional (O1) | 現行系・提出から参照なし |
| `offline_training_v1_support` | 62 | Optional | 同上 |
| `offline_training` | 12 | Optional | 同上 |
| `o2_training_loop` | 5 | Optional (O2) | 同上 |
| `opponents` | 19 | 支援 | 対戦相手プール。scripts から利用 |
| `optimization` | 20 | 支援 | CABT / 探索の実装 |
| `distillation` / `evaluation` / `league` / `belief` / その他 | 各 2〜8 | 支援 | — |

## 唯一の跨ぎ: `continuous_league.contracts`

現行系が旧系のパッケージに触る箇所は 1 つだけで、内容は R2D3/PSRO と無関係である。

```python
from mage_ptcg.continuous_league.contracts import content_id, require_sha
from mage_ptcg.continuous_league.contracts import publish_content_addressed_json
```

content-addressed JSON を書くための汎用ユーティリティが、たまたま旧系パッケージの中に
置かれている。`continuous_league` のうち R2D3/PSRO を参照するのは
`psro_manager` / `population_epoch` / `checkpoint_stream` / `learner_service` /
`collector` / `scheduler` / `cli` / `candidate_runtime` / `source_intake` /
`batching` / `replay_sealer` / `experience` の 12 ファイルであり、`contracts` は含まれない。

**TODO:** `continuous_league/contracts.py` を共有の `mage_ptcg/contracts/` へ移せば、
現行系から旧系への依存は完全になくなる。移動には `continuous_league` 側 12 ファイル以上と
`meta_specialist` 側の import 書き換えが伴うため、学習パイプラインの実行中は行わない。

## 提出に載るコードは狭い

`main.py` が import する `mage_ptcg` パッケージは `knowledge` / `solver` / `student` の
3 つだけである。ほかの 21 パッケージはすべて学習・評価・調査用であり、Kaggle へ出る
成果物には含まれない。提出物の安全性を見るときは、まずこの 3 つを見ればよい。

## この表の作り直し方

```bash
# main.py が使うパッケージ
grep -oE "mage_ptcg\.[a-z_]+" main.py | sort -u

# 現行系が使うパッケージ
grep -rhoE "mage_ptcg\.[a-z_0-9]+" src/mage_ptcg/meta_specialist/*.py | sort | uniq -c | sort -rn

# 旧系を名指しするパッケージ
grep -rl "r2d3\|R2D3\|psro\|PSRO" src/mage_ptcg/<package>/
```
