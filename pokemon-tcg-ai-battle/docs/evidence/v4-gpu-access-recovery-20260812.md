# V4 GPU access recovery evidence（2026-08-12）

## 結論

GPU は故障していない。Codex の通常 sandbox 実行では WSL2 の GPU device node（`/dev/dxg`）が公開されていないため、NVML と PyTorch CUDA が利用できない。sandbox 外の承認済み read-only 実行では同じ環境から GPU が正常に見え、CUDA 演算も完了した。

## 再現結果

通常 sandbox 内での確認:

```text
/usr/lib/wsl/lib/nvidia-smi
Failed to initialize NVML: GPU access blocked by the operating system
torch 2.11.0+cu128
torch.version.cuda 12.8
cuda available False
device count 0
```

同じ workspace を sandbox 外で read-only 診断した結果:

```text
GPU 0: NVIDIA RTX PRO 5000 Blackwell
driver 595.95
memory.total 48935 MiB
torch 2.11.0+cu128
torch.version.cuda 12.8
available True
count 1
device 0 NVIDIA RTX PRO 5000 Blackwell (12, 0)
```

さらに sandbox 外で `torch.randn((2048, 2048), device="cuda")` と行列積を実行し、`torch.cuda.synchronize()` まで成功した。したがって、ドライバ・CUDA runtime・PyTorch wheel・GPU本体のいずれも今回の停止原因ではない。

## 根本原因

Codex の通常コマンドは bwrap sandbox 内で起動され、`/dev` が限定されている。`ls -l /dev/dxg` は `No such file or directory` であり、WSL の GPU bridge がプロセスから不可視になっている。これはリポジトリの runner や checkpoint の破損ではない。

## 今後の運用

- GPU を使う学習・評価は、`sandbox_permissions=require_escalated` の承認済み実行で行う。
- 実験開始前に `nvidia-smi -L` と PyTorch の `torch.cuda.is_available()` を同一実行境界で確認する。
- 通常 sandbox 内で `CUDA unavailable` が出ても、直ちに runner や checkpoint を変更しない。まず `/dev/dxg` の有無と sandbox 外診断を確認する。
- read-only の CUDA smoke test は完了済み。次は strict loss mask の検証を終えたうえで、固定 budget の pilot だけを sandbox 外で実行する。

## 未実施

GPUを使った新規 strict-disagreement 学習、Champion変更、Kaggle提出は本記録時点では未実施。GPU access の復旧は確認したが、研究実験の開始条件とは分離して扱う。
