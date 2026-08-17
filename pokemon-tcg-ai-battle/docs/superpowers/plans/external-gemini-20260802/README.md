# 外部エージェント(Gemini)由来の計画草案（2026-08-02、未レビュー）

この配下は別エージェントが main repo 側で作成した計画草案の退避先であり、**正典ではない**。

## 経緯

Gemini は本 worktree ではなく main repo 側で、同名の `src/mage_ptcg/meta_specialist/` を
薄く再実装した（44 test / 0.71 秒。本 worktree は同領域で 748 test）。
その実装は**採用しない**。判断根拠は次の実測である。

- `entrypoint.py` は `deck.csv` が無いとき `(741,742,743)+range(1000,1057)` という
  架空の 60 枚デッキを合成し、`deck_file_sha256="packaged"`、`source_commit="a"*40`、
  `cabt_legality_status="passed"` を埋めていた。未検証の合法性を「合格」と主張するため、
  AGENTS.md の「データ欠落や実装不備を無意味な既定値で隠さない」および
  本系列の資格化契約に違反する。
- `calibration.py` の `z = 1.95996 if math.isclose(...) else 1.95996` は両分岐が同一で、
  `confidence` 引数が実効を持たない。
- `package.py` 5KB / `runtime.py` 3.5KB / `decks.py` 7.5KB は、本 worktree の
  60KB / 46KB / 33KB 版が担う契約検査を持たない。

## この計画草案の扱い

各 26〜35 行の短い草案であり、内容は既存の
[`2026-08-02-meta-specialist-learning-orchestration-v1.md`](../2026-08-02-meta-specialist-learning-orchestration-v1.md)
（382 行、Slice L1〜L8）が既により詳細に扱う範囲と重なる。

したがって**そのまま実行しない**。設計意図の参考としてのみ残し、採用する場合は
learning-orchestration 計画側へ統合してからとする。
