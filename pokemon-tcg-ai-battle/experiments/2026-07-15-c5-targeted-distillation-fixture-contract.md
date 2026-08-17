# C5 targeted distillation fixture contract

## メタデータ

| 項目 | 値 |
|---|---|
| 日時 | 2026-07-15 JST |
| 担当 | Codex |
| 種別 | local contract test |
| commit | `4ee1e69ac710c982a68b03356431827b3e0e9861` |
| branch | `feature/targeted-distillation-v0` |
| model provenance | GPT-5.6 Codex、high |
| simulator / data | synthetic fixture、actual cabt未使用 |

## 目的と反証条件

- **問い**: C5がprivacy、action identity、group split、synthetic/actual分離を崩さずoffline E2E artifactを出せるか。
- **仮説**: C4 Rule BC fixtureからcanonical record、target selection、C4互換dataset、model provenanceを決定的に作れる。
- **反証条件**: forbidden key/private ActionKey coreの保存、label非合法、split leakage、fake actualの受理、非決定的selection、0-game promotionが一つでも成立する。
- **変更点**: C5 dataset/selector/registry/league/gate CLIを追加した。
- **固定条件**: deck 60枚fixture、36 episode fixture、actual cabt 0 game。

## 再現

```bash
python -m pytest tests/test_targeted_distillation_v0.py tests/test_student_v0.py tests/test_bounded_search_v0.py -q
python -m pytest -q
python scripts/docs/validate_docs.py
python scripts/build_submission.py --output-dir /tmp/c5-submission-a
python scripts/build_submission.py --output-dir /tmp/c5-submission-b
python scripts/build_submission.py --verify-dir /tmp/c5-submission-a
```

## 結果

| condition | seeds | games | win rate | timeout | illegal action | runtime | 備考 |
|---|---:|---:|---:|---:|---:|---:|---|
| C5 fixture contract | 固定 | 0 | 未測定 | 未測定 | 未測定 | 0.64 s | focused 41 passed |
| repository regression | 固定 | 0 | 未測定 | 未測定 | 未測定 | 33.40 s | 494 passed、warning 3件 |

- **sanity check**: actual/synthetic混合を拒否し、League CLIのunavailableはexit 3へ分離した。
- **負の所見**: actual trace/runnerがないため実cabt性能と勝率を検証していない。
- **不確実性**: fixtureのdataset分布はcabt rolloutを代表しない。

## 解釈と判断

- **観測事実**: C5 contractsとC4/C3 regressionのfocused集合はpassした。
- **解釈**: 実データを受け取る前の安全な基盤としては利用可能である。
- **判断**: infrastructure foundationはGO。runtime Champion変更は保留する。
- **言わないこと**: StudentやC3の強さ、fidelity、paired non-inferiority。
- **次 action**: 正規cabt trace adapterを別Evidenceでattestし、actual group holdoutとpaired Leagueを実施する。adapter未取得ならRule v0を維持する。
