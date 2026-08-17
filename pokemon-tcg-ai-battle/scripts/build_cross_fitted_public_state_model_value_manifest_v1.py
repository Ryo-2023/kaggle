#!/usr/bin/env python3
"""Build a leave-fold-out actor-visible state-model value manifest.

This is a research-only companion to the public bucket baseline.  It fits a
fixed ridge value model on public state scalars and legal-domain structure
using only episodes outside each held-out fold.  The output is an anonymous
target manifest; it never emits opponent, seat, policy, or private metadata.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from mage_ptcg.meta_specialist.cross_fitted_public_state_value_v1 import (
    PublicStateValueError,
    build_cross_fitted_public_state_model_value_manifest_v1,
)
from scripts.build_cross_fitted_outcome_residual_manifest_v1 import (
    _read_train_episodes,
    _sha_file,
)


def build_manifest_from_screen_jsonl_v1(
    source: Path,
    *,
    output: Path,
    fold_count: int = 2,
    advantage_clip: float = 1.0,
    ridge_lambda: float = 1.0,
) -> dict[str, object]:
    source_sha = _sha_file(source)
    episodes = _read_train_episodes(source)
    manifest = build_cross_fitted_public_state_model_value_manifest_v1(
        episodes,
        fold_count=fold_count,
        advantage_clip=advantage_clip,
        ridge_lambda=ridge_lambda,
    )
    raw = json.dumps(manifest.to_dict(), ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(raw, encoding="utf-8")
    returns = [episode.return_value for episode in manifest.episodes]
    return {
        "schema_version": "meta-specialist-public-state-model-value-builder-v1",
        "source_file_sha256": source_sha,
        "output_file_sha256": _sha_file(output),
        "episodes": len(manifest.episodes),
        "transitions": sum(len(episode.targets) for episode in manifest.episodes),
        "fallback_target_count": manifest.fallback_target_count,
        "return_distribution": {
            "negative": sum(value < 0.0 for value in returns),
            "zero": sum(value == 0.0 for value in returns),
            "positive": sum(value > 0.0 for value in returns),
        },
        "target_kind": "signed_public_state_value_residual",
        "objective_kind": manifest.objective_kind,
        "value_feature_schema": manifest.value_feature_schema,
        "value_model_sha256": manifest.value_model_sha256,
        "ridge_lambda": manifest.ridge_lambda,
        "research_only": True,
        "training_permitted": False,
        "promotion_authority": False,
        "longrun_allowed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screen-transitions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fold-count", type=int, default=2)
    parser.add_argument("--advantage-clip", type=float, default=1.0)
    parser.add_argument("--ridge-lambda", type=float, default=1.0)
    args = parser.parse_args(argv)
    try:
        summary = build_manifest_from_screen_jsonl_v1(
            args.screen_transitions,
            output=args.output,
            fold_count=args.fold_count,
            advantage_clip=args.advantage_clip,
            ridge_lambda=args.ridge_lambda,
        )
    except (PublicStateValueError, OSError, TypeError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
