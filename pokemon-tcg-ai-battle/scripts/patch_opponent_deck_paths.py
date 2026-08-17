"""プール相手が自分の deck.csv を読むように直す (FROZEN-OPPONENT PATCH)。

## 何が壊れていたか

多くの公開エージェントは `open("deck.csv")` のように **cwd 相対**でデッキを読む。
Kaggle では cwd が提出ディレクトリなので正しいが、本リポジトリの harness では
cwd が repo root であり、そこにある `deck.csv` は**提出用デッキ**である。結果、
相手は自分のものではない 60 枚を前提に推論していた。

実測 (16 相手・座席均等 160 局):

| agent | 修正前 | 修正後 |
|---|---:|---:|
| `tomatomato_archaludon` | 5.6% | 75.0% |
| `public_archaludon_cinderace_r7` | 2.5% | 76.3% |

合法手は cabt が hard truth なので不正な手は出ない。壊れるのは相手の**判断**であり、
弱い相手として観測される。teacher の勝率はこの分だけ過大評価されていた。

## 直し方

module 自身の隣にある `deck.csv` を絶対パスで先に解決する。cwd 相対と Kaggle パスは
そのまま後段の fallback として残すので、提出物としての振る舞いは変わらない。

## 判定は静的解析ではなく実挙動

コード形が個体ごとに違うため、正規表現だけでは信用できない (実際に一度誤判定した)。
本スクリプトは patch 後に **`builtins.open` を観測する probe** を走らせ、自分の
deck.csv を読んだ個体だけを採用し、そうでなければ元へ戻す。
"""
from __future__ import annotations

import argparse
import ast
import builtins
import hashlib
import json
import os
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

_HELPER_V1 = '''

def _sidecar_deck_path():
    """The deck.csv shipped next to this policy, if present.

    FROZEN-OPPONENT PATCH (bench only; not part of the upstream submission).
    Upstream resolves a cwd-relative "deck.csv", which is correct on Kaggle (cwd
    is the submission directory) and wrong in this repository's harness (cwd is
    the repo root, whose deck.csv is the *submission* deck).  Without this the
    pilot reasons about 60 cards that are not its own.
    """
    try:
        _here = os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return None
    _candidate = os.path.join(_here, "deck.csv")
    return _candidate if os.path.exists(_candidate) else None

'''

# Bare cwd-relative uses of the literal.  Deliberately narrow: a "deck.csv"
# already inside an os.path.join(...) is a sidecar resolution and must not move.
_REWRITES_V1: tuple[tuple[str, str], ...] = (
    (r'(?<![\w.])open\(\s*"deck\.csv"\s*\)', 'open(_sidecar_deck_path() or "deck.csv")'),
    (r'(?<![\w.])open\(\s*"deck\.csv"\s*,', 'open(_sidecar_deck_path() or "deck.csv",'),
    (r'(?<![\w.(,])(\bfp|\bfile_path|\bpath|\bdeck_path|\bDECK_PATH)(\s*=\s*)"deck\.csv"',
     r'\1\2_sidecar_deck_path() or "deck.csv"'),
    (r'os\.path\.exists\(\s*"deck\.csv"\s*\)',
     'os.path.exists(_sidecar_deck_path() or "deck.csv")'),
    # `for fp in ("deck.csv", "/kaggle_simulations/agent/deck.csv"):`
    (r'\(\s*"deck\.csv"\s*,\s*"/kaggle_simulations/agent/deck\.csv"\s*\)',
     '(_sidecar_deck_path() or "deck.csv", "/kaggle_simulations/agent/deck.csv")'),
    # single-quoted variants of the bare literal
    (r"(?<![\w.(,])(\bfp|\bfile_path|\bpath|\bdeck_path|\bDECK_PATH)(\s*=\s*)'deck\.csv'",
     r"\1\2_sidecar_deck_path() or 'deck.csv'"),
    (r"(?<![\w.])open\(\s*'deck\.csv'\s*\)", "open(_sidecar_deck_path() or 'deck.csv')"),
)


class PatchV1Error(RuntimeError):
    """Raised when a policy cannot be patched safely."""


def _insert_helper(source: str) -> str | None:
    """Put the helper after the import block, before first use.

    Returns ``None`` only when insertion is impossible; callers must then refuse
    the patch rather than ship rewritten call sites with no helper behind them.
    """
    if "def _sidecar_deck_path" in source:
        return source
    if not re.search(r'^\s*import os\b|^\s*import os,', source, re.M):
        source = re.sub(r'(\A(?:"""(?:.|\n)*?"""\n|\'\'\'(?:.|\n)*?\'\'\'\n)?)',
                        r'\1import os\n', source, count=1)
    # Find the end of the import block with the parser, not a line regex: a
    # regex match on `from x import (` puts the helper *inside* the parenthesised
    # name list, which is a syntax error rather than a fix.
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    last_import = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last_import = max(last_import, node.end_lineno or node.lineno)
    if not last_import:
        return None
    lines = source.splitlines(keepends=True)
    candidate = "".join(lines[:last_import]) + _HELPER_V1 + "".join(lines[last_import:])
    try:
        ast.parse(candidate)
    except SyntaxError:
        return None
    return candidate


