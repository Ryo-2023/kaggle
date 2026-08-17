# O4 Live Own-Data Activation v1 Evidence

## 結果

### Episode agent mappingによるlive activation（2026-07-20）

Replay JSON単体にはsubmission-to-side mappingがないが、公式Kaggle Python SDK 2.2.3 / kagglesdk 0.1.33の`ApiListSubmissionEpisodesResponse`には`episodes`、各`ApiEpisode`には`agents`があり、実測したnested agent fieldは`submissionId`、`index`、`reward`、`state`、`teamName`、`teamId`だった。CLI 2.2.3のJSON rendererは`episode_fields`だけを表示しnested agentsを省略するため、own episode listingだけを型付きSDK transportへ切り替えた。

normalizerはcamelCase/snake_caseのagent mappingを保持し、authenticated own submission IDと一意照合した後にだけReplayを取得する。2026-07-20のlive own-only smokeではsubmission 28、episode 352、verified episode-agent mapping 18、mapping ambiguous quarantine 2、OWN_KAGGLE Replay 10、own logs 10をarchiveした。identityはepisode mapping由来でresolved、`PUBLIC_OTHER`の取得・trainingは0である。identity cacheはteam値を保存せず、submission listing hash、episode hash、agent index、identity hashのみを持つ`o4-identity-cache-v2`へ更新した。

既存のparticipant quarantine 140件は削除・書換えせず、`reports/o4_episode_agent_reclassification.json`へappend-onlyで再分類監査を追加した。legacy recordにはauthenticated submission-to-episode lineageが無いため、いずれも`NOT_REEVALUATED_MISSING_EPISODE_LINEAGE`であり、今回の成功結果へ混入していない。

2026-07-20にこの取得結果をread-only sourceとして処理し、10 Replayすべてを公式SDK由来の一意mappingで正規化した。Replay単体でown sideを推測していない。actor-visible `EpisodeRecord` 10、Rule v0再ラベル済み`DecisionRecord` 716、DeckObservation 20を生成し、opponent hand／future terminal result／`visualize` deck orderはpolicy入力から除外した。Replayの実行行動は合法性監査にだけ使い、Behavior Cloningの教師ラベルには使用していない。

Snapshotは`e89e2f2f64bc54225382e91a3574ef957f3de5f505b91c7f51c2fd903440937a`（10 episode、716 decision、split leakage pass）である。OWN_KAGGLEのみのRule v0再ラベルdatasetは709 records（元の716例中、splitを跨いだ7 duplicate decision identityを既存dataset builderがquarantine）、train/validation/test episode=6/2/2、dataset hash `ac2c61f1fedccb317842c135dd6e68f2dd06dc8949c1fda9c2b6d766a6c7fc23`となった。`NEURAL_ACTUAL_TRAINED`（model hash `b1f30199f9cf242c5365a00bad2b352864d8959fddf1bf6df2a8adb42cd9697c`）を既存Offline Training v1で学習し、clean-roomは8/8 legal、illegal 0、fallback 2、package hash `168853e84667816f7ede96df1103026f8e13199d746994837d2d1df7aab09f58`を確認した。

actual cabtでRule Agent v0対package Studentを16 seat-balanced games実行し、Student視点15-1-0、invalid/crash/timeout/privacy violation/fallback=0、Student decision latency p50/p95=1.676/4.184 ms、match latency p50/p95=0.136/0.353 sだった。engine seedは未対応のため`pairing_mode=seat_matched_unseeded`、`exact_paired_inference=false`であり、100 logical-pair未達として既存Promotion Reportは`INSUFFICIENT_EVIDENCE`である。Champion/defaultはRule Agent v0のまま、Kaggle submissionは行っていない。

開始canonicalは `74291683ea3a02a01726657913a48e3067aad4bd`、作業branchは `feature/o4-live-own-data-activation-v1` である。O3のlive quarantineを再現・追跡し、Kaggle CLI 2.2.3向けaction-specific parser、own team identity resolver、own submission → episode → Replay → own agent log chainを追加した。

旧preflightはOAuth `credentials.json`を認識せず、静的ファイル判定だけで `authentication_available=false` としていた。修正後は認証方式を候補として記録し、公式CLIの成功probeを能力の証拠とする。2026-07-20の公式 read-only probeはOAuth credentials方式・Kaggle CLI 2.2.3で成功し、submission listing 28件を `OWN_KAGGLE` としてarchiveした。fixture成功をactual成功として扱っていない。Kaggle submission、Champion変更、自動Promotionは実行していない。

