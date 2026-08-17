# O3 Continuous Competition Learning v1 運用

O3 は read-only acquisition、Snapshot、O2/C4 学習、評価を接続する運用導線である。Champion は常に Rule Agent v0 とし、この導線は Kaggle 提出も Champion 自動昇格も実行しない。

## 定期実行

`PYTHONPATH=src python -m mage_ptcg.competition_intelligence.run_live_acquisition --config configs/competition/external_acquisition_o3_v1.yaml --rules-attestation configs/competition/rules_attestation_o3_v1.yaml --run-root /home/bfe-lab-ono/kaggle-data/pokemon-tcg-ai-battle/o3/live` を毎時の own submissions、own episodes、own Replay 差分取得に使う。team identity は `O3_TEAM_ID` または `O3_TEAM_NAME` で与え、設定やログへ token、username、team secret を書かない。

Leaderboard は日次で同じ run root へ蓄積する。snapshot は content-addressed archive と run manifest に保存され、過去時点の比較は archive から生成する。規約 attestation が `UNVERIFIED_RULES_CONSTRAINT` の間、`PUBLIC_OTHER` は archive-only で、定期取得、分析、学習、Opponent Profile、active Deck Pool への登録を行わない。

## Team Bundle

Team Bundle は既存の `team-bundle-v1` を使う。`permission_statement` が無い bundle は archive-only となる。absolute path、`..` traversal、symlink escape、hash mismatch は quarantine されるため、bundle を展開済みのローカル運用ディレクトリから既存 importer に渡す。private binding は public artifact へコピーしない。

## 継続制御

`PYTHONPATH=src python -m mage_ptcg.continuous_learning.run --config configs/competition/continuous_learning_o3_v1.yaml --run-root /home/bfe-lab-ono/kaggle-data/pokemon-tcg-ai-battle/runs/o3` は phase manifest を作り、resume 時に config hash が異なれば停止する。fixture を渡した run は `BLOCKED_FIXTURE_CONTAMINATION` となり、actual-only dataset へ進まない。actual Intelligence Snapshot が無い run も `BLOCKED_MISSING_ACTUAL_SNAPSHOT` として停止する。

評価結果は `engine_seed_supported=false`、`pairing_mode=seat_matched_unseeded`、`exact_paired_inference=false` を必ず記録する。100 logical pair 未満は Promotion `INSUFFICIENT_EVIDENCE` であり、いかなる結果も Champion を変更しない。
