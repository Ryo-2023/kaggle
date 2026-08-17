# V4 長時間学習の再開修正と監視

## 結論

Archaludon V4 の既存 2 seed 学習は、重みを作り直さずに再開系譜を移行できた。各 seed は 3 epoch、1,536 optimizer updates、CUDA 学習 artifact として厳密検証済みである。held-out 96 局を各 seed で再評価し、seed 0 は 44/96 勝、seed 1 は 47/96 勝、fault 0 だった。

## 修正

`train_recurrent_bc_v4` が resume checkpoint の objective SHA を train+validation 連結順から再計算していたため、materializer が封印した physical sequence 順と一致しなかった。明示された `selected_sequence_sha256` を優先して resume identity に固定する回帰を追加した。

既存 artifact には `scripts/migrate_v4_resume_lineage.py` を一度だけ適用し、次を移行記録へ固定した。

- 旧 objective SHA → materialized sequence SHA
- 旧 trainer closure SHA → 現行 trainer closure SHA
- checkpoint の model/Adam tensor は変更せず、atomic 保存・再読込
- report の canonical training-config SHA を再計算

移行記録: `runs/meta-specialist-v4-archaludon-longrun-wave3/resume-lineage-migration-v1.json`

## 実測

| artifact | 勝敗 | fault | score rate |
|---|---:|---:|---:|
| V4 longrun seed 0 | 44-0-52 / 96 | 0 | 45.8% |
| V4 longrun seed 1 | 47-0-49 / 96 | 0 | 49.0% |
| 旧 V4 1 epoch reference | 29-0-67 / 96 | 0 | 30.2% |
| V2 baseline | 23-0-73 / 96 | 0 | 24.0% |

全比較は同じ Archaludon R7 subject deck、固定6 opponent、両seat、各8局/seatで行った。新 longrun の point estimate は改善しているが、96局規模では統計的な確定優位とはまだ言わない。

## 監視

端末で次を実行すると、TTY では単一の更新式 progress bar、非TTYでは集約スナップショットを表示する。`--once` は一回だけ JSON を出す。

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src .venv/bin/python scripts/monitor_meta_specialist_v4_longrun.py runs/meta-specialist-v4-archaludon-longrun-wave3
```

学習済みで wrapper が途中終了した場合も、training-progress と manifest の差を検出して `evaluation_pending` と表示する。現在の run は evaluation 完了済みで、最終表示は `complete`、`198/198`（学習6 epoch単位 + held-out 192局）になる。

## 未確定事項

- GPU/CUDA の実測値は学習 report に記録されているが、この実行環境から GPU 状態を独立再検証できない。
- 次の採用判断には、Alakazam も同じ長時間予算で実行し、両lane・seed・seat/opponent breakdownを比較する。
- STOP/END など action-type 別の弱点は残るため、勝率改善だけで本番採用を確定しない。
