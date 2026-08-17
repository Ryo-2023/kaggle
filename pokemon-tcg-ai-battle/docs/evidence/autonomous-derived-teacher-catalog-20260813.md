# Autonomous Derived Teacher Catalog v1（2026-08-13）

## 結論

6 件の明示的な派生資格を、評価 split と独立した fail-closed catalog として固定した。これは teacher のコピー済み code/deck の `local_eval_only` を拡張せず、派生重みを `training-local` に使えるという限定的な判断だけを表現する。catalog 自体には training/promotion/submission authority を付与していない。

## 一次 artifact

| artifact | SHA-256 |
| --- | --- |
| `runs/final-sprint-autonomous/derived-teacher-catalog-v1/catalog.json` | `d5216ebc83c8bbcdc3129f647201868a45a96f0ccbcd946c37a200a8a074d263` |
| catalog 内 `catalog_sha256` | `cbd485635efee7b24344d8210cec132b3101ab91cbbeae84d9578f31477396f0` |
| 派生資格判断 `docs/decisions/2026-08-05-archaludon-teacher-derivation.md` | `e64cc3f3e74bf5b96932438b4718af3079f56d1c7da64bc27524d02432e3a6fc` |

## 固定した teacher 集合

| teacher | kind | archetype | collection | 派生重み | code/deck submission |
| --- | --- | --- | --- | --- | --- |
| `tomatomato_archaludon` | pooled external | archaludon | `READY` (96局 / 5,146例) | `training-local` | 禁止 |
| `lucifer19_battlecore` | pooled external | archaludon | `READY` (96局 / 5,102例) | `training-local` | 禁止 |
| `plamen06_steel` | pooled external | archaludon | `READY` (96局 / 5,420例) | `training-local` | 禁止 |
| `ozawa_grimmsnarl_v2` | team internal | grimmsnarl_froslass_munkidori | `READY` (96局 / 7,808例) | `training-local` | 禁止 |
| `ozawa_rocket_v2` | team internal | rocket_mewtwo_spidops | `READY` (96局 / 6,048例) | `training-local` | 禁止 |
| `nihei_alakazam` | team internal | alakazam | `READY` (96局 / 8,091例) | `training-local` | 禁止 |

全6件は96/96・fault 0・48/48 seat・unlabelled/omission 0 の `READY` collection として固定した。catalog は dataset manifest の path/file SHA、teacher policy SHA、permission manifest ID に加え、snapshot index の path/file SHA、example総数、`train/development/test` split count を格納する。読み込み時には source policy/deck の実ファイル SHA、manifest の `local_eval_only`、`training-local`、decision ref、subject deck、snapshot source policyと各countまで再照合する。

## 安全境界

- catalog に `META_TRAIN` / `META_DEV` / `META_FINAL` などの評価 split は含めない。
- `teacher_code_submission_allowed=false`、`deck_submission_allowed=false`、`promotion_authority=false`、`submission_authority=false` を全 teacher へ固定する。
- catalog 全体を canonical JSON の自己 SHA へ bind し、未知 key・重複 JSON key・非 canonical JSON・source/deck/dataset/decision SHA の不整合を fail-closed とする。
- これは学習実行、CABT、package build、promotion、submission を起動しない。

## 再現・検証

```bash
pytest -q -s tests/meta_specialist/test_derived_teacher_catalog_v1.py
python scripts/build_derived_teacher_catalog_v1.py --replace
PYTHONPATH=src python - <<'PY'
from pathlib import Path
from mage_ptcg.meta_specialist.derived_teacher_catalog_v1 import verify_derived_teacher_catalog_v1
print(verify_derived_teacher_catalog_v1(
    Path("runs/final-sprint-autonomous/derived-teacher-catalog-v1/catalog.json"), Path(".")
)["catalog_sha256"])
PY
```

実測結果: focused test は `4 passed`、CLI build と独立 verifier は PASS。policy SHA またはsnapshot index SHAを偽値へ差し替え自己 SHA を再計算した fixture はそれぞれ拒否し、`evaluation_split=META_DEV` を同様に注入した fixture も closed schema で拒否した。

## 実装

- `src/mage_ptcg/meta_specialist/derived_teacher_catalog_v1.py`
- `scripts/build_derived_teacher_catalog_v1.py`
- `tests/meta_specialist/test_derived_teacher_catalog_v1.py`
