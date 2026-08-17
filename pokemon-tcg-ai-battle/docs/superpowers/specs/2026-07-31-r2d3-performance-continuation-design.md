# R2D3 性能変更時の継続実験設計

## P0 深度

R2。学習 checkpoint、Replay、holdout の一回性を保ったまま controller の
再開境界を拡張するため、通常の速度変更より高い誤用リスクがある。

## P1 問題と成功条件

v15 は `psro-best-response-seed0` を 12,500/12,500 update まで完走したが、
直後の validation worker の旧 host-memory 事前確認で停止した。v16 の速度変更
（validation concurrency と learner memory budget）で、この完走済み checkpoint
を捨てず、v16 artifact に provenance を残して validation から進める。

成功条件は、親 artifact の checkpoint、Replay、母集団、デッキ、semantic feature
の整合性を検証し、v16 が親の最新 checkpoint を読み込めること、既に使った
holdout を再実行しないこと、中断後の `--resume` でも同じ継続 lineage を検証する
ことである。互換性が証明できない場合に新規学習へ黙って切り替えることは失敗である。

## P2 前提

| 前提 | 状態 | 確認方法 |
|---|---|---|
| v15 の BR checkpoint は完走済み | 確実 | `checkpoints/psro-best-response-seed0/training_manifest.json` の step 12,500 |
| v15 の停止原因は validation 前の host-memory preflight | 確実 | `stages/psro_best_response/status.json` |
| 速度変更は policy の入力表現、デッキ、母集団、凍結 Replay を変えない | ユーザー承認済み | parent/current manifest hash と checkpoint load で再検証 |
| learner 実装の未コミット差分が weight schema と互換 | 未検証 | checkpoint load の回帰テストと実起動 preflight |

## P3 選択肢と決定

1. v15 artifact を直接 rebaseline する: 最短だが、source identity と既消費
   holdout の監査境界を曖昧にするため不採用。
2. v16 をゼロから実行する: 最も単純だが、完走済み 12,500 update を捨てるため不採用。
3. 親 artifact を明示指定して v16 に継続 lineage を作る: checkpoint と必要な
   immutable input を hash 検証し、既完了 stage を inherited と記録する。採用。

## アーキテクチャ

`--continue-from-artifact <parent>` を初回 v16 起動専用の明示的な引数として追加する。
controller は parent の stage output、`replay.json`、`psro_online_replay.json`、
最新の training checkpoint を検証して v16 artifact へ継続 manifest を作る。
親の PASS stage は immutable な inherited evidence として利用し、v16 では
`psro_best_response` を再開する。checkpoint は v16 側の checkpoint directory に
materialize し、親 hash・旧 training identity・移植時刻を durable manifest に記録する。

継続 checkpoint の load は population hash、Replay hash、checkpoint schema、model/target/
optimizer state、step、親 checkpoint hash を必須検査する。source patch の違いだけで
拒否しない代わりに、この移植経路は `--continue-from-artifact` による明示操作だけで
有効にする。通常の `--resume` は引き続き同一 v16 identity を要求する。

## エラー処理と一回性

- parent artifact が未完了、Replay hash 不一致、checkpoint 不在、checkpoint load 失敗、
  selected architecture 不一致の場合は fail-closed とする。
- 継続開始後、v16 に `continuation_manifest.json` があれば、`--resume` 時に親 hash と
  imported checkpoint hash を再検証する。
- parent の deck/final holdout はコピーも再実行もしない。inherited stage evidence を使う。
- Kaggle submission を呼ぶ経路は追加しない。

## P5 反証

最強の反論は、source patch が学習意味論を変えていて optimizer を継続すべきでない点である。
本件の v15 BR は既に最終 step なので、新しい learner update を加えず validation だけを
実行する。将来、未完了 checkpoint を継続する機能は、training identity の非性能項目が
一致する追加検証を通すまで拒否する。

