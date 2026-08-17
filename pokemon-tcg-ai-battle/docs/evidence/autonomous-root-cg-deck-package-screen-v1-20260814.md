# Autonomous self-owned cg deck/package screen — 2026-08-14

## 結論

self-owned `cg.api` policyを固定したdeck-policy alternatingの次段として、Dusk reserveと2-card coordinated packageをruntime smoke→weighted48→common24→384の順でscreenした。Explorerはcommon24で負、Xerosicはweightedで負。Dusk+Hilda/Bloodmoon packageはcommon24で負またはfault、Dusk+Bloodmoon/Ultra Ball packageはcommon24同率。Dusk+PetrelとPowerPro+Stretcherはcommon24で同じ+8.3333ptだったが、代表としてDusk+Petrelを384へ進めたところ−0.6510ptへ反転した。全候補candidate-only/research-only、768/longrun/promotion/submissionは起動しない。

## Reserve screens

Explorer’s Guidance（1102→1185）はclean-room smoke 2/2 DONE/fault0、weighted48 5/48対control3/48（+4.1667pt）、common24 13/96対13W-1D/96（−0.5208pt）。Xerosic’s Machinations（1102→1197）はsmoke 2/2 DONE/fault0、weighted48 5/48対9/48（−8.3333pt）。Explorer/Xerosicとも384へ進めない。

## Coordinated packages

| package | weighted48 | common24 | 384 | 判定 |
| --- | --- | --- | --- | --- |
| Dusk+Hilda/Bloodmoon | 9/48 vs 7/48 (+4.1667pt) | 8/96 vs 10/96、control fault1 | 未実施 | invalid/candidate-only |
| Dusk+Bloodmoon/Ultra Ball | 9/48 vs 7/48 (+4.1667pt) | 16/96 vs 16/96 (0pt) | 未実施 | candidate-only |
| Dusk+Petrel | 9/48 vs 3/48 (+12.5pt) | 18/96 vs 10/96 (+8.3333pt) | 53/384 vs 55W-1D/384 (−0.6510pt) | candidate-only |
| PowerPro+Stretcher | 6/48 vs 4/48 (+4.1667pt) | 16/96 vs 8/96 (+8.3333pt) | 未実施 | candidate-only |

全て同一24 broad opponent pool、両seat、paired seed strata、workers=12で実施した。weighted/common24はrecycle16、384はrecycle64。Dusk+Hilda/Bloodmoon common24はcontrol armに1 faultがあり、faultを勝利へ変換せずinvalid扱いとした。Dusk+Petrel 384は全768局DONE/fault0、candidate seat0=30/192・seat1=23/192、control seat0=23/192・seat1=32W-1D/192で、差はseat1側のcontrol優位を含めて縮小した。

## Artifact identity

policy source SHAは`617a23c060084c8b2601800b4f729238563925165f3520628d938eab065aebef`。candidate package manifest SHAは、Dusk+Petrel `28562fb01d0c152efdfc7e355aedacbf0382e1452857539eacbd70e05dbecb2f`、PowerPro+Stretcher `19c1b2af75291649353b2448e2756b37b506374e80ca0abdf835fb3cea6b88a1`。Dusk+Petrel 384 summary SHAは`5eabe4bbbf52ec0dbba0d9463a0c862a6c4f966460e9c9cbd975c73b23821311`。reserve/package screen summaryは各fresh root内に保存し、既存production/Champion/root deck artifactは変更していない。

## 次の条件

同一候補のblind retryはしない。次の実行は新しいnovel deck/package仮説をsmoke後workers12/recycle16 weighted48へ投入する。common24で明確に再現した最良candidateだけ384へ送る。公式verifier/runtime shape未接続のため、self-owned cg archiveは引き続き`submission_ready=false`であり、Kaggle submissionは行わない。