def patch_source_v1(source: str) -> tuple[str, int]:
    changed = 0
    patched = source
    for pattern, replacement in _REWRITES_V1:
        patched, count = re.subn(pattern, replacement, patched)
        changed += count
    if not changed:
        return source, 0
    with_helper = _insert_helper(patched)
    if with_helper is None:
        # Rewrites without the helper produce a NameError at import time -- worse
        # than the bug being fixed.  Refuse the whole patch instead.
        raise PatchV1Error("rewrote call sites but could not insert _sidecar_deck_path")
    return with_helper, changed


def probe_reads_own_deck_v1(opponent_id: str) -> tuple[bool, str]:
    """Import the policy from the harness cwd and watch which deck it opens."""
    sys.path[:0] = [str(_ROOT), str(_ROOT / "src")]
    from mage_ptcg.meta_specialist.opponent_pool_v1 import (
        default_pool_root_v1, load_opponent_agent_callable_v1,
        load_opponent_pool_v1, resolve_opponent_v1,
    )
    previous = os.getcwd()
    os.chdir(_ROOT)
    opened: list[str] = []
    real_open = builtins.open

    def watch(file, *args, **kwargs):
        try:
            text = os.fspath(file)
        except TypeError:
            text = ""
        if "deck.csv" in str(text):
            opened.append(os.path.abspath(str(text)))
        return real_open(file, *args, **kwargs)

    try:
        pool = load_opponent_pool_v1(default_pool_root_v1(_ROOT))
        instance = resolve_opponent_v1(pool, opponent_id, subject_deck_csv_path="x")
        builtins.open = watch
        callable_ = load_opponent_agent_callable_v1(instance)
        module = sys.modules[callable_.__module__]
        for name in ("read_deck_csv", "_resolve_deck_path"):
            if hasattr(module, name):
                getattr(module, name)()
        own = os.path.abspath(instance.deck_csv_path)
        repo_deck = str(_ROOT / "deck.csv")
        if any(p == repo_deck for p in opened):
            return False, "still reads the repo-root deck.csv"
        if any(p == own for p in opened):
            return True, "reads its own deck.csv"
        return False, f"no observed deck read (opened={opened})"
    except Exception as exc:  # noqa: BLE001 - probe must report, not raise
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        builtins.open = real_open
        os.chdir(previous)


def _write_manifest(path: Path, rows: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--opponent-ids", required=True,
                        help="カンマ区切り、または 'all'")
    parser.add_argument("--apply", action="store_true",
                        help="指定しない場合は差分の要約だけ出す")
    args = parser.parse_args()

    manifest_path = _ROOT / "opponents" / "pool_manifest.json"
    rows = json.loads(manifest_path.read_text(encoding="utf-8"))
    if args.opponent_ids == "all":
        targets = [r["id"] for r in rows]
    else:
        targets = [x.strip() for x in args.opponent_ids.split(",") if x.strip()]

    import hashlib as _h
    stale = [
        r["id"] for r in rows
        if (_ROOT / "opponents" / r["id"] / "main.py").is_file()
        and r.get("policy_hash")
        and r["policy_hash"] != _h.sha256(
            (_ROOT / "opponents" / r["id"] / "main.py").read_bytes()
        ).hexdigest()
    ]
    if stale:
        # load_opponent_pool_v1 validates every entry, so one stale row makes the
        # probe fail for all opponents and every patch look unverifiable.
        raise SystemExit(
            f"manifest is out of sync with disk for {stale}; repair those first"
        )

    report: list[dict[str, object]] = []
    for opponent_id in targets:
        path = _ROOT / "opponents" / opponent_id / "main.py"
        if not path.is_file():
            report.append({"id": opponent_id, "status": "missing"})
            continue
        original = path.read_text(encoding="utf-8")
        if "_sidecar_deck_path" in original:
            report.append({"id": opponent_id, "status": "already_patched"})
            continue
        patched, changed = patch_source_v1(original)
        if not changed:
            report.append({"id": opponent_id, "status": "no_cwd_literal"})
            continue
        if not args.apply:
            report.append({"id": opponent_id, "status": "would_patch", "sites": changed})
            continue

        row = next(r for r in rows if r["id"] == opponent_id)
        previous_hash = row.get("policy_hash", "")
        # The pool verifies policy_hash on load, so the manifest has to agree with
        # the new bytes *before* the probe can import the policy at all.
        path.write_text(patched, encoding="utf-8")
        row["policy_hash"] = hashlib.sha256(path.read_bytes()).hexdigest()
        _write_manifest(manifest_path, rows)

        ok, reason = probe_reads_own_deck_v1(opponent_id)
        if ok:
            report.append({"id": opponent_id, "status": "patched", "sites": changed})
        else:
            # The probe is the oracle: an unverified patch is reverted, never kept.
            path.write_text(original, encoding="utf-8")
            row["policy_hash"] = previous_hash
            _write_manifest(manifest_path, rows)
            report.append({"id": opponent_id, "status": "reverted", "reason": reason})
    counts: dict[str, int] = {}
    for item in report:
        counts[str(item["status"])] = counts.get(str(item["status"]), 0) + 1
    print(json.dumps({"counts": counts, "detail": report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
