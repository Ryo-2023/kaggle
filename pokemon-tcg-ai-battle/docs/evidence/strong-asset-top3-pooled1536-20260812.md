# Strong Asset top-3 pooled confirmation — 1,536局

## 結論

top-3 native deck+agent pair を独立 seed block 4本で各384局、合計1,536局/assetへ拡張した。全4,608局が `DONE`、fault 0 である。pooled point estimate は `tomatomato_archaludon` が首位だが、Luciferとの差は4勝、plamenとの差は5勝だけで、BestKnownの絶対的な優劣を確定するほど大きくない。

| 順位（点推定） | native pair | W/D/L/F | score rate | seat0 | seat1 |
|---:|---|---:|---:|---:|---:|
| 1 | `tomatomato_archaludon` | 1107/0/429/0 | 72.0703% | 561/768 | 546/768 |
| 2 | `lucifer19_battlecore` | 1103/0/433/0 | 71.8099% | 554/768 | 549/768 |
| 3 | `plamen06_steel` | 1102/0/434/0 | 71.7448% | 567/768 | 535/768 |

tomato は Lucifer に +4勝（+0.2604pt）、plamen に +5勝（+0.3255pt）。seed blockごとには首位が変動した（block1 tomato、block2 Lucifer、block3 tomato、block4 Lucifer）。従って `tomatomato_archaludon` を暫定 `EvaluationBestKnown` とするが、他2 pair との差は「現在の点推定」であって、強さの確定証明ではない。

## 契約と集計

- 4 blocks × 3 assets × 384 games = 4,608 rows
- assetごと 1,536 games、seatごと 768、24 opponents
- 全 row `DONE`、fault 0、draw 0、self-play 0
- evaluator implementation SHA: `ae476cc72ac4efcf080dff118b1c4ef15268edf8e1d22b9b04cb432d48f9a797`
- block1〜3 は `block_id=asset-ranking-primary` で game_id が block間重複する（seed base 9,000,000 / 9,100,000 / 9,200,000）。pooled集計時は seed/block を分離し、単純な unique game_id 集計をしない。
- block4 は `block_id=asset-ranking-top3-block4`（seed base 9,300,000）で独立 game_id を持つ。
- pooled値は各 block の outcomes を asset/seatへ集計したもの。

## 一次 artifact と SHA-256

| block | ranking | ledger | summary | manifest |
|---|---|---|---|---|
| 1 | `58df60b5c3ace39fb827ede3adf229c2d3d626e14b9dd685dda0d18506f5690b` | `6ecf59ef0d0248d48f3d3fb3f37292229ceab867f2ca7158bdd73812a12f5d73` | `eb56523d2090c81bd9b107315c49310d4cfe824f9cf77a2bae0bbcf08823417f` | `127e61f54fbd8753467d07ef3d6e2fd3e6f7703768ab74f1c41b65c22562576` |
| 2 | `776f499598d771af10bfcdec0b10e8578aa347d114b122099725c5ce38dc163e` | `338120c96c14d789f2778d17975621924bc4e34d385332e4f3a3e3f00730e658` | `d3b324f82db8fc664da0b5183e19140777f10f2f9ea0b504d92fc0d34aadb974` | `127e61f54fbd8753467d07ef3d6e2fd3e6f7703768ab74f1c41b65c22562576` |
| 3 | `e8ea484359d9085cdd2003c2877672f5245f9bb0fc8b1945148f141ab031acc7` | `c25f1d7754c1b2d851fa5fea345026264ea53a92d01f16221774404065c3352e` | `294c4feed29a84f6f49d50f833391559f66db41996b14f9ac9dcaed04e5b575d` | `127e61f54fbd8753467d07ef3d6e2fd3e6f7703768ab74f1c41b65c22562576` |
| 4 | `27d665871f2bad82dc9877a9dbd5fea51767caf9c5b28ad9b4804138fec01cc5` | `84da3af844423958e4203675b4ee3988ebf8005138f707038aaf884aac454ed8` | `4bc09a27dcb46ecb5225822500c3b4d4c909f57c8367e43e1f8815b938c384c7` | `4ec17e738cba550b9fc94948ac95e3b3ec667937f48877210476471421808f20` |

## BestKnownの扱い

この結果で確定できるのは native evaluation の暫定順位だけである。

- `EvaluationBestKnown`: 現時点の点推定では `tomatomato_archaludon`
- `TrainingEligibleBestKnown`: permission-qualified training source の audit と frozen artifact 突合が必要
- `SubmissionEligibleBestKnown`: native pool は local-eval-only であり、package closure と submission authority を別途満たさない限り未確定
- `GlobalBestKnown`: 未測定の smoke-ready 5 assets（kinoshita / ozawa metal / tientrum / water_box / waterbox v3）と smoke=false R7 が残るため未確定

次の fine-tune は tomato native pair を primary、Lucifer/plamenを control として開始し、改善候補が tomato を 96→384→768→1536局の同一 protocol で超えることを promotion 条件とする。既存の Lucifer hard-label/outcome-weighted BC の同型 sweep は再開しない。