## O3 quarantineの原因と修正

- own submission listingは8364 bytesのJSON list（28 records）であり、secret scan、participant resolver、archive書込みの失敗ではなかった。初回live responseにbaselineを作らないO3のTOFU拒否により、`untrusted_first_response` / `UNKNOWN`としてquarantineされた。
- leaderboardは2901 bytesで、Kaggle CLIが`--format json`指定にもかかわらずhuman headingをJSON arrayの前に付与した。O3の全体JSON parserは`malformed_json`と判定した。
- `live_payloads.py`はJSON list/object、heading付き完全JSON、CLI table、empty list、null field、文字列数値、追加fieldをaction別canonical DTOへ正規化する。必須submission/episode identifier欠損、Replay info/progression欠損、曖昧tableはquarantineする。raw bytesは引き続きarchiveの正本であり、normalized DTOだけをschema validationに使う。
- episode listingのJSON後に付く既知のCLI案内文は受理するが、任意の trailing bytesは拒否する。submission probe responseは後続のsubmission取得へ再利用し、同じread-only listingを二重実行しない。
- CLI failureは401（authentication）、403（permission）、DNS/接続（network）、dependency missingを別分類する。subprocessは継承環境（HOME、KAGGLE_CONFIG_DIR、PATH、SSL等）を明示的に渡し、project-local `.venv/bin/kaggle`を優先する。
- identity未設定でも、直前にarchiveされたauthenticated own listingのsubmission IDだけを`OwnSubmissionBootstrap`としてepisodesへ渡す。listing hashとIDの存在、最大submission/replay試行数を検証し、任意IDや第三者team-submissionsは入力できない。
- Replayは明示的なsubmission-to-side mapping（SubmissionIds等）を必須とし、mappingなし・曖昧・identity欠損は`participant_resolution_quarantined`とする。導出identityはrun-local hash-only cache（`o4-identity-cache-v1`）へ保存し、team ID/name本体はtracked evidenceへ書かない。
- 実Replayは約3MB以上のJSONだったため、transportの1MB上限をReplay専用32MB上限へ拡張した。quarantineが成功件数を増やさない場合にも最大試行数を超えないよう修正した。

## Own-only governance

- identityはCLI引数、gitignored `configs/competition/o4_live_identity.local.json`、環境変数、own submission上の一意team情報の順で解決する。tracked sampleはplaceholderのみである。
- 明示team IDはnameより優先し、name-onlyは一意一致だけを`OWN_KAGGLE`とする。曖昧一致はquarantine、自チーム不参加は`PUBLIC_OTHER`である。
- Replayからown agent indexを解決してそのindexのlogsだけを要求する。相手agent logs、第三者episode、PUBLIC_OTHER episodeはscheduleしない。
- latest live runは `/home/bfe-lab-ono/kaggle-data/pokemon-tcg-ai-battle/runs/o4-live-own-data-v1`。capability probeは成功し、submission 28件、episode 352件、verified episode-agent mapping 18、mapping ambiguous quarantine 2、OWN_KAGGLE Replay 10、own logs 10、identity `RESOLVED`である。Replayのown sideは公式SDKの`ApiEpisode.agents` recordだけで確定し、Replay単体のparticipant情報は補助照合にとどめた。PUBLIC_OTHERの探索・分析・学習は行っていない。

## Snapshot / dataset / training

Kaggle own Snapshotは`e89e2f2f64bc54225382e91a3574ef957f3de5f505b91c7f51c2fd903440937a`。正規化のSourceKindは`OWN_KAGGLE=10`、`PUBLIC_OTHER=0`、`TEAM_SHARED=0`であり、Replay由来のRule v0再ラベルtraining exampleは716（materialized 709）、DeckObservationは20、Replay quarantineは0である。`PUBLIC_OTHER = ARCHIVE only`、`rules = UNVERIFIED_RULES_CONSTRAINT`を維持し、PUBLIC_OTHER training recordsは0件である。

