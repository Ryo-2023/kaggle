# Rocket Dispatch Classifier Meta v1 Design

## 目的

受理済みRocket sourceのnumeric thetaとspecialist theta対応を再試行するのではなく、公開観測からfamily commitを決める `_TIER_A_TO_GROUP` classifierの限定的な変種を生成する。生成物はCABTの相手meta sourceとしてのみ使い、現行P1／root deck／BestKnown／Champion／提出物を変更しない。

## 入力と不変境界

- 入力sourceは `runs/cg-fresh-internal-meta-intake-20260815-f/internal_ozawa-rocket-rule_de797c3646e9/` にsealedされたsource commit `de797c3646e935157618be3edea17615430ccfec` とする。
- 変更対象は `main.py` 内の `_TIER_A_TO_GROUP` 辞書の既存整数キーに対応するfamily文字列だけとする。
- 許可キーは `{675, 676, 677, 678, 646, 647, 648, 741, 742, 743, 721, 722, 723}`、許可familyは `{A01, A09, A07, A11}` とする。
- deck、deck hash、観測抽出、stateful commit、theta数値、import、環境変数、fallback、runtime契約は変更しない。
- 相手のactive／bench／discard／公開logから得られる既存のcard IDだけを使う。非公開情報を追加取得しない。

## 生成recipe

各recipeは既存辞書の指定キーのvalueだけを置換し、全キー集合と辞書外のsource bytesを保持する。

| recipe | 変更キー |
|---|---|
| `A01_ENGINE_TO_A09` | 675, 676 → A09 |
| `A01_LUCARIO_TO_A09` | 677, 678 → A09 |
| `A01_ENGINE_TO_A11` | 675, 676 → A11 |
| `A01_LUCARIO_TO_A11` | 677, 678 → A11 |
| `A09_LINE_TO_A01` | 646, 647, 648 → A01 |
| `A09_LINE_TO_A07` | 646, 647, 648 → A07 |
| `A07_LINE_TO_A09` | 741, 742, 743 → A09 |
| `A07_LINE_TO_A11` | 741, 742, 743 → A11 |
| `A11_LINE_TO_A07` | 721, 722, 723 → A07 |
| `A11_LINE_TO_A01` | 721, 722, 723 → A01 |
| `A01_MIX_ENGINE_LUCARIO` | 675, 677 → A09、676, 678はA01を維持 |
| `A09_SPLIT_TO_A07` | 646, 648 → A07、647はA09を維持 |

12件をTRAIN 8件、DEV 2件、FINAL 2件へ固定し、smokeとCEMにはTRAINだけを渡す。DEV／FINALは昇格判断の前に使用しない。

## 安全性とfreshness

generatorはASTで対象辞書を一意に検出し、キー集合・value集合・recipe置換数を検証してfail-closedする。生成policyはcompile、static scan、exact 60-card、pool loaderを通過し、current poolのpolicy hashと重複しないことを要求する。全artifactは `local_eval_only`、`authority=false`、`research_only=true` とし、提出packageへの混入を拒否する。

各referenceにはsource commit、base policy SHA、derived policy SHA、canonical deck SHA、recipe、freshness evidenceを記録する。pool／fresh meta／historical split／intake reportをno-clobberでsealし、hashをevidenceへ固定する。

## 性能ゲート

1. TRAIN 8件だけの両seat smokeが全局DONE、fault 0、illegal 0、draw 0。
2. P1 control固定CEMをscreen→独立再評価で実行する。
3. candidateは独立re-evaluationのlower-tailが正、seat gapが5%以下、opponent×seat gapが5%以下の場合だけ候補扱いにする。
4. ゲート未達ならP1 centerを保持し、DEV／FINAL／generation 1／BestKnown loop接続を起動しない。
5. ゲート通過時だけ未使用DEV、続いて未使用FINALで確認し、`cg_bestknown_loop_v1.py`へ渡す。

## 検証

- unit testで辞書以外の差分拒否、recipe再現性、unknown／malformed／no-op拒否を確認する。
- seal後に全12 policyをcompileし、pool loader、split identity、focused test、docs validation、`git diff --check`を実行する。
- CABTログとsummaryはartifact pathとSHAだけを報告し、生ログを文書へ複製しない。
