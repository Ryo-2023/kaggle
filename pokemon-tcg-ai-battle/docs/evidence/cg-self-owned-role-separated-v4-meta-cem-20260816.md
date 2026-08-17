# self-owned role-separated v4 meta source / P1 CEM（2026-08-16）

## 結論

公式カードCSVだけから、既存の公開root deckに依存しない4件のself-owned deck＋policy sourceを新しいepochとして生成し、合法性・静的安全性・bounded runtime smoke・P1固定CEMまで接続した。source生成と8局のbounded smokeは fault 0 で完了したが、screenで一時的に見えた候補は独立再評価で差0ptとなった。positive-delta gateによりP1 centerを保持し、P2／BestKnown更新、DEV／FINAL読出し、`cg_bestknown_loop_v1.py`接続、Champion／production／submission変更は行っていない。

## source生成

- deck spec: `configs/meta_specialist/self_owned_cg_deck_spec_v4.json`（SHA `eba8c6792f887b1afa8feddf99e0b3f22443c14c1753620a82cc6df627e2e976`）
- policy／factorial plan: `configs/meta_specialist/self_owned_cg_policy_factorial_v2.json`（SHA `0e6c97c73943214f315309282fbef0bd987c7db8a6e717b719c9fdcfee2f372e`）
- card input: `data/raw/EN_Card_Data.csv`（既存の公式カードDB。生成時にSHAを固定）
- source epoch: `self_owned_official_card_data_role_separated_v4_20260816`
- seed／ordinal: `20260890..20260893`／`0..3`
- recipe: role-default／role-pressure／role-setup／role-retreat。各recipeは役割候補を分けた60枚deckを生成し、P1のparameter surfaceへ異なるoverlayを結合する。
- staged root: `runs/cg-self-owned-cg-policy-factorial-v2-20260816-retry1/`
- staged batch manifest SHA `2832d7b3e736f07feff35b4b1523f6b2d81529be8915af3c26bac235926a77a6`、pool manifest SHA `a6889e832738841bb6b3cfffd097e7937a4d0432df8707738c0bb757b7466293`
- promoted root: `runs/cg-self-owned-cg-policy-factorial-v2-20260816-promoted/`
- promoted pool／fresh meta／split SHA: `344134f98c87d9becf1cedf4fdf8726ac3564a4c07bb0a3bb14cb08704007ea0`／`aedac5f9251c4f4959b2d3556dfb387b07dc60c87cd541fb9cf2bde4b99e8d18`／`cf0baeea04f7fef6e5f76b899df77f5fde55bfbbdfed0b9791324fc0e8f7a5fd`
- promoted sourceは4件、`parent_deck=null`、`public_parent_read=false`、authority全false。公開artifactはcollision監査にだけ使い、deck／policyの生成入力にはしていない。

bounded smokeは `runs/cg-self-owned-cg-policy-factorial-v2-20260816-smoke/` で8/8 `DONE`、fault 0、5勝3敗（score 0.625）だった。smoke summary SHAは `baa73b086cc10f7bc508a465c0f61a1dab02673924c401f3dd08aeafd1073bf6`。splitはMETA_TRAIN=2、META_DEV=1、META_FINAL=1で、CEMはTRAIN sourceだけを読んだ。

## CEM結果

通常CLIはResourceGovernorの12 worker設定で、parent static smokeがnative `cg` moduleを先にimportした場合に worker 側へ `buffer full` が伝播し、途中で停止した。この artifact（`runs/cg-self-owned-cg-policy-factorial-v2-20260816-cem/`）はRUNNING／不完全であり、性能結果として扱わない。

loaderのcandidate `cg` と共有engine `cg` の隔離を修正し、さらにCEM parentのstatic smokeをcompile-onlyにした bounded retryを1 workerで実行した。実行rootは `runs/cg-self-owned-cg-policy-factorial-v2-20260816-cem-lowworkers-retry4/`、manifest SHA `afb33682fd1707827fb86ba7c2fda08e19b85464501d9e1e73077160986ac852`、generation results SHA `49a49fdc3af1e4b852a5ab6f70752347893fc0f6d8db8d2f6d8a22e84a32214e` である。

- generation 0、population／elite `4／1`
- screen: 40/40 `DONE`、fault 0
- independent re-evaluation: 16/16 `DONE`、fault 0
- c00はscreen `0.750` 対 control `0.625`（+12.5pt）だったが、独立では `0.750` 対 `0.750`（0pt）
- 他候補はscreen差が0pt以下で、独立positive gateを満たさなかった
- selection: `independent_train96_x1_positive_delta_gate_preserve_center`、eliteは `incumbent-center`

したがって、このepochは `SOURCE_GENERATION_PASS / POLICY_CEM_NO_UPDATE / BESTKNOWN_UNCHANGED` と判定する。DEV／FINALは未使用のまま保全し、同じsource／seed／候補のblind retryは行わない。

## loader修正と公開sourceの補足

新しい公開source候補としてSushanth Zacian snapshotも静的監査し、raw deckの不正な`Card ID` headerだけをstaged copyで除去して別epoch化した。`runs/cg-kaggle-kernel-meta-promoted-zacian-staged-20260816/` は1 sourceのpartial promotionで、pool／fresh meta SHAは `8f51f2f328c40a27385f2b0afcb21b2d6fe8548c3c0214e17cac762eddf9b197`／`bf601d871f04441bc7b4fdb109224d1737ea2fcf8fcebbf5637f8170eaba3750`。P1 smokeは2/2 `DONE`、fault 0、1勝1敗だった。1 sourceだけなのでCEM splitは作らず、性能候補には昇格していない。

candidate packageの`cg`とshared engineのmodule cacheを分離するloader回帰を追加した。focused testは opponent pool 7 passed、self-owned sourceを含む combined suiteは13 passed、generation／package suiteは17 passedである。

## 現在のBestKnownと `ono-` の出所

現行BestKnownは不変で、P1 policy SHA `1c505b2b5d345bfd897573a7586fb1232d1946d6a3405d8fb1e8486e4e8578e9` と root deck SHA `2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19` の組である。正確なラベルは「self-authored policy＋common/public root deck」であり、deckまでself-ownedとは呼ばない。

`ono-`は公開kernel作者名ではない。local Git identity `bfe-lab-ono`、branch `agents/ono-cg-lethal-v1`、commit `1965b42b028f10960d08ccb4980be5b76946f98b`に由来するローカル識別子である。root deck bytesは Aman、Makthanithin、Kojimar、Aristophanivan（2 snapshot）のlocal `deck.csv`と一致するが、同一bytesのため単一の元kernelはrepo証拠だけでは特定できない。

## 次の再開条件

次は同じrole-separated v4 CEMを盲目的に繰り返さず、(a) smoke用候補と性能holdoutを分離した新しいself-owned policy family、または (b) parent native importを別subprocessへ隔離したCEM runnerを用意する。その後、`legality → static safety → bounded fault0 → TRAIN-only smoke → independent positive → seat-safe/opponent×seat-safe → unused DEV → unused FINAL` を通過した候補だけを `cg_bestknown_loop_v1.py` へ渡す。
