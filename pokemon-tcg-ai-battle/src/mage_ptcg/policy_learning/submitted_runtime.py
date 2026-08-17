"""Pinned submitted-policy snapshots and isolated JSONL runtime workers.

All executable bytes come from ``git archive <qualified commit>``.  Remote
refs are recorded for drift diagnostics only and are never consulted by a
runtime worker.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Any, Mapping

from .submitted_opponents import SubmittedAsset


class SubmittedRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message); self.code = code

    def __reduce__(self) -> tuple[Any, tuple[str, str]]:
        """Keep the fault code across a process boundary.

        ``RuntimeError.__reduce__`` would replay only ``args`` (the message),
        so unpickling in a parent process raised a ``TypeError`` for the missing
        ``message`` argument.  In a ``ProcessPoolExecutor`` that surfaced as an
        opaque ``BrokenProcessPool`` and hid the real cause -- typically
        ``CALLBACK_TIMEOUT`` -- from the CABT fault gate.
        """
        return (self.__class__, (self.code, str(self.args[0]) if self.args else ""))


def _sha_bytes(value: bytes) -> str: return hashlib.sha256(value).hexdigest()
def _sha_file(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class SubmittedRuntimeSpec:
    asset_id: str
    source_commit: str
    snapshot_root: Path
    deck_path: Path
    entrypoint: str
    adapter_type: str
    python_path: tuple[Path, ...]
    environment: Mapping[str, str]
    working_directory_mode: str
    deck_hash: str
    policy_hash: str
    source_lineage: str
    deck_family: str
    callback_timeout_seconds: float = 8.0

    def __post_init__(self) -> None:
        root = self.snapshot_root.resolve()
        if self.source_commit == "" or not self.deck_path.resolve().is_relative_to(root): raise SubmittedRuntimeError("IDENTITY_MISMATCH", "runtime spec is not snapshot-bound")
        if self.working_directory_mode not in {"snapshot", "game_scratch"}: raise SubmittedRuntimeError("ADAPTER_CONFIG", "working directory mode is invalid")

    def payload(self) -> dict[str, Any]:
        value = asdict(self); value["snapshot_root"] = str(self.snapshot_root); value["deck_path"] = str(self.deck_path); value["python_path"] = [str(path) for path in self.python_path]; value["environment"] = dict(self.environment); return value

    @classmethod
    def from_payload(cls, value: Mapping[str, Any]) -> "SubmittedRuntimeSpec":
        return cls(**{**value, "snapshot_root": Path(str(value["snapshot_root"])), "deck_path": Path(str(value["deck_path"])), "python_path": tuple(Path(str(path)) for path in value.get("python_path", [])), "environment": dict(value.get("environment", {}))})


def _safe_extract(data: bytes, destination: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(destination, filter="data")


def pin_snapshot(repo: str | Path, asset: SubmittedAsset, destination: str | Path) -> dict[str, Any]:
    """Materialize and verify one runtime-qualified immutable tree."""
    root = Path(destination); root.mkdir(parents=True, exist_ok=False); repository = Path(repo)
    command = ["git", "-C", str(repository), "archive", "--format=tar", asset.source_commit]
    subtree: str | None = None
    if asset.asset_id.startswith("dev/"):
        subtree = f"opponents/{asset.asset_id.split('/', 1)[1]}"; command.extend((subtree, "cg"))
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if completed.returncode: raise SubmittedRuntimeError("SNAPSHOT_ARCHIVE_FAILURE", completed.stderr.decode(errors="replace")[-500:])
    with tempfile.TemporaryDirectory(prefix="submitted-pin-") as temporary:
        extracted = Path(temporary); _safe_extract(completed.stdout, extracted)
        source = extracted / subtree if subtree else extracted
        if not source.is_dir(): raise SubmittedRuntimeError("SNAPSHOT_SUBTREE_MISSING", f"{asset.asset_id} subtree is absent")
        shutil.copytree(source, root, dirs_exist_ok=True)
        if subtree and (extracted / "cg").is_dir():
            shutil.copytree(extracted / "cg", root / "cg", dirs_exist_ok=True)
    entrypoint = root / asset.entrypoint.split(":", 1)[0]; deck = root / asset.deck_id
    actual_policy = _sha_file(entrypoint) if entrypoint.is_file() else ""; actual_deck = _sha_file(deck) if deck.is_file() else ""
    if actual_policy != asset.policy_hash: raise SubmittedRuntimeError("POLICY_HASH_MISMATCH", f"{asset.asset_id}: expected {asset.policy_hash}, got {actual_policy}")
    if actual_deck != asset.deck_hash: raise SubmittedRuntimeError("DECK_HASH_MISMATCH", f"{asset.asset_id}: expected {asset.deck_hash}, got {actual_deck}")
    files = [{"path": path.relative_to(root).as_posix(), "sha256": _sha_file(path), "size": path.stat().st_size} for path in sorted(root.rglob("*")) if path.is_file()]
    payload = {"asset_id": asset.asset_id, "source_commit": asset.source_commit, "submission_source_commit": asset.submission_source_commit,
               "current_ref_commit": asset.current_ref_commit, "ref_drift": bool(asset.current_ref_commit and asset.current_ref_commit != asset.source_commit),
               "snapshot_root": str(root), "archive_sha256": _sha_bytes(completed.stdout), "deck_path": str(deck), "entrypoint": asset.entrypoint,
               "deck_hash": actual_deck, "policy_hash": actual_policy, "source_lineage": asset.source_lineage, "deck_family": asset.deck_family,
               "adapter_type": "isolated_jsonl_python_v1", "dependency_metadata": [name for name in ("requirements.txt", "pyproject.toml", "setup.py") if (root / name).is_file()], "files": files}
    (root / ".submitted_snapshot_manifest.json").write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return payload


def spec_from_manifest(manifest: Mapping[str, Any], *, callback_timeout_seconds: float = 8.0) -> SubmittedRuntimeSpec:
    root = Path(str(manifest["snapshot_root"])); return SubmittedRuntimeSpec(asset_id=str(manifest["asset_id"]), source_commit=str(manifest["source_commit"]), snapshot_root=root,
        deck_path=Path(str(manifest["deck_path"])), entrypoint=str(manifest["entrypoint"]), adapter_type=str(manifest["adapter_type"]), python_path=(root,), environment={"PYTHONNOUSERSITE": "1"},
        deck_hash=str(manifest["deck_hash"]), policy_hash=str(manifest["policy_hash"]), source_lineage=str(manifest["source_lineage"]), deck_family=str(manifest.get("deck_family") or "UNKNOWN"), callback_timeout_seconds=callback_timeout_seconds,
        working_directory_mode="snapshot")


class SubmittedAgentWorker:
    """Long-lived isolated callback worker with timeout and process-group cleanup."""
    def __init__(self, spec: SubmittedRuntimeSpec, *, scratch_root: str | Path) -> None:
        self.spec = spec; parent = Path(scratch_root); parent.mkdir(parents=True, exist_ok=True); self.scratch = Path(tempfile.mkdtemp(prefix="game-", dir=parent))
        environment = os.environ.copy(); environment.update(spec.environment); environment["PYTHONPATH"] = os.pathsep.join(map(str, spec.python_path))
        child = Path(__file__).parents[3] / "scripts" / "policy_learning" / "submitted_runtime_child.py"
        command = [sys.executable, str(child), "--spec", json.dumps(spec.payload(), ensure_ascii=False)]
        self.process = subprocess.Popen(command, cwd=self.scratch, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1, start_new_session=True)
        try: ready = self._read(timeout=spec.callback_timeout_seconds)
        except Exception:
            self.close(); raise
        if ready.get("status") != "READY" or ready.get("policy_hash") != spec.policy_hash or ready.get("deck_hash") != spec.deck_hash:
            self.close(); raise SubmittedRuntimeError("IDENTITY_MISMATCH", f"submitted worker identity handshake failed: {ready}")
        self.deck_requests = 0; self.closed = False; self.public_traces: list[dict[str, Any]] = []
    def _read(self, *, timeout: float) -> dict[str, Any]:
        assert self.process.stdout is not None
        selector = selectors.DefaultSelector(); selector.register(self.process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout): raise SubmittedRuntimeError("CALLBACK_TIMEOUT", f"submitted callback exceeded {timeout:.3f}s")
        line = self.process.stdout.readline()
        if not line:
            detail = self.process.stderr.read()[-800:] if self.process.stderr is not None and self.process.poll() is not None else ""
            raise SubmittedRuntimeError("PROCESS_EXIT", f"submitted worker exited {self.process.poll()}: {detail}")
        try: value = json.loads(line)
        except json.JSONDecodeError as exc: raise SubmittedRuntimeError("PROTOCOL_ERROR", "submitted worker emitted invalid JSON") from exc
        if not isinstance(value, dict): raise SubmittedRuntimeError("PROTOCOL_ERROR", "submitted worker response is not an object")
        return value
    def __call__(self, observation: object, configuration: object = None) -> list[int]:
        del configuration
        if self.closed or self.process.poll() is not None: raise SubmittedRuntimeError("PROCESS_EXIT", "submitted worker is unavailable")
        assert self.process.stdin is not None; self.process.stdin.write(json.dumps({"observation": observation}, ensure_ascii=False, separators=(",", ":")) + "\n"); self.process.stdin.flush(); response = self._read(timeout=self.spec.callback_timeout_seconds)
        if response.get("status") != "OK": raise SubmittedRuntimeError(str(response.get("code") or "POLICY_EXCEPTION"), str(response.get("message") or "submitted policy failed"))
        action = response.get("action")
        if not isinstance(action, list) or any(type(value) is not int for value in action): raise SubmittedRuntimeError("ILLEGAL_RESPONSE_TYPE", "submitted policy returned a non-index-list")
        if isinstance(observation, Mapping) and observation.get("select") is None and action:
            self.deck_requests += 1
            if self.deck_requests > 1: raise SubmittedRuntimeError("DECK_REQUEST_COUNT", "submitted policy returned its Deck more than once")
        if isinstance(observation, Mapping) and isinstance(observation.get("select"), Mapping) and len(action) == 1:
            try:
                from mage_ptcg.decision_state import build_decision_state
                from .r2d3.semantic_action import encode_legal_action
                from .r2d3.semantic_state import encode_public_state
                from .r2d3.sequence import public_prize_potential
                state = build_decision_state(dict(observation))
                matches = [index for index, item in enumerate(state.legal_actions) if item.option_index == action[0]]
                if len(matches) == 1:
                    encoded = []
                    for item in state.legal_actions:
                        key = item.action_key; encoded.append(encode_legal_action({"digest": key.digest, "action_type": key.selection_type,
                            "card_id": key.card_id, "source_zone": key.source_entity_key, "target_zone": key.target_entity_key,
                            "target_card": key.target_entity_key, "amount": None, "selection_order": item.option_index,
                            "phase": key.context, "optional": False, "semantic_role": key.semantic_operation}))
                    self.public_traces.append({
                        "state": encode_public_state(state.actor_view.public_state),
                        "actions": encoded,
                        "selected_action": matches[0],
                        "potential": public_prize_potential(state.actor_view.public_state),
                    })
            except Exception:
                # Runtime legality remains CABT's responsibility.  Missing
                # demonstration telemetry is explicit in the later replay gate.
                pass
        return action
    def close(self) -> None:
        if getattr(self, "closed", False): return
        self.closed = True
        if self.process.poll() is None:
            try: os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try: os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                self.process.wait(timeout=2)
        shutil.rmtree(self.scratch, ignore_errors=True)


def _load_agent(spec: SubmittedRuntimeSpec) -> Any:
    import importlib.util
    root = spec.snapshot_root.resolve(); entrypoint = (root / spec.entrypoint.split(":", 1)[0]).resolve()
    if not entrypoint.is_relative_to(root) or _sha_file(entrypoint) != spec.policy_hash or _sha_file(spec.deck_path) != spec.deck_hash: raise SubmittedRuntimeError("IDENTITY_MISMATCH", "snapshot bytes changed before import")
    os.chdir(root); sys.path.insert(0, str(root))
    module_name = "submitted_" + hashlib.sha256(f"{spec.asset_id}\0{spec.source_commit}".encode()).hexdigest()
    module_spec = importlib.util.spec_from_file_location(module_name, entrypoint)
    if module_spec is None or module_spec.loader is None: raise SubmittedRuntimeError("IMPORT_FAILURE", "cannot construct submitted module")
    module = importlib.util.module_from_spec(module_spec); sys.modules[module_name] = module; module_spec.loader.exec_module(module)
    callable_name = spec.entrypoint.split(":", 1)[1] if ":" in spec.entrypoint else "agent"; agent = getattr(module, callable_name, None)
    if not callable(agent): raise SubmittedRuntimeError("IMPORT_FAILURE", "submitted entrypoint is not callable")
    return agent


def _child(payload: str) -> int:
    try:
        spec = SubmittedRuntimeSpec.from_payload(json.loads(payload)); agent = _load_agent(spec)
        print(json.dumps({"status": "READY", "policy_hash": spec.policy_hash, "deck_hash": spec.deck_hash}), flush=True)
    except Exception as exc:
        print(json.dumps({"status": "ERROR", "code": getattr(exc, "code", "IMPORT_FAILURE"), "message": str(exc)[:500]}), flush=True); return 2
    for line in sys.stdin:
        try:
            request = json.loads(line); action = agent(request["observation"])
            print(json.dumps({"status": "OK", "action": action}, ensure_ascii=False), flush=True)
        except Exception as exc: print(json.dumps({"status": "ERROR", "code": "POLICY_EXCEPTION", "message": f"{type(exc).__name__}: {str(exc)[:400]}"}), flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(); parser.add_argument("--child"); args = parser.parse_args(argv)
    return _child(args.child) if args.child is not None else 2


if __name__ == "__main__": raise SystemExit(main())
