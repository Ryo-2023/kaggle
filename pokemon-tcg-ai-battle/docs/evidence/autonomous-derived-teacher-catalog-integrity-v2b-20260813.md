---
title: Autonomous Derived Teacher Catalog v2b Integrity Evidence
date: 2026-08-13
status: verified-local-training-input
scope: local-research-only
---

# Derived teacher catalog v2b — primary-artifact integrity gate

## 結論

fresh v2b の6 teacher collectionだけを参照する、新しいclosed catalogを生成した。
旧 `derived-teacher-catalog-v1/catalog.json` は上書きしていない。新catalogは6件すべて
`READY`だが、許可する用途は派生weightの`training-local`だけであり、teacher code、
source deck、promotion、Champion変更、package、submissionの権限は与えない。

`READY`判定はsnapshot indexの集計値を信用せず、次の一次artifactを毎回実読込して
再検証する。

- manifest v2とimmutable collection contract v2のclosed schema、path、file SHA
- teacher policy/deckの実bytes、source kind、permission ID/content hash/trusted bytes SHA
- collector source snapshot、pool manifest、engine source、contract schedule
- 96個のcurrent game sidecarと全attempt ledger、record path/SHA/count
- omissions ledgerのpath/SHA/countと、全sidecar omission rowの完全一致
- 全raw dataset chunkのSHA、record self-hash/source/teacher binding、chunk manifest identity
- 全training snapshot shardのofficial reader round-trip、snapshot ID、example/split count
- index/shard双方の7件のsource artifact provenanceとraw record↔sealed example対応

旧manifest v1は要約値を満たしていても`LEGACY_V1_BLOCKED`として`READY`にしない。
未知field、欠落field、path escape、SHA差替え、source kindやpermissionの不一致は
fail-closedとする。Niheiの表現不能decision 2件は失敗ではなく、ledger/sidecar/indexへ
完全にbindされた明示omissionとして保持する。

## 一次artifact

| artifact | SHA-256 |
|---|---|
| `runs/final-sprint-autonomous/derived-teacher-catalog-v2b/catalog.json` | `8f7c9ea02ea8ec23dcfb35d7d721c81fd0b92db3d31d451157b1396d542443a4` |
| catalog内 semantic `catalog_sha256` | `da6c44cc6042d4a2cb955d5429390c9e8955d4cdba8381bb0c361d35b5b1425e` |
| `src/mage_ptcg/meta_specialist/derived_teacher_catalog_v1.py` | `ae28bb7e22794fcf6a8bb3df15871d0171cde87e7055cb252ce42610980a3ace` |
| `tests/meta_specialist/test_derived_teacher_catalog_v1.py` | `a1f273c9b282e7c8bd527c5dcf435b39939611f5c2a8812e7933345e82f1f38a` |

collection manifest、contract、snapshot indexの個別SHAは
`docs/evidence/autonomous-derived-teacher-collection-v2b-20260813.md`を正とする。

## formal READY result

| teacher | examples | unlabelled | games | fault | seat |
|---|---:|---:|---:|---:|---:|
| `tomatomato_archaludon` | 5,110 | 0 | 96/96 | 0 | 48/48 |
| `lucifer19_battlecore` | 5,245 | 0 | 96/96 | 0 | 48/48 |
| `plamen06_steel` | 5,275 | 0 | 96/96 | 0 | 48/48 |
| `ozawa_grimmsnarl_v2` | 8,104 | 0 | 96/96 | 0 | 48/48 |
| `ozawa_rocket_v2` | 5,899 | 0 | 96/96 | 0 | 48/48 |
| `nihei_alakazam` | 7,051 | 2 | 96/96 | 0 | 48/48 |

## TDD反証と検証

先に次のtamper fixtureを追加し、従来validatorが拒否しない、または一次artifactより
後段でしか失敗しないREDを確認した。

- manifest v1を`READY`へ通す経路
- indexを変えずsnapshot shard実体だけを改変
- contractを改変してSHAだけmanifestへ追随
- omissions pathをcollection root外へ移動
- permission content hash、source kind、sidecar source bindingの改変
- manifest/contractへの未知field注入
- attempt file欠落
- raw recordのsource kindとself-hashを同時に改変
- omission件数を揃えたままledger rowとsidecar rowを異ならせる改変

実装後のfocused suite:

```text
PYTHONPATH=src:. pytest -s -q tests/meta_specialist/test_derived_teacher_catalog_v1.py
16 passed in 253.57s
```

新catalog buildは内部full validation、publish前validation、独立formal verifierの全段を
通過した。

```text
verification=PASS
catalog_file_sha256=8f7c9ea02ea8ec23dcfb35d7d721c81fd0b92db3d31d451157b1396d542443a4
catalog_sha256=da6c44cc6042d4a2cb955d5429390c9e8955d4cdba8381bb0c361d35b5b1425e
```

`py_compile`はPASSした。`ruff`は環境にmoduleが無いため未実施。commit、push、remote
branch、Champion変更、package変更、Kaggle submissionは行っていない。

## 下流gate

このcatalogのintegrity gate PASSは、結合後datasetのsplit leakageや学習性能を保証しない。
Student/AWR bridgeはcatalog file SHAとsemantic SHAをpinし、cross-teacher duplicate/
near-duplicateを別gateで監査する必要がある。そのgateがNO-GOなら、catalog自体を改変せず
結合split設計をfail-closedで修正する。
