"""model-only RuntimePolicy の CPU 読み込み境界。"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from typing import Any, Mapping

from mage_ptcg.policy_learning.r2d3.candidate import R2D3CandidatePolicy, deck_hash
from mage_ptcg.policy_learning.r2d3.model import (
    R2D3ModelConfig,
    RecurrentDistributionalQ,
)

from .checkpoint_stream import canonical_model_state_hash
from .contracts import LeagueContractError, content_id, load_json, require_sha256


class RuntimePolicyFactory:
    def __init__(
        self,
        *,
        model: Any,
        deck: list[int],
        runtime_policy_id: str,
        manifest: Mapping[str, Any],
    ) -> None:
        self.model = model
        self.deck = list(deck)
        self.runtime_policy_id = runtime_policy_id
        self.manifest = dict(manifest)

    def create(self, *, game_id: str, seat: int) -> R2D3CandidatePolicy:
        import torch

        return R2D3CandidatePolicy(
            self.model,
            deck=self.deck,
            device=torch.device("cpu"),
            policy_version=self.runtime_policy_id,
            action_mode="greedy",
            epsilon=0.0,
            game_id=game_id,
            seat=seat,
        )


def load_runtime_policy(runtime_dir: Path) -> RuntimePolicyFactory:
    import torch

    runtime_dir = Path(runtime_dir)
    manifest = load_json(runtime_dir / "manifest.json")
    required = {
        "runtime_policy_id",
        "model_state_hash",
        "model_config",
        "deck",
        "deck_hash",
        "weights_file",
        "action_mode",
        "q_reduction",
        "legal_mask_version",
        "recurrent_contract_version",
        "tie_break_version",
    }
    missing = required.difference(manifest)
    if missing:
        raise LeagueContractError(
            f"runtime policy manifest misses {sorted(missing)}"
        )
    require_sha256(manifest["runtime_policy_id"], "runtime_policy_id")
    require_sha256(manifest["model_state_hash"], "model_state_hash")
    identity_fields = {
        "model_state_hash",
        "model_config",
        "state_encoder_version",
        "action_encoder_version",
        "action_mode",
        "q_reduction",
        "legal_mask_version",
        "recurrent_contract_version",
        "tie_break_version",
        "deck",
        "deck_hash",
        "runtime_device",
        "torch_threads",
    }
    identity = {key: manifest[key] for key in identity_fields}
    if content_id("runtime-policy-v1", identity) != manifest["runtime_policy_id"]:
        raise LeagueContractError("runtime policy manifest identity mismatch")
    if manifest["action_mode"] != "greedy" or manifest["q_reduction"] != (
        "categorical-expected-value"
    ):
        raise LeagueContractError("unsupported evaluation action contract")
    deck = list(manifest["deck"])
    if len(deck) != 60 or deck_hash(deck) != manifest["deck_hash"]:
        raise LeagueContractError("runtime policy deck hash mismatch")

    config_fields = {field.name for field in fields(R2D3ModelConfig)}
    config_payload = manifest["model_config"]
    if set(config_payload) != config_fields:
        raise LeagueContractError("runtime model config fields mismatch")
    config = R2D3ModelConfig(**config_payload)
    if config.state_size != 128 or config.action_size != 64:
        raise LeagueContractError("runtime semantic feature dimension mismatch")
    torch.set_num_threads(int(manifest.get("torch_threads", 1)))
    model = RecurrentDistributionalQ(config)
    weights_path = runtime_dir / manifest["weights_file"]
    try:
        weights = torch.load(weights_path, map_location="cpu", weights_only=True)
        if canonical_model_state_hash(weights) != manifest["model_state_hash"]:
            raise LeagueContractError("runtime policy model state hash mismatch")
        model.load_state_dict(weights, strict=True)
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, LeagueContractError):
            raise
        raise LeagueContractError(f"cannot load runtime model: {exc}") from exc
    model.eval()
    return RuntimePolicyFactory(
        model=model,
        deck=deck,
        runtime_policy_id=manifest["runtime_policy_id"],
        manifest=manifest,
    )
