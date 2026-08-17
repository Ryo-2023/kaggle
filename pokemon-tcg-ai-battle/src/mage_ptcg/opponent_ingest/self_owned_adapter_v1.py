"""Generate a self-owned, deterministic action-perturbation source package.

The generated package embeds the base policy bytes and deck bytes, so it can
be evaluated as an ordinary ``main.py``/``deck.csv`` opponent without an
import-time dependency on the source candidate's directory.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any

from mage_ptcg.observability.cabt_trace import canonical_deck_sha256


_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_new(path: Path, data: bytes) -> None:
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


_ADAPTER_BODY = r'''
import hashlib as _soa_hashlib
import json as _soa_json
from collections.abc import Mapping as _soa_Mapping, Sequence as _soa_Sequence


def _soa_indices(value):
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, _soa_Sequence):
        return None
    result = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            return None
        result.append(item)
    return result


def _soa_option_type(option):
    return option.get("type") if isinstance(option, _soa_Mapping) else None


def _soa_digest(observation, salt):
    try:
        payload = _soa_json.dumps(
            {"salt": salt, "observation": observation},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        payload = repr((salt, observation)).encode("utf-8", errors="backslashreplace")
    return _soa_hashlib.sha256(payload).digest()


def _soa_adapt_action(base_action, observation, *, salt, perturbation_rate):
    if not isinstance(observation, _soa_Mapping):
        return list(base_action) if _soa_indices(base_action) is not None else []
    select = observation.get("select")
    base = _soa_indices(base_action)
    if select is None:
        return list(base_action) if base is not None else []
    if not isinstance(select, _soa_Mapping):
        return base or []
    options = select.get("option")
    if not isinstance(options, _soa_Sequence) or isinstance(options, (str, bytes, bytearray)):
        return base or []
    option_count = len(options)
    try:
        minimum = int(select.get("minCount", 0))
        maximum = int(select.get("maxCount", minimum))
    except (TypeError, ValueError):
        return base or []
    if minimum < 0 or maximum < minimum or maximum > option_count:
        return base or []
    valid_base = (
        base is not None and minimum <= len(base) <= maximum
        and len(base) == len(set(base))
        and all(0 <= index < option_count for index in base)
    )
    chosen = list(base) if valid_base else list(range(minimum))
    if len(chosen) < minimum or len(chosen) > maximum:
        return chosen
    if not 0.0 <= perturbation_rate <= 1.0:
        raise ValueError("perturbation_rate must be between 0 and 1")
    if not chosen or not options or perturbation_rate == 0.0:
        return chosen
    digest = _soa_digest(observation, salt)
    threshold = int.from_bytes(digest[:8], "big") / float(1 << 64)
    if threshold >= perturbation_rate:
        return chosen
    selected = set(chosen)
    for position, selected_index in enumerate(chosen):
        selected_type = _soa_option_type(options[selected_index])
        alternatives = [
            index for index, option in enumerate(options)
            if index not in selected and _soa_option_type(option) == selected_type
        ]
        if not alternatives:
            continue
        replacement = alternatives[digest[8 + position] % len(alternatives)]
        result = list(chosen)
        result[position] = replacement
        return result
    return chosen


def agent(obs_dict, configuration=None):
    del configuration
    return _soa_adapt_action(
        _base_agent(obs_dict),
        obs_dict,
        salt=__ADAPTER_SALT__,
        perturbation_rate=__PERTURBATION_RATE__,
    )
'''


def generate_self_owned_adapter_v1(
    *,
    base_candidate_root: Path | str,
    output_root: Path | str,
    adapter_id: str,
    salt: str,
    perturbation_rate: float,
) -> dict[str, Any]:
    """Create one immutable adapter package and return its hash manifest."""

    base_root = Path(base_candidate_root).resolve()
    output = Path(output_root).resolve()
    if not _ID.fullmatch(adapter_id):
        raise ValueError(f"invalid adapter_id: {adapter_id!r}")
    if not salt:
        raise ValueError("salt must be non-empty")
    if not 0.0 <= perturbation_rate <= 1.0:
        raise ValueError("perturbation_rate must be between 0 and 1")
    source_path = base_root / "payload" / "original_main.py"
    if not source_path.is_file():
        source_path = base_root / "main.py"
    deck_path = base_root / "deck.csv"
    if not source_path.is_file() or not deck_path.is_file():
        raise ValueError("base candidate must contain main.py/payload/original_main.py and deck.csv")
    if output.exists():
        raise FileExistsError(output)
    base_text = source_path.read_text(encoding="utf-8")
    marker = "def agent("
    if marker not in base_text:
        raise ValueError("base policy has no def agent(...) entrypoint")
    embedded = base_text.replace(marker, "def _base_agent(", 1)
    prefix = """from pathlib import Path as _soa_Path\nimport os as _soa_os\n_soa_root = _soa_Path(__file__).resolve().parent\n_soa_previous_cwd = _soa_os.getcwd()\n_soa_os.chdir(_soa_root)\n"""
    suffix = "\n_soa_os.chdir(_soa_previous_cwd)\ndel _soa_previous_cwd\n" + _ADAPTER_BODY
    policy_text = prefix + embedded + suffix
    policy_text = policy_text.replace("__ADAPTER_SALT__", repr(salt)).replace("__PERTURBATION_RATE__", repr(float(perturbation_rate)))
    policy_bytes = policy_text.encode("utf-8")
    deck_bytes = deck_path.read_bytes()
    generation = {
        "schema_version": "self-owned-meta-adapter-v1",
        "adapter_id": adapter_id,
        "method": "same-option-type-deterministic-action-perturbation",
        "base_candidate_root": str(base_root),
        "base_policy_sha256": _sha256_bytes(source_path.read_bytes()),
        "generated_policy_sha256": _sha256_bytes(policy_bytes),
        "deck_sha256": _sha256_bytes(deck_bytes),
        "salt": salt,
        "perturbation_rate": float(perturbation_rate),
        "usage_boundary": "local_eval_only",
        "research_only": True,
    }
    output.mkdir(parents=True, exist_ok=False)
    _write_new(output / "main.py", policy_bytes)
    _write_new(output / "deck.csv", deck_bytes)
    _write_new(output / "generation.json", _canonical_json(generation))
    _write_new(
        output / "SOURCE.md",
        (
            "# Self-owned generated meta source\n\n"
            f"- adapter id: `{adapter_id}`\n"
            "- method: same-option-type deterministic action perturbation\n"
            f"- base policy SHA-256: `{generation['base_policy_sha256']}`\n"
            f"- generated policy SHA-256: `{generation['generated_policy_sha256']}`\n"
            f"- deck SHA-256: `{generation['deck_sha256']}`\n"
            "- usage boundary: `local_eval_only`\n"
            "- no submission, training, promotion, or external publication authority\n"
        ).encode("utf-8"),
    )
    return generation


def seal_self_owned_adapter_pool_v1(
    *,
    candidate_package_root: Path | str,
    output_root: Path | str,
    source_epoch: str,
    seed_namespace: str,
) -> dict[str, Any]:
    """Seal one generated package as a research-only historical source pool."""

    candidate = Path(candidate_package_root).resolve()
    output = Path(output_root).resolve()
    if not source_epoch or not seed_namespace:
        raise ValueError("source_epoch and seed_namespace are required")
    main_path = candidate / "main.py"
    deck_path = candidate / "deck.csv"
    generation_path = candidate / "generation.json"
    if not main_path.is_file() or not deck_path.is_file() or not generation_path.is_file():
        raise ValueError("generated candidate must contain main.py, deck.csv, and generation.json")
    generation = json.loads(generation_path.read_text(encoding="utf-8"))
    adapter_id = str(generation.get("adapter_id", ""))
    if not _ID.fullmatch(adapter_id):
        raise ValueError("generation.json has an invalid adapter_id")
    deck_ids = [int(value) for value in deck_path.read_text(encoding="utf-8").split()]
    if len(deck_ids) != 60:
        raise ValueError(f"generated adapter deck must contain exactly 60 cards, got {len(deck_ids)}")
    if output.exists():
        raise FileExistsError(output)
    policy_sha = _sha256_bytes(main_path.read_bytes())
    deck_sha = canonical_deck_sha256(deck_ids)
    row = {
        "asset_preflight": "STATIC_AND_EXACT_60",
        "canonical_deck_hash": deck_sha,
        "id": adapter_id,
        "mean_decision_ms": None,
        "policy_hash": policy_sha,
        "smoke_ok": False,
        "source": "self_owned_generated_adapter",
        "source_branch": "self_owned/generated_same_option_type_adapter_v1",
        "source_commit": policy_sha,
        "source_policy_sha256": policy_sha,
        "usage_boundary": "local_eval_only",
    }
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(candidate, output / adapter_id)
    pool_path = output / "pool_manifest.json"
    _write_new(pool_path, _canonical_json([row]))
    pool_sha = _sha256_bytes(pool_path.read_bytes())
    evidence_payload = {
        "schema_version": "self-owned-meta-adapter-freshness-v1",
        "id": adapter_id,
        "source": "self_owned_generated_adapter",
        "base_policy_sha256": generation.get("base_policy_sha256"),
        "generated_policy_sha256": policy_sha,
        "canonical_deck_hash": deck_sha,
        "generation_json_sha256": _sha256_bytes(generation_path.read_bytes()),
        "fresh": True,
        "unused_before_run": True,
        "usage_boundary": "local_eval_only",
        "research_only": True,
    }
    evidence_path = output / "evidence" / f"{adapter_id}.json"
    _write_new(evidence_path, _canonical_json(evidence_payload))
    evidence_sha = _sha256_bytes(evidence_path.read_bytes())
    reference = {
        "id": adapter_id,
        "canonical_deck_hash": deck_sha,
        "policy_sha256": policy_sha,
        "source": "self_owned_generated_adapter",
        "fresh": True,
        "unused_before_run": True,
        "freshness_evidence_path": f"evidence/{adapter_id}.json",
        "freshness_evidence_sha256": evidence_sha,
        "usage_boundary": "local_eval_only",
    }
    seed_plan = {"source_epoch": source_epoch, "seed_namespace": seed_namespace, "reference_ids": [adapter_id]}
    fresh = {
        "schema_version": "meta-specialist-cg-fresh-meta-batch-v1",
        "batch_id": f"self-owned-{source_epoch}-{seed_namespace}",
        "source_epoch": source_epoch,
        "seed_namespace": seed_namespace,
        "seed_plan_sha256": _sha256_bytes(_canonical_json(seed_plan)),
        "pool_manifest_sha256": pool_sha,
        "reference_ids": [adapter_id],
        "references": [reference],
        "freshness_basis": "self-owned deterministic action adapter over an unused base policy",
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    _write_new(output / "fresh_meta.json", _canonical_json(fresh))
    report = {
        "schema_version": "self-owned-meta-adapter-pool-v1",
        "status": "SEALED",
        "output_root": str(output),
        "pool_manifest_sha256": pool_sha,
        "fresh_meta_sha256": _sha256_bytes((output / "fresh_meta.json").read_bytes()),
        "reference_ids": [adapter_id],
        "authority": {"training_allowed": False, "promotion_allowed": False, "submission_allowed": False, "longrun_allowed": False},
        "research_only": True,
    }
    _write_new(output / "seal_report.json", _canonical_json(report))
    return report
