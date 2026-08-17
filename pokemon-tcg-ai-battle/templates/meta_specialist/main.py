"""Kaggle-facing entrypoint for the meta-driven fixed-deck specialist bundle.

This file, together with ``policy_loader.py``, is the archive's trusted
entrypoint set (see ``package.py``'s ``entrypoint_contract``).  It resolves
its own bundle root, delegates every structural/runtime reconstruction fact
to :mod:`mage_ptcg.meta_specialist.entrypoint` (Task 5B), and exposes the
Kaggle-facing ``agent`` callable plus a module-level ``package_telemetry()``
snapshot function.

Known P0 limitation (not a defect in this file): building the final agent
binding requires a card vocabulary that satisfies
``mage_ptcg.meta_specialist.actor_visible_features_v1.require_production_card_vocabulary_v1``.
That gate unconditionally raises today because no trusted sealed
card-vocabulary registry has been published yet.  This file therefore
currently raises at import time instead of silently shipping an agent built
against a vocabulary nobody actually qualified.  See
``docs/runbooks/meta-specialist-p0-foundation.md`` for the current status of
that dependency.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _bundle_root() -> Path:
    """Resolve this file's own directory under normal import and Kaggle's raw exec."""
    if "__file__" in globals():
        source_name = __file__
    else:
        source_name = getattr(sys._getframe().f_code, "co_filename", "")
    if source_name and not str(source_name).startswith("<"):
        candidate = Path(source_name).resolve().parent
        if (candidate / "policy_loader.py").is_file():
            return candidate
    kaggle_candidate = Path("/kaggle_simulations/agent")
    if (kaggle_candidate / "policy_loader.py").is_file():
        return kaggle_candidate
    raise RuntimeError("main.py bundle root could not be resolved")


def _prepare_imports(root: Path) -> None:
    """Make sibling bundle modules and any bundled ``src/`` package importable."""
    for entry in (root, root / "src"):
        value = str(entry)
        if entry.is_dir() and value not in sys.path:
            sys.path.insert(0, value)


_ROOT = _bundle_root()
_prepare_imports(_ROOT)

import policy_loader  # noqa: E402  (sibling module this archive bundles alongside main.py)

from mage_ptcg.meta_specialist import entrypoint  # noqa: E402
from mage_ptcg.meta_specialist.actor_visible_features_v1 import CardVocabularyV1  # noqa: E402


def _unqualified_bundle_vocabulary(loaded: entrypoint.LoadedSpecialistBundle) -> CardVocabularyV1:
    """Build the best currently-available vocabulary from the bundle's own verified deck.

    P0 has no trusted sealed card-vocabulary registry yet (see this module's
    docstring and ``entrypoint.py``'s), so this cannot legitimately be marked
    ``bundle_allowed``.  Its fields are real, verified bundle facts -- never
    fabricated -- but its ``usage_decision``/``permission_decision`` are
    honestly reported as ``"unqualified"``, which keeps
    ``require_production_card_vocabulary_v1`` failing closed until that
    registry exists.
    """
    asset = loaded.qualified_deck_asset
    return CardVocabularyV1(
        recognized_card_ids=frozenset(asset.card_ids),
        source_sha256=asset.deck_file_sha256,
        environment_version=asset.card_database_version,
        usage_decision="unqualified",
        test_only=False,
        permission_decision="unqualified",
    )


def _build_binding():
    loaded = entrypoint.load_specialist_bundle(_ROOT)
    vocabulary = _unqualified_bundle_vocabulary(loaded)
    policy_factory = policy_loader.load_policy_factory(policy_identity=loaded.policy_identity)
    return entrypoint.build_packaged_agent(_ROOT, vocabulary=vocabulary, policy_factory=policy_factory)


_BINDING = _build_binding()


def package_telemetry() -> dict[str, object]:
    """Kaggle-facing telemetry snapshot; delegates to the bound runtime."""
    return _BINDING.package_telemetry()


agent = _BINDING.agent

__all__ = ["agent", "package_telemetry"]
