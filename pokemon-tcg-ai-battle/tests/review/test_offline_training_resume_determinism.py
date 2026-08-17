"""Adversarial checkpoint/resume determinism review for Offline Training v1.

Distinguishes bitwise-identical, numerically-equivalent, and non-reproducible
resume behaviour, and exercises corruption and incompatibility paths.
"""

from __future__ import annotations

import json

import pytest

torch = pytest.importorskip("torch")

from mage_ptcg.offline_training import neural


def _train(dataset_dir, ckdir, *, epochs, patience=9, seed=11, resume=False, hidden=(32,)):
    return neural.train(
        dataset_dir=dataset_dir, checkpoint_dir=ckdir, hidden_dims=list(hidden), epochs=epochs,
        learning_rate=3e-4, weight_decay=1e-4, grad_clip=1.0, patience=patience, seed=seed,
        max_batch_decisions=64, model_purpose=neural.MODEL_PURPOSE_SMOKE, device="cpu", resume=resume,
    )


def _state_tensors(ckdir, name):
    payload = torch.load((ckdir / name).with_suffix(".pt"), map_location="cpu", weights_only=True)
    return payload["model_state"], payload["optimizer_state"]


def test_resume_reproduces_continuous_training_bitwise(review_dataset_dir, tmp_path):
    """4 continuous epochs == 2 epochs + resume to 4, bitwise on model weights."""
    cont = tmp_path / "cont"
    _train(review_dataset_dir, cont, epochs=4)
    split = tmp_path / "split"
    _train(review_dataset_dir, split, epochs=2)
    _train(review_dataset_dir, split, epochs=4, resume=True)

    cont_model, cont_opt = _state_tensors(cont, "last")
    split_model, split_opt = _state_tensors(split, "last")
    for key in cont_model:
        assert torch.equal(cont_model[key], split_model[key]), f"model tensor {key} diverged on resume"
    cont_meta = neural.load_checkpoint_metadata(cont / "last")
    split_meta = neural.load_checkpoint_metadata(split / "last")
    assert cont_meta["epoch"] == split_meta["epoch"]
    assert cont_meta["state_sha256"] == split_meta["state_sha256"], "optimizer/model bytes diverged on resume"


def _scripted_evaluate(monkeypatch, values):
    """Make evaluate_module return scripted val NLLs, one per call, in order."""
    queue = list(values)
    real_evaluate = neural.evaluate_module

    def fake_evaluate(module, decisions, mean, std, **kwargs):
        metrics = real_evaluate(module, decisions, mean, std, **kwargs)
        metrics["nll"] = queue.pop(0)
        return metrics

    monkeypatch.setattr(neural, "evaluate_module", fake_evaluate)


def test_early_stop_resume_parity_with_continuous(review_dataset_dir, tmp_path, monkeypatch):
    """REV-F1 fix: run A trains continuously to early stop; run B checkpoints at
    an epoch where patience has already progressed, then a NEW trainer resumes
    to early stop.  Stop epoch, best epoch, bad-epoch count, best metric,
    global step, and final weights must all match.

    Scripted val NLL: [3.0, 2.0, 2.5, 2.6] with patience=2 ->
    epoch0 improve, epoch1 improve (best), epoch2 bad (patience 2->1),
    epoch3 bad (1->0) -> early stop at epoch 3, best epoch 1."""
    script = [3.0, 2.0, 2.5, 2.6]

    # Run A: continuous to early stop.
    _scripted_evaluate(monkeypatch, script)
    cont = tmp_path / "cont"
    result_a = _train(review_dataset_dir, cont, epochs=10, patience=2)
    assert result_a["stopped_early"] is True

    # Run B: stop after epoch 2 (patience already 1), then resume to early stop.
    monkeypatch.undo()
    _scripted_evaluate(monkeypatch, script[:3])
    split = tmp_path / "split"
    _train(review_dataset_dir, split, epochs=3, patience=2)
    monkeypatch.undo()
    _scripted_evaluate(monkeypatch, script[3:])
    result_b = _train(review_dataset_dir, split, epochs=10, patience=2, resume=True)
    assert result_b["stopped_early"] is True

    meta_a = neural.load_checkpoint_metadata(cont / "last")
    meta_b = neural.load_checkpoint_metadata(split / "last")
    best_a = neural.load_checkpoint_metadata(cont / "best")
    best_b = neural.load_checkpoint_metadata(split / "best")
    assert meta_a["epoch"] == meta_b["epoch"] == 3, "stop epoch diverged"
    assert best_a["epoch"] == best_b["epoch"] == 1, "best epoch diverged"
    assert meta_a["patience_left"] == meta_b["patience_left"] == 0, "bad-epoch accounting diverged"
    assert meta_a["best_metric"] == meta_b["best_metric"] == 2.0, "best metric diverged"
    assert meta_a["global_step"] == meta_b["global_step"], "global step diverged"
    assert meta_a["state_sha256"] == meta_b["state_sha256"], "final weights diverged on resume"
    assert result_a["epochs_run"] == result_b["epochs_run"] == 4


