# Kaggle公開kernel meta intake v1 設計仕様

## 目的

公開Kaggle kernelから取得した提出可能な`main.py`と`deck.csv`を、既存の`cg`実体へ結び付けた研究専用opponent poolとして安全に隔離し、fresh meta sourceとして`cg_bestknown_loop_v1.py`へ渡せる状態にする。公開kernelの成績をそのまま採用するのではなく、同一engine・同一subject・独立seedで再評価できる入力だけを作る。

## 非目的

- 既存`opponents/pool_manifest.json`、Champion、P1 package、submission bundleを変更しない。
- Kaggle APIへの提出、kernelの再配布、公開kernelをteacher labelとして学習する処理を行わない。
- 危険な副作用を持つkernelをサニタイズして実行可能にする。安全に隔離できないものはfail-closedで棄却する。

## 入力契約

各入力は次の`KernelSourceSpec`で表す。

```python
@dataclass(frozen=True, slots=True)
class KernelSourceSpec:
    candidate_id: str
    kernel_ref: str
    source_url: str
    tar_path: Path
    tar_sha256: str
    fetched_at_utc: str
```

`tar_path`はローカル取得済みtar.gzのみを受け取り、ネットワーク取得はCLIの外側で行う。tarのroot直下に`main.py`と`deck.csv`が必要で、`main.py`はmodule-levelのcallable `agent`を公開しなければならない。deckは既存の`normalize_deck_text`と`canonical_deck_sha256`で60枚構造を検証する。

## 安全境界

展開時に絶対パス、`..`を含む経路、symlink、hardlink、FIFOなどの非regular member、上限超過（member数、展開bytes、単一file bytes）を拒否する。`cg/`、`__pycache__/`、`.pyc`、submission archive、notebook outputは候補payloadへコピーしない。候補rootにはwrapperの`main.py`とsidecarの`deck.csv`だけを置き、取得元のroot `main.py`は`payload/original_main.py`として保存する。

保持したPython sourceはASTで走査し、network import/literal、subprocess、dynamic import/execution、filesystem write/delete、secret literal、危険なenvironment accessを一件でも検出した候補を棄却する。`list.remove`のような通常のgameplay bookkeepingはfilesystem writeと誤認しない。shared repoの`cg` importは許可し、engine本体を候補tarから実行しない。

wrapperは候補固有のmodule nameで`original_main.py`をロードする。ロード前に、過去候補のpayload配下にある`sys.modules`だけを除去し、候補間で`agents`などのgeneric moduleが再利用されないようにする。repo rootの`cg`は共有engineとして解決し、候補tarのbundled `cg`は実行対象から除外する。

## 出力契約

`seal_kaggle_kernel_meta_v1(...)`は既存outputを上書きせず、次をatomicに生成する。

```text
<output_root>/
  <candidate_id>/main.py
  <candidate_id>/deck.csv
  <candidate_id>/payload/original_main.py
  <candidate_id>/SOURCE.md
  evidence/<candidate_id>.json
  pool_manifest.json
  fresh_meta.json
  intake_report.json
```

pool rowは既存`load_opponent_pool_v1`で読める`id`、`policy_hash`（wrapper bytes）、`source_policy_sha256`（取得元root main bytes）、`canonical_deck_hash`、`source`、`source_branch`、`source_commit`（tar SHA）、`usage_boundary=local_eval_only`を持つ。`fresh_meta.json`は候補全件を`fresh=true`かつ`unused_before_run=true`としてhashで束ね、権限をtraining/promotion/submission/longrunすべてfalseにする。

## fresh性

fresh identityはcandidate id、wrapper policy hash、canonical deck hashを基準にする。current pool manifestと明示された既存performance artifact rootsへ同一identityが現れた場合は候補を棄却する。tar SHAやsource policy SHAは取得 provenanceであり、performance消費identityとは分離するため、source discovery文書の参照だけでfresh性を失わせない。

## 評価接続

生成後に`load_opponent_pool_v1`で全候補を解決し、候補を`META_TRAIN`だけへ割り当てて両seat fault-inclusive smokeを実行する。`META_DEV`と`META_FINAL`はCEMの候補選択へ渡さず、fresh validation専用にする。CEMはP1をcontrolとして`run_cg_p1_cem_v1.py --pool-root <output_root>`へ接続し、candidateがpositive gateを満たさない限りP1を保持する。公開kernel自体の勝率はこのgateの証拠にしない。

## エラーと監査

検出理由はcandidate単位で`intake_report.json`へ記録し、例外はoutputを部分的に成功扱いにしない。取得日時、kernel URL、tar SHA、source main/deck SHA、除外member、AST findings、freshness scan rootsをevidenceへ保存する。成果物はlocal-eval-onlyであり、submission builderから参照してはならない。

