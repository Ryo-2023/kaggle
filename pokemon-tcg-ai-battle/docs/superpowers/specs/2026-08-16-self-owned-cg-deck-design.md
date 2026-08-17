# Self-Owned CG Deck Baseline Design

**Date:** 2026-08-16  
**Status:** research-only design; no Champion, production, submission, or Git authority

## 目的

現在の P1 policy は self-authored だが、root deck の card bytes は複数の公開 snapshot と一致する。最終目標である「self-owned かつ提出可能な deck＋policy」を満たすため、公開 deck を親にしない scratch deck を生成し、P1 policy と同一の CABT 評価契約へ接続する。

ここでの self-owned は、公開 `deck.csv` の bytes／multiset を入力にせず、公式カードデータ、明示的な role quota、生成 seed、generator のみから候補を再生成できることを意味する。公開 deck と同じカードを使う可能性は残るため、完全なアルゴリズム的独自性は主張せず、既知の公開 snapshot と canonical multiset が一致した候補は reject する。

## 採用方針と代替案

### 採用: 公式データからの deterministic scratch generation

`data/raw/EN_Card_Data.csv` と、generator 内の固定 role specification を読み、カード名・タイプ・進化関係・ACE SPEC 属性から legal 60-card multiset を組み立てる。`deck.csv`、`opponents/**`、公開 kernel archive は生成入力にしない。seed を変えれば同一仕様から独立候補を再生成できる。

### 採用しない: 公開 root の swap

既存の `deck_mutation_v1` は CABT 候補生成器として安全だが、公開 root を parent とするため、今回の provenance 要件を満たさない。

### 後段: scratch 候補の CABT black-box 探索

最初の scratch baseline が合法性・runtime smoke を通った後、role quota と policy parameter を CABT outcome で探索する。初回から black-box 探索を行わず、deck の原因と性能を分離する。

## コンポーネント

### 1. Scratch generator

候補生成器は以下だけを入力とする。

- 公式 `EN_Card_Data.csv` の SHA と、既存の card vocabulary registry
- versioned な role specification（主攻撃ライン、Basic Pokémon、search、draw、energy、switch、support、stadium の quota）
- integer seed と candidate ordinal

生成器は次を fail-closed で検査する。

- 60 枚ちょうど、全 ID が公式 vocabulary 内
- 通常カードは同名 4 枚以下、ACE SPEC は全 deck でちょうど 1 枚
- Basic Pokémon が少なくとも 1 枚、基本 Energy が少なくとも 1 枚
- 選択した evolution line の stage 整合性
- canonical multiset hash が既知公開 snapshot／既存性能 artifact と衝突しない

同一候補の raw bytes と順序非依存 canonical multiset の両方を保存する。生成器は公開 deck の内容を参照しないため、collision scan は生成後の監査専用である。

### 2. Deck identity seal

候補 run root に no-clobber で次を保存する。

```json
{
  "schema_version": "self-owned-cg-deck-v1",
  "parent_deck": null,
  "generator_source_sha256": "sha256(generator.py bytes)",
  "role_spec_sha256": "sha256(role specification bytes)",
  "card_database_sha256": "sha256(EN_Card_Data.csv bytes)",
  "seed": 0,
  "candidate_ordinal": 0,
  "deck_file_sha256": "sha256(deck.csv bytes)",
  "canonical_deck_sha256": "sha256(sorted card multiset)",
  "public_parent_read": false,
  "research_only": true,
  "authority": {
    "training_allowed": false,
    "promotion_allowed": false,
    "submission_allowed": false
  }
}
```

既存の `DeckAssetInput`／`QualifiedDeckAsset` と submission-deck qualification を再利用し、CABT legality evidence は実際に完走した `DONE` game のみを受理する。

### 3. P1 policy binding

P1 package を候補 run に複製し、policy source と generated `deck.csv` を同じ package root に置く。P1 の初期 `select is None` deck read は維持するが、例外時の fallback が旧 `ROOT_DECK` を返さないよう、候補 package の deck bytes から合法な deterministic selection を生成する。P1 の固定カード優先順位はそのままの control とし、候補 deck で扱えないカードを silent に別カードへ置換しない。

初回の deck phase は「P1 policy 固定＋scratch deck」で、policy phase は「通過した deck 固定＋policy parameter/CEM」である。policy と deck を同時に変えた候補は別実験として扱い、BestKnown loop の phase contract を破らない。

### 4. 評価順序

1. static package/import/60-card/ACE/copy-cap gate
2. official CABT legality probe（両 seat、fault 0）
3. P1 candidate policy の bounded smoke
4. P1 control と同一 opponent・seat・seed の 96 局 screen
5. screen positive 候補だけを未使用 meta と独立 seed の 384 局へ送る
6. positive、fault 0、seat gap 5% 以下、opponent×seat-safe の候補だけを `cg_bestknown_loop_v1.py` の deck phaseへ接続

単一 seed の勝率、smoke の勝率、公開 pool との相関結果だけでは昇格しない。未使用 meta の freshness と split／pool manifest SHA を結果へ固定する。

## エラー処理と停止条件

- 公式 card ID、60 枚、copy cap、ACE SPEC、evolution line のどれかに違反したら候補を materialize せず reject。
- canonical hash が既知 snapshot と一致したら `source_collision` として reject。近似距離だけで self-owned と認定しない。
- CABT が `DONE` 以外、agent fault、illegal action、timeout を返したら legality／performance evidence として不採用。
- P1 policy が scratch deck で fallback または unknown-card handling に依存した場合、deck 性能の証拠にせず `policy_deck_incompatibility` として停止する。
- heavy runner は main coordinator のみが起動し、出力 root は既存 artifact を上書きしない。

## テストと証拠

- generator unit: seed 再現性、60 枚、同名 copy cap、ACE exactly-one、未知 ID、evolution mismatch、collision reject
- identity unit: manifest self-hash、deck raw/canonical hash、`parent_deck=null`、authority all false
- package contract: P1 source SHA、candidate deck binding、fallback legal selection、policy import
- runtime: official CABT smoke の status／agent status／illegal action を検査
- performance: control と candidate の matched seed、seat、opponent、split、freshness manifest を ledger で再検証

## 非目標

- root `deck.csv`、Champion、production `main.py`、submission archive の置換
- Kaggle への提出、commit、push
- 公開 policy／deck snapshot を self-owned source として再命名すること
- legality smoke を性能向上の証拠として扱うこと
