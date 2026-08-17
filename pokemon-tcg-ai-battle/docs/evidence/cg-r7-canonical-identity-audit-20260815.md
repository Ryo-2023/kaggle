# R7 canonical deck identity audit — 2026-08-15

## 結論

`public_archaludon_cinderace_r7` は、既存のCABT診断では96/96局が`DONE`・fault 0で完走した。しかし、pool manifestの`canonical_deck_hash`はカード構成のcanonical identityではなく、`deck.csv`生バイトのSHA-256である。さらに、この個体は過去のdiagnostic／holdoutへ既に投入済みであり、hash表記を修正しても未使用metaにはならない。

このため、pool manifestを遡及変更せず、R7をfresh・unused meta、CEMの選択母集団、BestKnown昇格根拠へ使わない。今回の監査はidentityと再利用可否の切り分けだけを目的とし、新規CABTは起動していない。

## Identity evidence

| 項目 | 値 |
|---|---|
| source | `opponents/public_archaludon_cinderace_r7` |
| pool manifest SHA | `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca` |
| declared `canonical_deck_hash` | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` |
| `deck.csv` raw-byte SHA-256 | `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e` |
| card-composition canonical SHA-256 | `e223210a3d0e3c1ae72f83479a3b9c9d06ac9f4a4c45e41793b1a484ad0d5c8b` |
| card count / distinct IDs | `60 / 15` |
| policy hash | `c08588467c3faa2cbc748703acc8e7099c6362c32747c84cb2cec8131d6a4ca3` |
| source boundary | `public` / `local_eval_only` |
| pool `smoke_ok` | `false` |

`src/mage_ptcg/observability/cabt_trace.py::canonical_deck_sha256` は、整数カードIDをsortしてJSON化したcomposition hashを定義している。R7のpool rowはこの値と一致しないため、宣言identityはcanonical deck identityとして不整合である。

## Existing execution evidence

既存の`runs/meta-specialist-asset-ranking-r7-diagnostic-20260812/`は、R7を`include_smoke_false=true`の診断対象として96局実行している。結果は`68W-0D-28L`、fault 0、score 70.8333%だが、各ledger rowにも`subject_smoke_ok=false`が保存されている。この結果はplumbingが動いたことを示すだけで、poolのsmoke gateまたはfreshness gateを解除しない。

## Freshness decision

- R7は上記diagnosticと複数の既存evaluation artifactへ出現済みで、未使用ではない。
- raw SHAとcomposition SHAの不一致を解消するには、新しいidentity・manifest・評価履歴が必要であり、既存artifactの意味を遡及変更してはならない。
- 新しいpublic sourceまたは明示的に許可されたfresh metaが供給されるまで、現行P1＋root deckのBestKnown loopは`BLOCKED_NO_LOCAL_UNUSED_META`として研究専用に停止する。

## Reproduction

```bash
cd /home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle
python - <<'PY'
import hashlib, json
from pathlib import Path
from mage_ptcg.observability.cabt_trace import canonical_deck_sha256

p = Path("opponents/public_archaludon_cinderace_r7/deck.csv")
ids = [int(line) for line in p.read_text().splitlines() if line.strip()]
print("raw_sha256", hashlib.sha256(p.read_bytes()).hexdigest())
print("canonical_sha256", canonical_deck_sha256(ids))
print("count", len(ids), "distinct", len(set(ids)))
PY
```

No pool, Champion, production package, or submission artifact was changed by this audit.
