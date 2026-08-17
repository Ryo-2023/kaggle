# 公式カードデータ由来 self-owned meta batch v2 / P1 CEM（2026-08-16）

## 判定

`SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。公開root deckを入力にせず、公式`data/raw/EN_Card_Data.csv`と新しいrole spec v2から3種類の60枚deckを生成し、P1 policyを各deckへ再束縛したsource batchを作成した。全sourceのruntime smokeはfault 0だったためfresh metaとしてhash sealし、P1固定CEMへ接続したが、独立META_DEVで候補がcontrolを下回った。P1、root deck、BestKnown、Champion、production、submission、commit、pushは不変である。

## 生成経路とidentity

- role spec: `configs/meta_specialist/self_owned_cg_deck_spec_v2.json`
- generator: `scripts/generate_self_owned_cg_deck_v1.py`
- source seal: `src/mage_ptcg/opponent_ingest/self_owned_cg_meta_source_v1.py`
- source CLI: `scripts/seal_self_owned_cg_meta_source_v1.py`
- policy parent SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- public canonical-hash collision: 3件とも `0`

| source | canonical deck SHA | policy SHA |
|---|---|---|
| `...s20260830-o0000-210155470edb` | `210155470edbe072f5c4237d84f799afeec69ac1819e715ce4dfff6ec1901963` | `7a74c7d5e3676de4acf84b43651f379cbfba57bb46f419b2b89d721789601065` |
| `...s20260831-o0001-522bf8d7e6ca` | `522bf8d7e6ca9edf4ef12b5379eba7f0a356bbd44f340888c4468ddf5934078b` | `9051630c541570f93127535d12eeed9a7eda79dcc23265812cdd08e434f239fe` |
| `...s20260832-o0002-67531bdd9b16` | `67531bdd9b168cbe6e3233e1e99286d6fbbdb573e86c7e0b176717e5ad58477b` | `f60bb6da9626b9fc6e85ea7892f86e8cd3f7b2addda2160460d0a7f58b67ba44` |

生成rootは各々 `runs/cg-self-owned-deck-generation-v2-20260816-00/`、`-01/`、`-02/`。batch staged rootは `runs/cg-self-owned-cg-meta-batch-v2-20260816-staged/`、smoke-promoted rootは `runs/cg-self-owned-cg-meta-batch-v2-20260816-promoted/` である。

promoted artifactのSHAは次の通り。

- pool: `a6d48cd9d5335bc349867dc91320e9154f92530f3e408b1023fc95ba0b55ef57`
- fresh meta: `5468ddc0773ace25ca9306c6e7b064562ddba16dfddb4d6e66b95138cc278d66`
- CEM split: `45c72b42b380fa58d3570c9d97ddca33352f2991a2dd3255e4a208e8ceeb0451`

各sourceのpackage manifestは`parent_deck=null`、`public_parent_read=false`、authority全falseで、sourceの正確な意味は「公式データ由来deck＋P1 policyのdeck-bound variant」である。独立policy lineageやnative/public性能の証明とは扱わない。

## runtime smoke

`runs/cg-self-owned-cg-meta-batch-v2-20260816-smoke/` で、candidate（v2 source 00）とP1 controlを、3 source・両seat・各2 repetitionへmatched投入した。

- 合計: 24局、`DONE=24/24`、fault `0`
- candidate: `7W-0D-5L`、score `58.33%`
- P1 control: `8W-0D-4L`、score `66.67%`
- candidate-control: `-8.33pt`
- seat rate: candidate `50.00% / 66.67%`、control `50.00% / 83.33%`

これはruntime安全性の証拠であり、sourceの強さやBestKnown性能の証拠ではない。3 sourceすべてをこのsmokeで実行したため、`META_FINAL`はCEM選抜には未使用だが、runtime smokeまで含めた意味で完全な未接触holdoutではない。この点をfreshnessの解釈に残す。

## P1固定CEM

runnerは`scripts/run_cg_p1_cem_v1.py`、campaign rootは `runs/cg-self-owned-cg-meta-batch-v2-20260816-cem/`。P1をcontrolに固定し、`META_TRAIN`はsource 00のみ、population `4`、elite `1`、initial scale `0.10`、campaign seed `2026081901`、独立再評価1 block、positive-delta gateを使用した。`META_DEV`はgeneration 1のvalidationだけに使い、`META_FINAL`はCEM選抜・validationへ渡していない。

| stage | games | result |
|---|---:|---|
| generation 0 screen | 20 | 全row fault 0。identity center候補がscreen `+50pt`だったが小標本 |
| generation 0 independent | 16 | identity center `6W-0D-2L` 対 control `4W-0D-4L`、`+25pt`。centerは実質P1 identityのため昇格しない |
| generation 1 screen | 20 | candidate `c02`がscreen `+25pt`、他候補はgate外 |
| generation 1 independent | 16 | `c02`: `6W-0D-2L` 対 control `5W-0D-3L`、`+12.5pt`、両seat rate `75%` |
| generation 1 META_DEV | 32 | `c02`: `7W-1D-8L`、score `46.875%`; control `9W-0D-7L`、score `56.25%`; 差 `-9.375pt` |

全CEM rowはfault `0`、generation 1 DEVもfault `0`だった。しかしDEVでcandidateが負差となり、同一候補をP2／BestKnownへ昇格しない。generation 1の新centerは診断用にartifactへ残しただけで、現行P1には反映しない。

## 実装・検証

- source seal focused tests: `tests/test_self_owned_cg_meta_source_v1.py` 5 passed
- self-owned deck/package/CLI/screen tests: 既存focused suiteを含めてPASS
- source/package/runner `py_compile`: PASS
- `build_fresh_meta_batch_v1` と`load_weekend_split(verify_sources=True)`: PASS
- active heavy process: なし
- commit、push、Champion変更、production変更、Kaggle提出: 未実施

## 解釈と次のゲート

今回の成果は「公式データだけからdeck familyを生成し、source pool・freshness・CABT CEMへ接続できる」ことの実証である。性能面では、同じP1 policyをdeckへ束ねただけのsourceでは、CEM screenの見かけの正差がDEVへ転移しなかった。したがってこのv2 role familyのblind retryは行わない。

次は、同じdeck proxyを増やすのではなく、相関の低いself-owned policy family（source側の明示的な対P1 hard-negative生成、または異なる公式deck archetype＋submission-safe policy）を新epochとして設計し、`fault0 → TRAIN-only → independent positive → seat-safe/opponent×seat-safe → 未使用DEV → 未使用FINAL`を再実行する。全ゲート通過前に`cg_bestknown_loop_v1.py`へcandidateを渡さない。
