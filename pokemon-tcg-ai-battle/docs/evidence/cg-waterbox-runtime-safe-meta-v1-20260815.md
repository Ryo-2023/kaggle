# Water Box runtime-safe meta v1 — 2026-08-15

## 判定

`SOURCE_GENERATION_PASS / PERFORMANCE_PROMOTION_FAIL`。Water Box/Starmie の高LB探索系を、そのまま対戦相手へ流さず、探索停止・極小予算・周期ゲートの allow-list transform として封印できた。新規 hash 群の TRAIN smoke は fault 0 だったが、P1 fixed CEM の独立 lower-tail / seat gate を満たさず、P1 center を保持した。

## 生成物

- base: `opponents/waterbox_search_v3`、source branch `feature/water-box-search`、commit label `0ed1995`
- final root: `runs/cg-waterbox-runtime-safe-meta-20260815-g/`
- 12 policy（TRAIN 8 / DEV 2 / FINAL 2）、全件 static findings 0、compile PASS、exact 60 cards、`local_eval_only`、authority 全 false
- pool SHA: `1179ac28d253f892be3acf651c9f802575794b74f98e156a83a67006c76281ed`
- fresh meta SHA: `54c84a50f65f834ae2a92f5027b106b6134c0c7c8dbfed7904cc7031ff4f4be5`
- split SHA: `9acebe5e9431e3a7ad9770377242c670097f9e91d7c870dbe201ba475e2553b2`

初回 probe `runs/cg-waterbox-runtime-safe-meta-20260815-e` は周期 variant が重いことを測るための smoke に限定し、CEMへは再利用していない。`-f` は split 再配置だけで既使用 hash を再利用するため、fresh source として採用していない。`-g` は予算帯を 0.005 / 0.015 / 0.03 / 0.07 秒へ変更し、過去 probe の hash を scan で拒否した上で新規 seal した。

## 評価

P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` に対し、seed `20260911` の TRAIN smoke（8 refs × 両seat）は `4W-0D-12L`、16/16 DONE、fault 0、runtime 46.64 秒。

P1 fixed CEM（seed `20260912`、generation 0、population 4、elite 1、TRAIN 全8 refs、screen 160局、independent re-eval 2 block × 1局/相手/seat）は全て DONE・fault 0。screen top は candidate c02 `+21.875pt`（candidate 0.25 vs control 0.03125）だったが、独立 blocks は `+6.25pt` と `0pt`、mean `+3.125pt`、worst `0pt`。candidate seat-safe は false（2 block の一方で seat collapse）となり、`independent_reeval_x2_positive_delta_gate_preserve_center` で P1 を保持した。DEV/FINAL は未使用のまま分離している。

この source family は「新しい deck/policy lineage を安全な opponent pool として生成し、CEMへ接続する」方法としては成立したが、現サンプルでは性能昇格根拠にならない。Water Box deckをself-owned提出 deckへ転用せず、P1、root deck、BestKnown、Champion、production、submission は不変とする。
