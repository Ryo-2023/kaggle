# V4 strict-disagreement pilot CUDA OOM 診断（2026-08-12）

## 結論

GPUアクセス境界の問題を解消した後に起動した2-seed strict-disagreement pilotは、入力shardの検証・展開を完了したが、学習開始時のモデル転送で `CUDA error: out of memory` により停止した。今回のOOMは、V4モデルがRTX PRO 5000 Blackwell（48,935 MiB）単独で必要とする規模を超えたことを示すものではない。停止時点で同じWSL2 GPUを使用していた別ワークスペースのCUDAプロセスがあり、`nvidia-smi` も同時に `NVML: N/A` となっていたため、GPU共有状態／ドライバコンテキスト競合を第一原因候補とする。

別プロセスを他作業の承認なしに停止していない。GPUが空いた後に、`nvidia-smi`、PyTorch CUDA smoke、V4 runnerの最小モデル転送を順に再確認してからpilotを再実行する。

## 実行と結果

実行対象は、seedごとのscreen/checkpoint provenanceを固定した3 epochのbounded pilotである。提出、Champion変更、longrunは行っていない。

```text
output: runs/meta-specialist-v4-archaludon-dagger-wave5-strict-disagreement-pilot
lane: archaludon
seeds: 0,1
init: Wave6 seed0 / seed1 対応checkpoint
selection: recurrent-selection/archaludon.json
dagger_fraction: 1/3
strict action types: 9,13,14
strict mean behavior log probability: <= -0.2
epochs: 3
device: cuda:0
```

- 起動時の通常sandboxではなく、`/dev/dxg` が見える承認済みsandbox外境界を使用した。
- 15分以上かけてsealed selection shardを読み込み、12 shardをprivate spoolへコピー・hash検証した。
- screen/checkpoint binding、selection SHA、strict selectionのCPU preflightは通過した。
- 学習開始時に `scripts/run_meta_specialist_v4_dagger_bc.py:1359` の `.to(device)` で停止した。
- tracebackは `torch.AcceleratorError: CUDA error: out of memory`。`bc.json` やcandidate checkpointは生成されていない。

## 同時に観測したGPU状態

pilot終了直前は外部 `nvidia-smi` が複数回 `Failed to initialize NVML: N/A` を返し、pilot終了後のPyTorch診断も次の状態だった。

```text
torch 2.11.0+cu128
torch.cuda.is_available() = False
torch.cuda.device_count() = 0
cudaGetDeviceCount(): Unexpected error ... invalid argument
```

同時に、別ワークスペースで次のCUDAプロセスが稼働していた。

```text
PID 3474717
cwd /home/bfe-lab-ono/av-suara
python common/separation/BSS_AI/experiments/avsuara_gc_fastmnmf/enhancer/run_gc_ena_spectral_policy.py \\
  --dataset-root ROS2/output/gc_ena_online_60s_v1 --device cuda \\
  --grid-levels 20 --steps 2000 --frame-batch 256 --hidden 128 --depth 3 \\
  --progress-every 100 --output ROS2/output/gc_ena_spectral_policy_60s_gpu_v1
```

このプロセスは確認時点で約7分以上継続し、`/dev/dxg` を保持していた。別作業の所有物であるため停止していない。単独のGPU smokeでは同じ環境で行列積とV4 `cuda:0`解決が成功しているため、今回の新しい失敗は「GPUが見えない」問題ではなく、「GPU共有中にCUDAコンテキストが正常に初期化できず、OOM/NVML異常として表面化した」可能性が高い。ただし、別プロセスのVRAM使用量と終了時刻はNVML異常のため未測定である。

別プロセス終了後もNVMLとPyTorch CUDAは自動回復せず、kernel logには次が記録された。

```text
misc dxg: dxgk: dxgkio_query_adapter_info: Ioctl failed: -22
misc dxg: dxgk: dxgvmb_send_create_allocation: send_create_allocation failed ffffffb5
misc dxg: dxgk: dxgkio_create_allocation: Ioctl failed: -75
```

したがって、単に別プロセスの終了を待つだけでは十分でなく、WSL2 GPU bridgeの再初期化が必要な状態である。`wsl.exe --shutdown` は有力な回復手段だが、現在のWSL内のCodex・他プロジェクト・他エージェントをすべて終了させるため、ユーザー承認なしには実行しない。

