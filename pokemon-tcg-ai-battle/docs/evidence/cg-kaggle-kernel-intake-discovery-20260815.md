# cg BestKnown — 公開Kaggle kernel source intake discovery（2026-08-15）

## 目的

既存 `opponents/pool_manifest.json` と internal remote/history source の identity がほぼ消費済みになったため、次の `P1 → policy CEM → fresh validation` に供給できる別系統の meta source acquisition 方法を調査した。今回は公開 Kaggle kernel を研究専用の local-eval source として安全に隔離できるかを、CABTを起動せずに確認した。

## 発見した候補

| 項目 | 値 |
|---|---|
| kernel | `tetsutani/grimmsnarl-ex-damage-transfer-control` |
| URL | https://www.kaggle.com/code/tetsutani/grimmsnarl-ex-damage-transfer-control |
| 取得方法 | `kaggle kernels output ... -p <temporary-root>` |
| 保存root | `runs/cg-kaggle-kernel-intake-20260815-tetsutani-a/raw/` |
| `submission.tar.gz` SHA-256 | `04f9779b77d17417570189d06a1b7ff5b0016797639a2a45f4b53bc02e945712` |
| 取得ログ SHA-256 | `39749bc98da69b3e5700578d568d8a619b27c624b5a7a2d0e8b81baa4d4db3ee` |
| tar内元 `main.py` SHA-256 | `c61e540bcb45aa2e8184ae912e7e17efaa900dba3df4536468da41899b09dcd8` |
| tar内 `deck.csv` SHA-256 | `92b92bac9f9163ecff933b3dc39294d2cc154c8684f3c8497877661419ebc59d` |
| canonical deck SHA-256 | `cafa7652a6349be806d8ac2b9abfdb6c72ca3821f368e0d912e2d989f3b54cdd` |
| 現行poolとのexact identity | 未検出（read-only文字列照合） |

`submission.tar.gz` には root `main.py` / `deck.csv` のほか、policy payload、model binary、`EN_Card_Data.csv`、独自 `cg/` が含まれる。payloadのPythonは183ファイルで、root policyは独自 `policy_features`、`strategic_policy`、複数のexpert/guardを参照する。現行pool loaderのままでは候補ディレクトリ内のtop-level payload importを解決できないため、取り込み時は候補横の隔離 wrapper からpayloadを明示的にロードする必要がある。

## 静的・境界監査（CABT未起動）

- bundled `cg/` は shared engineとのparityを壊すため、取り込み対象から除外する。
- `ctypes` は bundled `cg/sim.py` だけで検出された。`cg/`除外後のpolicy payloadでは network import、subprocess import、dynamic import、filesystem write は検出されなかった。
- `list.remove` のようなゲーム内コンテナ操作は filesystem mutation と誤認しない。`Path.write_*`、書き込みモード `open`、`os/shutil`の削除だけをfail-closed対象にする。
- tar内の `submission.tar.gz` や notebook生成物は再配布・提出に不要なため、隔離payloadへコピーしない。
- 公開sourceの利用境界は `local_eval_only`。training、public/native teacher、submission bundle、Kaggle送信、外部再配布には使わない。

## 未実施と再開条件

まだ staged `pool_manifest.json` / `fresh_meta.json` は生成しておらず、candidate wrapperのCABT smoke、seed分離、CEM、DEV/FINAL確認も未実施である。次の実装は、ユーザーが公開kernelを研究専用sourceとして明示許可した場合だけ行う。

1. tar path、kernel ref、元hash、取得時刻を固定した intake manifest を作る。
2. safe memberだけを新規rootへ展開し、bundled `cg` と提出用archiveを除外する。
3. payload import wrapper、exact 60-card/canonical deck、AST security、shared-engine loader を検証する。
4. 新規 `pool_manifest` / `fresh_meta` / 8-2-2 splitをsealし、両seat fault0 smokeを行う。
5. smokeが通った場合だけ、P1 control固定の小規模screen→独立複数block→fresh DEV/FINALへ接続する。

判定は `SOURCE_ACQUISITION_PASS` と `PERFORMANCE_PROMOTION_PASS` を分離する。性能を測るまではBestKnown、Champion、production、submissionを変更しない。
