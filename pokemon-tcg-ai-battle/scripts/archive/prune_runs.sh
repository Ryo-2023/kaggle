#!/usr/bin/env bash
# runs/ から生の学習・対局データを削除し、評価証拠だけを残すアーカイブ用スクリプト。
#
# 背景:
#   Kaggle Pokemon TCG AI Battle の作業期間終了に伴い、runs/ を約 133GB から
#   約 5GB へ縮小する。削除対象は再生成可能な生データに限り、報告書
#   docs/postmortems/2026-08-17-project-final-report.md が参照する評価証拠
#   （summary / manifest / ledger / telemetry）は残す。
#
# 使い方:
#   bash scripts/archive/prune_runs.sh          # 試算のみ（既定・削除しない）
#   bash scripts/archive/prune_runs.sh --apply  # 実際に削除する
#
# 注意:
#   --apply は不可逆。実行前に archive/ への退避が済んでいることを確認する。

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

if [ ! -d runs ]; then
  echo "runs/ が見つからない: $REPO_ROOT" >&2
  exit 1
fi

# 退避済みであることの確認（--apply 時のみ）
if [ "$APPLY" = "1" ]; then
  for required in \
    archive/final-baseline-p1/package/main.py \
    archive/final-baseline-p1/package/deck.csv \
    archive/final-baseline-p1/evidence \
    archive/final-baseline-p1/telemetry
  do
    if [ ! -e "$required" ]; then
      echo "退避が未完了のため中止する: $required がない" >&2
      exit 1
    fi
  done
fi

# 削除対象。KEEP されるのは summary.json / manifest*.json / run_summary.json /
# progress_summary.json / stage-spec.json / ledger.jsonl / *telemetry*.jsonl /
# *.md / *.csv / *.py など、評価証拠と方策ソース。
find_targets() {
  # 1. 対局ごとの生記録（games/ 配下すべて）
  find runs -type f -path '*/games/*'
  # 2. 教師データ・学習コーパス
  find runs -type f -not -path '*/games/*' \
    \( -name 'game-*.jsonl' \
    -o -name 'dataset-*.jsonl' \
    -o -name 'snapshot-*.json' \
    -o -name 'rule_bc*.jsonl' \)
  # 3. 学習済みチェックポイント
  find runs -type f -not -path '*/games/*' \( -name '*.pt' -o -name '*.pth' \)
  # 4. run ごとに複製されたエンジンバイナリ（正本は リポジトリ直下 cg/ にある）
  find runs -type f -not -path '*/games/*' \
    \( -name '*.so' -o -name '*.dll' -o -name '*.dylib' \)
  # 5. キャッシュとワーカーログ
  find runs -type f -not -path '*/games/*' \( -name '*.pyc' -o -name '*.log' \)
}

echo "=== 削除対象の集計 ==="
find_targets | sort -u > /tmp/prune_runs_targets.txt
FILES=$(wc -l < /tmp/prune_runs_targets.txt)
BYTES=$(xargs -a /tmp/prune_runs_targets.txt -d '\n' -r stat -c '%s' 2>/dev/null | awk '{t+=$1} END {print t+0}')
printf 'ファイル数 : %s\n' "$FILES"
printf '合計サイズ : %.2f GB\n' "$(echo "$BYTES/1073741824" | bc -l)"
printf 'runs/ 現在 : %s\n' "$(du -sh runs | cut -f1)"

if [ "$APPLY" != "1" ]; then
  echo
  echo "試算のみ。実際に削除するには --apply を付ける:"
  echo "  bash scripts/archive/prune_runs.sh --apply"
  exit 0
fi

echo
echo "=== 削除を実行する ==="
xargs -a /tmp/prune_runs_targets.txt -d '\n' -r -n 500 rm -f
echo "ファイル削除が完了した。空ディレクトリを整理する。"

# 空になったディレクトリを下位から順に除去する
while find runs -type d -empty -print -quit | grep -q .; do
  find runs -depth -type d -empty -exec rmdir {} + 2>/dev/null || break
done

echo
echo "=== 完了 ==="
printf 'runs/ 現在 : %s\n' "$(du -sh runs | cut -f1)"
echo "残存しているのは評価証拠（summary / manifest / ledger / telemetry）と方策ソース。"
