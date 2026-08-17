# P2 context CEM Campaign 2 — 2026-08-15

## 結論

signed `damaged_active_threat_attack_bonus=-6000` を初期centerにしたCEM Campaign 2は、screenでは2件のpositiveかつseat-safe eliteを得て分布更新まで進んだ。しかし、同じ2候補を独立seed・repetitions=16へ拡大した確認では、c06が `-0.4862pt`、c03が `-2.0654pt` とどちらもP2 controlを下回った。screenの正差は再現せず、CEM候補はP3/BestKnown/Championへ昇格しない。

全CABT blockは fault 0 / `DONE` で、失敗は性能ではなく再現性gateである。全て既存 `META_TRAIN` の再利用であり、`fresh_unused_meta_confirmation=BLOCKED_NO_LOCAL_UNUSED_META`。P1 `cg-lethal-target-v1`＋root deck、BestKnown、Champion、production、submission packageは不変である。

## Campaign 2

設定は population 8、elite 2、2 generations、各候補2 repetition、workers 12、base seed `490260000`（generationごとに`100003`加算）、初期scale 8000。各generationは候補384局＋共有control 48局の432局である。

| generation | gate | 結果 |
|---:|---|---|
| 0 | positiveかつseat-safe eliteを2件要求 | 0件。center保持。上位safe候補は1件のみ（+3.1192pt） |
| 1 | 同上 | 2件で更新。c06 `+13.0584pt`、c03 `+5.9510pt` |

generation-1の更新後centerは次のとおり。

```text
damaged_active_threat_attack_bonus = -3812
full_bench_attack_bonus             = -12060
near_lethal_attack_bonus            = -8836
threat_energy_attack_bonus          = -9362
```

各generationは432/432局、status `DONE`、fault 0 だった。Campaign summary SHAは `b97548bfba963ed9333b87357dd11f2759ad0b8e0ecefad51e5f08bbf633283b`、complete manifest SHAは `f0abb63e1026b6f46526beec81d8aee6b92aa6eb3fc24e915217522319d6c95c`。

## 独立確認

同じMETA_TRAIN・同じP2 controlに対し、各candidate/control 384局（12 opponent、両seat、16 repetitions）の計768局を別base seedで実行した。

| candidate | config | candidate objective | control objective | 差 | candidate seat gap | 判定 |
|---|---|---:|---:|---:|---:|---|
| c06 | `(-6114, -8020, -12769, -15294)` | 0.1522582 | 0.1571200 | −0.4862pt | 1.8229% | `NOT_PROMOTABLE` |
| c03 | `(-1509, -16100, -4902, -3430)` | 0.1643357 | 0.1849893 | −2.0654pt | 4.4271% | `NOT_PROMOTABLE` |

両確認とも768/768局、status `DONE`、fault 0。c06 summary / manifest SHAは `759948e8137b9e171e4ae3120241d109520ee9396a86b528c7c6fd7ba2522402` / `2e67a49012abeda422fc20b3062edcddf5cc50c93de07d886b19e438a71ee321`、c03は `48ca50f3222fb48e3756d2d8dede72af08152f72a288354828f38d41b3be9479` / `bb8650f6e1df0ae692895b605ed086bdbfd68e069c7d4a9db676d6f594eaf258`。

## 解釈と次のgate

- screen上位だけを次centerへ流すと、同じmetaでも独立seedで負差へ反転する。CEMのelite gateは必要条件であって十分条件ではない。
- `−6000` parent近傍の追加blind retry、P3昇格、deck mutation、Champion変更、提出は行わない。
- 新しいpolicy surfaceまたは、canonical policyとdeck hashの両方が未使用でsmoke-readyなmeta sourceが得られるまで、再利用metaのpositiveは探索分布の診断に限定する。
- 次回のCEMは、screen後に少なくとも2 blockの独立再評価を先に行い、両blockでpositive・seat-safeの候補だけをupdate対象にする。

なお、同時に実装したpublic decklist holdoutのfreshness gateは、sourceのcanonical hashとCABT ledgerの`deck.csv` byte hashが異なる場合も検出するよう修正した。この再監査ではpublic-onlyかつ未使用のdecklistは0件であり、decklist proxyを実行してgateを迂回することはしなかった。
