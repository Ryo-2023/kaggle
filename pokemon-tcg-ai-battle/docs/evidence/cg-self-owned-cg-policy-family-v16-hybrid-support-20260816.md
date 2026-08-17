# self-owned CG policy family v16 hybrid-support / P1 CEM

## 結論

公式カードIDだけから生成した self-owned deck＋P1 policy の4候補を、P1固定CEMの対戦相手sourceとして接続した。source生成、sealed promotion、fault-free runtime smoke、META split、CEM本体はすべて完走したが、P1を上回るcandidateは得られなかった。P1中心、BestKnown、Champion、提出物は変更していない。

## source生成と境界

- source epoch: `self_owned_cg_policy_family_v16_hybrid_support_20260816`
- deck spec: [`self_owned_cg_deck_spec_v9_hybrid_support.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/configs/meta_specialist/self_owned_cg_deck_spec_v9_hybrid_support.json)、SHA-256 `7165a812050b6556d2ff9381dda927485e6774dc8c8090cdfad739c79964697d`
- policy plan: [`self_owned_cg_policy_family_v16_hybrid_support.json`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/configs/meta_specialist/self_owned_cg_policy_family_v16_hybrid_support.json)、SHA-256 `289f37211e8fbad6f7aee9d579c08f3f6e6559522e0ec40c132405a5afebbccf`
- staged batch: `4 source / 4 deck / 4 policy`、pool SHA `3c3cd07e9395177034656b7ecc2cf4e019662566dfdb7172652c516834aca812`
- promoted batch: [`runs/cg-self-owned-cg-policy-family-v16-hybrid-support-20260816/promoted`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/cg-self-owned-cg-policy-family-v16-hybrid-support-20260816/promoted)、pool SHA `eb13f0848da593d85266902ed93fb596c2b1609ffc7a4c86b52f3e7f6dfcaae4`
- `fresh_meta.json` SHA `494ff6584d164b86217990a9b8fa2f5aba2e17827ddb69679d61830a6502ad70`
- split SHA `3a32fd100ebbdbcc43001fa319ec9b9e096fd335618b83a1ee9fdc33a57dc8fb`
- authorityは `training/promotion/submission/longrun=false`、用途は local evaluation only。

候補は balanced / lethal / setup / tempo の4 recipeで、各candidateは公式カードIDとローカルP1 policy parameter overrideだけから生成した。公開kernelの実行・deckコピー・外部データ取得は行っていない。

## runtime smoke

self-owned sourceをsubjectにする独立source smoke runnerは、v16候補だけでなく既存v15 source対official_randomでも native `buffer full. capacity:7` で失敗したため、candidate rejectionの根拠にはしなかった。CEMで必要な向き（P1 subject、sealed v16 source opponent）で [`cg-self-owned-cg-policy-family-v16-hybrid-support-20260816-historical-smoke`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/cg-self-owned-cg-policy-family-v16-hybrid-support-20260816-historical-smoke) を実行し、8/8 `DONE`、fault 0、W4-L4だった。

- smoke summary SHA `dc9c2ed511cee1b725a5621505899bd5ddd3664ef9e156e6b4fa151b140fd327`
- evaluator SHA `b1c1eefa8240d724a85228d4e87e93b43bf974a23e081c38706222e1a2e41c08`

## P1固定CEM

実行root: [`runs/cg-p1-cem-self-owned-cg-policy-family-v16-hybrid-support-20260816`](/home/bfe-lab-ono/kaggle/pokemon-tcg-ai-battle/runs/cg-p1-cem-self-owned-cg-policy-family-v16-hybrid-support-20260816)

- generation: 1
- population: 8
- META_TRAIN: balanced / lethal の2 source（72 screen games、24 reevaluation games）
- META_DEV / META_FINAL: splitには存在するが、search・update中は未読
- screen結果: 全候補 fault 0。deltaは `-0.375`〜`0.0`、最大でもP1同率。
- elite reevaluation: `c01` は repeat delta `-0.25, 0.0`、mean `-0.125`、`seat_safe=false`; `c06` は `0.0, 0.0`、mean `0.0`、`seat_safe=false`。
- selection: `incumbent-center` × 2、new centerはP1設定と同一。

主要artifact SHA:

- campaign manifest `0923d66c8b780ec639739726002b3d8edfe2591858afc0d71b3285425c787c37`
- generation manifest `119d991291aee555975261d602f0452b34bfd6b22fc0c010b9b7348c6c69135c`
- results `87e524fcdbf44f3ed7df91a289b484d07fa746b5039e794c98a51a0715d2764b`

## 判定と次の条件

判定は `SOURCE_GENERATION_PASS / PROMOTION_PASS / RUNTIME_SMOKE_PASS / CEM_NO_UPDATE / BESTKNOWN_UNCHANGED`。この4候補を同じseed・同じ4点でblind retryしない。次は、official-card-onlyで deck archetype と policy family の両方を変えた、より低相関な新source batchを作り、最低でもMETA_TRAINのsource数を増やしてからP1→CEM→fresh validationへ戻す。v16のDEV/FINALは今回の採否判断には使っていない。

