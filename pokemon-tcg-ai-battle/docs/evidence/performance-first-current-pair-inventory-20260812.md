---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-12
scope: phase1-current-pair-inventory
---

# Performance-First Phase 1 — current pair inventory

## 結論

現在の研究成果と実際の提出面は一致していない。リポジトリの public entrypoint は
`main.py` の `_DEFAULT_AGENT = make_rule_agent()` であり、現在の worktree の
`deck.csv` と組み合わせた実際の runtime pair は **Rule Agent v0 + root deck** である。
Wave6 は Archaludon subject deck 用の研究 checkpoint であり、root deck を使う提出 bundle
も、Wave6 を含む提出 bundle も現在は存在しない。

従って、次のように分けて扱う。

| pair | 現在の扱い | 評価開始判断 |
|---|---|---|
| Rule v0 + 現在の root `deck.csv` | 実際の submission runtime pair | **評価可能だが、同じ pair の再現可能な CABT 結果が未登録**。まずこれを基準として測る |
| Rule v0 + Archaludon subject deck | 研究用 cross-deck 診断 | raw artifact は 8/96 のみ。提出性能の根拠にしない |
| Wave6 seed0/seed1 + Archaludon subject deck | coherent research pair | 既存 checkpoint と raw heldout があり、fixed-six development 評価は開始可能 |
| `neural-student-v1` package + package 内旧 deck | stale fixture package | `NEURAL_FIXTURE_SMOKE`、Rule v0 fallback。Wave6 候補・現在の root package ではない |

Kaggle への送信、Champion の変更、既存 dirty file の削除・巻き戻しは行っていない。

## 1. 監査時点の repository identity

- branch: `feature/belief-guided-search`
- HEAD: `30cade0e5d349d6ea545f019fc411e9d53288f16`
- worktree は dirty。今回の監査では既存差分を変更していない。
- `main.py` の source entrypoint は `agents.choose_rule_indices` / `rank_rule_indices`
  を使う Rule Agent v0。`make_rule_agent_v1`、`make_bounded_search_agent`、
  `make_student_agent` は factory として存在するが、public `agent(obs_dict)` の
  `_DEFAULT_AGENT` からは呼ばれない。

### root deck は worktree と HEAD が異なる

`deck.csv` は既存の未コミット変更である。従って「root deck」は HEAD の old fixture と
現在 worktree の deck を分けて記録する。

| deck | raw file SHA-256 | comma-sorted canonical SHA-256 | JSON-sorted canonical SHA-256 | 枚数 |
|---|---|---|---|---:|
| current worktree `deck.csv` | `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` | `c6ca39850d15dce9d006f62585869e901a3339872acf34d52f63e1206bea9b94` | `ed840b99364baa5b5cc03a3120e9d3c982d7c905e2ed8bea2b9e9d2017fa19b7` | 60 |
| `HEAD:deck.csv` / stale package deck | `e92d5717fd04865b0b528307df7a9d9aecc2c7b917bfbd5042fe58e3d1f26997` | `b702e251e3b56104f84b60fddff309b0e5d4fae865e4dbb57311d4b0d45ec17e` | `aa5d50ee4398433b98b930af4d8de669cf4db1b0adb9fde1e31f04ed2bd93dd7` | 60 |

canonical hash は hash convention に依存するため、採用時は下記の再現コマンドで再計算し、raw SHA と区別する。

Current root deck のカード multiset は、card ID `6` が 14 枚、`673/674/675/1123`
が各2枚、`676/677/678/1102/1141/1142/1192/1227` が各3〜4枚、その他が
`1152:2, 1159:1, 1182:3, 1252:1` で合計60枚である。root deck の履歴上の
deck identity（variant `DV-000007` 等）と raw/canonical hash は別 namespace として
混同しない。

## 2. Rule v0 の raw result と 12/96 discrepancy

### raw JSON として存在する result

`runs/meta-specialist-strength/rule-v0-archaludon-fixed6-seed9700000-96.json`

- file SHA-256: `827e508fb1b8613409fa2f120e6a4f67b59b28dd2ce41720eba531afcba8d691`
- schema: `rule-v0-fixed-heldout-ad-hoc-v1`
- base seed: `9700000`
- subject deck: `opponents/public_archaludon_cinderace_r7/deck.csv`
- subject deck raw SHA-256: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- `games_played=requested_games=96`, `games_per_seat=8`, `wins=8`, `losses=88`,
  `draws=0`, `faults=0`, `score_rate=0.08333333333333333`