def test_resume_of_early_stopped_run_does_not_train_further(review_dataset_dir, tmp_path, monkeypatch):
    """Resuming a checkpoint whose early-stop decision already fired must not
    grant extra non-improving epochs (the pre-fix behaviour)."""
    _scripted_evaluate(monkeypatch, [3.0, 4.0])
    ckdir = tmp_path / "ck"
    result = _train(review_dataset_dir, ckdir, epochs=10, patience=1)
    assert result["stopped_early"] is True
    meta_before = neural.load_checkpoint_metadata(ckdir / "last")
    monkeypatch.undo()
    resumed = _train(review_dataset_dir, ckdir, epochs=10, patience=1, resume=True)
    meta_after = neural.load_checkpoint_metadata(ckdir / "last")
    assert resumed["stopped_early"] is True
    assert meta_after["epoch"] == meta_before["epoch"], "resume trained past the early stop"
    assert meta_after["state_sha256"] == meta_before["state_sha256"]


def test_saved_rng_state_is_never_restored(review_dataset_dir, tmp_path):
    """Documents REV-F2: the checkpoint records full RNG state but resume never
    restores it.  Harmless for the current model (per-epoch seeded shuffle, no
    dropout) but misleading; assert the recording exists so the gap is visible."""
    ckdir = tmp_path / "ck"
    _train(review_dataset_dir, ckdir, epochs=1)
    meta = neural.load_checkpoint_metadata(ckdir / "last")
    assert "rng_state" in meta and meta["rng_state"].get("torch_cpu"), "rng state disappeared from checkpoints"
    import inspect

    source = inspect.getsource(neural.train) + inspect.getsource(neural._restore_state)
    assert "set_rng_state" not in source and "setstate" not in source, (
        "resume now restores RNG state; REV-F2 may have been fixed"
    )


def test_resume_rejects_dataset_change(review_dataset_dir, tmp_path):
    ckdir = tmp_path / "ck"
    _train(review_dataset_dir, ckdir, epochs=1)
    meta_path = (ckdir / "last").with_suffix(".json")
    meta = json.loads(meta_path.read_text())
    with pytest.raises(neural.NeuralError):
        neural.assert_checkpoint_compatible(
            meta,
            dataset_hash="0" * 64,
            feature_schema_hash=meta["feature_schema_hash"],
            spec=neural.ModelSpec(input_dim=int(meta["model_spec"]["input_dim"]), hidden_dims=(32,)),
            model_purpose=meta["model_purpose"],
        )


def test_resume_rejects_feature_schema_change(review_dataset_dir, tmp_path):
    ckdir = tmp_path / "ck"
    _train(review_dataset_dir, ckdir, epochs=1)
    meta = neural.load_checkpoint_metadata(ckdir / "last")
    with pytest.raises(neural.NeuralError):
        neural.assert_checkpoint_compatible(
            meta,
            dataset_hash=meta["dataset_hash"],
            feature_schema_hash="f" * 64,
            spec=neural.ModelSpec(input_dim=int(meta["model_spec"]["input_dim"]), hidden_dims=(32,)),
            model_purpose=meta["model_purpose"],
        )


