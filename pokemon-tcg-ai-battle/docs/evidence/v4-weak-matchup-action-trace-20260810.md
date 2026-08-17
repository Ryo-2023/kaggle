# V4 弱 matchup・seat・action trace

## 目的

固定 six-opponent heldout 評価で、aggregate 勝率だけでは見えない
`opponent × seat × action type × prefix` の偏りを測る。診断用であり、
checkpoint の promotion authority や heldout protocol は変更しない。

## 実装

`scripts/measure_v4_checkpoint_strength.py` に `--trace-output` を追加した。
有効化した場合だけ、V4 runtime が既に保持している privacy-safe public
decision trace から次の bounded JSONL を atomic に出力する。

- opponent id、seat、game index、seed、decision index
- selection type/context、min/max count、order semantics、selected count
- semantic action type と complete-action log probability
- trace variant

public state digest、action-set digest、candidate identity、option index、
private state、手札・賞品・山札などは保存しない。private/hidden/opaque な
キーを含む投影は fail-closed する。出力 artifact には JSONL の SHA、行数、
action type/opponent/seat 別の集計を記録する。

## 検証

2026-08-10 時点で以下を確認済み。

- trace helper/privacy tests: 2 passed
- existing V4 heldout runner tests: 3 passed
- imitation metrics tests: 3 passed
- py_compile / `git diff --check`: pass

seed0/seed1 各24局の screen を実行し、いずれも fault 0、13/24。seed0 は
seat0=5/12、seat1=8/12、seed1 は seat0=6/12、seat1=7/12 で、trace rows は
1,331 / 1,316 行だった。runtime の duplicate-public-identity aggregate は
semantic identity を保持しないため action_types は空となった。privacy projection
に private field が混在しても CABT 全体を落とさず、該当行だけを
`public-v1-redacted` として保存する修正を実装した。これは action type 推定の根拠
ではなく、次の runtime contract 拡張の入力である。

## 次の実行

必要なら runtime trace contract に coarse semantic-operation multiset を追加する。
その後、offline の `EVOLVE` / `ATTACK` / `END` metrics と seat/opponent の CABT
結果を結合し、action-balanced objective の候補を決める。
