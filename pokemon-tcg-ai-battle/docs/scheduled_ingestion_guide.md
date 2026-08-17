# Scheduled opponent ingestion

`bash scripts/opponent_ingest/06_run_incremental_ingestion.sh` は候補 Registry と外部 artifact のみを更新します。Git の add/commit/checkout/pull、active Population への昇格、Champion/default/submission の変更、長時間 CABT は実行しません。

Windows では、明示的に `powershell -ExecutionPolicy Bypass -File scripts/opponent_ingest/install_windows_task.ps1 -Action install -IntervalHours 12` を実行して登録します。WSL systemd では unit/timer を確認してから利用者が enable してください。cron 例は `0 */12 * * * cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle && bash scripts/opponent_ingest/06_run_incremental_ingestion.sh` です。

手動投入は artifact root の `incoming/{decks,agents,submissions,notebooks,metadata}/` に置きます。外部 Python は静的監査で quarantine され、明示的な承認・隔離 smoke なしには実行されません。
