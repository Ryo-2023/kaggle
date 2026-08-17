---
title: Autonomous Derived Teacher Collection v2b Evidence
date: 2026-08-13
status: verified-local-training-input
scope: local-research-only
---

# Derived teacher collection v2b — fresh six-teacher evidence

## 結論

旧 collector v1 / 再構築済み corpus は、resume 時に non-DONE 局、omission ledger、
fault history、collection inputs を完全には復元できないため、学習入力から隔離した。
collector と seal を v2b 契約へ硬化した後、許諾済み 6 teacher を新しい run 名で
各 96 局、共通 16-opponent schedule、両 seat 48 局ずつ fresh 収集した。

正式 v2b は合計 576/576 局が `DONE`、fault 0 であり、36,684 labelled records を
得た。Nihei の 2 decision は complete-action 列挙数 4,097 が上限 4,096 を超えた
ため、黙って消さず omissions ledger と game sidecar に理由付きで保存した。
他の 5 corpus は omission 0 だった。

全 corpus は collector manifest、immutable collection contract、current per-game
sidecar、attempt ledger、raw record SHA/count、omissions ledger、collector source
snapshot、permission canonical bytes、teacher/deck/policy bytesを照合してから
training snapshot として封印した。全 shard は正規 reader で round-trip PASS した。

この結果は local `training-local` の派生weight入力を認めるが、teacher code/deckの
submission、Champion変更、promotion、Kaggle submission の権限を与えない。

## 旧 corpus を隔離した理由

独立監査で次を確認した。

- nonempty JSONL が terminal `DONE` の証明なしにresume完了扱いされ得た。
- run name再利用時にopponent schedule、seed、deck、teacher、engine、source bytesの
  immutable contractを照合していなかった。
- resume/reconstructionが過去のomission/unlabelled/fault情報を復元せず、空ledgerで
  上書きできた。
- serial pathだけ最終corpus全体のmatchup cap再計算を通らない経路があった。
- internal teacherのrecord `source.kind` がexternal固定になり得た。
- snapshot indexだけのSHA照合ではshard内容の差替えを検出できない経路があった。

そのため旧catalog、旧snapshot、旧AWR sidecarは`LEGACY_SOURCE_NO_GO / AUDIT_ONLY`
とし、今回のv2b catalogへ混ぜない。

## collector v2bの閉じた契約

1. run開始時にcollection contractをatomic作成し、resumeでは完全一致を要求する。
2. contractはrun/archetype/deck/teacher/opponents/seed/seat schedule/max steps/
   source commit/pool manifest/engine/vocabulary/permission/collector sourceをbindする。
3. dirty worktree上のcollector bytesをrun内`collector_source_snapshot.py`へ保存し、
   workerも同じsource SHAを検証する。
4. gameごとにcurrent sidecarとappend-only attempt ledgerをatomic保存する。
5. `DONE`はterminal outcome、nonempty labelled records、record SHA/countが揃う場合だけ。
6. STEP_LIMIT、exception、label 0件はrecordを残さずnon-DONE sidecarとして再実行対象。
7. omissionはgame sidecarとroot omissions ledgerへ同じ内容を保存する。
8. corpus-global matchup capをserial/parallel共通post-passで適用する。
9. weight変更時はrecord self-hash、current sidecar SHA、最新attempt ledgerを更新する。
10. manifestは全aggregate、contract/omission/source/permission SHAを保存する。

collector source SHA:

`a9c49337b6686ea528bf213e9b75cc7ee1862fea0cdf23a64745cc4568fd1198`

## fresh collection result

