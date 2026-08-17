"""Boots a specialist agent binding from one already-extracted bundle root.

``package.py`` (Task 5A) is deliberately structural-only: it builds and
verifies archive *bytes* and never imports a policy or constructs a runtime.
This module is the Task 5B counterpart it defers to.  It consumes a bundle
*root directory* that already contains the exact files a specialist archive
carries -- ``main.py``, ``deck.csv``, ``policy_loader.py``,
``meta_specialist_bundle.json``, and whichever ``policy_members``/
``model_member`` files the manifest declares -- and reconstructs the exact
runtime objects :func:`mage_ptcg.meta_specialist.runtime.make_agent` needs.

That root directory is either the output of
:func:`mage_ptcg.meta_specialist.package.extract_verified_archive` (local
testing) or the directory Kaggle itself extracts a submitted archive into
(``/kaggle_simulations/agent/`` at competition runtime).  Neither caller can
hand this module the original archive bytes, so this module does not redo
``package.py``'s tar/gzip structural verification.  It instead treats every
fact as untrusted input from disk: every value used to build the runtime is
either independently recomputed from the current bytes under ``root`` or
cross-checked against a value that was.  A missing file, a short file, or any
mismatch raises :class:`EntrypointContractError` -- this module never
substitutes a placeholder, a fabricated hash, or an unmeasured CABT result.

Card-vocabulary qualification is a separate, not-yet-solved dependency.
:func:`build_packaged_agent` requires its ``vocabulary`` argument to satisfy
:func:`mage_ptcg.meta_specialist.actor_visible_features_v1.require_production_card_vocabulary_v1`,
which unconditionally raises today because no trusted sealed card-vocabulary
registry has been published yet.  This module does not weaken or bypass that
gate; :func:`build_packaged_agent` will therefore raise for every caller
until that registry exists.  That is intentional fail-closed behaviour, not
a defect here: no bundle may claim a qualified card vocabulary that was
never actually qualified.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from mage_ptcg.continuous_league.contracts import content_id
from mage_ptcg.exact_file import ExactFileSnapshotError, read_exact_regular_file
from mage_ptcg.meta_specialist.actor_visible_features_v1 import (
    CardVocabularyV1,
    require_production_card_vocabulary_v1,
)
from mage_ptcg.meta_specialist.decks import (
    ArchetypeSpec,
    DeckAssetInput,
    DeckLineageError,
    DeckLockDecision,
    DeckQualificationError,
    QualifiedDeckAsset,
    create_deck_lock,
    qualify_deck_asset,
    require_lineage_deck,
)
from mage_ptcg.meta_specialist.runtime import (
    PackagedAgentBinding,
    RuntimeConstraintManifest,
    StepLogitPolicyFactory,
    make_agent,
)


class EntrypointContractError(ValueError):
    """Raised when an extracted specialist bundle directory cannot be safely booted."""


_MANIFEST_NAME = "meta_specialist_bundle.json"
_MANIFEST_SCHEMA = "meta-specialist-bundle-manifest-v1"
_STATIC_POLICY_DOMAIN = "meta-specialist-static-policy-v1"
_MAX_MANIFEST_BYTES = 4 * 1024 * 1024
# Matches package.py's own per-member bound; a member cannot legally exceed
# the whole-archive expansion limit it enforces at build time.
_MAX_MEMBER_BYTES = 12_388_608 * 1024
_CANDIDATE_CLASSES = frozenset({"checkpointed_specialist", "static_rule_bundle"})

_QUALIFIED_ASSET_KEYS = frozenset({
    "schema_version", "asset_id", "archetype_id", "card_ids", "deck_identity",
    "deck_file_sha256", "source_ref", "source_commit", "asset_class",
    "usage_boundary", "policy_compatibility", "card_database_version", "card_count",
    "cabt_legality_status", "cabt_legality_evidence",
})
_DECK_LOCK_KEYS = frozenset({
    "schema_version", "archetype_id", "selected_deck_identity", "compared_deck_identities",
    "foundation_init_id", "joint_race_schedule_id", "equal_transition_budget",
    "deck_lock_id", "policy_lineage_id",
})


def _read_member(root: Path, name: str, *, max_bytes: int) -> bytes:
    try:
        return read_exact_regular_file(Path(root) / name, max_bytes=max_bytes).payload
    except ExactFileSnapshotError as exc:
        raise EntrypointContractError(
            f"required bundle member {name!r} is missing, unreadable, or too large under {root}"
        ) from exc


def _load_manifest_document(root: Path) -> dict[str, object]:
    payload = _read_member(root, _MANIFEST_NAME, max_bytes=_MAX_MANIFEST_BYTES)
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EntrypointContractError("bundle manifest is not valid UTF-8 JSON") from exc
    if type(document) is not dict:
        raise EntrypointContractError("bundle manifest must be a JSON object")
    if document.get("schema_version") != _MANIFEST_SCHEMA:
        raise EntrypointContractError("bundle manifest schema_version is not supported")
    return document


def _reconstruct_qualified_deck_asset(manifest: Mapping[str, object], root: Path) -> QualifiedDeckAsset:
    """Replay the recorded qualification against ``deck.csv``'s exact current bytes.

    This mirrors ``package.py``'s own private ``_qualify_payload_from_source``
    pattern used to reload a local ``BundleSpec``.  Nothing here re-runs a
    real CABT legality measurement: it replays the exact evidence already
    frozen into the manifest at build time (which itself must have come from
    a genuine, externally supplied measurement -- see the CLI's
    ``qualify-deck`` command) and requires every field to bind byte-for-byte
    to the current ``deck.csv``.  Any mismatch raises.
    """
    raw = manifest.get("qualified_deck_asset")
    if type(raw) is not dict or set(raw) != _QUALIFIED_ASSET_KEYS:
        raise EntrypointContractError("bundle manifest qualified_deck_asset has invalid fields")
    recorded_evidence = raw.get("cabt_legality_evidence")
    if type(recorded_evidence) is not str or not recorded_evidence.strip():
        raise EntrypointContractError("recorded cabt_legality_evidence must be a nonempty string")
    deck_path = Path(root) / "deck.csv"
    try:
        asset = DeckAssetInput.from_path(
            asset_id=raw["asset_id"],
            archetype_id=raw["archetype_id"],
            path=deck_path,
            source_ref=raw["source_ref"],
            source_commit=raw["source_commit"],
            asset_class=raw["asset_class"],
            usage_boundary=raw["usage_boundary"],
            policy_compatibility=raw["policy_compatibility"],
            card_database_version=raw["card_database_version"],
        )
        qualified = qualify_deck_asset(
            asset,
            ArchetypeSpec(asset.archetype_id, (), (asset.card_ids[0],), "qualified_not_trained"),
            known_card_ids=set(asset.card_ids),
            cabt_legality=lambda _cards, _evidence=recorded_evidence: (True, _evidence),
        )
    except (DeckQualificationError, KeyError, IndexError, TypeError) as exc:
        raise EntrypointContractError(
            f"could not replay the bundle's qualified deck asset from deck.csv at {deck_path}"
        ) from exc
    if (
        list(qualified.card_ids) != raw.get("card_ids")
        or qualified.deck_identity != raw.get("deck_identity")
        or qualified.deck_file_sha256 != raw.get("deck_file_sha256")
        or qualified.card_count != raw.get("card_count")
        or qualified.cabt_legality_status != raw.get("cabt_legality_status")
        or qualified.cabt_legality_evidence != recorded_evidence
    ):
        raise EntrypointContractError(
            "deck.csv under the bundle root does not exactly match the recorded qualified deck asset"
        )
    return qualified


def _reconstruct_deck_lock(manifest: Mapping[str, object]) -> DeckLockDecision:
    raw = manifest.get("deck_lock")
    if type(raw) is not dict or set(raw) != _DECK_LOCK_KEYS:
        raise EntrypointContractError("bundle manifest deck_lock has invalid fields")
    try:
        lock = create_deck_lock(
            archetype_id=raw["archetype_id"],
            selected_deck_identity=raw["selected_deck_identity"],
            compared_deck_identities=raw["compared_deck_identities"],
            foundation_init_id=raw["foundation_init_id"],
            joint_race_schedule_id=raw["joint_race_schedule_id"],
            equal_transition_budget=raw["equal_transition_budget"],
        )
    except (DeckLineageError, TypeError) as exc:
        raise EntrypointContractError("could not replay the bundle's deck lock") from exc
    if lock.deck_lock_id != raw.get("deck_lock_id") or lock.policy_lineage_id != raw.get("policy_lineage_id"):
        raise EntrypointContractError("bundle deck_lock content-addressed IDs do not match its own fields")
    return lock


def _reconstruct_runtime_constraints(manifest: Mapping[str, object]) -> RuntimeConstraintManifest:
    constraints = RuntimeConstraintManifest.frozen_v1()
    if constraints.to_payload() != manifest.get("runtime_constraints"):
        raise EntrypointContractError(
            "bundle runtime_constraints do not match the frozen v1 contract this runtime enforces"
        )
    return constraints


def _reconstruct_policy_members(manifest: Mapping[str, object]) -> tuple[str, str | None, tuple[str, ...]]:
    candidate_class = manifest.get("candidate_class")
    if candidate_class not in _CANDIDATE_CLASSES:
        raise EntrypointContractError("bundle candidate_class is invalid")
    policy_members_raw = manifest.get("policy_members")
    if (
        type(policy_members_raw) is not list
        or not policy_members_raw
        or any(type(item) is not str for item in policy_members_raw)
        or tuple(policy_members_raw) != tuple(sorted(policy_members_raw))
        or len(set(policy_members_raw)) != len(policy_members_raw)
    ):
        raise EntrypointContractError("bundle policy_members must be a sorted, unique, nonempty list of strings")
    model_member = manifest.get("model_member")
    if model_member is not None and type(model_member) is not str:
        raise EntrypointContractError("bundle model_member is invalid")
    return candidate_class, model_member, tuple(policy_members_raw)


def _reconstruct_policy_identity(
    root: Path, *, candidate_class: str, policy_members: tuple[str, ...], model_member: str | None,
) -> str:
    """Recompute ``policy_identity`` from the current on-disk policy bytes.

    Uses exactly the two formulas ``package.py`` freezes into the manifest at
    build time (``sha256`` of the sole model file for a checkpointed
    specialist; a domain-separated content ID over sorted member records for
    a static rule bundle) so a corrupted or tampered extraction is detected
    here rather than silently trusted.
    """
    if candidate_class == "checkpointed_specialist":
        if model_member is None or policy_members != (model_member,):
            raise EntrypointContractError("checkpointed bundle must declare exactly one model policy member")
        payload = _read_member(root, model_member, max_bytes=_MAX_MEMBER_BYTES)
        return sha256(payload).hexdigest()
    if model_member is not None:
        raise EntrypointContractError("static rule bundle must not declare a model_member")
    records = []
    for name in sorted(policy_members):
        payload = _read_member(root, name, max_bytes=_MAX_MEMBER_BYTES)
        records.append({"path": name, "sha256": sha256(payload).hexdigest(), "size": len(payload)})
    return content_id(_STATIC_POLICY_DOMAIN, records)


@dataclass(frozen=True, slots=True)
class LoadedSpecialistBundle:
    """Every runtime-facing fact reconstructed from one verified bundle root."""

    root: Path
    manifest: Mapping[str, object]
    qualified_deck_asset: QualifiedDeckAsset
    deck_lock: DeckLockDecision
    runtime_constraints: RuntimeConstraintManifest
    policy_identity: str
    candidate_class: str
    policy_members: tuple[str, ...]
    model_member: str | None


def load_specialist_bundle(root: Path) -> LoadedSpecialistBundle:
    """Reconstruct every runtime-facing fact from an already-extracted bundle root.

    Raises :class:`EntrypointContractError` on any missing file, malformed
    manifest field, or byte-level inconsistency.  Never substitutes a
    placeholder deck, a fabricated hash, or an unmeasured CABT result.
    """
    root = Path(root)
    manifest = _load_manifest_document(root)
    qualified = _reconstruct_qualified_deck_asset(manifest, root)
    lock = _reconstruct_deck_lock(manifest)
    try:
        require_lineage_deck(lock, qualified.deck_identity)
    except DeckLineageError as exc:
        raise EntrypointContractError("bundle deck_lock does not bind the qualified deck") from exc
    constraints = _reconstruct_runtime_constraints(manifest)
    candidate_class, model_member, policy_members = _reconstruct_policy_members(manifest)
    policy_identity = manifest.get("policy_identity")
    if type(policy_identity) is not str or len(policy_identity) != 64 or any(
        char not in "0123456789abcdef" for char in policy_identity
    ):
        raise EntrypointContractError("bundle policy_identity must be a lowercase SHA-256 hex digest")
    recomputed_policy_identity = _reconstruct_policy_identity(
        root, candidate_class=candidate_class, policy_members=policy_members, model_member=model_member,
    )
    if recomputed_policy_identity != policy_identity:
        raise EntrypointContractError(
            "bundle policy_identity does not match the current on-disk policy bytes"
        )
    return LoadedSpecialistBundle(
        root=root, manifest=manifest, qualified_deck_asset=qualified, deck_lock=lock,
        runtime_constraints=constraints, policy_identity=policy_identity,
        candidate_class=candidate_class, policy_members=policy_members, model_member=model_member,
    )


def build_packaged_agent(
    root: Path,
    *,
    vocabulary: CardVocabularyV1,
    policy_factory: StepLogitPolicyFactory,
    monotonic: Callable[[], float] = time.monotonic,
) -> PackagedAgentBinding:
    """Assemble the final Kaggle-facing :class:`PackagedAgentBinding` for one bundle root.

    ``vocabulary`` must independently satisfy
    :func:`require_production_card_vocabulary_v1`; see the module docstring
    for why this currently raises for every caller.  ``policy_factory`` must
    produce policy objects whose telemetry reports
    ``loaded.policy_identity`` -- ``policy_loader.py`` in the bundle is
    responsible for wiring that binding.
    """
    loaded = load_specialist_bundle(root)
    require_production_card_vocabulary_v1(vocabulary)
    return make_agent(
        deck_asset=loaded.qualified_deck_asset,
        deck_lock=loaded.deck_lock,
        vocabulary=vocabulary,
        policy_factory=policy_factory,
        expected_policy_identity=loaded.policy_identity,
        constraints=loaded.runtime_constraints,
        monotonic=monotonic,
    )


__all__ = [
    "EntrypointContractError",
    "LoadedSpecialistBundle",
    "build_packaged_agent",
    "load_specialist_bundle",
]
