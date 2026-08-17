# Meta Specialist v3 作業中断時点の引き継ぎ

作成日: 2026-08-09 JST  
状態: ユーザー指示により作業中断。commit、push、Kaggle 提出は実施していない。

## 結論

現時点で「長時間学習を開始してよい」と判定できる状態には到達していない。Task 1 の既存修正は実装・テスト上かなり進んでおり、今回残っていた二つの load-bearing 指摘（legacy v1 の新規 write 許可、persistent timeout から spawn fallback への exit code 欠落）について再修正コードと回帰テストが作業ツリーに見えている。ただし、再修正サイクル後の主担当による独立した最終検証・レビュー・ledger 完了記録までは終わっていないため、修正済みと確定扱いしない。

Task 2/3 の read-only 監査では、性能向上の前提となる表現、データ分割、teacher/critic、sealed theta0 が未接続であることを確認した。したがって、現時点で長時間学習を開始すると、誤った表現やデータ漏洩、synthetic critic、未封印 checkpoint を増幅するリスクが残る。

## 統合状態

- 現在の checkout は `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle`。
- branch は `feature/belief-guided-search`。
- `git worktree list` ではこの checkout のみが登録されており、元の `pokemon-tcg-battle-worktree` は現在の Git worktree として残っていない。内容は本 checkout の多数の tracked/untracked 差分と、保全 artifact の `runs/from-worktree/meta-specialist-canonical/` に統合・保存されている。
- 既存のユーザー差分を整理、削除、commit していない。`git status --short` には deck、opponent、meta-specialist 実装、tests、scripts、docs、opponent artifact などが大量に残っている。これらを今回の作業の新規差分とみなして一括処理してはならない。
- GPU は OS 側で利用できないことを確認済み（`torch.cuda.is_available() == False`、device 0）。Python は 3.12.3、Torch は 2.11.0+cu128 だが、現セッションでは CUDA 実行不可。

## ここまでに確認・実施したこと

### 正典・計画

以下を読み、計画と現行実装を照合した。

- `AGENTS.md`
- `docs/superpowers/plans/2026-08-08-meta-specialist-v3-remediation.md`
- `.superpowers/sdd/2026-08-08-meta-specialist-v3-remediation/task-1-report.md`
- `docs/evidence/meta-specialist-v3-final-report.md`
- 保全 artifact `runs/from-worktree/meta-specialist-canonical/`

実行計画の critical path は `Task 1 → Task 2 → Task 3/4 → Task 5/6`。Promotion は evidence gate を通過するまで行わない方針を維持する。

### Task 1 の既存成果

Task 1 の5回の修正と既存 Sol XHigh レビューで、次の主要項目が実装済みとして記録されている。

- engine 非決定時の paired promotion 推論を fail-closed 化
- 独立 fresh-game・seat/opponent 層化評価
- synthetic/unattested evidence による promotion bypass の閉鎖
- retry 時の game identity と controlled RNG の統一
- spawn/persistent fault provenance と1回 retry
- source exception、traceback、state/action trace の伝搬
- trace-aware retry 分類
- collector summary の最終 attempt 基準化
- 既存 v1 record の read/resume 互換
- bool seat 拒否、NumPy/Torch/Python RNG 分離テスト

Task 1 report に記録された既存 focused suite は以下。

```text
tests/meta_specialist/test_actor_pool_v1.py
tests/meta_specialist/test_fault_diagnostics_v1.py
tests/meta_specialist/test_collect_trajectories_cli.py
tests/meta_specialist/test_evaluation_protocol_v2.py
tests/meta_specialist/test_evaluation_suites_v1.py
160 passed in 35.70s
```

同 report には、同じ5モジュールを一時的な `tests` package 経由で実行した isolated suite も `160 passed in 33.32s` とある。`py_compile` と `git diff --check` も当時は成功している。

### 今回開始した Task 1 再修正サイクル

ユーザーが提示した残存2件に対し、Terra担当を1名起動してTDD修正を依頼した。作業は中断時に停止した。

1. legacy v1 record を read/resume のみで許可し、`write_actor_pool_game_record_v1` の新規出力では厳密な現行 schema のみを受け付ける。
2. persistent timeout 後の spawn fallback の実 child exit code を、persistent worker の PID 表だけで再束縛して `None` に上書きしない。

中断時点でファイル上は次の変更が確認できる。

- `write_actor_pool_game_record_v1` が `_validate_game_record_shape_v1(..., allow_legacy=False)` を呼ぶ。
- reader は `allow_legacy=True` で既存 record を読み、`replay_evidence_status="unavailable_legacy_v1"` を読み取り時だけ付加する。
- legacy record write の拒否テストが追加されている。
- persistent timeout/fallback の PID と exit code を分けて検証する回帰テストが追加されている。
- spawn fallback（persistent metadata ではない結果）は joined persistent PID map で再束縛しない実装になっている。

ただし、上記は差分の静的確認であり、再修正サイクル後の独立 clean run とレビューが未完了である。`task-1-report.md` にはこのサイクルの最終結果をまだ追記していない。

### Task 2 gap audit

監査報告: `.superpowers/sdd/2026-08-08-meta-specialist-v3-remediation/task-2-gap-audit.md`  
判定: **PASS 0 / PARTIAL 3 / MISSING 5**。

主な未達は以下。

