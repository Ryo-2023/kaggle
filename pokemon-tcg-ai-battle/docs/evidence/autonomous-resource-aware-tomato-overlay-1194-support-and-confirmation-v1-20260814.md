# Tomato native Colress line: weighted→common24→384（2026-08-14）

## 結論

Tomato native parent の Supporter 枠を Colress’s Tenacity（1194）へ1枚置換する2候補を比較した。`1182 Boss’s Orders→1194` は META_TRAIN weighted48 で +17.490pt、common24-96 で +3.125pt、fresh 384 confirmation で **+6.510pt** を維持した。`1227 Lillie’s Determination→1194` は weighted48 では +23.221pt だったが common24-96 で −2.083pt に反転したため停止する。

`1182→1194` は性能上の provisional candidate-only / high-priority follow-up であり、BestKnown昇格・submission・Champion変更・longrun起動を意味しない。authority は全 artifact で execution/training/promotion/submission/longrun=false のまま。

## 結果

| stage | parent | 1182→1194 | 1227→1194 |
|---|---:|---:|---:|
| META_TRAIN weighted48 | 25-0-23, 0.534296385 | 34-0-14, 0.709196826 (**+17.490pt**) | 37-0-11, 0.766506229 (**+23.221pt**) |
| common24-96 | 64-0-32, 66.667% | 67-0-29, 69.792% (**+3.125pt**) | 67-0-29, 69.792% vs 69-0-27, 71.875% (**−2.083pt**) |
| fresh 384 | 256-0-128, 66.667% | 281-0-103, 73.177% (**+6.510pt**) | 未実施（common24負） |

## 一次 artifact SHA-256

### weighted48

- root: `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-support-weighted-v1-20260814/`
- manifest `b9abfd5a916a04da6f1e2c77b49e2dec24ec6e0e8c3e563bd29be96ea6e143a4`
- weighted summary `4be32a5acf246061e13320a492df6b3d3f40a2a4f8aa4f4a2ec29e945a167b8c`
- wrapper `8d85fcfd6bb66ce0aced5741fc6cfae6c5bc34259c21a2b9d113a252f2cef125`
- candidate deck SHAs: 1182→1194 `4eb9c6f911b63a9f80aaaf8ec6a4bc0f9d67e701f03105f3ca6f9981a28de4c0`; 1227→1194 `098ef8d3e6b418b238ba82379e8b9eba3d40636b753241348d0180d2968ade78`

### common24-96

- 1182 root: `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-common24-1182-v1-20260814/`
- 1182 summary `d08b64900b3882d531e6b1532909b771dd682277c6ae1964317c191b20b16ddd`; MD `a4293fdc27a9dfcf8bdb96fcaec4b379eb59316981115afe21229c9f982bf1d9`; final `bb4bfc3bc4fc8b6ff35a15356bb32004c4e600e7c6646b8e5a27ecf3c2b8c9c3`
- 1227 root: `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-common24-1227-v1-20260814/`
- 1227 summary `bc856cbda275a4b692339f7e6a31d30d2ed572386ba09ce80aebfffc2632e65a`; MD `d2273d97f9b5b3e47411168705051df21d85c6ae868938d26cc9d86bc4170a80`; final `5cf326b05d9dec5a7a245dcadbcfcdbb70bdd2168832e0bcac9b5abf3208f36a`
- common24 wrapper `d621d0621d4c4fe52f52e07aed03b9bf81f0f3edf42532058daf238a20684712`

### fresh 384 confirmation

- root: `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-confirmation384-1182-v1-20260814/`
- candidate multiset `984f9ee374cd132acbb7dc21560fcdbb29d720009cfa9b293e5ea8bd4cfd8fba`; deck `4eb9c6f911b63a9f80aaaf8ec6a4bc0f9d67e701f03105f3ca6f9981a28de4c0`
- confirmation summary JSON `a1eb3a52e11ad105cde2fa6db0cba310ff5788d42abfce8244f5946df1b418f0`; MD `5527f2429cdb37f91c521be1351fd9e00c4b14240f827027471ef7d2a97e8e49`; final `1ab573cc0c9d34697e80e89133e3b59e999828a91b59046e6f0ec04e0984c68b`
- evaluation ledger `c531e5a57e903470e8d7391895885efaf21842f069db648b62892ce513c9be4d`; evaluation manifest `211e888309e333ea637c8ecb8da92ce9757acf898575c7051e26a5053cf07f91`
- warmup telemetry `32613b854d772f43bb921efbb53d19a07e28377606eddc9d1aafd8655d656992`; wrapper `fa3401b63152a4a8dda50de723a20d4b435371639b52eef9cc4ba378b7c317eb`

## Integrity／resource gate

Weighted stage: all144 DONE/fault0, seat24/arm/seat, unique GID/seed and paired strata. Common24 stages: all192 DONE/fault0 per root, seat48/arm/seat, 24 opponents×4, paired keys/seed/GID valid. Confirmation: all768 DONE/fault0, 384/arm, seat192/arm/seat, 24 opponents×16, repetition0–7, paired keys/seed and GID uniqueness valid. ResourceGovernor confirmation used workers12, recycle64, warmup ramp1/2/4/8/12 all fault0, restart/kill0. No production runner or existing artifact was modified.

## Gate／次段

- `1227→1194`: common24反転により停止、384不要。
- `1182→1194`: 384 positive を記録したが、768/longrun/submission は自動起動しない。次の判断は独立 seed-disjoint confirmation または package/permission auditを明示的に行ってからとする。
- すべて research-only、authority false、candidate-only。Kaggle submission／Champion変更なし。

## 768 confirmation update

親から明示された再現性ゲートとして、同一 candidate/parent を seed-disjoint な fresh root で各768局（24×2seat×repetition16、workers12、recycle64）再評価した。candidate は **535-0-233 / 768 = 69.6615%**、parent は **533-0-235 / 768 = 69.4010%**、差分は **+0.2604pt（+2勝）** に縮小した。全1536局 DONE/fault0、seat384/arm/seat、opponent32×24、paired key/seed/GID gate、warmup ramp、resource telemetry は正常だったが、改善幅は小さく longrun／BestKnown promotion の根拠にはしない。

- 768 root: `runs/final-sprint-autonomous/resource-aware-tomato-overlay-1194-confirmation768-1182-v1-20260814/`
- confirmation summary JSON `aaf8cd27c81789d8c89c4a21f86676be714be2e3fc5a57beeb82e3d8181d09a5`
- confirmation summary MD `97a5931ee2200437ea56875195bc82a2bf25753988d7a0cfbb51264d181f3993`
- final summary `e613ada6dc43bbef4a8aecafb17c8643b07a5578daa425d0f90f760f5c8d58d5`
- evaluation ledger `615e0c95dbc372fe023fb8cc6bb26bb325905cea93c02f38f523c3130c44b2d6`
- evaluation manifest `72581792729f106d05d788d704bfcd9c61f9a8011cd0c3ec1f1728f3979f7c05`
- warmup telemetry `4544c0d95636d9132f6aa70b409a72d4a2323afe97dec3fafbe13f72ad292387`
- wrapper `67c2f2e784a80fdb22afac071a163630859a4496bfe3cb2384625b1876151b16`

この結果をもって当該 deck mutation は `candidate-only / no longrun` とする。768 の先へ自動的に進めず、package/permission と他候補の比較を別判断に委ねる。
