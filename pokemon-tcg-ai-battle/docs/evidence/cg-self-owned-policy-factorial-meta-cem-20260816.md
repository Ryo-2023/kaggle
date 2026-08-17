# self-owned deck × policy factorial source / P1 CEM（2026-08-16）

## 結論

公式カードCSVだけから新しいdeck recipeを生成し、各deckへP1のbounded parameter configを一つずつ結合する `self-owned deck × policy factorial` source generation method を実装・実測した。8件のsourceはdeck canonical SHA・policy SHAとも相互に一意で、32局のbounded runtime smokeを全て `DONE`・fault 0で通過し、fresh meta batchとしてpromoteできた。新poolに対するP1固定CEMはscreen 216局、独立再評価144局を全て `DONE`・fault 0で完走したが、独立 positive かつ seat-safe／opponent×seat-safeを満たす候補は0件だった。P1 center、BestKnown、Champion、production、submission、root `deck.csv`は変更していない。

## 生成方法

- plan: `configs/meta_specialist/self_owned_cg_policy_factorial_v1.json`（SHA `b5c6bf46c01c4b5488fba6204f209468bc041cb8e0955dd913283080abf2a951`）
- generator: `scripts/generate_self_owned_cg_policy_meta_v1.py`（SHA `6703227954a647937a012eb593fb699d21feab0f9333386de506ff40fd081798`）
- split builder: `scripts/build_self_owned_cg_policy_factorial_split_v1.py`（SHA `fed5ea82f29f6f0c0c03ef8482ff3aa6496113b4408f807cb67a97446356a8a9`）
- official card database: `data/raw/EN_Card_Data.csv`（SHA `a0ea63cf7adcb65d35436ce0eb390de6e2e35654a7c67c065a45f4abaa00f373`）
- immutable P1 parent policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- deck recipe: self-owned v2／v3 role specs、seed namespace `self-owned-cg-factorial-v1-20260816`
- policy surface: P1の15 integer knob。lethal／attack／setup／retreat／ability／conservative／mixedの8 configを各deckへ1対1で結合した。

最初の試行 `runs/cg-self-owned-cg-policy-factorial-v1-20260816/` は、spec v1がcanonical deckをseedで変えないため公開canonical hash collisionで2件目にfailした。失敗artifactは削除せず保全し、spec v2／v3のみへ変更したretry1を採用した。これは性能結果ではない。

retry1の生成rootは `runs/cg-self-owned-cg-policy-factorial-v1-20260816-retry1/`（factorial manifest SHA `b665fd3e10a0394acad9b6346cd242d6a1d448202b243c86698d40acb07a3797`）。8/8 deck、8/8 policy、canonical deck SHA 8件相互distinct、policy SHA 8件相互distinct、`parent_deck=null`、`public_parent_read=false`、authority全falseである。staged pool manifest SHAは `fe5ff9269e34f5e943df427706eebba3712b4dcf92ccdb614ba7609a4d39a60c`、batch manifest SHAは `33ce59baa2ba4337c74b02ab4e7da5d1e12a64ef76d23e41a7645e82a50ff9ce`。

## smoke / promotion / split

source packageをP1 controlと `aman_crustleaware_fighting` へ両seat各1局、公式CLI起動で評価した。stdinからのmultiprocessing起動を試した最初のsummaryはspawnが`<stdin>`を読めず32局FAULTになったため採用せず、CLI起動の別rootを正とした。

- smoke root: `runs/cg-self-owned-cg-policy-factorial-v1-20260816-smoke-v2/`
- smoke summary SHA: `21f5c8be0d9e3cfb2e4845324e519a9972514e4ed370d03dda47307cb32919a5`
- result: 32/32 `DONE`、fault 0。smoke deltaは小標本のruntime診断であり性能昇格根拠ではない。
- promoted root: `runs/cg-self-owned-cg-policy-factorial-v1-20260816-promoted/`
- promoted pool SHA: `505e77becc5b342db958f9fbe08ec967f3c9c3252c5de5e1fc1f2336504c7911a`
- promoted fresh meta SHA: `bf4db869e61440a4ae9ab409a60a876f0b987acb6f33335f31d4085049768f3a`
- meta manifest SHA: `04e9f397fa250f225043350d258a28e637152175dd3b6160abec967fd0f4efb5`
- split SHA: `0bc8a3462cb6a83c4c4277808c80bfe641349f6636be4d997c83f0f8705d1f98`
- split: `META_TRAIN=6 / META_DEV=1 / META_FINAL=1`。source verificationと`load_weekend_split(..., verify_sources=True)`はPASS。DEV／FINALはCEM中未読である。

## P1 fixed CEM

- root: `runs/cg-self-owned-cg-policy-factorial-cem-v1-20260816/`
- campaign seed `2026084611`
- population／elite `8／2`、generation `1`
- `META_TRAIN_ALL`、screen各candidate/control 2 games × opponent × seat、独立再評価2 block・各2 games × opponent × seat
- evaluator SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`
- screen: 216/216 `DONE`・fault 0
- independent re-evaluation: 144/144 `DONE`・fault 0
- campaign manifest SHA `7c7d83b03e0e171cd53870621bc8a9b517f300e91228eb5b9b5dc2e501f1de40`
- generation manifest SHA `295a374972741523c5babd53a2905778f1bbbcc6c4f28a976d054cf7b441f545`
- results SHA `8e391af356ee7a4930e31c2ec73c1d1ddca5f12fea99ffa59cb461e1fd24eb43`

screen上位は c04 `+29.17pt`、c06 `+25.00pt`だった。独立で再評価された候補は次の通り。

| candidate | independent result | gate |
|---|---:|---|
| `cg-p1-cem-g00-c04-42be502e56b4` | 17/48 vs control 18/48、`−2.08pt` | fail |
| `cg-p1-cem-g00-c06-285b0f5c1e36` | 26/48 vs control 18/48、`+16.67pt` | opponent×seat-safe fail |

c06は全体ではpositiveだが、opponentごとのseat rateが例えば `0.50/0.75`、`0.75/0.50`、`0.75/0.25`のように揺れ、risk-aware gateでvalidにならない。選定ラベルは `risk_aware_independent_train96_x2_valid_candidates_below_elite_count_preserve_center`、new centerはP1 default configと同一、valid eliteは0件である。

## 判定 / 次の再開条件

判定は `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。このfactorial poolはCEM性能使用済みなのでblind retryしない。新しい生成方法は「同じP1既定policyのdeck複製」よりは相関を下げ、c06の一時的なpositiveを発見できたが、現budgetではseat／opponent cellの識別力が不足している。

次はこのpoolをDEV／FINALへ戻さず、別の未性能使用policy lineageまたは相関の低い複数runtime-safe familyを、smoke候補と性能holdoutを分離して生成する。全候補は `legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` の順に進め、全ゲート通過候補だけを `cg_bestknown_loop_v1.py` の `P1 → policy CEM → fresh validation → deck → policy` へ渡す。

## 固定状態

- current branch／HEAD: `feature/belief-guided-search`／`30cade0e5d349d6ea545f019fc411e9d53288f16`
- P1 policy SHA: `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`
- root deck SHA: `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`
- BestKnown／Champion／production／submission／commit／push: 不変・未実施
- active heavy process: なし