- seat: seat0 `4/48`, seat1 `4/48`
- opponents: `kiyotah_lucario`, `sue124_alakazam`, `skarin_dragapult`,
  `ozawa_crustle_v2`, `nihei_megalopunny`, `yaroslav_crustleaware_lucario`
- opponent wins: `1, 5, 1, 0, 1, 0` in the order above.

### 文書にある 12/96

`docs/evidence/v4-wave3-postrun-audit-20260812.md` と
`docs/reviews/2026-08-12-independent-zero-based-review.md` は、同一 Archaludon
deck・固定6相手・両 seat の Rule v0 を `12/96 (12.50%)` と記録している。しかし、
その記録が参照する `/tmp/rule-v0-heldout-20260811.json` は監査時点で存在せず、
リポジトリ内にも `games_played=96,wins=12` の対応 raw result は見つからなかった。
文書は entrypoint SHA、protocol、base seed `10000000` を説明しているが、raw JSON
とその SHA を再検証できない。

従って、現在の evidence の階層は次である。

1. **8/96** — raw JSON と SHA が存在する事実。base seed は `9700000`。
2. **12/96** — 文書上の別 run の主張。raw artifact がないため、現時点では再現不能。
3. root deck + Rule v0 の 96 局結果 — **未測定**。8/96 と 12/96 は root deck ではなく
   Archaludon subject deck 上の診断値である。

8/96 と 12/96 は同一測定の単純な訂正値として合算・平均してはならない。12/96 を
採用するには、対応する JSON、entrypoint/policy SHA、deck raw SHA、opponent/policy
fingerprint、protocol SHA、base seed、fault/seat内訳を再生成して freeze する必要がある。

## 3. Archaludon subject deck

- path: `opponents/public_archaludon_cinderace_r7/deck.csv`
- raw file SHA-256: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- comma-sorted canonical SHA-256: `0963c2daca1844b539e1be78c4dfcc10ec6806d6b9bd6142b22c64efe49f7501`
- JSON-sorted canonical SHA-256: `e223210a3d0e3c1ae72f83479a3b9c9d06ac9f4a4c45e41793b1a484ad0d5c8b`
- `opponents/tomatomato_archaludon/deck.csv` は raw bytes が同一。
- `SOURCE.md` の permission boundary は `local_eval_only` 相当で、submission bundle への
 自動利用を許可するものではない。

`pool_manifest.json` の `public_archaludon_cinderace_r7` 行だけは、
`canonical_deck_hash` に raw SHA `421659...` が入っており、実ファイルの comma-sorted
canonical `0963...` と一致しない。これは current pool identity の一次監査で検出した
単独の整合性不一致であり、評価前に manifest row を再生成または explicit exception と
して固定する必要がある（既存ファイルは変更していない）。

## 4. Wave6 checkpoint / Archaludon research pair

Manifest: `runs/meta-specialist-v4-archaludon-longrun-wave6-current/run-manifest.json`

- manifest SHA-256: `080d54682e0d41c83392bb18adee1ec8ddc3ceea79acbc4cb16ad6930aa92ee6`
- schema: `meta-specialist-v4-archaludon-longrun-v2`
- status: `complete`
- subject deck raw SHA: `42165967b565dd42ec426ecccfe79bfa7d72aa8306590e149dface0ee8bd530e`
- training lane: `archaludon`; device `cuda:0`; embedding 64; hidden 128; TBPTT 8;
  learning rate 0.001; epochs 8; max steps 2000; fixed-six, 8 games/seat.

| seed | checkpoint path | checkpoint file SHA-256 | tensor-state SHA-256 | heldout raw SHA | wins / games | faults |
|---:|---|---|---|---|---:|---:|
| 0 | `.../seed-0/best-recurrent-bc-v4.pt` | `9eb22970fb9917d3632f415e19b943c752e60e31595efe5419960d9c27e6c8de` | `36b6ace02e69849ea476565bd0ef283d206a6786ba2ac0c0040e82d263f3292a` | `95d38cb27313fb815eb02c6b1b1ecba333c821b892524877862babf978562622` | 41 / 96 | 0 |
| 1 | `.../seed-1/best-recurrent-bc-v4.pt` | `5d137fd6e6b76b993d1d7dcc4d975bcf9c43358c9e43510a8db0c9c6181dddf6` | `046968c1a295af0ae84594fedb8503a33b6f8a5dd33314e8fc4fe4ae09985e8a` | `31e2c88e683218302ef559bbd5adbb8ba37e023b187b403e1af33d5fa064cf0a` | 42 / 96 | 0 |