| teacher | source kind | seed | W-D-L | records | omissions | manifest SHA | contract SHA | snapshot index SHA |
|---|---|---:|---:|---:|---:|---|---|---|
| `tomatomato_archaludon` | pooled external | 17,100,000 | 68-0-28 | 5,110 | 0 | `de04f029ff18cbe0e2209c57dd17a73d90d5ae7a4ac6a0bc8706543349e2d41c` | `ad694afe690b21c4be2f9293ad4688d7997b8b4e8ba82129cc5996dae47b8a83` | `21cc9cbfc9ca2f9a7a7ce9863fd86968bc5345599362d89d209815bdf9a20b28` |
| `lucifer19_battlecore` | pooled external | 17,200,000 | 61-0-35 | 5,245 | 0 | `a03dfc1f410cdf23b2404cdd3411271776805018f679ad61f6777f32ea949e0d` | `75db78a399e8901a95b0c43be168c7647a62ec0b569f95e0d4110a82b935e5c9` | `dd6988551fe9bb52deaecfdb018dfb94de30ac3167c619156c2732fd6c3db85c` |
| `plamen06_steel` | pooled external | 17,300,000 | 76-0-20 | 5,275 | 0 | `ade4643925ac9ec3b4c737499b0cb8994279555505fb458d629ae5fdc8e1f45e` | `6c4e29d9e65677eb28e3f79b221f3a44bcfaddf8736adcaada7713b93af9d9a5` | `6a422607a446b8a810a6d765463b2793c78381f68051e1def920bbe8016169f7` |
| `ozawa_grimmsnarl_v2` | team internal | 17,400,000 | 59-0-37 | 8,104 | 0 | `156326f87670f1517e106a04c3a8461377cc692130afc325f34dd2f276436bbf` | `bf01e205888a598b6f1b83be4b8717801bdd3ef6e3b74679a4763edab6fd534e` | `a2b6992fb9097405a6ffb1a93feb2d3135ae91dd62f9d089ae58f3718483305a` |
| `ozawa_rocket_v2` | team internal | 17,500,000 | 65-0-31 | 5,899 | 0 | `487e09bb881dc1e72ea6a24062ce294a820ea40b6e6dcef215a1fe33e53d7cdc` | `7f5473699a3f0f248358bd1199ac7dec462a5c77271b9cca948e25629e38a6a1` | `a35fd0731a2794f9810846e8783a64ed288656baf80ed440e690fc251222f10b` |
| `nihei_alakazam` | team internal | 17,600,000 | 55-0-41 | 7,051 | 2 | `56ff72db8b6d43663d5faaf3952c19b291453d61de9ec13992f0a23a8e2261af` | `29f0f14265c5ff29e8798e1f676304018bf32dd9efaf93163c80df5fe77de320` | `6e3db7700db4ee22bb6126babc7d0fc86629abcfb1c827cddef0c4e12a97bed9` |

全行で`games_requested=games_completed=96`、`games_faulted=0`、
`games_other_status=[]`、`seat_counts={subject_first:48, subject_second:48}`、
`game_result_sidecars=96`、`game_attempts_non_done=0`を確認した。

Nihei omissions ledger SHA:

`38eb2a7a3e177aded9e08ea53ebf2fa560bd6ff1f365d557da1885b016c835ca`

他5 corpusの空ledger SHA:

`e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

## sealed snapshot result

| teacher | train | development | test | total | shards | source artifacts |
|---|---:|---:|---:|---:|---:|---:|
| Tomato | 3,623 | 486 | 1,001 | 5,110 | 1 | 7 |
| Lucifer | 3,734 | 894 | 617 | 5,245 | 1 | 7 |
| Plamen | 3,723 | 1,011 | 541 | 5,275 | 1 | 7 |
| Grimmsnarl | 5,463 | 1,426 | 1,215 | 8,104 | 1 | 7 |
| Rocket | 4,555 | 744 | 600 | 5,899 | 1 | 7 |
| Nihei | 4,825 | 1,167 | 1,059 | 7,051 | 1 | 7 |

source artifactsは、元teacher policyに加えて次を含む。

- `teacher_collection_manifest_v2`
- `teacher_collection_contract_v2`
- `teacher_collection_omissions_v2`
- `teacher_collector_source_snapshot_v2`
- `teacher_permission_trusted_bytes_v1`
- `teacher_source_kind:<exact source kind>`

## verification

実行したfocused/nearby suite:

```text
73 passed, 1 skipped in 15.50s
```

対象はcollector cap/resume/parallelism、matchup weight integrity、teacher dataset、
sharded snapshot、seal v2 preflightである。skipは既存環境依存の既知skipで、今回の
失敗ではない。

追加実測:

- v2b 2-game CABT smoke: 2/2 DONE、52 records、fault/omission 0。
- 同一run同一contract resume: PASS、再対局0。
- 同一runでbase seed変更: contract mismatchでfail-closed。
- smoke seal: 52 examples、1 shard、official reader round-trip PASS。
- formal all6 seal: 全shard official reader round-trip PASS。
- indexの各provenance SHAとcollection manifest実体を再照合: PASS。
- `py_compile`: PASS。
- `git diff --check`: PASS。

主要実装/test SHA:

- collector: `a9c49337b6686ea528bf213e9b75cc7ee1862fea0cdf23a64745cc4568fd1198`
- collector focused test: `04a8d521f433da69d128b00b003ff0dd34615bcbf92323841689eee1b8c51eda`
- seal script: `f56a09cd35636ede117fd961a3bd20010977c67f26e3957f4fcbc387ef11d888`
- seal focused test: `03fa381900a36dac38921255bdfd526c5bfabdf35aecb0c9c39c1706497ee688`

## 解釈と次のgate

収集・封印のintegrity gateはPASSした。性能改善はまだ主張しない。次はこの6 corpus
だけを新しいclosed catalogへ登録し、train splitからactor-visible cross-fitted value
とAWR/filtered-BC weightを生成する。その後Student v3 set/cardinality learnerへexact
record-id joinし、GPU tiny/full training、runtime legality、common24 native controlの
96→384→768→1536評価へ進む。

Student v3またはAWRがcatalog/source/snapshot/record-idのどれかを完全照合できない場合、
学習は開始せずfail-closedとする。
