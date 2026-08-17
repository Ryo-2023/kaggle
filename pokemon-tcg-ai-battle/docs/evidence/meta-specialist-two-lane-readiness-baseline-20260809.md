# Meta Specialist 2レーン Readiness Baseline（2026-08-09）

## 結論

現行 CABT 環境は engine seed と verified replay を証明していないため、paired performance inference は使用禁止のままとする。Readiness 評価は `IndependentEvaluationRecordV3` の事前固定した独立 arm ledger だけを受理する。

## 固定した評価契約

- 各 lane × training seed × policy arm は、6 held-out opponents × 2 seats × 8 repetitions、計 96 attempted games を完全に持つ。attempt 自体の欠落と key 重複は fail closed である。
- `fault` は provenance を伴う attempted game として保存し、candidate の勝利には数えない。除外・補完はしない。
- candidate の `theta0_sha256` は同じ lane/seed の theta0 arm artifact hash と一致しなければならない。
- cell 内は opponent × seat を等重み、lane × seed cell も等重みで macro delta を作る。bootstrap は seed `20260809`、20,000 replicates、primary interval は片側 95% lower bound とする。
- evidence kind は `measured` だけを受け付ける。synthetic/unit-only evidence は readiness または promotion inference に利用できない。

## Engine capability と paired 禁止

- 実 CABT runner call shape の回帰テストは configuration に `seed` がないことを確認し、`ENGINE_SEED_UNSUPPORTED` を観測する。
- `evaluation_inference_allowed_v2(engine_seed_supported=False, replay_verified=False)` は例外で拒否する。verified engine replay が両方 true でない限り paired/promotion inference は使えない。

## 実行環境と baseline 状態

- Git HEAD: `30cade0e5d349d6ea545f019fc411e9d53288f16`。
- worktree は開始時から dirty であり、他作業の追跡済み・未追跡差分が多数ある。baseline は clean checkout を主張しない。
- CUDA: `nvidia-smi` は `Failed to initialize NVML: GPU access blocked by the operating system` で、GPU/driver/CUDA version はこの実行環境から未確認。
- isolated import は `DeckAssetInput` の import 時に `agents` package を必要としない。deck lock/lineage の content ID と lowercase SHA-256 表現は従来の canonical JSON byte contract を維持する。

## 検証

- RED: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_decks.py::test_meta_specialist_parses_an_explicit_deck_with_poisoned_root_main tests/meta_specialist/test_evaluation_protocol_v2.py -k 'readiness_v3 or poisoned_root'` → 1 failed。`decks.py` の eager `continuous_league` import が `ModuleNotFoundError: No module named 'agents'` を起こした。
- RED: v3 test 追加後の同 command → collection error。`IndependentEvaluationRecordV3` が未定義だった。
- GREEN: 同 command → `4 passed, 13 deselected in 0.86s`。
- affected: `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/meta_specialist/test_evaluation_protocol_v2.py tests/meta_specialist/test_decks.py` → `63 passed in 1.36s`。
- Meta Specialist full suite は fresh 実行を試行したが、実行基盤が約30秒で終了状態を返し、`[4%]` までの progress のみで exit status を返さなかった。そのため full-suite PASS は未主張であり、継続実行可能な terminal で再実行が必要である。

## 非性能根拠の注意

この文書の unit test fixture と bootstrap output は契約検証用であり、learner 性能または長時間学習 readiness の根拠ではない。実測 pilot record と sealed manifest は未作成である。
