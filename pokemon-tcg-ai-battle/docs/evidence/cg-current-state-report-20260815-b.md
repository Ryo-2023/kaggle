# cg BestKnown 現状報告と次の meta-source 設計候補（2026-08-15）

## 結論

実CABTの現行基準は self-owned の `cg-lethal-target-v1`（P1）＋root deckである。P1のpolicy SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHAは `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`。P2、Champion、production、submissionは変更されていない。

直近のcross-lineage recombinationとfailure-conditioned public adapterは、静的合法性・bounded fault-free CABT接続には成功したが、独立positive／seat-safe／opponent×seat-safe gateを満たさずP1を保持した。いまのボトルネックは `cg_bestknown_loop_v1.py` の実装ではなく、CEMへ渡せる相関の低いmeta sourceの獲得・生成である。

## 実repoのスナップショット

| 項目 | 観測値 |
|---|---|
| branch | `feature/belief-guided-search` |
| HEAD | `30cade0e5d349d6ea545f019fc411e9d53288f16` |
| active heavy process | なし（確認時点） |
| current opponent pool | 102 rows、public 71／internal 31、smoke-ready 101 |
| BestKnown package | `runs/final-sprint-autonomous/kaggle-cg-wrapper-p1-20260815-v1/` |
| package policy SHA | `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` |
| package deck SHA | `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` |

作業ツリーには既存の大きな未コミット差分がある。今回の報告ではそれらを整形・削除せず、commit／push／Champion変更／Kaggle提出も行っていない。

## 直近のsource-generation結果

- `CROSS_LINEAGE_POLICY_DECK_RECOMBINATION_V1`: 4候補、8/8 smoke `DONE`・fault 0、CEM 304/304 `DONE`・fault 0。ただし独立差が反転し、robust positive・seat-safe候補は0件。
- `FAILURE_CONDITIONED_PUBLIC_COUNTERPRESSURE_V1`: 4候補、8/8 smoke `DONE`・fault 0、CEM 304/304 `DONE`・fault 0。ただし独立positiveまたはseat/opponent×seat gateを満たす候補は0件。
- 公開kernel v7–v9: v7 Raunak、v9 Prvsiyan controlは部分昇格済みで両seat smokeは安全。v4 Koushikrudraも2/2 `DONE`・fault 0だが、P1との確認は2敗であり、性能根拠ではなく未CEM source候補として扱う。v8の3 sourceはCEM／診断へ投入済みで新しい未使用holdoutとは扱わない。

既存policyのpriority table再配置、同一P1-base adapter、同じpairのblind retryは期待値が低い。source identity、policy/deck SHA、smoke exposure、CEM使用履歴を分離して記録する必要がある。

## 次の設計候補（実装・heavy runは未承認）

### 推奨: actor-visible routed ensemble source

未CEMの安全な親policy（v4／v7／v9）を2つずつ組み合わせ、相手の非公開情報やexpert/action labelを使わず、公開状態（turn、双方のactive／benchのcard ID、stadium、selection context等）の決定的なhash／bucketで、毎局どちらか一方の親policyを選ぶ。各組合せは新しいwrapper SHAとpair-level `source_sha256`を持つため、単なる名前変更やdeckの複製ではなく、policy surfaceの新しい候補になる。

生成器の境界は次の通りとする。

1. 親rootのstatic scan、exact 60、公式card ID、ACE SPEC exactly one、runtime budgetを検査する。
2. routed wrapper、deck bytes、parent SHA、routing recipe、freshness evidenceをno-clobberで封印する。
3. 初期poolは `smoke_ok=false` とし、TRAIN候補だけをbounded smokeへ投入する。DEV／FINALは性能測定へ投入しない。
4. P1固定CEMをscreen→独立seed複数block→seat-safe→opponent×seat-safeの順で実行する。CEM未使用のDEV／FINALで再現した候補だけを `cg_bestknown_loop_v1.py` へ渡す。

### 代替案

- 新しい公開kernel／別commitをさらに取得する: provenanceは最も強いが、現在はsource identity枯渇と取得可能性がボトルネック。
- 既存cross-lineageを親集合だけ変えて再実行する: 実装コストは低いが、同じpolicy/deck recombinationの相関を残しやすく、v1のno-update結果を覆す期待値は低い。

### 反証条件

routed ensembleが、(a)親payloadのimport／runtime isolationを壊す、(b)bounded smokeでfault／timeoutを出す、(c)独立複数blockのlower-tailが0以下、または(d)seat gapが5%を超える場合、そのepochは性能探索へ進めずsource recipeとして失敗扱いにする。

## 現在の判断

この報告時点では、routed ensemble generatorの実装、CEM、DEV／FINAL測定、`cg_bestknown_loop_v1.py`接続はまだ開始していない。ユーザーがこの設計を承認した場合に限り、次の実装計画を作成してから、v4／v7／v9の親候補を使った新epochをsealする。承認前にP1、BestKnown、Champion、production、submissionを変更しない。

## 検証

- P1 packageのpolicy／deck SHA確認: PASS
- active heavy process確認: なし
- 直近source evidence: `docs/evidence/cg-cross-lineage-meta-cem-20260815.md`、`docs/evidence/cg-self-owned-failure-adapter-cem-20260815.md`、`docs/evidence/cg-kaggle-kernel-meta-intake-v7-v9-20260815.md`
- commit／push／Kaggle提出: 未実施
