# Autonomous evidence — Rule v0 root-deck coordinated package v13

新規2-card package v13をruntime smoke→META_TRAIN weighted48→common24で評価した。候補362fdf94…（[1141,1152]→[3,3]）はweighted +1.3570ptからcommon24 −5.2083ptへ反転。候補ee8f3a06…（[1102,1102]→[1121,3]）はweighted +6.4260ptからcommon24 −1.0417ptへ反転した。全432局（smokeを除くweighted/common24）はDONE/fault0/draw0、seat/opponent/paired seed/GID gate PASS。両候補はcandidate-only/hard-negativeで停止し、384/768/longrun/promotion/submission/trainingは起動しない。

- weighted root manifest: 66ebf8859c4d2ab2d0baee055b0ea0a67cc3c9b7ee1cca91efae333a09c109ad
- weighted summary: 95092d3eb779dde1e77e84703640af21c6924ad01c1c14a1f21becbedeeafd7f
- common24 manifest: 7126c4a554712160d66c4a18c1b5cf7418c1ee6d0946634ab392b35e3617851a
- common24 summary: e90fc7d0026246c7a57f16c3cb8a42c6305c2dcd4af7d96468a62c26dd311b54
- authority: research-only、execution/training/promotion/submission/longrun=false、heldout training exposure=0

SubmissionEligibleBestKnown（Rule v0＋root deck 11/96、fault0）、production、Championは変更しない。
