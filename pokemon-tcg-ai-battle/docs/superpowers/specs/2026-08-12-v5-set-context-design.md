# V5 SetContext sidecar 設計仕様

## 結論

V5はV4の提出経路・学習器・既存checkpointを変更せず、V4表現とdecoderをベースにした研究専用のsidecarとして実装する。新しい情報は、同一stateで有効な候補集合から得るmean/count contextと、候補ごとのcontext residualだけに限定する。STOP logitはV4のbase global tokenから計算し、V5のcontextはSTOPへ流さない。

## 背景と目的

V4は候補を個別に`candidate_bias(tanh(candidate + global))`へ通すため、候補集合内の相対構造を直接参照しない。V5では、候補順序を意味のない交換可能な集合として扱い、同一候補集合の統計量を全候補へ共有する。目的は、既存V4の転移恒等性を保ったまま、候補間の競合・重複・集合規模を学習可能にすることである。

本仕様の対象はモデル、checkpoint manifest、strict loader、focused testsである。policy adapter、actor pool、提出package、V4 trainer、性能pilotは対象外とする。

## 不変条件

1. V4本体のソース、V4 checkpoint、V4 loader、提出経路は変更しない。
2. V5作成直後は新headの出力がゼロであり、同一入力に対するsemantic logitsとSTOP logitがV4と一致する。
3. candidate contextは`excludes_selected_duplicate=True`の候補をpoolへ含めない。
4. 有効候補を並べ替えるとsemantic logitsも同じ順で並べ替わり、STOP logitとbase global tokenは変わらない。
5. 有効候補数が0または1でもNaNを生成しない。空集合のcontextはゼロベクトルとする。
6. V5 loaderはV4 artifactを受け入れず、V4 loaderもV5 artifactを受け入れない。
7. V5 artifactは、V4 base file SHA、V4 tensor-state SHA、転送allowlistとそのSHA、V5 tensor-state SHA、実装digest、head schemaをmanifestへ保存する。いずれかが不一致ならfail closedする。

## アーキテクチャ

`SpecialistModelV5`は`SpecialistModelV4`を継承し、V4のencoder、GRU、候補表現、base candidate logitを再利用する。V5で追加するのは次の2段の小さなheadである。

1. 有効候補tokenのmean、V4 base global token、正規化した有効候補countを連結し、context projectionでhidden_dimの集合contextを作る。
2. 各候補token、集合context、要素積を連結し、candidate residual headでscalar residualを作る。

`v5_logits = v4_logits + residual`とする。residual headの最終線形層はゼロ初期化し、初期転移時は完全にV4と一致させる。poolはmeanなのでcandidate permutationに対して不変であり、候補ごとのresidualは同じ共有contextを使うので全体としてequivariantである。

STOPは常に`stop_vector @ base_global_token + stop_bias`で計算する。V5 contextをglobal tokenへ加算した値を`PolicyOutput.global_token`として返さない。これにより既存recurrent BCのSTOP計算規約を壊さず、semantic headだけを研究対象にできる。

## V4からの明示的転送

V5生成時は、まずV4のstrict loaderでbase artifactを読み込み、V4 descriptor、file SHA、tensor-state SHAを検証する。その後、V4 state dictの固定allowlistを名前で列挙してV5へcopyする。`strict=False`による暗黙転送、未知keyの無視、raw state dictの直接読込は禁止する。V5 headはV5 constructorで初期化し、転送対象に含めない。

V5のmanifest descriptorには次を必須化する。

| 項目 | 内容 |
|---|---|
| `checkpoint_schema` | V5専用schema文字列 |
| `model_config` | V4寸法とV5 head設定 |
| `base_provenance` | V4 checkpoint path、file SHA、tensor-state SHA、V4 schema |
| `transfer` | allowlist、allowlist SHA、転送元schema |
| `head_config` | mean/count/residual version、STOP policy |
| `implementation_digest_sha256` | V5とV4表現のsource closure digest |
| `tensor_state_sha256` | V5 state dictのcanonical digest |

## Maskと入力契約

candidate tokenのエンコードはV4と同じpublic stateだけを使う。duplicate-maskはlogit maskingだけでなくcontext poolにも適用する。候補がすべてmaskedの場合はpoolを空集合としてゼロcontextを使い、masked logitsはV4と同様に`-inf`とする。合法候補がない状態の実戦処理は既存cabt契約に委ね、本sidecarがrandom fallbackを追加しない。

## Loaderと安全性

V5 saveはdescriptorとCPU clone済みstateをatomicに保存する。loadはimmutable file snapshotをSHA検証してから`weights_only=True`で読み、descriptorの完全なkey set、schema、config、digest、provenance、tensor digestを検証する。V5モデルのstate dictはV4 markerとV5 markerを含む完全一致でなければならない。

V5 loaderは、V4 schemaやV4 model typeをV5として扱わない。V4 loaderは既存のexact-type制約を維持するため、V5 subclassを渡しても拒否される。V5用loaderはこの制約を迂回するのではなく、V4 baseを先にstrict検証したうえで独自manifestを検証する。

## エラー処理

- モデル寸法、候補型、state型、mask型の不一致は専用`NeuralModelV5Error`で拒否する。
- 非finite tensor、symlink、regular fileでないcheckpoint、読込中変更、SHA不一致はfail closedする。
- base provenanceの不足・schema不一致・allowlist改変・実装digest不一致はloadを拒否する。
- V5 stateへのV4 state dict直接loadや、V5 artifactのV4 loader利用はテストで明示する。

## テスト受入条件

focused test fileで次を固定seedで検証する。

1. V4→V5 zero-init transferのsemantic logits/STOP一致。
2. candidate permutation equivarianceとSTOP不変。
3. duplicate mask、N=0、N=1、invalid candidate追加時のcontext不変、finite性。
4. V5 headを非ゼロ化した後もSTOPがV4 baseと一致。
5. V4 artifactのV5 strict rejection、V5 artifactのV4 strict rejection。
6. 保存manifestのV4 SHA、allowlist SHA、head schema、V5 tensor SHAの検証と改ざん拒否。

## 非対象と次段階

V5 policy、recurrent trainer、actor pool登録、opponent evaluation、performance pilotはこの実装で開始しない。focused testsと`py_compile`が通った後に、親タスクでpolicy接続点と1epoch固定六pilotの見積りを再確認する。V4 Champion、提出、Kaggle送信は変更しない。
