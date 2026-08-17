# cg internal Comfey adapter epoch19／P1 CEM（2026-08-16）

## 結論

epoch17で未使用の安全なGit履歴sourceが尽きたことを確認したため、epoch16bで得た唯一の新規Comfey snapshotを親に、既存のbehavior factorialとは別の `self-owned-meta-adapter-v1` recipe（同一option type内の決定的action perturbation）で4件のpolicy variantを生成した。4件とも静的封印と2局のbounded smokeをfault 0で通過し、P1＋root deck固定のepoch19 CEMへ接続した。

epoch19 CEMはscreen 72局、独立再評価24局を全て `DONE`・fault 0で完走した。しかしscreen上位の改善は再評価で安定せず、seat-safe／opponent-seat-safe条件を満たす候補はなかった。`incumbent-center`×2を選択し、P1 center、BestKnown、Champion、production、submission、`cg_bestknown_loop_v1.py`は不変である。META_DEV／META_FINALは候補選定中に読んでいない。

この4件は同一Comfey parentからの相関した派生であり、独立作者lineageや公開/native性能の証拠ではない。生成sourceの研究上の有用性は確認できたが、BestKnown更新候補には昇格しない。

## source生成

- epoch17 broad remote-history intake: `runs/cg-internal-historical-epoch17-depth32-20260816/`
  - accepted `0`、rejected `615`
  - `artifact_identity_reused`、`consumed_ledger_reused`、`source_commit_reused`等でfail-closed
  - intake report SHA: `0d837a29f6ed41a1843aca25099c5178fdcb3da15f4a1a73837dfa0dafaad950`
- epoch16bのfresh parent: `internal_nihei-comfey-library-out_24ce278aa99f`
  - base policy SHA: `1cdf325ccfc7f9723c62d34f402c9a7daed6e672b7f3cff38cf979e2215928d4`
  - canonical deck SHA: `18d915fa45986691f6dbcd489e399fa1025b412aed1c814cbc098143185370c4`
  - deck bytes SHA: `27ae00f17af0b187033e7e558a041e139ac63d81ed84ad3150a000796e443157`
- recipe: `scripts/self_owned_adapter_v1.py`／`scripts/seal_self_owned_adapter_meta_v1.py`
- generated variants: perturbation rates `0.04 / 0.08 / 0.12 / 0.18`
  - p04 policy SHA `046575ca928d22152548279789ab4d4763ffefdad7e2a2a9379c83cb7e36c9a0`
  - p08 policy SHA `7862520ae206af19c6ff442d94e8dd55dc862f986f484748fa68a821a92ed7f6`
  - p12 policy SHA `4adff239647170d61f308fce4d760849e7fe638cc1d7623a6ea4e8bc6784ad04`
  - p18 policy SHA `50fe0ec2831c3ce49b9f9587dd89578b4c20980edcbee56bb97d43b113aea66a`

生成rootは `runs/cg-internal-comfey-adapter-epoch19-20260816/`。各variantの2局 smokeは `DONE`・fault 0（p04 `2W-0L`、p08 `1W-1L`、p12 `2W-0L`、p18 `2W-0L`）で、authorityは training／promotion／longrun／submission 全て `false`。

4つのpromoted rootを統合したpoolは `runs/cg-internal-comfey-adapter-epoch19-merged-20260816-v2/` である。

- pool SHA: `8c88578a7c7558f6c718aa767cd824132f1508729172041ccee735c278a0d071`
- fresh meta SHA: `7ceee2cf9867e1c5af13ea97b1b911dfe58963253b7f1bde6a7c5c1f571ccc5a`
- reference IDs: `p04 / p08 / p12 / p18`
- split: `META_TRAIN=p04,p08`、`META_DEV=p12`、`META_FINAL=p18`
- split SHA: `7017036dd9b2bfe738ff916018867458efcb8f5b3bd0ea7e357f616aea2cde75`

## P1固定CEM

run rootは `runs/cg-p1-cem-internal-comfey-adapter-epoch19-20260816/`。P1 policy SHAは `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9`、root deck SHAは `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19`、evaluator SHAは `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`。

- seed `2026081608`、population／elite `8／2`、1 generation
- screen: `72/72 DONE`、fault `0`
- independent re-evaluation: `24/24 DONE`、fault `0`
- campaign manifest SHA: `c5f3b2488d9871ec754e06a32ac82cc2c83d9a231fa6fd10b07aaa52578fb890`
- generation manifest SHA: `eae116b1897cb75d22ee1c119162e5c516280cb2ae1fb7305f6d64abda8d8aa5`
- results SHA: `197c53b68248ce3d0db841e3a365429e8b5a2563254f2b0172fb67b3d084fe1a`
- evaluation summary SHA: `1f727bd42a73d772a41de79b68e496437545b8b11c342831ada2f7309660d27b`
- reevaluation summary SHA: `f119603350af82445dba7ee46cb9eee4cb7786be4f3cf9ad04d3c26faab33c2b`
- reevaluation result: 24局、13W-11L、fault 0

screen上位の挙動は次のとおり。

| candidate | screen差 | 独立TRAIN差 | 再評価差 | 判定 |
|---|---:|---:|---:|---|
| `c03-b642fdf6e516` | +25.0pt | +25.0pt | +50.0pt / 0.0pt | seat-safe不成立、更新不可 |
| `c07-1e82e2d15d6e` | +25.0pt | −12.5pt | 0.0pt / −25.0pt | positive不成立 |

選抜結果は `risk_aware_independent_train96_x2_positive_delta_gate_preserve_center`、elitesは `incumbent-center`×2、new centerはP1 centerと同一である。したがってこのepochは `SOURCE_GENERATION_PASS / BOUNDED_SMOKE_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED` と判定する。

## 再開条件

epoch19 pool、seed、候補は性能使用済みとしてblind retryしない。次は同一Comfey parentのrate違いを追加するのではなく、相関を下げる別のpermission済みmeta sourceまたは明示的に異なる生成recipeを新epochで作る。`legality → static safety → bounded fault0 → TRAIN-only → independent positive → seat-safe/opponent-seat-safe → unused DEV → unused FINAL`を満たす候補だけをBestKnown loopへ接続する。root deckはself-ownedとはまだ呼ばず、現行ラベルは self-authored P1 policy＋common/public root deck のままである。
