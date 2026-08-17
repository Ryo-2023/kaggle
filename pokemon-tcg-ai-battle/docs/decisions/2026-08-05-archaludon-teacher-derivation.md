# 判断記録: archaludon teacher の派生資格 (2026-08-05)

`FoundationInitProvenanceV1` の `TeacherRefV1.decision_ref` が指す判断記録である。
この記録なしに `derivation_boundary=derivation_qualified` を設定してはならない。

## 判断

次の 3 asset を archaludon レーンの BC 蒸留 teacher として **`derivation_qualified`** とする。

| teacher_id | canonical deck | 出典 Kaggle kernel |
|---|---|---|
| `tomatomato_archaludon` | 系統A | `masamikobayashi/a-sample-archaludon-75-wr-vs-my-1300-starmie` |
| `lucifer19_battlecore` | 系統B | `lucifer19/battlecore-compact-agent` |
| `plamen06_steel` | 系統B | `plamen06/pokemon-steel` |

**判断者**: ユーザー (2026-08-05)。

## 判断の対象と、対象でないもの

- **対象**: これら 3 agent の**挙動を蒸留した重み**を、提出 bundle に含まれる方策の
  初期値 θ0 として使うこと。
- **対象でない**: これらの `main.py` そのもの、または改変したものを提出 bundle へ
  含めること。**これは引き続き禁止**であり、`usage_boundary=local_eval_only` のまま
  package test で機械検査する (正典 §22)。
- **対象でない**: deck.csv を提出 bundle へ含めてよいかの判断。これは別途
  `SeedAssetManifest` の qualification で扱う (設計 §10 の未確定事項)。

## 提起した懸念 (記録として残す)

各 `SOURCE.md` は次を明記している。

> This copy was pulled solely for **local, offline evaluation** ...
> It is not redistributed, not published, and never submitted as-is to the competition.

「as-is」が agent のコードを指すのか、そこから派生した成果物までを含むのかは
文面から一意に定まらない。正典 §5 は「派生 checkpoint の扱いも source policy と
競技規約を qualification で判定する」と定めており、この判断はその qualification に
当たる。エージェントはこの曖昧さをユーザーへ提起し、ユーザーが上記のとおり判断した。

## 実装上の帰結

- `qualify_pooled_teacher_v1(instance, derivation_boundary=DERIVATION_QUALIFIED_V1,
  decision_ref="docs/decisions/2026-08-05-archaludon-teacher-derivation.md")`
- 蒸留した checkpoint の `metadata.foundation_init.teachers` に上記 3 件の
  `policy_hash` が記録され、後から追跡できる。
- 本判断は archaludon レーンの 3 asset に限定する。他 asset へ自動的に拡張しない。

## 再検討の条件

- Kaggle Rules または各 kernel の licence 表示が変わった場合
- 元 kernel の作者から明示的な制限があった場合
- 提出前の最終確認で、bundle に `local_eval_only` asset が混入していた場合

---

## 追記: 内製 teacher の派生資格 (2026-08-05)

3 レーン並列化に伴い、次の**チーム内製**エージェントを teacher として
`derivation_qualified` / `allowed_usages=("training-local",)` とする。

| teacher_id | レーン | 出所 | 対プール実測 |
|---|---|---|---|
| `ozawa_grimmsnarl_v2` | `grimmsnarl_froslass_munkidori` | `origin/agents/ozawa-grimmsnarl-rule+RL` | 76.4% [0.65,0.85] n=72 |
| `ozawa_rocket_v2` | `rocket_mewtwo_spidops` | `origin/agents/ozawa-rocket-rule+RL` | 72.2% [0.61,0.81] n=72 |
| `nihei_alakazam` | `alakazam` | `origin/agents/nihei-alakazam` | **89.6% [0.78,0.95] n=48** |

公開 Kaggle kernel 由来の 3 体と異なり、これらはチーム自身の成果物であるため
派生物の作成に外部 licence の制約は無い。ただし `opponents/` へ取り込んだコピーの
`SOURCE.md` は `local_eval_only` を宣言しており、**agent コードそのものを提出
bundle へ入れないこと**は同じく維持する。蒸留した重みは別物として扱う。

`submission-bundle` は付与しない。
