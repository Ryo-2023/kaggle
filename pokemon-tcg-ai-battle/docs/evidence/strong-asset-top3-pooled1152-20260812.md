# Strong Asset top-3 pooled confirmation — 1,152局

## 結論

同一の native pair ranking protocol で、top-3（`tomatomato_archaludon`, `plamen06_steel`, `lucifer19_battlecore`）を独立 seed block 3本で各384局、合計1,152局/assetへ拡張した。3 block 全て fault 0 で、pooled 順位は `tomatomato_archaludon` > `plamen06_steel` > `lucifer19_battlecore` となった。

| 順位 | native pair | W/D/L/F | score rate | seat0 | seat1 |
|---:|---|---:|---:|---:|---:|
| 1 | `tomatomato_archaludon` | 832/0/320/0 | 72.2222% | 414/576 | 418/576 |
| 2 | `plamen06_steel` | 825/0/327/0 | 71.6146% | 422/576 | 403/576 |
| 3 | `lucifer19_battlecore` | 821/0/331/0 | 71.2674% | 408/576 | 413/576 |

tomato は plamen に +7 wins（+0.6076pt）、Lucifer に +11 wins（+0.9549pt）である。96局の screen では plamen が首位、最初の384局では tomato > plamen > Lucifer、seed block 2単独では Lucifer が首位だった。したがって、この1,152局 pooled 結果は現時点の暫定 `EvaluationBestKnown` を tomato に固定する根拠になるが、差は小さいため、training/submissionの authority や Champion変更を意味しない。

## 集計方法と重複注意

使用した一次 artifact は次の3 blockである。

1. `runs/meta-specialist-asset-ranking-top3-confirm384-20260812/`
2. `runs/meta-specialist-asset-ranking-top3-confirm384-block2-20260812/`
3. `runs/meta-specialist-asset-ranking-top3-confirm384-block3-20260812/`

各 block は `block_id=asset-ranking-primary` のまま実行されたため、block 間では `game_id` が同一で seed のみが異なる。したがって ledger を単純連結して一つの unique-game ledger として扱ってはならない。pooled 値は block ごとの outcome を asset/seat へ集計したもので、重複を block/seed 単位で識別している。

- block 1: seed base 9,000,000、1,152 rows、fault 0
- block 2: seed base 9,100,000、1,152 rows、fault 0
- block 3: seed base 9,200,000、1,152 rows、fault 0
- pooled: 3,456 rows（各 asset 1,152、各 seat 576）、全行 DONE、fault 0
- evaluator implementation SHA: `ae476cc72ac4efcf080dff118b1c4ef15268edf8e1d22b9b04cb432d48f9a797`

## block artifact SHA-256

### block 1

- ranking: `58df60b5c3ace39fb827ede3adf229c2d3d626e14b9dd685dda0d18506f5690b`
- ledger: `6ecf59ef0d0248d48f3d3fb3f37292229ceab867f2ca7158bdd73812a12f5d73`
- summary: `eb56523d2090c81bd9b107315c49310d4cfe824f9cf77a2bae0bbcf08823417f`

### block 2

- ranking: `776f499598d771af10bfcdec0b10e8578aa347d114b122099725c5ce38dc163e`
- ledger: `338120c96c14d789f2778d17975621924bc4e34d385332e4f3a3e3f00730e658`
- summary: `d3b324f82db8fc664da0b5183e19140777f10f2f9ea0b504d92fc0d34aadb974`

### block 3

- ranking: `e8ea484359d9085cdd2003c2877672f5245f9bb0fc8b1945148f141ab031acc7`
- ledger: `c25f1d7754c1b2d851fa5fea345026264ea53a92d01f16221774404065c3352e`
- summary: `294c4feed29a84f6f49d50f833391559f66db41996b14f9ac9dcaed04e5b575d`

All block manifests use the same evaluator manifest content and SHA `127e61f54fbd8753467d07ef3d6e2fd3e6f7703768ab74f1c41b65c22562576`.

## 次の判断

`tomatomato_archaludon` を現時点の native `EvaluationBestKnown` として freeze し、次の改善はこの pair を明確に超えるかで判定する。`TrainingEligibleBestKnown` と `SubmissionEligibleBestKnown` は permission/package closure の audit 結果を別途満たした場合だけ付与する。未測定の5 slow assets と smoke=false R7 は GlobalBestKnown確定前の残課題である。

