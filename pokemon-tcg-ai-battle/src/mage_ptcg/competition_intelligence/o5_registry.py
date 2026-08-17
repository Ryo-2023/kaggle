"""O5 deck/archetype registry and read-only source acquisition primitives.

The module deliberately has no Kaggle client.  Environment callers pass data
obtained by the existing typed transport/archive path; branch callers read
objects through ``git ls-tree``/``git show`` without checking out a ref.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .atomic_io import atomic_write_json
from .canonical import digest
from .contracts import AllowedUse, SourceKind
from .rules_attestation import RulesAttestation


O5_REGISTRY_SCHEMA_VERSION = "o5-deck-archetype-registry-v1"
ENVIRONMENT_POLICY_SCHEMA_VERSION = "o5-environment-top-decks-policy-v1"
CAPTURE_ONLY = "CAPTURE_ONLY"
CLASSIFY_AND_ANALYZE = "CLASSIFY_AND_ANALYZE"
TEAM_SHARED_PENDING_PERMISSION = "TEAM_SHARED_PENDING_PERMISSION"


class O5RegistryError(RuntimeError):
    """A typed, user-facing O5 registry failure."""


def _sha256(value: bytes | str) -> str:
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_deck_hash(cards: Iterable[int], *, card_pool_version: str = "unknown") -> str:
    """Stable identity for a card-pool-scoped, unordered 60-card multiset."""
    normalized = sorted(cards)
    if len(normalized) != 60 or any(type(card) is not int or card <= 0 for card in normalized):
        raise O5RegistryError("exact deck must be a 60-card integer multiset")
    return digest({"card_pool_version": card_pool_version, "cards": normalized}, domain="o5-deck-identity")


def parse_exact_deck_text(text: str) -> tuple[int, ...] | None:
    """Parse only plain integer deck CSV.  Invalid/partial content stays non-exact."""
    tokens = [item.strip() for item in re.split(r"[\s,]+", text.strip()) if item.strip()]
    if len(tokens) != 60:
        return None
    try:
        cards = tuple(int(item) for item in tokens)
    except ValueError:
        return None
    return cards if all(card > 0 for card in cards) else None


@dataclass(frozen=True, slots=True)
class EnvironmentTopDeckPolicy:
    top_rating_submissions: int = 50
    recent_or_rising_submissions: int = 25
    diversity_submissions: int = 25
    max_episodes_per_submission_lineage: int = 5
    max_new_replays_per_run: int = 200
    max_total_exact_decks_per_snapshot: int = 500
    minimum_archetype_diversity: int = 8
    resume: bool = True

    def __post_init__(self) -> None:
        if any(getattr(self, key) < 0 for key in self.__dataclass_fields__ if type(getattr(self, key)) is int):
            raise O5RegistryError("environment acquisition limits must be non-negative")

    @classmethod
    def load(cls, path: str | Path) -> "EnvironmentTopDeckPolicy":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise O5RegistryError("environment policy must be an object")
        content = raw.get("environment_top_decks", raw)
        if not isinstance(content, Mapping):
            raise O5RegistryError("environment_top_decks must be an object")
        allowed = set(cls.__dataclass_fields__)
        unknown = set(content) - allowed - {"schema_version"}
        if unknown:
            raise O5RegistryError(f"unknown environment policy keys: {sorted(unknown)}")
        return cls(**{key: value for key, value in content.items() if key in allowed})

    def as_dict(self) -> dict[str, Any]:
        return {"schema_version": ENVIRONMENT_POLICY_SCHEMA_VERSION, **{key: getattr(self, key) for key in self.__dataclass_fields__}}


DEFAULT_ENVIRONMENT_POLICY = EnvironmentTopDeckPolicy()


class DeckArchetypeRegistry:
    """Small canonical JSON registry with one deck row and many observations."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.path = self.root / "deck_archetype_registry.json"
        self.data = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": O5_REGISTRY_SCHEMA_VERSION,
                "deck_lists": {}, "deck_observations": [], "acquisition_runs": [],
                "source_candidates": [], "environment_submission_samples": [],
                "environment_episode_samples": [], "branch_inventories": [],
                "branch_artifacts": [], "agent_deck_links": [], "candidate_exclusions": [],
            }
        try:
            result = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise O5RegistryError("registry is not valid JSON") from exc
        if not isinstance(result, dict) or result.get("schema_version") != O5_REGISTRY_SCHEMA_VERSION:
            raise O5RegistryError("unsupported deck archetype registry schema")
        for key in ("deck_lists", "deck_observations", "acquisition_runs", "source_candidates", "environment_submission_samples", "environment_episode_samples", "branch_inventories", "branch_artifacts", "agent_deck_links", "candidate_exclusions"):
            result.setdefault(key, {} if key == "deck_lists" else [])
        return result

    def save(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, self.data)
        return self.path

    @property
    def known_observation_ids(self) -> set[str]:
        return {str(row["observation_id"]) for row in self.data["deck_observations"]}

    def add_exact_deck(self, cards: Iterable[int], *, card_pool_version: str, provenance: Mapping[str, Any]) -> str:
        cards_tuple = tuple(cards)
        deck_hash = canonical_deck_hash(cards_tuple, card_pool_version=card_pool_version)
        existing = self.data["deck_lists"].get(deck_hash)
        if existing is None:
            self.data["deck_lists"][deck_hash] = {
                "deck_hash": deck_hash, "card_pool_version": card_pool_version,
                "cards": sorted(cards_tuple), "provenance": [dict(provenance)],
            }
        elif dict(provenance) not in existing["provenance"]:
            existing["provenance"].append(dict(provenance))
        return deck_hash

    def add_observation(self, row: Mapping[str, Any]) -> bool:
        value = dict(row)
        required = {"observation_id", "source_kind", "observation_channel", "observed_at", "exact"}
        if not required.issubset(value):
            raise O5RegistryError(f"observation is missing fields: {sorted(required - set(value))}")
        if value["observation_id"] in self.known_observation_ids:
            return False
        if bool(value["exact"]) != bool(value.get("deck_hash")):
            raise O5RegistryError("exact observation must have a deck_hash; incomplete observation must not")
        self.data["deck_observations"].append(value)
        return True

    def add_unique(self, table: str, row: Mapping[str, Any], *, keys: Sequence[str]) -> bool:
        rows = self.data[table]
        value = dict(row)
        if any(all(item.get(key) == value.get(key) for key in keys) for item in rows):
            return False
        rows.append(value)
        return True

    def reconcile(self) -> dict[str, dict[str, Any]]:
        stats: dict[str, dict[str, Any]] = {}
        branch_deck_provenance: dict[str, set[tuple[str, str]]] = defaultdict(set)
        for deck_hash, deck in self.data["deck_lists"].items():
            for item in deck.get("provenance", []):
                if item.get("source_kind") == "TEAM_SHARED":
                    branch_deck_provenance[deck_hash].add((str(item.get("branch_ref")), str(item.get("path"))))
        agent_counts: Counter[str] = Counter(
            str(link["deck_hash"]) for link in self.data["agent_deck_links"]
            if link.get("deck_hash") and link.get("link_status") == "VERIFIED_LINK"
        )
        for deck_hash in sorted(self.data["deck_lists"]):
            observations = [row for row in self.data["deck_observations"] if row.get("deck_hash") == deck_hash]
            source_kinds = Counter(str(row.get("source_kind")) for row in observations)
            lineages = {str(row["submission_lineage"]) for row in observations if row.get("submission_lineage")}
            timestamps = sorted(str(row["observed_at"]) for row in observations if row.get("observed_at"))
            rating_bands = Counter(str(row.get("rating_band")) for row in observations if row.get("rating_band"))
            stats[deck_hash] = {
                "deck_hash": deck_hash,
                "environment_observation_count": sum(row.get("source_kind") in {"PUBLIC_OTHER", "OWN_KAGGLE"} for row in observations),
                "own_encounter_count": sum(row.get("observation_channel") == "OWN_ENCOUNTER" for row in observations),
                "team_branch_count": len(branch_deck_provenance[deck_hash]),
                "team_agent_count": agent_counts[deck_hash],
                "submission_lineage_count": len(lineages),
                "first_seen": timestamps[0] if timestamps else None,
                "last_seen": timestamps[-1] if timestamps else None,
                "rating_band_distribution": dict(sorted(rating_bands.items())),
                "source_kind_distribution": dict(sorted(source_kinds.items())),
                "exact_observation_count": sum(bool(row.get("exact")) for row in observations),
                "incomplete_observation_count": sum(not bool(row.get("exact")) for row in observations),
            }
        self.data["deck_source_statistics"] = stats
        return stats


