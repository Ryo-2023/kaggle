"""Seal factorial visible-state behavior-family variants.

The recipes compose two already audited Alakazam or Comfey transforms.  They
are research-only: resulting policies retain the source deck and observation
boundary and are never treated as native/public opponents.
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from .behavior_family_meta_v1 import (
    _replace_comfey_behavior,
    _replace_alakazam_behavior,
    _seal_behavior_family_v1,
)
from .derived_internal_meta_v1 import DerivedInternalMetaError


FACTORIAL_BEHAVIOR_FAMILY_META_SCHEMA_V1 = "meta-specialist-cg-factorial-behavior-family-meta-v1"
ALAKAZAM_FACTORIAL_VARIANTS_V1 = (
    "ABRA_POFFIN",
    "ABRA_FEZANDIPITI",
    "DUNSPARCE_POFFIN",
    "DUNSPARCE_FEZANDIPITI",
)
COMFEY_FACTORIAL_VARIANTS_V1 = (
    "DECKOUT_AGGRESSIVE_COMFEY",
    "DECKOUT_AGGRESSIVE_LITWICK",
    "DECKOUT_CONSERVATIVE_COMFEY",
    "DECKOUT_CONSERVATIVE_LITWICK",
)
_FACTORIAL_STEPS = {
    "ABRA_POFFIN": ("ABRA_FIRST", "POFFIN_FIRST"),
    "ABRA_FEZANDIPITI": ("ABRA_FIRST", "FEZANDIPITI_DRAW_FIRST"),
    "DUNSPARCE_POFFIN": ("DUNSPARCE_FIRST", "POFFIN_FIRST"),
    "DUNSPARCE_FEZANDIPITI": ("DUNSPARCE_FIRST", "FEZANDIPITI_DRAW_FIRST"),
}
_COMFEY_FACTORIAL_STEPS = {
    "DECKOUT_AGGRESSIVE_COMFEY": ("DECKOUT_AGGRESSIVE", "COMFEY_SETUP_FIRST"),
    "DECKOUT_AGGRESSIVE_LITWICK": ("DECKOUT_AGGRESSIVE", "LITWICK_SETUP_FIRST"),
    "DECKOUT_CONSERVATIVE_COMFEY": ("DECKOUT_CONSERVATIVE", "COMFEY_SETUP_FIRST"),
    "DECKOUT_CONSERVATIVE_LITWICK": ("DECKOUT_CONSERVATIVE", "LITWICK_SETUP_FIRST"),
}


def _replace_alakazam_factorial_behavior(source: bytes, variant: str) -> tuple[bytes, str]:
    """Compose one Pokemon-priority and one setup/item-priority transform."""

    try:
        steps = _FACTORIAL_STEPS[variant]
    except KeyError as exc:
        raise DerivedInternalMetaError(f"unsupported Alakazam factorial variant: {variant}") from exc
    transformed, first_recipe = _replace_alakazam_behavior(source, steps[0])
    transformed, second_recipe = _replace_alakazam_behavior(transformed, steps[1])
    first_name = first_recipe.rsplit(":", 1)[-1]
    second_name = second_recipe.rsplit(":", 1)[-1]
    return transformed, f"ALAKAZAM_FACTORIAL_BEHAVIOR_FAMILY_V1:{first_name}+{second_name}"


def _replace_comfey_factorial_behavior(source: bytes, variant: str) -> tuple[bytes, str]:
    """Compose one deckout-reserve and one visible setup-priority transform."""

    try:
        steps = _COMFEY_FACTORIAL_STEPS[variant]
    except KeyError as exc:
        raise DerivedInternalMetaError(f"unsupported Comfey factorial variant: {variant}") from exc
    transformed, first_recipe = _replace_comfey_behavior(source, steps[0])
    transformed, second_recipe = _replace_comfey_behavior(transformed, steps[1])
    first_name = first_recipe.rsplit(":", 1)[-1]
    second_name = second_recipe.rsplit(":", 1)[-1]
    return transformed, f"COMFEY_FACTORIAL_BEHAVIOR_FAMILY_V1:{first_name}+{second_name}"


def seal_alakazam_factorial_behavior_family_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    variants: Sequence[str] = ALAKAZAM_FACTORIAL_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Seal a deterministic factorial source pool with a fresh split."""

    return _seal_behavior_family_v1(
        base_root=base_root,
        output_root=output_root,
        source_epoch=source_epoch,
        seed_namespace=seed_namespace,
        p1_package=p1_package,
        variants=variants,
        current_pool_manifest=current_pool_manifest,
        scan_roots=scan_roots,
        transformer=_replace_alakazam_factorial_behavior,
    )


def seal_comfey_factorial_behavior_family_v1(
    *,
    base_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
    p1_package: Path | str,
    variants: Sequence[str] = COMFEY_FACTORIAL_VARIANTS_V1,
    current_pool_manifest: Path | str | None = None,
    scan_roots: Sequence[Path | str] = (),
) -> dict[str, object]:
    """Seal a deterministic Comfey factorial source pool with a fresh split."""

    return _seal_behavior_family_v1(
        base_root=base_root,
        output_root=output_root,
        source_epoch=source_epoch,
        seed_namespace=seed_namespace,
        p1_package=p1_package,
        variants=variants,
        current_pool_manifest=current_pool_manifest,
        scan_roots=scan_roots,
        transformer=_replace_comfey_factorial_behavior,
    )


__all__ = [
    "FACTORIAL_BEHAVIOR_FAMILY_META_SCHEMA_V1",
    "ALAKAZAM_FACTORIAL_VARIANTS_V1",
    "COMFEY_FACTORIAL_VARIANTS_V1",
    "DerivedInternalMetaError",
    "_replace_alakazam_factorial_behavior",
    "_replace_comfey_factorial_behavior",
    "seal_alakazam_factorial_behavior_family_v1",
    "seal_comfey_factorial_behavior_family_v1",
]
