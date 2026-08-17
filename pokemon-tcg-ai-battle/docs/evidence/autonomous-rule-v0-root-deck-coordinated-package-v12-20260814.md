# Autonomous evidence — Rule v0 root-deck coordinated package v12

## 結論

既存multisetを除外した新規2-card packageをRule v0＋root deck固定で評価した。候補3a949927…（[1141,1227]→[1121,3]）はweighted48で+8.4805ptだったが、common24で−2.0833ptとなり停止。候補ea4a5c77…（[1142,1192]→[3,3]）はweighted48で+9.9898pt、common24で+3.1250ptだったが、seed-disjoint 384で親46/384対候補44/384（−0.5208pt）へ反転した。全実施局fault0で、768/longrun/promotion/submissionへ進めない。

## 条件と結果

- Rule v0＋root deck固定、META_TRAIN subset SHA 09176f164b0f7719de70c903195e6b11b00dc3895ee8a98a154263fd8cbd72ed
- smoke 6局、weighted48 144局（workers=12/recycle=16、base seed 23721000）
- common24 288局（workers=12/recycle=16、base seed 23722000）
- confirmation384 768局（workers=12/recycle=64、base seed 23723000）
- parent: weighted 1/48、common24 9/96、384 46/384
- 3a949927…: weighted 5/48、common24 7/96、384未実施
- ea4a5c77…: weighted 6/48、common24 12/96、384 44/384
- 全DONE/fault0/draw0、seat/opponent/paired seed/GID gate PASS、authority=false、heldout training exposure=0

## Artifact hashes

- weighted root manifest 7c1c81ba1dd4a953e66382c2dd3730fed499704e018594f55f860dc9a44a9bc1
- weighted summary fc6f25ddc57379c71f363bb10d3e25a86cbb34d3dfcb1bf2cfb9863a2d9843f7
- common24 manifest 1f806df84fcf6bafabf00a36f824efa2d29ccac5575d7a58e8f7d692895bd776
- common24 summary 0fa711fec6f4fce8739af4619914b2acfb86c4c4c4cf1bbeca87ce4196b0c6b3
- confirmation384 manifest e6af79181584b9bcc4c5948911d42d5f58461ed007f40801893103151ba4dddb
- confirmation384 summary 8f07175099ac248d2caf6269cde620b9e3bd7cb0b2ac8847eb9bf1a70c6c5471

このv12はcandidate-only/hard-negativeであり、Champion、production、SubmissionEligibleBestKnown（Rule v0＋root deck 11/96）は変更しない。新規candidate生成は継続できるが、v12候補のblind retryは行わない。