def _rating_band(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return f"{int(value) // 100 * 100}-{int(value) // 100 * 100 + 99}"


@dataclass(frozen=True, slots=True)
class EnvironmentDeckAcquisitionResult:
    mode: str
    candidate_count: int
    sampled_submission_count: int
    discovered_episode_count: int
    acquired_replay_count: int
    exact_deck_count: int
    incomplete_observation_count: int
    manifest_path: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {key: getattr(self, key) for key in self.__dataclass_fields__}


class EnvironmentTopDeckCollector:
    """Select, archive, and register replay observations supplied by O3/O4."""

    def collect(
        self, leaderboard_snapshot: Sequence[Mapping[str, Any]], submission_registry: Mapping[str, Mapping[str, Any]],
        episode_registry: Mapping[str, Sequence[Mapping[str, Any]]], acquisition_policy: EnvironmentTopDeckPolicy,
        rules_attestation: RulesAttestation | None, *, registry: DeckArchetypeRegistry, now: str | None = None,
    ) -> EnvironmentDeckAcquisitionResult:
        observed_at = now or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        mode = CLASSIFY_AND_ANALYZE if rules_attestation and rules_attestation.permits_public_other_collection() else CAPTURE_ONLY
        scored: list[dict[str, Any]] = []
        for rank, item in enumerate(leaderboard_snapshot, start=1):
            submission_id = str(item.get("submission_id", item.get("id", "")))
            if not submission_id:
                continue
            meta = dict(submission_registry.get(submission_id, {}))
            lineage = str(meta.get("submission_lineage", item.get("submission_lineage", submission_id)))
            score_parts = {
                "rating_rank_weight": max(0, acquisition_policy.top_rating_submissions - rank + 1),
                "rating_growth_weight": float(meta.get("rating_growth", item.get("rating_growth", 0)) or 0),
                "recency_weight": float(meta.get("recency_weight", item.get("recency_weight", 0)) or 0),
                "archetype_novelty": float(meta.get("archetype_novelty", 0) or 0),
                "deck_distance": float(meta.get("deck_distance", 0) or 0),
                "student_weakness": float(meta.get("student_weakness", 0) or 0),
            }
            score_parts["duplicate_lineage_penalty"] = 0.0
            score_parts["already_sampled_penalty"] = 1000000.0 if submission_id in {row.get("submission_id") for row in registry.data["environment_submission_samples"]} else 0.0
            score = sum(value for key, value in score_parts.items() if not key.endswith("penalty")) - score_parts["duplicate_lineage_penalty"] - score_parts["already_sampled_penalty"]
            scored.append({"submission_id": submission_id, "submission_lineage": lineage, "rank": rank, "score": score, "score_parts": score_parts, "rating_band": _rating_band(item.get("rating"))})
        scored.sort(key=lambda row: (-row["score"], row["submission_id"]))
        selected: list[dict[str, Any]] = []
        lineage_count: Counter[str] = Counter()
        maximum = acquisition_policy.top_rating_submissions + acquisition_policy.recent_or_rising_submissions + acquisition_policy.diversity_submissions
        for candidate in scored:
            if len(selected) >= maximum:
                break
            if lineage_count[candidate["submission_lineage"]] >= 1:
                candidate["score_parts"]["duplicate_lineage_penalty"] = 1.0
                continue
            lineage_count[candidate["submission_lineage"]] += 1
            selected.append(candidate)
            registry.add_unique("source_candidates", candidate, keys=("submission_id",))
            registry.add_unique("environment_submission_samples", candidate, keys=("submission_id",))
        replay_count = exact_count = incomplete_count = discovered = 0
        already_episode = {row.get("episode_id") for row in registry.data["environment_episode_samples"]}
        for candidate in selected:
            per_lineage = 0
            for episode in episode_registry.get(candidate["submission_id"], ()):
                episode_id = str(episode.get("episode_id", episode.get("id", "")))
                if not episode_id or episode_id in already_episode or per_lineage >= acquisition_policy.max_episodes_per_submission_lineage or replay_count >= acquisition_policy.max_new_replays_per_run:
                    continue
                already_episode.add(episode_id)
                per_lineage += 1
                discovered += 1
                replay_sha = str(episode.get("replay_content_hash", episode.get("content_hash", "")))
                if replay_sha and any(row.get("replay_content_hash") == replay_sha for row in registry.data["environment_episode_samples"]):
                    continue
                registry.add_unique("environment_episode_samples", {"episode_id": episode_id, "submission_id": candidate["submission_id"], "submission_lineage": candidate["submission_lineage"], "replay_content_hash": replay_sha or None, "mode": mode}, keys=("episode_id",))
                replay_count += 1
                cards = episode.get("cards")
                source_kind = str(episode.get("source_kind", SourceKind.PUBLIC_OTHER.value))
                if source_kind not in {SourceKind.PUBLIC_OTHER.value, SourceKind.OWN_KAGGLE.value}:
                    raise O5RegistryError("environment episode source_kind must be PUBLIC_OTHER or OWN_KAGGLE")
                own = bool(episode.get("own_encounter")) or source_kind == SourceKind.OWN_KAGGLE.value
                channel = "OWN_ENCOUNTER" if own else "ENVIRONMENT_TOP_DECK"
                observation_id = _sha256(_canonical({"episode_id": episode_id, "seat": episode.get("seat", 0), "replay_content_hash": replay_sha, "channel": channel}))
                if isinstance(cards, Sequence) and not isinstance(cards, (str, bytes)) and len(cards) == 60 and all(type(card) is int for card in cards):
                    card_pool_version = str(episode.get("card_pool_version", "unknown"))
                    prospective_hash = canonical_deck_hash(cards, card_pool_version=card_pool_version)
                    if prospective_hash not in registry.data["deck_lists"] and len(registry.data["deck_lists"]) >= acquisition_policy.max_total_exact_decks_per_snapshot:
                        continue
                    deck_hash = registry.add_exact_deck(cards, card_pool_version=card_pool_version, provenance={"source_kind": source_kind, "branch_ref": None, "path": None, "episode_id": episode_id})
                    registry.add_observation({"observation_id": observation_id, "source_kind": source_kind, "source_envelope_id": episode.get("source_envelope_id"), "observation_channel": channel, "observed_at": observed_at, "exact": True, "deck_hash": deck_hash, "episode_id": episode_id, "replay_content_hash": replay_sha or None, "submission_lineage": candidate["submission_lineage"], "rating_band": candidate["rating_band"], "archetype_hint": episode.get("archetype_hint"), "allowed_use": AllowedUse.ARCHIVE.value if mode == CAPTURE_ONLY else AllowedUse.ANALYSIS.value})
                    exact_count += 1
                else:
                    observed = episode.get("observed_card_counts", {})
                    registry.add_observation({"observation_id": observation_id, "source_kind": source_kind, "source_envelope_id": episode.get("source_envelope_id"), "observation_channel": channel, "observed_at": observed_at, "exact": False, "observed_card_counts": observed if isinstance(observed, Mapping) else {}, "episode_id": episode_id, "replay_content_hash": replay_sha or None, "submission_lineage": candidate["submission_lineage"], "rating_band": candidate["rating_band"], "allowed_use": AllowedUse.ARCHIVE.value})
                    incomplete_count += 1
        registry.data["acquisition_runs"].append({"run_kind": "ENVIRONMENT_TOP_DECKS", "mode": mode, "observed_at": observed_at, "candidate_count": len(scored), "sampled_submission_count": len(selected), "discovered_episode_count": discovered, "acquired_replay_count": replay_count, "exact_deck_count": exact_count, "incomplete_observation_count": incomplete_count, "policy": acquisition_policy.as_dict()})
        return EnvironmentDeckAcquisitionResult(mode, len(scored), len(selected), discovered, replay_count, exact_count, incomplete_count)


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(("git", "-C", str(repo), *args), capture_output=True, check=False, text=True)
    if completed.returncode != 0:
        raise O5RegistryError(f"git read failed for {' '.join(args[:2])}")
    return completed.stdout


def _blob_sha(repo: Path, ref: str, path: str) -> str:
    result = _git(repo, "ls-tree", ref, "--", path).strip().split()
    return result[2] if len(result) >= 3 else ""


def _candidate_kind(path: str, text: str, cards: tuple[int, ...] | None) -> str | None:
    lowered = path.lower()
    tokens = set(re.split(r"[/. _-]+", lowered))
    fixture = bool(tokens & {"test", "tests", "fixture", "fixtures", "sample", "samples", "example", "examples"})
    if cards is not None:
        return "INVALID_OR_STALE" if fixture else "EXACT_DECK"
    if lowered.endswith(".py") and ("def agent(" in text or "make_" in text and "agent" in text):
        return "INVALID_OR_STALE" if fixture else "EXECUTABLE_AGENT"
    if ("deck" in tokens or "agent" in tokens) and (lowered.endswith(".json") or lowered.endswith(".yaml") or lowered.endswith(".yml")):
        return "CONFIG_ONLY"
    return None


def _deck_candidate_classification(path: str) -> str:
    lowered = path.lower()
    tokens = set(re.split(r"[/. _-]+", lowered))
    if tokens & {"test", "tests", "fixture", "fixtures", "sample", "samples", "example", "examples"}:
        return "TEST_FIXTURE"
    if tokens & {"docs", "doc", "readme"}:
        return "DOCUMENTATION_EXAMPLE"
    if tokens & {"dist", "generated", "output", "artifacts"}:
        return "GENERATED_OUTPUT"
    if lowered == "deck.csv" or "/opponents/" in lowered or lowered.startswith("opponents/"):
        return "EXACT_DECK_PRODUCTION"
    return "EXACT_DECK_HISTORICAL"


@dataclass(frozen=True, slots=True)
class TeamBranchInventoryImporter:
    repository_root: Path

    def inventory(self, registry: DeckArchetypeRegistry, refs: Sequence[str] | None = None, *, observed_at: str | None = None) -> dict[str, Any]:
        repo = self.repository_root
        timestamp = observed_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        branch_refs = tuple(refs or [line.strip() for line in _git(repo, "for-each-ref", "--format=%(refname:short)", "refs/heads", "refs/remotes/origin").splitlines() if line.strip() and not line.endswith("/HEAD")])
        branch_count = deck_count = agent_count = 0
        for ref in branch_refs:
            try:
                commit_sha = _git(repo, "rev-parse", ref).strip()
            except O5RegistryError:
                registry.add_unique("branch_inventories", {"branch_ref": ref, "commit_sha": None, "status": "STALE", "observed_at": timestamp, "artifact_count": 0, "deck_count": 0, "agent_count": 0, "permission": TEAM_SHARED_PENDING_PERMISSION}, keys=("branch_ref", "commit_sha"))
                continue
            provider_identity_hash = _sha256(f"git-ref:{ref}")
            paths = [line.strip() for line in _git(repo, "ls-tree", "-r", "--name-only", ref).splitlines()]
            artifacts: list[dict[str, Any]] = []
            exact_by_dir: dict[str, str] = {}
            agent_rows: list[dict[str, Any]] = []
            for path in paths:
                lowered = path.lower()
                filename = Path(lowered).name
                path_tokens = set(re.split(r"[/. _-]+", lowered))
                deck_candidate = filename in {"deck.csv", "deck.txt", "decklist.csv"}
                agent_candidate = lowered.endswith(".py") and (filename in {"main.py", "agent.py"} or "agent" in path_tokens or "opponent" in path_tokens)
                config_candidate = lowered.endswith((".json", ".yaml", ".yml")) and bool(path_tokens & {"config", "configs", "submission", "submissions", "package", "packages"})
                # Documentation and generated artifacts are evidence only; do
                # not expand every JSON/Markdown file as a candidate artifact.
                if not (deck_candidate or agent_candidate or config_candidate):
                    continue
                text = _git(repo, "show", f"{ref}:{path}")
                cards = parse_exact_deck_text(text) if lowered.endswith(".csv") else None
                kind = _candidate_kind(path, text, cards)
                if kind is None:
                    continue
                blob = _blob_sha(repo, ref, path)
                dependencies = sorted(set(re.findall(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_.]*)", text, re.MULTILINE)))
                content_sha = _sha256(text)
                config_hash = _sha256(text) if kind == "CONFIG_ONLY" else None
                agent_identity_hash = _sha256(_canonical({"content_sha256": content_sha, "agent_factory": "agent" if "def agent(" in text else None, "entrypoint": path if kind == "EXECUTABLE_AGENT" else None, "config_hash": config_hash, "dependency_summary": dependencies})) if kind == "EXECUTABLE_AGENT" else None
                row = {"branch_ref": ref, "commit_sha": commit_sha, "path": path, "git_blob_sha": blob, "content_sha256": content_sha, "artifact_kind": kind, "schema_version": O5_REGISTRY_SCHEMA_VERSION, "agent_factory": "agent" if "def agent(" in text else None, "entrypoint": path if kind == "EXECUTABLE_AGENT" else None, "linked_deck_hash": None, "config_hash": config_hash, "agent_identity_hash": agent_identity_hash, "dependency_summary": dependencies, "provider_identity_hash": provider_identity_hash, "branch_owner_or_origin": "UNKNOWN", "source_kind": SourceKind.TEAM_SHARED.value, "permission": TEAM_SHARED_PENDING_PERMISSION, "allowed_uses": [AllowedUse.ARCHIVE.value], "agent_use": False, "deck_analysis_use": False, "evaluation_use": False, "training_use": False, "redistribution": False, "validation_status": "SOURCE_AVAILABLE" if kind == "EXECUTABLE_AGENT" else "VALID" if kind == "EXACT_DECK" else "UNKNOWN", "evidence_refs": [f"git:{ref}:{path}"]}
                row["candidate_classification"] = _deck_candidate_classification(path) if kind == "EXACT_DECK" else ("TEST_ONLY" if kind == "INVALID_OR_STALE" else "UNKNOWN")
                if kind == "EXACT_DECK" and cards is not None:
                    deck_hash = registry.add_exact_deck(cards, card_pool_version="unknown", provenance={"source_kind": "TEAM_SHARED", "branch_ref": ref, "path": path, "commit_sha": commit_sha, "git_blob_sha": blob, "permission": TEAM_SHARED_PENDING_PERMISSION})
                    row["linked_deck_hash"] = deck_hash
                    exact_by_dir[str(Path(path).parent)] = deck_hash
                    registry.add_observation({
                        "observation_id": _sha256(_canonical({"branch_ref": ref, "commit_sha": commit_sha, "path": path, "git_blob_sha": blob})),
                        "source_kind": SourceKind.TEAM_SHARED.value, "source_envelope_id": None,
                        "observation_channel": "TEAM_BRANCH", "observed_at": timestamp, "exact": True,
                        "deck_hash": deck_hash, "branch_ref": ref, "commit_sha": commit_sha,
                        "git_blob_sha": blob, "allowed_use": AllowedUse.ARCHIVE.value,
                        "permission": TEAM_SHARED_PENDING_PERMISSION,
                    })
                    deck_count += 1
                if kind == "EXECUTABLE_AGENT":
                    agent_rows.append(row)
                    agent_count += 1
                artifacts.append(row)
                registry.add_unique("branch_artifacts", row, keys=("branch_ref", "commit_sha", "path", "git_blob_sha"))
            for agent in agent_rows:
                directory = str(Path(str(agent["path"])).parent)
                deck_hash = exact_by_dir.get(directory)
                code = _git(repo, "show", f"{ref}:{agent['path']}")
                deck_reference = re.search(r"(?:open|Path)\s*\(\s*['\"]deck\.csv['\"]", code) or re.search(r"(?:file|deck|submission)_path\s*=\s*['\"]deck\.csv['\"]", code)
                status = "VERIFIED_LINK" if deck_hash and deck_reference else "UNRESOLVED_AGENT_DECK_LINK"
                registry.add_unique("agent_deck_links", {"branch_ref": ref, "commit_sha": commit_sha, "agent_path": agent["path"], "deck_hash": deck_hash, "link_status": status, "resolution_evidence": "entrypoint_deck_reference" if status == "VERIFIED_LINK" else "no_verified_reference", "permission": TEAM_SHARED_PENDING_PERMISSION, "allowed_uses": ["ARCHIVE"]}, keys=("branch_ref", "commit_sha", "agent_path", "deck_hash"))
            registry.add_unique("branch_inventories", {"branch_ref": ref, "commit_sha": commit_sha, "provider_identity_hash": provider_identity_hash, "branch_owner_or_origin": "UNKNOWN", "observed_at": timestamp, "artifact_count": len(artifacts), "deck_count": sum(row["artifact_kind"] == "EXACT_DECK" for row in artifacts), "agent_count": sum(row["artifact_kind"] == "EXECUTABLE_AGENT" for row in artifacts), "permission": TEAM_SHARED_PENDING_PERMISSION, "allowed_uses": [AllowedUse.ARCHIVE.value], "agent_use": False, "deck_analysis_use": False, "evaluation_use": False, "training_use": False, "redistribution": False}, keys=("branch_ref", "commit_sha"))
            branch_count += 1
        inventories = registry.data["branch_inventories"]
        return {"team_branches_scanned": branch_count, "team_branches_with_decks": sum(row["deck_count"] > 0 for row in inventories), "team_branches_with_agents": sum(row["agent_count"] > 0 for row in inventories), "team_exact_deck_candidates": deck_count, "team_agent_candidates": agent_count, "runnable_agents": 0, "missing_artifact_agents": 0, "manifest_path": str(registry.path)}


def coverage_report(registry: DeckArchetypeRegistry) -> dict[str, Any]:
    stats = registry.reconcile()
    links = registry.data["agent_deck_links"]
    unresolved = [row for row in links if row.get("link_status") != "VERIFIED_LINK"]
    blocked = [row for row in registry.data["deck_observations"] if row.get("source_kind") == SourceKind.PUBLIC_OTHER.value and row.get("allowed_use") == AllowedUse.ARCHIVE.value]
    classifications: list[dict[str, Any]] = []
    for deck_hash, stat in sorted(stats.items()):
        active = [row for row in registry.data["deck_observations"] if row.get("deck_hash") == deck_hash and row.get("allowed_use") == "ANALYSIS"]
        hints = Counter(str(row["archetype_hint"]) for row in active if isinstance(row.get("archetype_hint"), str) and row["archetype_hint"])
        if not active:
            status = "RULES_BLOCKED" if any(row.get("source_kind") == "PUBLIC_OTHER" for row in registry.data["deck_observations"] if row.get("deck_hash") == deck_hash) else "PERMISSION_BLOCKED"
            classifications.append({"deck_hash": deck_hash, "archetype": "UNKNOWN", "variant": "UNKNOWN", "classification_status": status, "classification_confidence": 0.0})
            continue
        archetype, count = hints.most_common(1)[0] if hints else ("UNKNOWN", 0)
        classifications.append({"deck_hash": deck_hash, "archetype": archetype, "variant": "UNKNOWN", "classification_status": "ACTIVE", "classification_confidence": count / len(active) if active else 0.0})
    registry.data["deck_classifications"] = classifications
    archetype_rows: dict[str, dict[str, Any]] = {}
    for row in classifications:
        if row["classification_status"] != "ACTIVE":
            continue
        aggregate = archetype_rows.setdefault(row["archetype"], {"archetype": row["archetype"], "exact_deck_count": 0, "environment_prevalence": 0, "team_branch_adoption_count": 0, "linked_runnable_agent_count": 0, "classification_confidence": []})
        stat = stats[row["deck_hash"]]
        aggregate["exact_deck_count"] += 1
        aggregate["environment_prevalence"] += stat["environment_observation_count"]
        aggregate["team_branch_adoption_count"] += stat["team_branch_count"]
        aggregate["linked_runnable_agent_count"] += stat["team_agent_count"]
        aggregate["classification_confidence"].append(row["classification_confidence"])
    coverage = []
    for row in archetype_rows.values():
        confidence = sum(row.pop("classification_confidence")) / row["exact_deck_count"]
        row["classification_confidence"] = confidence
        row["policy_pack_priority"] = row["environment_prevalence"] + row["team_branch_adoption_count"] + row["linked_runnable_agent_count"] - (1.0 - confidence)
        coverage.append(row)
    coverage.sort(key=lambda row: (-row["policy_pack_priority"], row["archetype"]))
    agent_artifacts = [row for row in registry.data["branch_artifacts"] if row.get("artifact_kind") == "EXECUTABLE_AGENT"]
    deck_artifacts = [row for row in registry.data["branch_artifacts"] if row.get("artifact_kind") == "EXACT_DECK"]
    unique_agents = {row.get("agent_identity_hash") for row in agent_artifacts if row.get("agent_identity_hash")}
    verified_links = [row for row in links if row.get("link_status") == "VERIFIED_LINK"]
    linked_decks = {row.get("deck_hash") for row in verified_links if row.get("deck_hash")}
    active_exact = [row for row in classifications if row.get("classification_status") == "ACTIVE"]
    runnable_agents = 0  # O5 inventories source; execution is deliberately deferred to O5-C.
    candidate_reasons: list[str] = []
    if not active_exact:
        candidate_reasons.append("NO_ACTIVE_EXACT_DECK")
    if runnable_agents == 0:
        candidate_reasons.append("NO_ACTIVE_RUNNABLE_AGENT")
    if not verified_links:
        candidate_reasons.append("NO_VERIFIED_AGENT_DECK_LINK")
    if any(row.get("classification_status") == "PERMISSION_BLOCKED" for row in classifications):
        candidate_reasons.append("PERMISSION_BLOCKED")
    if any(row.get("classification_status") == "RULES_BLOCKED" for row in classifications):
        candidate_reasons.append("RULES_BLOCKED")
    candidate_requirements = []
    if "NO_ACTIVE_EXACT_DECK" in candidate_reasons:
        candidate_requirements.append({"requirement": "active exact Deck", "status": "missing", "permission": "deck_classification"})
    if "NO_ACTIVE_RUNNABLE_AGENT" in candidate_reasons:
        candidate_requirements.append({"requirement": "runnable Agent", "status": "missing", "permission": "agent_execution"})
    if "PERMISSION_BLOCKED" in candidate_reasons:
        candidate_requirements.append({"requirement": "team permission manifest", "status": "missing", "permission": "evaluation"})
    return {
        "schema_version": O5_REGISTRY_SCHEMA_VERSION,
        "environment_candidates": len(registry.data["source_candidates"]),
        "environment_captured_decks": sum(1 for row in registry.data["deck_lists"].values() if any(item.get("source_kind") in {"PUBLIC_OTHER", "OWN_KAGGLE"} for item in row.get("provenance", []))),
        "environment_exact_decks": sum(1 for row in registry.data["deck_lists"].values() if any(item.get("source_kind") in {"PUBLIC_OTHER", "OWN_KAGGLE"} for item in row.get("provenance", []))),
        "environment_incomplete_observations": sum(not row.get("exact") and row.get("source_kind") in {"PUBLIC_OTHER", "OWN_KAGGLE"} for row in registry.data["deck_observations"]),
        "own_encounter_decks": len({row.get("deck_hash") for row in registry.data["deck_observations"] if row.get("observation_channel") == "OWN_ENCOUNTER" and row.get("deck_hash")}),
        "team_branches_scanned": len(registry.data["branch_inventories"]),
        "branches_containing_production_decks": sum(row.get("deck_count", 0) > 0 for row in registry.data["branch_inventories"]),
        "branches_containing_runnable_agents": 0,
        "raw_deck_candidates": len(deck_artifacts),
        "production_exact_decks": sum(row.get("candidate_classification") == "EXACT_DECK_PRODUCTION" for row in deck_artifacts),
        "historical_exact_decks": sum(row.get("candidate_classification") == "EXACT_DECK_HISTORICAL" for row in deck_artifacts),
        "fixtures_examples": sum(row.get("candidate_classification") in {"TEST_FIXTURE", "DOCUMENTATION_EXAMPLE"} for row in registry.data["branch_artifacts"]),
        "invalid_decks": sum(row.get("artifact_kind") == "INVALID_OR_STALE" and str(row.get("path", "")).lower().endswith(".csv") for row in registry.data["branch_artifacts"]),
        "team_exact_decks": sum(any(item.get("source_kind") == "TEAM_SHARED" for item in row.get("provenance", [])) for row in registry.data["deck_lists"].values()),
        "team_agents": len(agent_artifacts),
        "unique_agent_implementations": len(unique_agents),
        "cross_branch_duplicates": sum(1 for row in stats.values() if row.get("team_branch_count", 0) > 1),
        "cross_source_duplicate_decks": sum(bool(stat["environment_observation_count"]) and bool(stat["team_branch_count"]) for stat in stats.values()),
        "unique_decks_after_reconciliation": len(registry.data["deck_lists"]),
        "runnable_agents": runnable_agents,
        "verified_agent_deck_links": len(verified_links),
        "inferred_high_confidence_links": 0,
        "inferred_low_confidence_links": 0,
        "unresolved_agent_deck_links": len(unresolved),
        "decks_without_agent": len(set(registry.data["deck_lists"]) - linked_decks),
        "agents_without_deck": sum(row.get("deck_hash") is None for row in links),
        "agents_without_verified_deck": len(unresolved),
        "permission_blocked_records": sum(row.get("permission") == TEAM_SHARED_PENDING_PERMISSION for row in registry.data["branch_artifacts"]),
        "rules_blocked_records": len(blocked),
        "permission_gate_status": "TEAM_SHARED_PENDING_PERMISSION" if any(row.get("permission") == TEAM_SHARED_PENDING_PERMISSION for row in registry.data["branch_artifacts"]) else "NO_PENDING_RECORDS",
        "rules_gate_status": "UNVERIFIED_RULES_CONSTRAINT" if any(row.get("mode") == CAPTURE_ONLY for row in registry.data["acquisition_runs"]) else "NO_ENVIRONMENT_RUN",
        "forced_classification_count": 0,
        "eligible_exact_decks": len(active_exact),
        "known_archetype_count": sum(row.get("archetype") != "UNKNOWN" for row in active_exact),
        "unknown_archetype_count": sum(row.get("archetype") == "UNKNOWN" for row in classifications),
        "hybrid_candidate_count": 0,
        "variant_count": sum(row.get("variant") != "UNKNOWN" for row in classifications),
        "core_package_count": 0,
        "engine_package_count": 0,
        "flex_package_count": 0,
        "tech_package_count": 0,
        "archetype_coverage": coverage,
        "o5_c_candidate_archetypes": [row["archetype"] for row in coverage[:8]],
        "o5_c_candidate_reasons": sorted(set(candidate_reasons)),
        "o5_c_candidate_requirements": candidate_requirements,
        "deck_classifications": classifications,
        "deck_source_statistics": stats,
    }


__all__ = ["CAPTURE_ONLY", "CLASSIFY_AND_ANALYZE", "DEFAULT_ENVIRONMENT_POLICY", "DeckArchetypeRegistry", "EnvironmentDeckAcquisitionResult", "EnvironmentTopDeckCollector", "EnvironmentTopDeckPolicy", "O5RegistryError", "O5_REGISTRY_SCHEMA_VERSION", "TEAM_SHARED_PENDING_PERMISSION", "TeamBranchInventoryImporter", "canonical_deck_hash", "coverage_report", "parse_exact_deck_text"]