Kaggle Replayが無い場合に許される既存O2 local actual collectorも別run rootで検証した。`/tmp/o4-local-actual32-ziqZGY`はactual cabt 32 episode、574 supervised decisions、2,954 candidates、privacy violation 0、split overlap 0で完走した。collection dataset hashは `c5b22e4d13f33f7fc7d01fefb3aec5057c637c19913bc7da47725df4aebd328c`、canonical training dataset hashは `7d234056b549a864871ff8346aee56962958f504c05554615e764b52fe638d03`（558 records、train/validation/test episode = 16/8/8、quarantined identity 16）、model hashは `841d11dbe9aee37725f59a1bc44a00e100b75d8c4e22293397385b1c7d152226`、package hashは `cf7c12834fc37c4ad708ea1b758e6afb1b151f6997bbbe01bd555bab456b7b58` である。

ただし、この短いlocal collectionは既存gateの`performance_eligible=false` / `COLLECTION_SMOKE`であり、modelは`NEURAL_FIXTURE_SMOKE`のままである。`NEURAL_ACTUAL_TRAINED`への偽装、actual Student evaluation、Promotion根拠化は行わなかった。package clean-roomは8/8 legal、illegal 0、意図したnegative fallback 2件、verified=trueである。

## Evaluation と不変条件

actual Student評価はOWN_KAGGLEのRule v0再ラベルartifactをpackageからロードし、16 gameを完走した。Student視点W-L-D=15-1-0、illegal/crash/timeout/fallback/privacy violation=0、inference completed=358、Rule v0=442 decisionである。Promotionは100 logical-pair未達のため`INSUFFICIENT_EVIDENCE`、`engine_seed_supported=false`、`pairing_mode=seat_matched_unseeded`、`exact_paired_inference=false`、Champion/default Rule Agent v0、Kaggle submission not performedである。

## 検証

- 編集前baseline: O3 focused 30 pass、privacy/secret 15 pass、full regression 1485 pass / 0 fail、docs validation 12 canonical documents、protected files 20 unchanged、conflict marker 0。
- O4/bootstrap focused: 38 pass。recorded O3 bytesはsubmission 28 records / leaderboard 20 recordsとしてadapterで再検証した。full regressionは`1497 passed, 5 warnings`（213.14s）で完走した。package publishは`MAGE_PTCG_DIST_ROOT`でtest tmp_pathへ隔離し、tracked `dist/`差分は発生しなかった。
- 今回のReplay normalizer focused testは3 pass、関連competition-intelligence testは12 pass、最終full regressionは`1503 passed, 5 warnings`（241.66s）で完走した。`python3 scripts/docs/validate_docs.py`は12 canonical documentsを検証し、`git diff --check`と変更対象のconflict marker scanもpassした。
- live own-only probe、16-game local actual smoke、32-game local actual collectionはすべてrepository外run rootに保存した。fixtureはcontract regressionだけに使用し、actual evidenceには含めない。

## 再開条件

Replayの安全なsubmission-to-side mapping確認、OWN_KAGGLE Snapshot／actual datasetのmaterialize、`NEURAL_ACTUAL_TRAINED`の16-game viability評価は完了した。feature publication、clean clone、canonical integrationも完了している。Promotionの100 logical-pair閾値は未達のまま変更しない。

## Git publication / canonical integration

feature branchは開始HEAD `a809e05e72d526bd217a44131e746b4cb5599eee`から、Replay normalizer・O4 orchestration・CLI・tests・docsの5 commit（`2a2ddb7`、`7fb87ff`、`2e89f11`、`821eaad`、`570f6be`）へ分割し、feature HEAD `570f6be332060850079c974064276d0842f6b90f`を通常pushした。remote featureとのdivergenceは`0 0`、feature clean cloneはfocused 82 pass、security/privacy 77 pass、docs 12/12、protected 20 unchanged、Rule v0 package clean-room passである。

canonical開始HEAD `74291683ea3a02a01726657913a48e3067aad4bd`にno-ff mergeし、merge commit `064a383b6d2a1239be6a58ee8a6be64b2b7cc287`を作成した。統合後full regressionは`1503 passed, 5 warnings`、security/privacy 77 pass、docs 12/12、protected 20 unchanged、package compatibility passであり、canonical remote `feature/belief-guided-search`へ通常push済みである。final canonical HEADは`064a383b6d2a1239be6a58ee8a6be64b2b7cc287`、remote divergenceは`0 0`、canonical clean cloneもfocused 82 pass／security/privacy 77 pass／docs 12/12／package clean-room passである。
