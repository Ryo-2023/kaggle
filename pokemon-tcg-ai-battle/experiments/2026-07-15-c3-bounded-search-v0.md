# C3 Bounded Search v0 契約fixture評価

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-07-15 JST |
| 担当 | Codex |
| 種別 | local contract fixture / ablation |
| commit | この文書を含む`feature/bounded-search-v0` commit |
| branch | `feature/bounded-search-v0` |
| model provenance | OpenAI Codex / GPT-5 / effort非公開 / CLI versionは実行環境で未提示 |
| simulator / data | `kaggle-environments==1.32.0`公開契約の調査、評価自体はdeterministic fake adapter 5件 |

## 目的と反証条件

- **問い**: primitive-completeなbounded searchが、Rule／Knowledgeをpruningに使わず、明示budgetとfallbackの下で決定論的に動くか。
- **仮説**: guided／unguidedが同じprimitive setを100%展開し、engine valueをpriorより優先し、同一fixtureのdecision signatureが一致する。
- **反証条件**: 非合法selection、primitive coverage欠落、budget超過、priorによるcandidate削除、同一入力の通常完了signature不一致、例外／timeoutでの部分結果採用。
- **変更点**: C3 solver、EngineAdapter protocol、factory、telemetry、fake評価CLIを追加。Rule v0 submission defaultは不変。
- **固定条件**: 同一deck、同一5 fixture、同一1-ply budget、terminal value table、実行順。match、seed、opponentは該当なし。

## 再現

```bash
python scripts/evaluate_bounded_search.py --output-dir /tmp/c3-bounded-search-eval
sha256sum /tmp/c3-bounded-search-eval/{summary.json,counterexamples.json,decisions.jsonl}
```

生成物のうち決定論的counterexampleだけを`artifacts/search/c3_bounded_search_v0_counterexamples.json`へ保存する。latencyを含む`summary.json`／`decisions.jsonl`はGit管理外とする。

## 結果

| condition | seeds | games | win rate | timeout | illegal action | runtime | 備考 |
|---|---:|---:|---:|---:|---:|---:|---|
| Rule v0 | 該当なし | 0 | 未測定 | 0/5 fixture | 0/5 fixture | p50 0.011209 ms / p95 0.023008 ms | fake decision baseline |
| guided | 該当なし | 0 | 未測定 | 0/5 fixture | 0/5 fixture | p50 0.212192 ms / p95 0.343321 ms | oracle 5/5、calls 2.8/decision |
| unguided | 該当なし | 0 | 未測定 | 0/5 fixture | 0/5 fixture | p50 0.200624 ms / p95 0.220722 ms | oracle 4/5、calls 2.8/decision |

- **sanity check**: 3条件×5件を集計し、guided／unguidedのlegal action率とprimitive coverageは100%。2回のdecision signatureは一致した。
- **負の所見**: actual cabt paired matchesは未実施。C2a sample priorは5件で有効だが、Knowledge固有の寄与はfixtureから分離できない。
- **不確実性**: latencyはfake adapter、少数fixture、単一processの値であり、cabt engine call時間を含まない。
- **artifact hash**: summary `6dcb5cc64794654adb9fd4a6c160516318290e1d78a23cb44dbc08c782f0d273`、counterexamples `c83d643c269798c568e5a295b829c3f400f7376ed77ef8e589acb4cc8e3f150e`、decisions `2964eaab4569e018ccbf23fe25bdb870534270d51b1210ba1d525fa9bbb61803`。

## 解釈と判断

- **観測事実**: fake transitionではengine valueがRule／Knowledge priorを上書きし、例外・timeout・adapter不在はRule selectionへ戻った。
- **解釈**: solver制御とoffline teacher traceの入口は成立した。対局強度は評価していない。
- **判断**: coreを採用し、runtime searchは無効のまま保留。Rule v0をChampionに維持する。
- **言わないこと**: synthetic/fake結果を実cabt勝率、latency、性能改善として報告しない。
- **次 action**: 公式のagent-observation forward契約を確認できた場合だけpublic adapterとactual cabt paired評価を追加する。確認できなければoffline teacher契約として保持する。

## Kaggle 提出（該当時）

| 項目 | 値 |
|---|---|
| submission name | 該当なし |
| submitted at | 該当なし |
| source commit | この文書を含むcommit |
| local verification | Rule v0 standalone artifactを別途検証 |
| Public LB | 未提出 |
| Private LB | 未提出 |
| Kaggle URL / ID | 該当なし |
| 備考 | C3 searchはsubmission runtimeで無効 |