## 再開条件

1. 別CUDAプロセスの終了を読み取り確認する（停止は所有者の承認後のみ）。
2. ユーザー承認後、WSL2 GPU bridgeを再初期化する（通常は`wsl.exe --shutdown`。全WSLプロセス終了を伴う）。
3. `nvidia-smi -L`、`torch.cuda.is_available()`、`torch.cuda.mem_get_info()`、2048行列積を再実行する。
4. V4モデル単独の `.to(cuda:0)` と小batch forwardを確認する。
5. 同じ固定budget pilotを同じoutputとは別の再試行ディレクトリで起動し、OOMならモデル容量・allocator設定を切り分ける。
6. pilotが完走した場合のみ、matched controlと評価へ進む。

## 制約

- このartifactは失敗診断であり、性能結果ではない。
- OOMを理由にモデル縮小、batch変更、epoch変更を先に行わない。まずGPU共有状態を排除して同一条件を再現する。
- `nvidia-smi` が正常化しないまま再実行しない。

## 追加診断 — WSL再起動後もGPU lost（2026-08-12）

ユーザー承認後に `wsl.exe --shutdown` を実行してWSLを再起動したが、再起動後も `dxgvmb_send_open_adapter failed: -22` が継続し、PyTorchは `Found no NVIDIA driver on your system` を返した。Windows側の `C:\Windows\System32\nvidia-smi.exe -L` は次を返した。

```text
Unable to determine the device handle for gpu 0000:01:00.0: GPU is lost. Reboot the system to recover this GPU
```

これはWSL再起動で解消する範囲を超えたWindowsホスト側のGPU lost状態であり、ホスト再起動が必要である。ホスト再起動は全Windowsアプリ・WSL・Codexを終了するため、ユーザーの明示承認なしに実行しない。

対象GPUのInstanceIdに対するWindows `pnputil /restart-device` も試したが、`Access is denied` で実行されず、PnP状態は変更されなかった。Windows側のPnP表示は`Status: OK`でも、NVIDIA runtimeは`GPU is lost`のままである。

## 追加診断 — Windowsホスト再起動は未成立（2026-08-12）

ユーザー申告ではWindows再起動済みだったが、Windows側の現在の実測は次のとおりである。

```text
Win32_OperatingSystem.LastBootUpTime: 2026-08-10 13:48:41.500 +09:00
System Event LogのKernel-General起動: 2026-08-10 13:48:42
System Event LogのEventLog開始: 2026-08-10 13:48:59
Windows nvidia-smi.exe -L: GPU is lost. Reboot the system to recover this GPU
```

したがって、今回のGPU lost以後にWindowsホストの実際の再起動は成立していない。WSL再起動（WSL側のPID/uptimeが更新される）とWindows再起動（`LastBootUpTime`とKernel-General起動イベントが更新される）を混同している可能性がある。現時点の最も直接的な復旧条件は、Windowsの「再起動」を実際に完了させ、再起動後に`LastBootUpTime`が現在時刻へ更新されることを確認することである。
## 追加診断 — 強制ホスト再起動後のGPU復旧（2026-08-12）

保留していた再起動要求が完了していなかったため、ユーザーの再起動承認を受けてWindowsを`shutdown.exe /r /f /t 0`で再起動した。再起動後、`LastBootUpTime=2026-08-12 03:01:36.500 +09:00`、EventLog 6006/6005（03:00:59/03:01:52）、Windows/WSL双方の`nvidia-smi`（RTX PRO 5000 Blackwell、driver 595.95、48,935 MiB）を確認した。

PyTorch `2.11.0+cu128`でもCUDA availability、compute capability `(12, 0)`、2048x2048行列積と同期が成功した。V4 `SpecialistModelV4(card_vocabulary_size=1267, hidden_dim=128, embedding_dim=64)`（857,474 parameters）の`.to(cuda:0)`とGPU上テンソル演算も成功した。今回のGPU lostは未完了ホスト再起動が根因で、実再起動後に復旧したと判定する。残留`av-suara`/旧pilotプロセスはなく、GPU空きは約46 GiBである。

同一条件のstrict-disagreement pilotは旧OOM出力を再利用せず、`runs/meta-specialist-v4-archaludon-dagger-wave5-strict-disagreement-pilot-rerun-20260812`へ再実行中であり、完走結果は別途追記する。