The six opponent fingerprints in the manifest are policy-distinct. `kiyotah_lucario`
and `yaroslav_crustleaware_lucario` share the same fixed-six deck raw SHA
`b4464eb525a25e6598a972d00efc5e5b5156372e77f51853f4076d8ebb34fd7d` and comma canonical
`b39573132435a9bdacf978f14e13ac69518678db88693f08342c97ae1725b797`, but have different
policy hashes. This is two policy instances on one deck identity, not two independent deck
identities.

## 5. Existing package artifacts

### `dist/kaggle/neural-student-v1`

- archive: `dist/kaggle/neural-student-v1/submission.tar.gz`
- archive SHA-256: `7ee7113e20b5a4bbf1f66b191e41c646986a0566877429a33859c9f569428f41`
- manifest `package_identity`: `neural-student-v1-rule-v0-fallback`
- manifest `model_purpose`: `NEURAL_FIXTURE_SMOKE`
- `build_commit`: `a9f3852c9c88ffc169a64804a40d077dc86f7d16`
- package deck raw SHA: `e92d5717fd04865b0b528307df7a9d9aecc2c7b917bfbd5042fe58e3d1f26997`
- model JSON fallback policy: `rule-agent-v0`
- 18 tar members; no Wave6 checkpoint or Wave6 runtime is present.

This archive is a stale smoke/fixture artifact and is neither the current worktree pair nor
a promotion candidate. Its deck is the old HEAD deck, not current `deck.csv`.

### Other archives

`runs/from-worktree/meta-specialist-canonical/test_package_build/submission.tar.gz`,
`.../e2e-multi-opponent-experiment/archive/submission_multi_opponents.tar.gz`, and the
quarantined `quarantine/2026-08-05-fabricated/.../submission_primary_archaludon.tar.gz`
are all the same 2,173-byte static fixture archive with SHA
`e30e9d4dec630eb76067c8067094984ead5a7d054979701929eac66f0fbcaee1`. They contain a
fixture `deck.csv` and static rule bundle, not the current root deck or Wave6. There is no
current `artifacts/submission/rule-v0` directory and no verified Wave6 submission archive.

## 6. Opponent pool and duplicate identity audit

Current `opponents/pool_manifest.json`:

- raw file SHA-256: `e0013cf31b3e6e24db54591faeef6f092b9ebf85247bd0e57598eb8d447f20ca`
- 102 entries (HEAD had 66; current worktree has an existing uncommitted expansion)
- source: public 71 / internal 31
- usage boundary: all 102 are `local_eval_only`
- `smoke_ok`: true 101 / false 1 (`public_archaludon_cinderace_r7`)
- declared canonical deck identities: 77 unique
- policy hashes: 58 unique
- actual on-disk comma-sorted canonical identities: 76 unique; the one mismatch is the
  `public_archaludon_cinderace_r7` row described above. All policy hashes checked for the
  sampled/current entries match their `main.py` bytes.

Declared/current duplicate groups that matter for evaluation:

- current root deck composition (`c6ca3985...`) is also used by five pool IDs:
  `aman_crustleaware_fighting`, `aristophanivan_multiply`, `aristophanivan_probabilistic`,
  `kojimar_lucario`, `makthanithin_baseline1084`; their deck.csv raw SHA is exactly the
  current root raw SHA `2a541d7b...`, while policies differ.
- fixed-six `kiyotah_lucario` and `yaroslav_crustleaware_lucario` share one deck identity,
  with different policy hashes.
- fixed-six `skarin_dragapult` shares its deck identity with three other pool entries;
  `ozawa_crustle_v2` shares with two other current pool entries; other declared duplicate
  deck groups exist throughout the 102-entry pool.
