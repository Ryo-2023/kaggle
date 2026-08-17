# cg historical meta source / CEM 実験（2026-08-15）

## 結論

公開 source は現在 `UNVERIFIED_RULES_CONSTRAINT` のため active meta にできず、remote branch head も current pool／artifact identity と重複していた。そこで、許可済み `origin/agents/*` から **first-parent の未使用 historical snapshot** を read-only で取得する source-acquisition lane を追加した。9 snapshot（Festival、Rocket、Starmie の3系統）は実 CABT smoke へ接続できたが、P1 policy CEM の独立再評価・DEVで安定した正差を示さず、P1 は BestKnown のまま保持する。

これは native/public meta の獲得ではなく、明示的に `local_eval_only` の internal source である。得られた差を native 72%級の証拠や提出性能として扱わない。

## Historical source intake

変更した取得契約は次の通り。

- `scripts/discover_fresh_internal_meta_v1.py --history-depth N` の opt-in first-parent traversal。
- 同一 commit の root `main.py`＋`deck.csv` を読み、checkout/import/network を行わない。
- current pool、既存 artifact、consumed identity を `source_commit`／policy SHA／deck identity で照合する。
- static security scan、60枚 deck、canonical deck SHA、freshness evidence を従来の `fresh_meta.json` へ束縛する。
- 同一 batch 内の policy＋canonical deck 重複を除外する。
- `--include-ref` と `--max-candidates` で source diversity と計算量を明示的に制御する。

accepted IDs は次の9件である。

| 系統 | snapshot |
|---|---|
| Festival | `internal_nihei-festival-lead_7e1398e6ad54`, `..._dff8b4ef05af`, `..._e399eda8f533` |
| Rocket | `internal_ozawa-rocket-rule_1c2ba4dbc8a7`, `..._1fe2909c735a`, `..._900a92aee30c`, `..._b78d0ddecacc` |
| Starmie | `internal_ozawa-starmie_4ad06625ce7e`, `..._8d12411f4e59` |

sealed root は `runs/cg-historical-internal-meta-20260815-b/` で、主要 SHA は以下である。

- pool: `b09c9239c35af2a12afd52835bb8171882d8a762a1d9fb68e126d5fb30f9b071`
- fresh meta: `c261783d3dd232ace34903a0528a50f93aaaeb62c5a72c40fe6e0b159cf8a541`
- meta manifest: `e173922a36c53cb2bad48f48b460cf8532b1195ae6cd303b728f32988ef7afc4`
- split: `e4bf12e666abb50607a6977782256276c07098a82f903a64dc7c37b59665bc00`

`cg_bestknown_loop_v1.build_fresh_meta_batch_v1` と split source verification は PASS した。P1 subject の全9 reference・両 seat、18局 smoke は `DONE=18/18`、fault 0（summary SHA `2805ae7508e93e4485ff5f7771c16be2bc54dae4a02340b3f6b4224194f0c847`）だった。

## P1 policy CEM

P1 `cg-lethal-target-v1` を control／parent に固定し、historical split の `META_TRAIN` 3件（Festival／Rocket／Starmie）だけで次を実行した。

- 2 generations
- population 8、elite 2
- `--initial-scale-fraction 0.05`
- `--reeval-for-update --reeval-repeats 2 --positive-delta-gate --risk-aware-update`
- screen 216局、独立 re-evaluation 144局、generation-1 DEV 96局、合計456局
- 全 heavy block `DONE`、fault 0、illegal/timeout 0

gen0 は screen 上で正差の候補があったが、独立2 block の lower-tail は最大 `+8.33pt` に留まった。gen1 は独立 gate を満たす候補がなく、center を保持した。center の未使用 `META_DEV`（3 refs、candidate/control 各48局）は candidate `12W-0D-36L` 対 control `13W-0D-35L`、差 `−2.0833pt`、candidate seat gap 0% だった。

判定は `NOT_PROMOTABLE`。`META_FINAL` は CEM の選抜・DEV判定には使っていないが、全9件を含む18局 smoke の実行対象には含めたため、将来の fresh holdout 用には未使用ではない。DEV負差により deck phase／`cg_bestknown_loop_v1` の `POSITIVE_CONTINUE`／BestKnown・Champion・submission 変更は起動していない。

artifact は `runs/cg-historical-cem-20260815-a/`（manifest SHA `1041fcbaba6b0260e5c764ed28705a821ebface21355c8e5d5c8dd867c1b3b7f`）。gen0/gen1 results SHA はそれぞれ `3a45eb813ea074781805e632a73da3e1f0259becfdda4034ef5f3a6f7d936bff`、`287b9c29f1be2a591ca9b0d416e9f716b8a1c2b9e208615678274b533d6830e4` である。

## 判断と次の source epoch

Historical snapshot intake は、source が完全に枯渇した場合の「履歴から安全に新しい identity を得る」方法として機能した。一方、同一 branch の履歴は相関しているため、今回の CEM は **source diversity の取得可能性を確認しただけ**であり、性能上の fresh/native 転移を証明しない。

次は historical Rocket/Festival/Starmie の blind retry ではなく、次のいずれかを先に満たす。

1. permission と runtime が確認された新しい team/public source を取得する。
2. 現在とは異なる behavior family を生成する source recipe を明示し、lineage と相関を別 manifest へ固定する。
3. 新 source epoch を `fresh_meta` として seal し、P1→risk-aware CEM→fresh DEV→fresh FINAL の順で再開する。

commit、push、Champion変更、Kaggle提出は行っていない。
