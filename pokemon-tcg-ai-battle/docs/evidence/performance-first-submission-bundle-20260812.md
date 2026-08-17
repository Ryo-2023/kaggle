# Performance-First Final Sprint: 提出 bundle 監査・archive-only smoke

## 結論

2026-08-12 時点で、実際の提出エントリポイントへ接続されている候補は `main.py` の Rule Agent v0 とリポジトリ直下の `deck.csv` だけである。この組み合わせは、提出アーカイブを作成し、アーカイブ展開後の clean-room subprocess から CABT を 2 ゲーム実行して、両ゲーム `DONE`、fault 0、illegal action 0 を確認した。したがって、本記録の判定は `RULE_V0_ARCHIVE_SMOKE_PASS` とする。

Wave6 V4 seed-0 checkpoint と Archaludon deck は、checkpoint と deck の対応自体は確認できたが、現行の production `main.py` は checkpoint をロードしない。さらに production card vocabulary gate と runtime dependency closure の検証が未完了であるため、V4 は提出 package として生成・採用していない。判定は `WAVE6_V4_NOT_SUBMISSION_READY` である。

この作業では Kaggle API、Kaggle CLI、外部提出を一切実行していない。production `main.py` は変更していない。

## 監査対象と再現条件

* リポジトリ: `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle`
* branch: `feature/belief-guided-search`
* 監査時 HEAD: `30cade0e5d349d6ea545f019fc411e9d53288f16`
* worktree: dirty（他作業の既存差分を保全し、reset/checkout/commit は実行していない）
* Python 実行: `.venv/bin/python`
* audit/build module: `scripts/build_performance_submission_bundle_v1.py`
* focused tests: `tests/test_performance_submission_bundle_v1.py`

この evidence は研究 checkpoint の性能や Kaggle leaderboard の結果を表すものではなく、「現在の候補を提出可能なファイル集合として閉じ込め、提出環境から読み込めるか」を確認する記録である。

## Candidate A: Rule Agent v0 + root deck

### 実装経路

```text
main._DEFAULT_AGENT
  -> make_rule_agent()
  -> agents.choose_rule_indices
```

`main.py` の root route が使用するのは Rule Agent v0 であり、research-only の V4 checkpoint loader は呼ばれない。監査時の combined policy source SHA-256 は次の通り。

```text
750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b
```

対象ファイルは `main.py`、`agents/__init__.py`、`agents/rule_agent.py` である。

### deck identity

* path: `deck.csv`
* card count: 60
* SHA-256: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
* `coherent_pair`: true

### archive

生成済み artifact:

```text
runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck/submission.tar.gz
size: 5908 bytes
SHA-256: da4bbe9d65c6d42feae2070fc02711efde4ffbe1ca08b9f9f00bfed3d96f9b9a
```

archive の runtime member は次の 4 ファイルだけである。manifest は archive 外の検証 metadata として同じ artifact directory に保存される。

| member | bytes |
|---|---:|
| `main.py` | 16514 |
| `deck.csv` | 241 |
| `agents/__init__.py` | 183 |
| `agents/rule_agent.py` | 5863 |

### archive-only smoke

実行コマンド:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  scripts/build_performance_submission_bundle_v1.py archive-smoke \
  --archive runs/meta-specialist-performance-sprint-v1/rule-v0-root-deck/submission.tar.gz \
  --games 2 --seed 33000
```

ハーネスは archive を一時ディレクトリへ展開し、抽出された archive root を project code root として `python -I` subprocess を起動する。repo source path は subprocess から除外するが、venv の site-packages は保持する。これは、site-packages パス自体に repository 名が含まれる環境で、文字列 substring 除外により `kaggle_environments` まで消える回帰を防ぐためである。

2026-08-12 の実測値:

| 項目 | 結果 |
|---|---:|
| games | 2 |
| wins / losses / draws | 1 / 1 / 0 |
| game status | 2 件とも `["DONE", "DONE"]` |
| faults | 0 |
| illegal actions | 0 |
| legality | `pass` |
| latency samples | 292 |
| p50 | 0.014191 ms |
| p95 | 0.025082 ms |
| p99 | 0.055494 ms |
| max | 1.127999 ms |
| archive_only | true |
| status | `PASS` |

勝敗は smoke の動作確認用であり、2 ゲームから性能優位や Champion 昇格を主張しない。ここでの合格条件は、アーカイブだけで import・対戦が完了し、fault と非合法手がないことである。

## Candidate B: Wave6 V4 seed-0 + Archaludon

### identity

* checkpoint: `runs/meta-specialist-v4-archaludon-longrun-wave6-current/archaludon-training-checkpoints/seed-0/best-recurrent-bc-v4.pt`
* checkpoint SHA-256: `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de`
* deck: `opponents/public_archaludon_cinderace_r7/deck.csv`
* deck card count: 60
* deck SHA-256: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
* `coherent_pair`: true

### package blockers

監査結果の blocker は以下の 3 件である。

1. `production_entrypoint_not_connected`: root `main.py` の `_DEFAULT_AGENT` は V4 checkpoint をロードせず、V4 は research-only factory 経路に留まっている。
2. `production_card_vocabulary_gate`: production 用 card vocabulary の sealed gate を通過した証拠がない。V4 の研究用データ／factory を提出 runtime のカード語彙として代用しない。
3. `runtime_dependency_closure_unvendored`: `src/mage_ptcg` 等の依存閉包を提出 archive 内へ完全に vendoring し、外部 checkout に依存しないことが未証明である。

このため V4 の tarball は作っていない。checkpoint と deck の SHA が揃っていることだけを「提出可能」と解釈してはならない。production entrypoint、カード語彙、依存閉包、clean-room runtime の全てが接続・検証されるまで、V4 は研究候補である。

## テストと検証

先に追加した focused test は、bundle audit、deck 60 枚制約、V4 blocker 表示、archive-only smoke subprocess の path isolation を対象とする。

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python \
  -m pytest -q -p no:cacheprovider \
  tests/test_performance_submission_bundle_v1.py
```

結果:

```text
....                                                                     [100%]
4 passed in 0.08s
```

archive の構造検証と clean-room verification は既存 `scripts/build_submission.py` の検証機能を再利用している。追加ハーネスは提出操作を持たず、archive の展開・import・CABT smoke だけを担当する。

## 変更範囲と未実施事項

追加したのは次の研究・検証用ファイルである。

* `scripts/build_performance_submission_bundle_v1.py`: 現行 Rule route と Wave6/V4 pair の監査、Rule archive build、archive-only smoke。
* `tests/test_performance_submission_bundle_v1.py`: 4 件の focused regression tests。
* `docs/evidence/performance-first-submission-bundle-20260812.md`: 本 evidence。

以下は意図的に未実施である。

* `main.py` の production policy 差し替え
* V4 の未証明 package 生成
* Kaggle submit、Kaggle API、外部状態変更
* Champion 更新、leaderboard 性能の主張
* worktree の cleanup、既存差分の reset、commit、push

## 次の再開条件

V4 を提出候補に進める場合は、まず production entrypoint が固定した checkpoint を実際に読み込む最小 bundle を別途設計し、production card vocabulary gate と dependency closure を閉じる。その後、archive-only subprocess で Rule v0 と同じ fault/legality/latency checks を通し、manifest と全 SHA を固定する。これらが完了するまで、今回の Rule v0 archive を現在の実提出経路として扱う。
