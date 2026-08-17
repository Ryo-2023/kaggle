#!/usr/bin/env python3
"""Build and formally verify a common24 META_TRAIN outcome adapter."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from mage_ptcg.meta_specialist.common24_curriculum_outcome_adapter_v1 import (
    build_common24_curriculum_outcome_adapter_v1,
    verify_common24_curriculum_outcome_adapter_v1,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--reconciliation", type=Path, required=True)
    parser.add_argument("--meta-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    manifest = build_common24_curriculum_outcome_adapter_v1(
        repo_root=args.repo_root,
        reconciliation_path=args.reconciliation,
        meta_manifest_path=args.meta_manifest,
        output_dir=args.output_dir,
    )
    manifest_path = args.output_dir.resolve() / "adapter-manifest.json"
    verified = verify_common24_curriculum_outcome_adapter_v1(
        manifest_path, args.repo_root
    )
    if verified != manifest:
        raise RuntimeError("adapter post-write verification drift")
    print(
        json.dumps(
            {
                "adapter_manifest": str(manifest_path),
                "adapter_manifest_file_sha256": _sha(manifest_path),
                "adapter_sha256": manifest["adapter_sha256"],
                "outcome_ledger": str(args.output_dir.resolve() / "outcome-ledger.jsonl"),
                "outcome_ledger_file_sha256": manifest["output"]["file_sha256"],
                "emitted_meta_train_rows": manifest["summary"]["emitted_meta_train_rows"],
                "excluded_meta_dev_rows": manifest["summary"]["excluded_meta_dev_rows"],
                "excluded_meta_final_rows": manifest["summary"]["excluded_meta_final_rows"],
                "authority": manifest["authority"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