def test_truncated_checkpoint_tensor_file_raises_typed_error(review_dataset_dir, tmp_path):
    """REV-F3 fix: a truncated ``last.pt`` raises CheckpointValidationError
    (a NeuralError subclass, so the CLI records FAILED_RETRYABLE)."""
    ckdir = tmp_path / "ck"
    _train(review_dataset_dir, ckdir, epochs=1)
    pt = (ckdir / "last").with_suffix(".pt")
    pt.write_bytes(pt.read_bytes()[: len(pt.read_bytes()) // 2])
    with pytest.raises(neural.CheckpointValidationError) as excinfo:
        _train(review_dataset_dir, ckdir, epochs=2, resume=True)
    assert excinfo.value.reason == "tensor_deserialization_failed"
    assert isinstance(excinfo.value, neural.NeuralError)


def test_random_bytes_checkpoint_raises_typed_error(review_dataset_dir, tmp_path):
    ckdir = tmp_path / "ck"
    _train(review_dataset_dir, ckdir, epochs=1)
    (ckdir / "last").with_suffix(".pt").write_bytes(b"\x89" * 4096)
    with pytest.raises(neural.CheckpointValidationError) as excinfo:
        _train(review_dataset_dir, ckdir, epochs=2, resume=True)
    assert excinfo.value.reason == "tensor_deserialization_failed"


def test_non_dict_checkpoint_payload_raises_typed_error(review_dataset_dir, tmp_path):
    ckdir = tmp_path / "ck"
    _train(review_dataset_dir, ckdir, epochs=1)
    pt = (ckdir / "last").with_suffix(".pt")
    torch.save(torch.zeros(3), pt)
    with pytest.raises(neural.CheckpointValidationError) as excinfo:
        _train(review_dataset_dir, ckdir, epochs=2, resume=True)
    assert excinfo.value.reason == "tensor_payload_invalid"


def test_missing_optimizer_state_raises_typed_error(review_dataset_dir, tmp_path):
    ckdir = tmp_path / "ck"
    _train(review_dataset_dir, ckdir, epochs=1)
    pt = (ckdir / "last").with_suffix(".pt")
    payload = torch.load(pt, map_location="cpu", weights_only=True)
    del payload["optimizer_state"]
    torch.save(payload, pt)
    with pytest.raises(neural.CheckpointValidationError) as excinfo:
        _train(review_dataset_dir, ckdir, epochs=2, resume=True)
    assert excinfo.value.reason == "tensor_payload_invalid"


def test_missing_tensor_file_raises_typed_error(review_dataset_dir, tmp_path):
    ckdir = tmp_path / "ck"
    _train(review_dataset_dir, ckdir, epochs=1)
    (ckdir / "last").with_suffix(".pt").unlink()
    with pytest.raises(neural.CheckpointValidationError) as excinfo:
        _train(review_dataset_dir, ckdir, epochs=2, resume=True)
    assert excinfo.value.reason == "tensor_file_missing"


def test_state_shape_mismatch_raises_typed_error(review_dataset_dir, tmp_path):
    ckdir = tmp_path / "ck"
    _train(review_dataset_dir, ckdir, epochs=1, hidden=(32,))
    pt = (ckdir / "last").with_suffix(".pt")
    payload = torch.load(pt, map_location="cpu", weights_only=True)
    key = next(iter(payload["model_state"]))
    payload["model_state"][key] = torch.zeros(7, 7)  # wrong shape
    torch.save(payload, pt)
    with pytest.raises(neural.CheckpointValidationError) as excinfo:
        _train(review_dataset_dir, ckdir, epochs=2, resume=True)
    assert excinfo.value.reason == "state_shape_mismatch"


def test_metadata_checksum_mismatch_reason_code(review_dataset_dir, tmp_path):
    import json

    ckdir = tmp_path / "ck"
    _train(review_dataset_dir, ckdir, epochs=1)
    meta_path = (ckdir / "last").with_suffix(".json")
    meta = json.loads(meta_path.read_text())
    meta["epoch"] = meta["epoch"] + 7
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(neural.CheckpointValidationError) as excinfo:
        neural.load_checkpoint_metadata(ckdir / "last")
    assert excinfo.value.reason == "checksum_mismatch"


def test_checkpoint_error_messages_carry_no_paths(review_dataset_dir, tmp_path):
    ckdir = tmp_path / "ck"
    _train(review_dataset_dir, ckdir, epochs=1)
    (ckdir / "last").with_suffix(".pt").write_bytes(b"junk")
    with pytest.raises(neural.CheckpointValidationError) as excinfo:
        _train(review_dataset_dir, ckdir, epochs=2, resume=True)
    assert str(tmp_path) not in str(excinfo.value)
    assert "/home/" not in str(excinfo.value)
