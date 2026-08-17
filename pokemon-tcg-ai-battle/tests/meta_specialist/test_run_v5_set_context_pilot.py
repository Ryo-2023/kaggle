"""V5 SetContext pilot runner の provenance 契約テスト。"""

from __future__ import annotations

import pytest

from scripts.run_v5_set_context_pilot import _validate_checkpoint_binding_v5


def test_v5_pilot_checkpoint_binding_requires_lowercase_sha256(tmp_path) -> None:
    checkpoint = tmp_path / "wave6.pt"
    checkpoint.write_bytes(b"checkpoint")
    with pytest.raises(ValueError, match="file_sha256"):
        _validate_checkpoint_binding_v5(
            checkpoint,
            file_sha256="A" * 64,
            tensor_state_sha256="b" * 64,
        )


def test_v5_pilot_checkpoint_binding_returns_verified_identity(tmp_path) -> None:
    checkpoint = tmp_path / "wave6.pt"
    checkpoint.write_bytes(b"checkpoint")
    import hashlib

    file_sha = hashlib.sha256(checkpoint.read_bytes()).hexdigest()
    identity = _validate_checkpoint_binding_v5(
        checkpoint,
        file_sha256=file_sha,
        tensor_state_sha256="b" * 64,
    )
    assert identity == {
        "path": str(checkpoint.resolve()),
        "file_sha256": file_sha,
        "tensor_state_sha256": "b" * 64,
        "checkpoint_schema": "specialist-neural-checkpoint-v4",
    }
