# Autonomous evidence — Rule v0 root-deck coordinated package v11

## 結論

新規2-card packageをruntime smoke→META_TRAIN weighted48→common24→seed-disjoint 384の順で評価した。候補\`8f75789b6d00bda18716b65c2a95d3aa4f8502aea414ed61b20dd34d4a03ee29\`（\`[1142,1182]→[1,3]\`）は全段階fault0だったが、384で親比+1.0417ptに縮小したためcandidate-onlyで停止し、768/longrun/promotion/submissionへ進めない。候補\`3e338bf4f1a8a7397006993ba8d995037bdf734c2a5691a554841c1dfb12ca84\`（\`[1123,1182]→[1121,3]\`）はcommon24で−3.125ptとなり停止した。

## 実験条件

- policy: submission-compatible Rule v0固定、production \`main.py\`/\`agents/\`不変
- parent deck: root \`deck.csv\` SHA \`2a541d7bf3d9e6b36037123f53f4dfef6348223f79fd27095dafc602a5357c19\`
- root policy closure SHA: \`750a8dacaa283fecfb42edca05eb3cc6ce0d6a21525395d2866b2234de081e3b\`
- META_TRAIN subset SHA: \`09176f164b0f7719de70c903195e6b11b00dc3895ee8a98a154263fd8cbd72ed\`
- weighted48: 12 opponents × 2 seats × 2 repetitions、workers=12、recycle=16、base seed 23711000
- common24: 24 opponents × 2 seats × 2 repetitions、workers=12、recycle=16、base seed 23712000
- confirmation384: 24 opponents × 2 seats × 8 repetitions、workers=12、recycle=64、base seed 23713000
- authority: research-only、execution/training/promotion/submission/longrun=false、heldout training exposure=0

## 結果

| arm | weighted48 | common24 | confirmation384 |
|---|---:|---:|---:|
| parent | 3/48 (6.25%) | 15/96 (15.625%) | 46/384 (11.9792%) |
| \`[1123,1182]→[1121,3]\` | 6/48 (12.5%, +6.4336pt) | 12/96 (12.5%, −3.1250pt) | 未実施 |
| \`[1142,1182]→[1,3]\` | 5/48 (10.4167%, +4.6583pt) | 20/96 (20.8333%, +5.2083pt) | 50/384 (13.0208%, +1.0417pt) |

全実施局はDONE/fault0/draw0で、candidate/controlのseat、opponent、seed、paired-strata、GID integrity gateを通過した。weightedのpositiveはcommon24への進行権に留まり、384の小差はpromotion evidenceとみなさない。

## Artifact hashes

- weighted root: \`runs/final-sprint-autonomous/rule-v0-root-deck-package-v11-20260814/\`
  - manifest \`444110489b77835c55bb2306888dab031a8137b5fbd729c7fc641be3f0c00a2c\`
  - runtime smoke \`3e283644dc515ec2ac2eb67eb9b46f411d5f498866375e2133bd6607e96a0216\`
  - weighted summary \`5cff2bca008ecd90e4357ed3a14e3af5abb5db3c0098a1a81185f0491c656e40\`
- common24 root: \`runs/final-sprint-autonomous/rule-v0-root-deck-package-v11-common24-20260814/\`
  - manifest \`3d8f857daa423f880d7ace1a29c5186b18b515d9b58030616c9622b736c71b55\`
  - summary \`c156a52a2e1e3ea6d598a6c47185bca342e8676a3ec539664964667372ae417a\`
- confirmation384 root: \`runs/final-sprint-autonomous/rule-v0-root-deck-package-v11-confirmation384-20260814/\`
  - manifest \`b209881fa5241b712830240ffb6740ada107e1b4e47938344391a670b912e2be\`
  - summary \`fe469826586265c151efc884e24a7dacbf6f9343d5bee18406f6675be32a6aa2\`

No candidate was promoted, trained, submitted, or sent externally. Existing Champion and SubmissionEligibleBestKnown (Rule v0 + root deck, 11/96) remain unchanged.