- shadow-A has 6 deck / 6 policy identities.
- shadow-B has 6 deck / 6 policy identities.
- shadow-C has 6 deck identities but **one shared policy hash**
  `6336b4d54e63c5da780860b95565e1b6b99b68926b5610995fc8b83ca62f7f10`; it is an external
  deck diagnostic cohort, not six independent policy replications.

Frozen shadow manifest SHAs and statuses:

| cohort | manifest SHA-256 | status | IDs |
|---|---|---|---|
| shadow-A | `6ddaf3588bb22869a808fd75f84721b640dde6d75f665a11beb10f578af72107` | frozen candidate, not evaluated | `aristophanivan_multiply`, `kiyotah_abomasnow`, `masamikobayashi_garchomp`, `naoto714_kangaskhan`, `naoto714_slowking`, `yaminh_agent` |
| shadow-B | `27e43feecad8d66bc80c2f43c23e9276d42398bf3f47eeba2bc5914087c168e0` | frozen untouched candidate, not evaluated | `biohack44_crustlecounter2`, `harukiharada_crustle`, `kiyotah_iono`, `naoto714_ursaluna`, `pilkwang_lucario_alakazam`, `prvsiyan_grimmsnarl` |
| shadow-C | `52acf95a05b5b4d592fb6a2f9788051a1caedf3c0003c322cf55b09af5d84014` | frozen untouched candidate, not evaluated | `medal_0001_77a53ffc`, `medal_0004_01501d64`, `medal_0006_07bedfff`, `medal_0010_4bf59ca5`, `medal_0015_5e60b8c7`, `medal_0016_706fa912` |

The pool assets are local evaluation assets. A local opponent evaluation does not authorize
placing the opponent source in a submission archive.

## 7. Evaluation start gate

### Can start now

The existing Wave6 seed0/seed1 checkpoint + Archaludon subject deck + fixed-six development
evaluation can start as a **research diagnostic**, provided the run records:

1. Wave6 manifest SHA and both checkpoint file/tensor SHAs;
2. subject deck raw SHA `421659...`;
3. fixed-six policy and raw deck file SHAs (not only the declared canonical identity);
4. evaluator implementation/protocol SHA, base seed, games/seat, and fault count;
5. output artifact SHA after completion.

The raw Rule v0 + Archaludon 8/96 artifact is also available as a historical diagnostic, but
it is not a root-deck baseline and should not be used as a submission claim.

### Must be closed before promotion or submission

- measure and freeze **Rule v0 + current root deck** under the same evaluator protocol;
- resolve the `public_archaludon_cinderace_r7` manifest canonical identity mismatch;
- build a fresh package from the intended current root `main.py` + root `deck.csv` and run
  package closure/deck legality/runtime checks;
- do not reuse the stale `neural-student-v1` fixture as Wave6 evidence;
- keep `local_eval_only` opponent assets out of any submission bundle;
- treat 12/96 as unverified until its raw JSON and identity bundle are restored or rerun.

## 8. Reproduction commands

```bash
git branch --show-current
git rev-parse HEAD
git status --short
sha256sum deck.csv
sha256sum runs/meta-specialist-strength/rule-v0-archaludon-fixed6-seed9700000-96.json
sha256sum runs/meta-specialist-v4-archaludon-longrun-wave6-current/run-manifest.json
sha256sum runs/meta-specialist-v4-archaludon-longrun-wave6-current/archaludon-seed-{0,1}-heldout-96.json
sha256sum opponents/pool_manifest.json
sha256sum opponents/public_archaludon_cinderace_r7/deck.csv
tar -tzf dist/kaggle/neural-student-v1/submission.tar.gz
```

For pool canonical identity, use the exact historical freeze convention (comma-separated,
sorted card IDs), not `src/mage_ptcg/observability/cabt_trace.py`'s JSON convention:

```bash
python - <<'PY'
import hashlib
from pathlib import Path
for path in (Path('deck.csv'), Path('opponents/public_archaludon_cinderace_r7/deck.csv')):
    cards = sorted(int(x) for x in path.read_text().split())
    print(path, hashlib.sha256(','.join(map(str, cards)).encode()).hexdigest())
PY
```

## 9. Audit status

`PHASE1_INVENTORY_COMPLETE` for current local evidence. This report is an inventory and
identity audit, not a promotion decision. The key unresolved question is no longer whether a
Wave6 checkpoint exists; it is whether the intended current root deck/policy pair has been
measured and packaged as one coherent deliverable.
