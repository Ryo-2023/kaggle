param(
  [ValidateSet('install','status','uninstall')] [string]$Action = 'status',
  [ValidateSet(12,24)] [int]$IntervalHours = 12,
  [string]$Distro = 'Ubuntu-24.04',
  [string]$Repository = '/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle',
  [string]$ArtifactRoot = '/home/bfe-lab-ono/kaggle/handoff-artifacts/family-opponent-population-expansion-v1'
)
$TaskName = 'PokemonOpponentIncrementalIngestion'
$Command = "wsl.exe -d $Distro -- bash -lc 'cd $Repository && . .venv/bin/activate && bash scripts/opponent_ingest/06_run_incremental_ingestion.sh $ArtifactRoot'"
if ($Action -eq 'status') { Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue; exit 0 }
if ($Action -eq 'uninstall') { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue; exit 0 }
$taskAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -Command $Command"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1)
$trigger.Repetition.Interval = "PT${IntervalHours}H"
$trigger.Repetition.Duration = 'P3650D'
Register-ScheduledTask -TaskName $TaskName -Action $taskAction -Trigger $trigger -Description 'Candidate-only Pokemon opponent ingestion; no promotion or git mutation.' -Force | Out-Null
