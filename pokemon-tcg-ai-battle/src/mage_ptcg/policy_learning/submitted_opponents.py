"""Read-only submitted-opponent registry and leakage-safe population splits.

This module deliberately never checks out, edits, or otherwise mutates an
``agents/*`` or ``dev/*`` ref.  The historical qualification ledger remains
the authority for runtime evidence; git is only used to discover additional
refs and to derive identity metadata for them.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import hashlib
import json
from pathlib import Path
import random
import subprocess
from typing import Any, Iterable


SCHEMA = "submitted-opponent-registry-v1"
SPLIT_SCHEMA = "submitted-opponent-population-v1"
ELIGIBLE_RUNTIME = {"PROXY_RUNTIME_PASSED", "OFFICIAL_VALID_LOCAL_RUNTIME_PASSED"}
UNSUPPORTED_RUNTIME = {"LOCAL_RUNTIME_UNSUPPORTED", "OFFICIAL_VALID_LOCAL_RUNTIME_UNSUPPORTED"}


class SubmittedOpponentError(ValueError):
    pass


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, check=False)
    if completed.returncode:
        raise SubmittedOpponentError(completed.stderr.strip() or "git read failed")
    return completed.stdout.strip()


def enumerate_refs(repo: str | Path) -> list[str]:
    """Return the current remote agent/dev refs without changing them."""
    root = Path(repo)
    values = _git(
        root,
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/remotes/origin/agent",
        "refs/remotes/origin/agents",
        "refs/remotes/origin/dev",
    )
    return sorted(
        value
        for value in values.splitlines()
        if value.startswith(("origin/agent/", "origin/agents/"))
        or value == "origin/dev"
        or value.startswith("origin/dev/")
    )


def _git_file_hash(repo: Path, ref: str, name: str) -> str | None:
    completed = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{name}"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    return hashlib.sha256(completed.stdout).hexdigest() if completed.returncode == 0 else None


@dataclass(frozen=True, slots=True)
class SubmittedAsset:
    asset_id: str
    ref: str
    source_commit: str
    submission_source_commit: str
    source_lineage: str
    exactness: str
    deck_id: str
    deck_hash: str
    policy_id: str
    policy_hash: str
    adapter_hash: str
    runtime_config_hash: str
    deck_family: str
    entrypoint: str
    local_runtime_status: str
    official_runtime_evidence: bool
    previous_smoke_evidence: bool
    previous_tournament_evidence: bool
    current_ref_commit: str = ""
    ref_matches_source_commit: bool = False
    qualification: str = "UNQUALIFIED"
    duplicate_of: str | None = None

    @property
    def identity_key(self) -> tuple[str, str, str]:
        return (self.policy_hash, self.deck_hash, self.source_lineage)

    def population_entry(self) -> dict[str, Any]:
        value = asdict(self)
        value["opponent_id"] = self.asset_id
        value["opponent_type"] = "SUBMITTED_ASSET"
        value["bucket"] = "submitted_agents_dev"
        return value


def _asset_from_row(row: dict[str, str]) -> SubmittedAsset:
    asset_id = row.get("asset_id", "").strip()
    if not asset_id:
        raise SubmittedOpponentError("asset ledger row has no asset_id")
    branch_tip = row.get("branch_tip", "").strip()
    submission_source_commit = row.get("source_commit", "").strip()
    # Runtime qualification was executed against the archived branch_tip
    # bytes.  A separate official-submission commit may describe lineage but
    # cannot replace those bytes unless Deck/Policy hashes also match.
    source_commit = branch_tip or submission_source_commit
    policy_hash = row.get("policy_hash", "").strip()
    deck_hash = row.get("deck_hash", "").strip()
    if not source_commit or not policy_hash or not deck_hash:
        raise SubmittedOpponentError(f"asset ledger row lacks identity fields: {asset_id}")
    status = row.get("local_runtime_status", "").strip() or "UNQUALIFIED"
    qualification = "TRAINING_ELIGIBLE" if status in ELIGIBLE_RUNTIME else (
        "DIAGNOSTIC_ONLY_LOCAL_UNSUPPORTED" if status in UNSUPPORTED_RUNTIME else "INCOMPATIBLE")
    evidence = row.get("evidence", "")
    return SubmittedAsset(
        asset_id=asset_id, ref=row.get("ref", "").strip(), source_commit=source_commit,
        submission_source_commit=submission_source_commit,
        # The old ledger has no source-lineage column.  Its individual
        # ``asset_id`` is the narrowest supported provenance boundary; using
        # one shared ``origin/dev`` commit would falsely merge unrelated
        # submitted policies into a single holdout group.
        # Submission lineage defines split isolation; runtime qualification
        # commit defines executable bytes.  They intentionally differ for
        # proxy-qualified dev assets and must not be conflated.
        source_lineage=row.get("source_lineage", "").strip() or f"{submission_source_commit or source_commit}:{asset_id}",
        exactness=row.get("exactness", "").strip() or "UNKNOWN", deck_id=row.get("deck_id", "").strip() or "deck.csv",
        deck_hash=deck_hash, policy_id=row.get("policy_id", "").strip() or asset_id, policy_hash=policy_hash,
        adapter_hash=row.get("adapter_hash", "").strip(), runtime_config_hash=row.get("runtime_config_hash", "").strip(),
        deck_family=row.get("deck_family", "").strip() or "UNKNOWN", entrypoint=row.get("entrypoint", "").strip() or "main.py:agent",
        local_runtime_status=status, official_runtime_evidence=row.get("official_runtime_evidence", "").strip().lower() == "true",
        previous_smoke_evidence=int(row.get("smoke_games", "0") or 0) > 0,
        previous_tournament_evidence=row.get("teacher_eligible", "").strip().lower() == "true", qualification=qualification,
    )


def _discovered_asset(repo: Path, ref: str) -> SubmittedAsset:
    commit = _git(repo, "rev-parse", ref)
    suffix = ref.removeprefix("origin/")
    asset_id = suffix
    # A bare origin/dev has no individual executable identity; it is retained
    # as an auditable incompatible discovery instead of inventing an adapter.
    deck_hash = _git_file_hash(repo, ref, "deck.csv") or ""
    policy_hash = _git_file_hash(repo, ref, "main.py") or ""
    entrypoint = "main.py:agent" if policy_hash else ""
    state = "INCOMPATIBLE" if not deck_hash or not policy_hash else "UNQUALIFIED"
    return SubmittedAsset(asset_id=asset_id, ref=ref, source_commit=commit, submission_source_commit="", source_lineage=commit,
                          exactness="DISCOVERED_UNQUALIFIED", deck_id="deck.csv", deck_hash=deck_hash or _digest([ref, "no-deck"]),
                          policy_id=asset_id, policy_hash=policy_hash or _digest([ref, "no-policy"]), adapter_hash="",
                          runtime_config_hash="", deck_family="UNKNOWN", entrypoint=entrypoint,
                          local_runtime_status="UNQUALIFIED", official_runtime_evidence=False,
                          previous_smoke_evidence=False, previous_tournament_evidence=False,
                          current_ref_commit=commit, ref_matches_source_commit=True, qualification=state)


def load_registry(repo: str | Path, ledger: str | Path, *, include_discovered: bool = True) -> list[SubmittedAsset]:
    """Merge the ledger with every currently visible ``agents``/``dev`` ref."""
    root = Path(repo); ledger_path = Path(ledger)
    with ledger_path.open(newline="", encoding="utf-8") as handle:
        # Score-only Rule/student calibration rows share this ledger but are
        # not submitted assets and have no immutable external identity.
        values = [_asset_from_row(row) for row in csv.DictReader(handle)
                  if str(row.get("asset_id", "")).startswith(("agents/", "dev/"))]
    reconciled: list[SubmittedAsset] = []
    for item in values:
        current = ""
        if item.ref:
            try: current = _git(root, "rev-parse", item.ref)
            except SubmittedOpponentError: current = ""
        # The runtime identity is pinned to ``source_commit``.  A ref that
        # advanced after qualification is visible metadata, never silently
        # substituted for the qualified snapshot.
        reconciled.append(SubmittedAsset(**{**asdict(item), "current_ref_commit": current,
                                            "ref_matches_source_commit": bool(current and current == item.source_commit)}))
    values = reconciled
    by_ref = {item.ref: item for item in values if item.ref}
    if include_discovered:
        for ref in enumerate_refs(root):
            if ref not in by_ref:
                values.append(_discovered_asset(root, ref))
    # One canonical asset represents policy/deck/lineage duplicates; merely
    # changing an adapter or submission version cannot increase its weight.
    canonical: dict[tuple[str, str, str], SubmittedAsset] = {}
    output: list[SubmittedAsset] = []
    for item in sorted(values, key=lambda value: (value.asset_id, value.ref)):
        existing = canonical.get(item.identity_key)
        if existing is None:
            canonical[item.identity_key] = item; output.append(item)
        else:
            output.append(SubmittedAsset(**{**asdict(item), "qualification": "DUPLICATE_POLICY" if item.policy_hash == existing.policy_hash else "DUPLICATE_LINEAGE", "duplicate_of": existing.asset_id}))
    return output


def registry_document(assets: Iterable[SubmittedAsset]) -> dict[str, Any]:
    entries = [asdict(asset) for asset in sorted(assets, key=lambda value: value.asset_id)]
    return {"schema": SCHEMA, "assets": entries, "registry_hash": _digest(entries)}


def _groups(assets: Iterable[SubmittedAsset]) -> list[list[SubmittedAsset]]:
    """Return connected identity components for leakage-safe splitting.

    A split boundary must not separate assets that share *any* executable
    policy, source lineage, or exact deck.  Grouping by lineage alone allowed
    the same deck to appear in training and ``deck_holdout`` when independently
    submitted policies used identical cards.  Unioning all three identities
    also handles transitive cases (same deck as B, while B shares a policy with
    C) without depending on ledger row order.
    """
    eligible = sorted(
        (asset for asset in assets if asset.qualification == "TRAINING_ELIGIBLE"),
        key=lambda value: value.asset_id,
    )
    parent = list(range(len(eligible)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[max(left_root, right_root)] = min(left_root, right_root)

    owners: dict[tuple[str, str], int] = {}
    for index, asset in enumerate(eligible):
        identities = (
            ("policy_hash", asset.policy_hash),
            ("source_lineage", asset.source_lineage),
            ("deck_hash", asset.deck_hash),
        )
        for kind, value in identities:
            if not value:
                raise SubmittedOpponentError(f"eligible asset {asset.asset_id} lacks {kind}")
            prior = owners.setdefault((kind, value), index)
            union(index, prior)

    grouped: dict[int, list[SubmittedAsset]] = {}
    for index, asset in enumerate(eligible):
        grouped.setdefault(find(index), []).append(asset)
    return sorted(
        (sorted(items, key=lambda value: value.asset_id) for items in grouped.values()),
        key=lambda items: tuple(value.asset_id for value in items),
    )


def split_assets(assets: Iterable[SubmittedAsset], *, seed: int = 71000) -> dict[str, list[SubmittedAsset]]:
    """Deterministically split policy/lineage groups into four disjoint uses."""
    groups = _groups(assets)
    if len(groups) < 4:
        raise SubmittedOpponentError("at least four unique eligible policy/lineage groups are required")
    random.Random(seed).shuffle(groups)
    # 60/20/10/10 is the smallest four-way refinement preserving the stated
    # 60/20/20 train/development/final envelope while retaining a deck gate.
    names = ("training", "validation", "deck_holdout", "final_holdout")
    desired = (0.60, 0.20, 0.10, 0.10)
    counts = [max(1, round(len(groups) * fraction)) for fraction in desired]
    while sum(counts) > len(groups):
        index = max(range(len(counts)), key=lambda value: (counts[value], -value))
        if counts[index] == 1:
            raise SubmittedOpponentError("insufficient groups for four-way split")
        counts[index] -= 1
    while sum(counts) < len(groups):
        counts[0] += 1
    result: dict[str, list[SubmittedAsset]] = {name: [] for name in names}; offset = 0
    for name, count in zip(names, counts, strict=True):
        result[name] = [asset for group in groups[offset:offset + count] for asset in group]; offset += count
    assert_no_leakage(result)
    return result


def assert_no_leakage(splits: dict[str, Iterable[SubmittedAsset]]) -> None:
    seen_policy: dict[str, str] = {}; seen_lineage: dict[str, str] = {}; seen_deck: dict[str, str] = {}
    for split, assets in splits.items():
        for asset in assets:
            for value, seen, label in (
                (asset.policy_hash, seen_policy, "policy hash"),
                (asset.source_lineage, seen_lineage, "source lineage"),
                (asset.deck_hash, seen_deck, "deck hash"),
            ):
                prior = seen.get(value)
                if prior is not None and prior != split:
                    raise SubmittedOpponentError(f"{label} leaks from {prior} into {split}")
                seen[value] = split


def population_document(assets: Iterable[SubmittedAsset], *, split: str, seed: int) -> dict[str, Any]:
    entries = [asset.population_entry() for asset in sorted(assets, key=lambda value: value.asset_id)]
    document = {"schema": SPLIT_SCHEMA, "split": split, "split_seed": seed,
                "sampling": {"mode": "policy_hash_uniform", "bucket": "submitted_agents_dev",
                             "bucket_weights": {"submitted_agents_dev": 0.50, "rule_v0_v1": 0.20,
                                                "family_policies": 0.15, "historical_candidate_snapshots": 0.10,
                                                "stress_uniform": 0.05}}, "entries": entries}
    document["population_hash"] = _digest(document)
    return document


def write_split_manifests(output_dir: str | Path, assets: Iterable[SubmittedAsset], *, seed: int = 71000) -> dict[str, Path]:
    root = Path(output_dir); root.mkdir(parents=True, exist_ok=True)
    splits = split_assets(assets, seed=seed)
    names = {"training": "population-submitted-training-v1.json", "validation": "population-submitted-validation-v1.json", "deck_holdout": "population-submitted-deck-holdout-v1.json", "final_holdout": "population-submitted-final-holdout-v1.json"}
    outputs: dict[str, Path] = {}
    for split, name in names.items():
        path = root / name; path.write_text(json.dumps(population_document(splits[split], split=split, seed=seed), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"); outputs[split] = path
    report = {"schema": "submitted-opponent-leakage-report-v1", "leakage": False, "split_sizes": {name: len(value) for name, value in splits.items()}}
    (root / "split_leakage_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return outputs


def materialize_runtime_population(*, source_population: str | Path, assets: Iterable[SubmittedAsset], split: str, seed: int = 71000) -> dict[str, Any]:
    """Translate a metadata split into the existing executable population schema.

    Runtime adapters are copied only from an already validated source
    population.  This is the bridge that lets the current PPO/V-trace CABT
    runner use submitted assets without importing their source in-process.
    """
    from mage_ptcg.offline_scaleup.pipeline import POPULATION_SCHEMA, _digest, validate_population
    source = json.loads(Path(source_population).read_text(encoding="utf-8"))
    if source.get("schema_version") != POPULATION_SCHEMA: raise SubmittedOpponentError("runtime source population has unsupported schema")
    selected = split_assets(assets, seed=seed).get(split)
    if selected is None: raise SubmittedOpponentError("unknown submitted split")
    by_policy = {asset.policy_hash: asset for asset in selected}; entries: list[dict[str, Any]] = []
    # Keep Rule-v0/v1 diagnostic fallbacks available while submitted assets
    # occupy the configured 50% sampler bucket at scheduling time.
    for entry in source.get("entries", []):
        policy = str(entry.get("agent_digest", "")); is_rule = str(entry.get("opponent_type", "")).startswith("RULE_")
        if policy not in by_policy and not is_rule: continue
        copied = json.loads(json.dumps(entry))
        if policy in by_policy:
            asset = by_policy[policy]; provenance = dict(copied.get("provenance") or {})
            provenance["submitted_identity"] = {"policy_hash": asset.policy_hash, "deck_hash": asset.deck_hash, "source_lineage": asset.source_lineage, "deck_family": asset.deck_family, "sampling_bucket": "submitted_agents_dev"}
            copied["provenance"] = provenance
        entries.append(copied)
    if len({entry.get("agent_digest") for entry in entries if entry.get("agent_digest") in by_policy}) != len(by_policy):
        missing = sorted(set(by_policy) - {str(entry.get("agent_digest")) for entry in entries})
        raise SubmittedOpponentError(f"validated runtime source lacks submitted policy adapters: {missing}")
    entries.sort(key=lambda entry: str(entry["opponent_id"])); semantic = [{key: value for key, value in entry.items() if key not in {"source_path", "evidence_paths"}} for entry in entries]
    result = {"schema_version": POPULATION_SCHEMA, "entries": entries, "semantic_population_digest": _digest(semantic, "population"), "alias_count": 0,
              "created_by": "submitted-opponents-runtime-bridge-v1", "submitted_split": split, "split_seed": seed,
              "sampling": {"submitted_agents_dev": 0.50, "rule_v0_v1": 0.20, "family_policies": 0.15, "historical_candidate_snapshots": 0.10, "stress_uniform": 0.05}}
    result["population_id"] = "population-" + result["semantic_population_digest"][:16]
    # Strip bridge-only top-level fields for validation, since the executor's
    # immutable population schema intentionally stays narrow.
    validate_population({key: value for key, value in result.items() if key not in {"submitted_split", "split_seed", "sampling", "created_by"}})
    return result


__all__ = ["SubmittedAsset", "SubmittedOpponentError", "assert_no_leakage", "enumerate_refs", "load_registry", "materialize_runtime_population", "population_document", "registry_document", "split_assets", "write_split_manifests"]
