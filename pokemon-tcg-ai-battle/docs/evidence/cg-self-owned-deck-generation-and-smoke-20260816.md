---
project: MAGE-PTCG
document_status: evidence
as_of: 2026-08-16
---

# Official-data-only self-owned deck generation and CABT smoke

## 判定

公式カードデータと明示的な役割仕様だけから、公開deck snapshotを親にしない60枚deck artifactを生成し、P1 policy/runtimeへ再束縛できる経路を追加した。生成物の静的identity、package hash、通常interpreter runtimeは確認できた。一方、4局のCABT smokeは性能昇格用ではなく、candidateとP1 root-deck controlがともに0勝・fault 0だったため、BestKnown／Champion更新や`cg_bestknown_loop_v1.py`接続は行わない。

判定は `SELF_OWNED_DECK_GENERATION_PASS / PERFORMANCE_NOT_STARTED` である。

## 入力境界とidentity

生成器 `scripts/generate_self_owned_cg_deck_v1.py` は、deck生成時に次だけを読む。

- 公式カードDB: `data/raw/EN_Card_Data.csv`
- 役割仕様: `configs/meta_specialist/self_owned_cg_deck_spec_v1.json`
- deterministic `seed=20260816`, `ordinal=0`

公開deckは生成入力としてコピーしていない。`--public-scan-root opponents` は生成後のcanonical deck hash衝突を拒否する監査に限って使い、deck bytesやカード構成を候補へ取り込まない。P1 packageはpolicy/runtimeの再束縛元であり、parent deckではない。生成manifestは`parent_deck=null`、`public_parent_read=false`、authority全falseを記録する。

| artifact | 値 |
|---|---|
| candidate | `fighting-lucario-scratch-v1-s20260816-o0000-c60e368cad31` |
| card DB SHA-256 | `a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373` |
| role spec SHA-256 | `ed8c761fe59e42e18dd8c19561c26b89f0427ceb4df697f59d88c1fce87d65a1` |
| generator SHA-256 | `a74779bf8a9ca2a7f0eee613b46e9bac92e3666d4e61a1270cb28fd78a3a3432` |
| candidate canonical deck SHA | `c60e368cad31e90192afb820db02ac9528177ae495945a904dbfd9f0fe75ac0c` |
| candidate deck-file SHA | `b144ff9909a33d39c467c74a876bac71128f9ff2d9951297db8db3390f22c0db` |
| candidate package policy SHA | `fd59353369da8a28e8944170e25d0886dc5d6646edb2e65f2096b4489a23c0ab` |
| package manifest SHA | `4030aa1d0cb7a50ac44748ba7c28b3ed86261089d38b5dd6cf2661ef2e7d21a1` |
| public canonical collisions | `0` |

一次生成rootは `runs/cg-self-owned-deck-generation-v1-20260816/` である。`deck-artifact/manifest.json` と `package/self_owned_cg_package_manifest.json` のsemantic SHAを検証した。

## CABT smoke

runnerは `scripts/run_self_owned_cg_deck_screen_v1.py`。candidateは上記self-owned package、controlは既存P1 package（policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`）である。opponentは`aristophanivan_multiply`、各seat 1局、合計4局、worker 1、recycle 1、base seed `2026081601`で固定した。

| arm | W-D-L | fault | score |
|---|---:|---:|---:|
| self-owned candidate | 0-0-2 | 0 | 0.00% |
| P1 root-deck control | 0-0-2 | 0 | 0.00% |

candidate deltaは`0.0pt`、全4局`DONE`、evaluator SHAは`b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`である。screen manifestは`runs/cg-self-owned-deck-screen-v1-20260816-smoke/manifest-complete.json`、summaryは同ディレクトリの`summary.json`に保存した。

この比較はdeck差分を含むjoint smokeであり、1 opponent×両seat×1 repetitionのため性能証拠ではない。新meta source、独立DEV／FINAL、CEM、BestKnown gateは未使用である。

## 検証コマンド

```bash
python -m pytest \
  tests/meta_specialist/test_self_owned_cg_deck_v1.py \
  tests/meta_specialist/test_self_owned_cg_package_v1.py \
  tests/meta_specialist/test_self_owned_cg_deck_cli_v1.py \
  tests/meta_specialist/test_self_owned_cg_deck_screen_v1.py \
  -q --capture=no
# 16 passed

python -m py_compile \
  scripts/generate_self_owned_cg_deck_v1.py \
  scripts/run_self_owned_cg_deck_screen_v1.py \
  src/mage_ptcg/meta_specialist/self_owned_cg_deck_v1.py \
  src/mage_ptcg/meta_specialist/self_owned_cg_package_v1.py
```

## 次の再開条件

このdeck lineageをP1 fixed parentへ接続する次段階は、(1) fresh・unused meta sourceを独立に固定、(2) candidateと同じdeckを束ねたP1 policy controlを用意、(3) legality→static→bounded fault0→TRAIN-only→独立seed・両seat→unused DEV→unused FINALの順で評価、である。4局smokeの結果からdeck採用・policy更新・Champion変更を推論しない。

なお、`ono-`は公開kernel作者名ではない。local Git identity `bfe-lab-ono`、branch `agents/ono-cg-lethal-v1`、commit `1965b42b028f10960d08ccb4980be5b76946f98b`に由来するローカル識別子であり、公開deckの単一元kernelを意味しない。
