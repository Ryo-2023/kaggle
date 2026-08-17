# Autonomous Meta-Fine-Tuning Phase 4: deck mutation generator

作成日: 2026-08-13 (JST)

## 結論

既存の `joint_optimization_v1` が定義する `CoreSignatureV1` と
`deck_multiset_identity_v1` を再利用した、研究専用の deck mutation candidate
generator を追加した。候補生成は 1 / 2 / 3 / 4 枚の物理 swap を扱い、60 枚・正の
card ID・core signature・任意の外部 legality checker を全候補で検証する。親デッキと
同一 multiset は捨て、候補には exact multiset SHA を記録する。

生成物は **candidate-only** であり、次の権限は常に `false` である。

| 権限 | 値 |
|---|---:|
| promotion | `false` |
| training | `false` |
| submission | `false` |

`DeckMutationAuthorityV1` は `true` を受け取ると fail-closed で拒否するため、候補を
そのまま promotion / training / submission の入力へ昇格させない。

## 実装契約

- `src/mage_ptcg/meta_specialist/deck_mutation_v1.py`
  - `generate_deck_mutation_candidates_v1(...)`
  - `DeckMutationCandidateV1`
  - `DeckMutationAuthorityV1`
  - `DeckMutationV1Error`
- core signature で指定された最小コピー数を lock し、超過分だけを flex card として
  swap 対象にする。
- `swap_counts` は重複を除いて昇順化し、1〜4 の整数以外を拒否する。
- `candidates_per_swap` と `seed` による deterministic 生成を行う。
- `known_card_ids` を与えた場合は base / replacement の未知カードを拒否する。
- `legality_checker` は既存 CABT adapter 互換の `bool` または `(bool, reason)` を受け、
  拒否された候補を黙って捨てず fail-closed する。
- deck identity は既存 `deck_multiset_identity_v1` と同じ multiset SHA-256 契約で、
  CSV 上の順序に依存しない。
- 既存の `main.py`、`deck.csv`、production agent、evaluator、DeckLock を変更していない。

## TDD / 検証

最初に新規テストを追加し、module が存在しない RED（collection error）を確認した後、
最小実装を追加して GREEN 化した。

実行コマンド:

```bash
PYTHONPATH=src pytest -q -s tests/meta_specialist/test_deck_mutation_v1.py
PYTHONPATH=src pytest -q -s \
  tests/meta_specialist/test_deck_mutation_v1.py \
  tests/meta_specialist/test_joint_optimization_v1.py
```

結果:

```text
6 passed in 0.02s
22 passed in 0.03s
```

追加テストが確認する項目:

1. 1 / 2 / 3 / 4 swap の全区分と deterministic 候補列。
2. 親 multiset、候補 multiset、候補順序不変性、重複排除。
3. 60 枚、正の card ID、core signature、外部 legality checker。
4. empty replacement pool、mutable card 不在、引数範囲エラー。
5. promotion / training / submission authority が false のままであること。

## Artifact SHA

実装とテストの検証時 SHA-256:

```text
01c0a74f6b122904d417958ef3413c3ab835b840387f7b164fe3364ff70becf6  src/mage_ptcg/meta_specialist/deck_mutation_v1.py
50b51f2b75e0dc4a71e49efefc6dd74cf97c2198d402a125551a5e7742cb748e  tests/meta_specialist/test_deck_mutation_v1.py
```

## 未実施と次の実行

この段階では CABT、学習、長時間 runner、提出を起動していない。次の段階では、
Phase 1 の固定 meta manifest が指定する `TrainingEligibleBestKnown` の deck を親にし、
archetype ごとの合法な card pool と実際の CABT legality callback を渡して候補 manifest を
生成する。候補は native pair と同一条件で 96 → 384 → 768 → 1536 局の順に screen し、
native baseline を各 block に含める。

例:

```python
from mage_ptcg.meta_specialist.deck_mutation_v1 import (
    generate_deck_mutation_candidates_v1,
)

candidates = generate_deck_mutation_candidates_v1(
    base_cards=best_known_cards,
    signature=archetype_signature,
    replacement_pool=legal_flex_pool,
    swap_counts=(1, 2, 3, 4),
    candidates_per_swap=8,
    seed=20260813,
    known_card_ids=card_vocabulary,
    legality_checker=cabt_legality_checker,
)
```

この generator 自体は BestKnown を更新せず、評価・promotion・submission の判断は親の
longrun gate と別の監査工程に残す。