- `EntityTokenV3` に locator がなく、同じ card が active/bench や同一 zone に複数ある場合に endpoint を一意解決できない。
- R3-A が owner×zone の own-active / own-bench / opponent-active / opponent-bench / other-public pool になっていない。
- R3-B の FFN が 384 で、要求の 512 ではない。same-owner、same-host、active、source/target、public-evolution relation も不足。
- stable action ID の hash bucket embedding が semantic input に混入している。
- multi-selection の selected mask、duplicate exclusion、canonical set/order-sensitive step が未接続。
- episode と non-ubiquitous near-duplicate の transitive connected-component split がなく、単純な文字列 group split に留まる。
- current v2 の real policy baseline と full legal-candidate/complete-action target ではなく、`_R2NegativeControl` と `option_type` 中心の比較になっている。
- metrics、3 deterministic seeds、validation early stopping が未接続。
- artifact root は保全されているが、formal Gate 1 runner と現 checkout 用の canonical path 固定がない。

### Task 3 gap audit

監査報告: `.superpowers/sdd/2026-08-08-meta-specialist-v3-remediation/task-3-gap-audit.md`  
判定: **PASS 0 / PARTIAL 2 / MISSING 4**。

主な未達は以下。

- teacher manifest に real current-pool evidence、policy/deck provenance、fault、confidence/agreement/search/strength から導出した quality weight がない。現存 manifest は quality weight がほぼ 1.0。
- recurrent BC は GRU 部品を持つが、各 decision を `episode_start=True` で処理しており hidden state が継続しない。padding、burn-in、ordered sequence batch、CC split がない。
- critic は synthetic features/labels 中心で、real eventual outcome、seat/opponent-family/trajectory-position strata、uniform 比較が未接続。
- conditioning は game-seed mode を許容し、stable category の頻度閾値（<64 unknown、>=128 dedicated 等）がない。
- checkpoint は存在するが、artifact が `not_sealed` と記録しており、selected source/config/data/split/model/critic の canonical manifest chain がない。
- 4 lane × 3 seed の real bounded orchestration がなく、single root/single seed smoke に留まる。

## 中断時に未完了だった作業

- Task 1 再修正サイクルの独立レビュー。
- Task 1 の再修正後 focused suite の fresh execution と、旧 160件結果との差分確認。
- Task 1 report/progress ledger への再修正結果の追記。
- Task 2 の実装（locator、R3 topology、stable ID 除去、multi-selection、CC split、real-v2 Gate 1 runner、metrics/3 seeds/early-stop）。
- Task 3 の実装（teacher evidence/weight、ordered recurrent BC、real critic、conditioning threshold、sealed theta0、4×3 orchestration）。
- Task 4 の read-only gap audit。Terra担当を起動したが、ユーザー指示により中断したため、完了報告は未採用。
- Task 5/6 の read-only gap audit。Terra担当を起動したが、ユーザー指示により中断したため、完了報告は未採用。
- GPU が利用できないため、正式な4 lane × 3 seed、real learner、独立層化評価、長時間学習の実行。

## 再開時の順序

1. Task 1 の二件を fresh test で再現・確認し、独立 reviewer に差分と証拠を渡す。
2. Task 2 を TDD で実装する。最初に locator と stable-ID semantic independence、次に R3-A/R3-B と multi-selection、続いて shared CC split、最後に real-v2/full-candidate Gate 1 runner を接続する。
3. Gate 1 を同一 split・同一 target・同一 compute 条件で3 seed 実行し、top-1/top-3、rare recall、action-type NLL、p50/p95、CPU preprocess、可能なら CUDA VRAM を記録する。
4. Task 3 の teacher evidence/quality weight、ordered recurrent BC、real-outcome critic、conditioning threshold、sealed theta0、4 lane × 3 seed bounded run を実装・検証する。
5. Task 4 の実 trajectory → learner loss 接続と、同一 sealed theta0 から PPO/V-trace/AWR-CRR が実 optimizer update を行うことを確認する。
6. 2 lane × 3 seed の短期 pilot、独立 fresh-game・seat/opponent 層化評価、baseline 差の正方向を確認する。
7. 上記が揃うまで長時間学習は開始しない。GPU が戻らない場合は、CPU smoke/evidence と未実施の理由を記録し、正式 readiness は保留する。

## 長時間学習開始 Gate

最低限、以下を全て満たす必要がある。

- R2/R3 legal-action 比較で Gate 1 通過
- episode/near-duplicate leakage ゼロ
- recurrent BC が独立 validation で改善
- critic Brier が uniform を上回り、負相関なし
- 同一 theta0 から PPO/V-trace/AWR-CRR が実 optimizer update
- 2 lane × 3 seeds の短期 pilot で複数 seed に改善傾向
- 独立層化評価で baseline より正方向
- artifact provenance、fault、source hash、split、checkpoint hash が sealed manifest で再現可能

## Git・成果物の扱い

この作業停止時点では commit していない。現在の dirty worktree は今回だけの変更とは限らず、既存の統合作業・実験生成物を含む。再開時も、まず `git status --short` と対象ファイルの差分を確認し、無関係な差分を上書き・整形・削除しないこと。

主要参照先:

- 計画: `docs/superpowers/plans/2026-08-08-meta-specialist-v3-remediation.md`
- Task 1: `.superpowers/sdd/2026-08-08-meta-specialist-v3-remediation/task-1-report.md`
- Task 2 監査: `.superpowers/sdd/2026-08-08-meta-specialist-v3-remediation/task-2-gap-audit.md`
- Task 3 監査: `.superpowers/sdd/2026-08-08-meta-specialist-v3-remediation/task-3-gap-audit.md`
- 現状総括: `docs/evidence/meta-specialist-v3-final-report.md`

