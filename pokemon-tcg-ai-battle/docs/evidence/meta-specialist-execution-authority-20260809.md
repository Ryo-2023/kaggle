# Meta Specialist execution authority（2026-08-09）

## 結論

`pokemon-tcg-battle-worktree` 統合後の canonical execution tree は、次へ固定する。

```text
/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
```

旧 `/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle-worktrees/meta-specialist-canonical`
は確認時点で存在せず、`git worktree list --porcelain` にも現れない。今後の
preflight、Gate、teacher-quality、theta0、learner pilot はすべて上記treeを起点にし、
旧worktreeの相対pathやstale resultを実験authorityとして使わない。

## 2026-08-09T18:32:12+09:00 の確認値

- branch: `feature/belief-guided-search`
- HEAD: `30cade0e5d349d6ea545f019fc411e9d53288f16`
- tracked dirty diff (`git diff --binary`) SHA-256:
  `ce97892044509f3938aff6a540603e1672c4fe23a9dcc2019d5a95581b1b5da9`
- 全untracked path list SHA-256:
  `1f77a17257038bc63d195e0012d8c975ef1d9aedd5aa4b40d2b6a7b3351409fa`
- `src/tests/scripts/configs` untracked path list SHA-256:
  `8daed183a4b75badd69a670a74f9b083ef2e3f752c61d8e4662712e0f182745b`
- dirty/untracked status rows: `147`

これはcurrent preflight開始時のtree識別であり、actual recurrent Gateのsource sealではない。
teacher-quality、theta0、lane-specific Gate修正が進行中なので、actual CUDA Gate開始直前に
同じ項目と対象source bytesのtree SHAを再取得し、Gate command/resultへ外部anchorとして固定する。

## recurrent input authority

- Alakazam manifest file SHA-256:
  `8093116b9071847cc17ed0f742bf6000697646386dbcc410d924e145d021bc7e`
- Archaludon manifest file SHA-256:
  `b3044504df1192ce072377f1ddfbeeafdf071a715ef896076b5adb1471eaf0cc`

## static artifact boundary

static Gateの入口は `gate1-selection-v3-cpu.json` または
`gate1-selection-v3-cuda-0.json` の外部anchor付きmanifestに限定する。
旧 `gate1-result-v3.json` はstale artifactとして使用しない。

## 再固定条件

次のいずれかが変化した場合、actual Gate前にauthorityを再生成する。

- HEAD、tracked diff、untracked source/test/script/config bytes
- recurrent selection manifest/index
- teacher-quality overlay
- model vocabulary/config
- Gate runner、selection rule、threshold
- CUDA runtime/device
