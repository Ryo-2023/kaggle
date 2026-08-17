# V4 untouched shadow-C freeze evidence

日時: 2026-08-12 (Asia/Tokyo)

## 結論

次候補の学習・候補選択後だけに評価する、medal-zone deck identity の shadow-C を凍結した。候補は fixed-six、shadow-A、shadow-B と deck SHA-256 および policy SHA-256 が重複せず、freeze 時点の `runs/meta-specialist-v4*` JSON/Markdown に候補 ID が存在しない。

ただし、medal opponent は `agents.generic_agent` を同一の `main.py` で共有して生成されているため、6 件の policy content hash はすべて同じである。この cohort は「独立した6 policy の外部テスト」ではなく、「同一 local-eval policy を異なる公開 medal deck identity へ materialize した deck-OOD 診断」として扱う。これを独立再現数や policy-family 汎化の証拠として数えてはならない。

勝率評価、CABT smoke の再実行、fault/速度確認は行っていない。shadow-C は今後の候補選択・development 評価に使わず、最後の外部診断まで untouched のまま保持する。

## 凍結 artifact

| artifact | SHA-256 |
|---|---|
| `runs/meta-specialist-v4-shadow-pool-20260812-c/shadow_pool_manifest.json` | `52acf95a05b5b4d592fb6a2f9788051a1caedf3c0003c322cf55b09af5d84014` |
| `scripts/freeze_v4_shadow_pool_c.py` | `2dc4e64bc2432db30c6d8fa24df06998604ee15a92b0702ad0316fa272cf0bb3` |
| `tests/meta_specialist/test_freeze_v4_shadow_pool_c.py` | `ce9824b77c117a65a9c04331c9c9e35fbc69b1e21211b239b5b6c007e711c294` |
| `opponents/pool_manifest.json` | `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca` |
| shadow-A manifest | `6ddaf3588bb22869a808fd75f84721b640dde6d75f665a11beb10f578af72107` |
| shadow-B manifest | `27e43feecad8d66bc80c2f43c23e9276d42398bf3f47eeba2bc5914087c168e0` |

`pool_manifest.json` は既存の dirty worktree にあり、commit SHA を根拠にしていない。freeze 時点の入力 content SHA-256 と各 source file hash を manifest に保存した。

## 対象候補

上位 medal 圏から archetype の重複をできるだけ避けて 6 deck identity を選んだ。medal の `source=public` は decklist の公開 replay 由来を意味するだけで、元 leaderboard team の agent や戦略を再現したものではない。

| ID | 観測 rank | 主な archetype | canonical deck SHA-256 | policy SHA-256 |
|---|---:|---|---|---|
| `medal_0001_77a53ffc` | 1 | Mega Lucario ex / Hariyama | `77a53ffc32f89b22562f6b4ac0b8cbde9e8210923cd0ef512551b8a8eb9003f8` | `6336b4d54e63c5da780860b95565e1b6b99b68926b5610995fc8b83ca62f7f10` |
| `medal_0004_01501d64` | 4, 12, 574 | Mega Lopunny ex / Dudunsparce | `01501d644249c08144b169d9af115042260d89f0990e04b81c5aeadfcb7d7b84` | 同上 |
| `medal_0006_07bedfff` | 6 | Dragapult ex / Fezandipiti ex | `07bedfffbfad6ecb31733acc54c8110bb1934d8b1dc98bd9c4d37f6ba5c5e725` | 同上 |
| `medal_0010_4bf59ca5` | 10 | Mega Kangaskhan ex / Cornerstone Mask Ogerpon ex | `4bf59ca589c2d685d74e3535424c2dbe3c11389dffba59eddba4567be7e437df` | 同上 |
| `medal_0015_5e60b8c7` | 15 | Teal Mask Ogerpon ex | `5e60b8c7eafc87e69cb063ae0ed7f7351fbf415b4c021567790b977bece49fbf` | 同上 |
| `medal_0016_706fa912` | 16 | Thwackey / Dipplin | `706fa9122e5b9ca9aab2f6d40984e0822845fc6f44032c99653a3c103338dd35` | 同上 |

全 6 件について、`SOURCE.md`、`deck.csv`、`main.py` が存在し、`deck.csv` の sorted-card canonical hash と pool manifest の hash が一致し、`main.py` の content hash と pool manifest の policy hash が一致することを確認した。

## 重複確認

凍結スクリプトは fixed-six、shadow-A、shadow-B の各 candidate から canonical deck/policy hash を抽出し、shadow-C 各候補と比較する。

| 検査 | 結果 |
|---|---|
| shadow-C 内の canonical deck hash 一意性 | PASS (6/6) |
| fixed-six との deck hash 重複 | なし |
| shadow-A との deck hash 重複 | なし |
| shadow-B との deck hash 重複 | なし |
| fixed-six / shadow-A / shadow-B との policy hash 重複 | なし |
| shadow-C 内の policy hash 一意性 | **FAIL by design: 1 hash / 6 IDs** |
| freeze 時点の V4 artifact への candidate ID 参照 | なし |

policy hash の内部重複は次の 1 group である。

```text
6336b4d54e63c5da780860b95565e1b6b99b68926b5610995fc8b83ca62f7f10
  medal_0001_77a53ffc
  medal_0004_01501d64
  medal_0006_07bedfff
  medal_0010_4bf59ca5
  medal_0015_5e60b8c7
  medal_0016_706fa912
```

この重複は accidental な同一 ID ではなく、`scripts/make_medal_opponents.py` が全 medal deck に同じ generic local-eval agent を生成する設計による。したがって、次候補の shadow-C の評価結果を `6 policy × N games` として解釈せず、deck identity strata として層化する。

## 実行した検証

```bash
PYTHONDONTWRITEBYTECODE=1 TMPDIR=.tmp-test PYTHONPATH=.:src \
  .venv/bin/pytest -q -s tests/meta_specialist/test_freeze_v4_shadow_pool_c.py
# 2 passed in 0.26s

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:src \
  .venv/bin/python scripts/freeze_v4_shadow_pool_c.py \
  --output runs/meta-specialist-v4-shadow-pool-20260812-c/shadow_pool_manifest.json
# manifest JSON を出力し、上記 artifact を生成
```

今回の freeze では、以下を意図的に実行していない。

- CABT 勝率、seat 別成績、fault 0、速度、時間制限の確認
- Wave6、frozen residual、ensemble、Rule v0 との比較
- shadow-C の候補選択に基づく学習・threshold・arm の変更
- Kaggle 送信

## 次の利用規則

1. まず Wave6 recurrence/noise の必要な診断と、次候補（frozen residual / logit ensemble / value-AWR）の development 評価を完了する。
2. shadow-C の ID、deck、policy content、manifest は候補選択の入力へ戻さない。
3. 外部評価時は Rule v0、Wave6、採用候補を同一 protocol で測り、shadow-C の policy hash 内部共有を明記する。
4. shadow-C の結果だけで Champion 変更、longrun、Kaggle 提出を行わない。

