"""Submission-local privacy boundary regressions."""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import mage_ptcg.meta_specialist.submission_privacy as submission_privacy
import scripts.build_student_submission as student_submission
from scripts.build_student_submission import (
    ARCHIVE_NAME,
    KAGGLE_STUDENT_RUNTIME_PATHS,
    MANIFEST_NAME,
    MODEL_MEMBER,
    StudentArtifactError,
    build_student_submission,
    verify_student_submission,
)
from scripts.kaggle_student_entrypoint import (
    render_student_cabt_trace,
    render_student_entrypoint,
    render_student_package_init,
    render_student_runtime_model,
)
from mage_ptcg.decision_state import build_decision_state
from mage_ptcg.meta_specialist.submission_privacy import (
    SPECIALIST_V2_MODEL_MANIFEST_ROLE,
    SubmissionPrivacyError,
    validate_submission_members,
)
from mage_ptcg.student.model import MODEL_FEATURE_DIM, StudentV0Model

def _model(path: Path) -> Path:
    StudentV0Model((0.0,) * MODEL_FEATURE_DIM).export(path)
    return path


def _kaggle_generated_files() -> dict[str, bytes]:
    source_root = PROJECT_ROOT / "src" / "mage_ptcg"
    return {
        "src/mage_ptcg/student/__init__.py": render_student_package_init().encode(
            "utf-8"
        ),
        "src/mage_ptcg/student/model.py": render_student_runtime_model(
            (source_root / "student" / "model.py").read_text(encoding="utf-8")
        ).encode("utf-8"),
        "src/mage_ptcg/observability/cabt_trace.py": render_student_cabt_trace(
            (source_root / "observability" / "cabt_trace.py").read_text(
                encoding="utf-8"
            )
        ).encode("utf-8"),
    }


def test_extra_local_jsonl_is_rejected_before_student_artifact_is_created(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"

    with pytest.raises(StudentArtifactError, match="privacy"):
        build_student_submission(
            _model(tmp_path / "student.json"),
            artifact,
            extra_files={
                "local/decisions.jsonl": (
                    b'{"schema_version":"canonical-specialist-decision-v2-local"}\n'
                )
            },
        )

    assert not artifact.exists()


def _replace_archive_member(artifact: Path, name: str, replacement: bytes) -> None:
    archive_path = artifact / ARCHIVE_NAME
    with tarfile.open(archive_path, "r:gz") as archive:
        members = [
            (member.name, archive.extractfile(member).read())
            for member in archive.getmembers()
        ]
    rewritten = artifact / "rewritten.tar.gz"
    with rewritten.open("xb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT) as archive:
                for member_name, data in members:
                    payload = replacement if member_name == name else data
                    info = tarfile.TarInfo(member_name)
                    info.size = len(payload)
                    info.mode = 0o644
                    info.uid = info.gid = info.mtime = 0
                    info.uname = info.gname = ""
                    archive.addfile(info, fileobj=io.BytesIO(payload))
    rewritten.replace(archive_path)


def test_tampered_archive_local_member_is_rejected_after_extraction_boundary(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)

    _replace_archive_member(
        artifact,
        MODEL_MEMBER,
        b'{"schema_version":"canonical-specialist-decision-v2-local"}',
    )
    manifest_path = artifact / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_sha256"] = hashlib.sha256(
        (artifact / ARCHIVE_NAME).read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StudentArtifactError, match="privacy"):
        verify_student_submission(artifact)


def test_runtime_source_contract_identifiers_are_not_treated_as_local_data() -> None:
    validate_submission_members(
        [
            (
                "main.py",
                b'LOCAL_SCHEMA = "canonical-specialist-decision-v2-local"\n',
            )
        ],
        allowed_members={"main.py"},
    )


def test_builder_allows_runtime_source_with_contract_identifier(tmp_path: Path) -> None:
    package = tmp_path / "package"
    generated_main = (PROJECT_ROOT / "main.py").read_bytes() + (
        b'\n# canonical-specialist-decision-v2-local is a contract identifier.\n'
    )

    build_student_submission(
        _model(tmp_path / "student.json"),
        package,
        generated_main=generated_main,
    )

    assert (package / "main.py").read_bytes() == generated_main


def test_builder_rejects_private_python_literal_in_generated_runtime(tmp_path: Path) -> None:
    package = tmp_path / "package"
    generated_main = (PROJECT_ROOT / "main.py").read_bytes() + (
        b'\nPRIVATE_EXPORT = {"own_private_state": [*[101]], "serial": 7}\n'
    )

    with pytest.raises(StudentArtifactError, match="privacy"):
        build_student_submission(
            _model(tmp_path / "student.json"),
            package,
            generated_main=generated_main,
        )

    assert not package.exists()


def test_runtime_source_private_contract_mapping_with_dynamic_value_is_allowed() -> None:
    validate_submission_members(
        [
            (
                "main.py",
                b"def render(private_state):\n"
                b"    return {'own_private_state': private_state}\n",
            )
        ],
        allowed_members={"main.py"},
    )


def test_runtime_source_computed_key_with_dynamic_value_is_allowed() -> None:
    validate_submission_members(
        [
            (
                "main.py",
                b"def render(key, private_state):\n"
                b"    return {key: private_state}\n",
            )
        ],
        allowed_members={"main.py"},
    )


@pytest.mark.parametrize(
    "source",
    (
        b"PRIVATE_EXPORT = dict(own_private_state=[101], serial=7)\n",
        b"def export_private_payload():\n"
        b"    return {'own_private_state': [101], 'serial': 7}\n",
        b"class Export:\n"
        b"    value = {'own_private_state': [101], 'serial': 7}\n",
        b"PRIVATE_EXPORT = dict([('own_private_state', [101]), ('serial', 7)])\n",
        b"PRIVATE_EXPORT = {'own' + '_private_state': [101], 'serial': 7}\n",
    ),
)
def test_runtime_source_rejects_static_private_payloads_at_every_ast_depth(
    source: bytes,
) -> None:
    with pytest.raises(SubmissionPrivacyError, match="local-only"):
        validate_submission_members(
            [("main.py", source)],
            allowed_members={"main.py"},
        )


@pytest.mark.parametrize(
    "source",
    (
        # List, tuple, and set displays can all expand a statically known
        # payload.  They must not turn an otherwise inspectable mapping into
        # a dynamic-looking expression.
        b'PRIVATE_EXPORT = {"own_private_state": [*[101]], "serial": 7}\n',
        b'PRIVATE_EXPORT = {"own_private_state": (*[101],), "serial": 7}\n',
        b'PRIVATE_EXPORT = {"own_private_state": {*[101]}, "serial": 7}\n',
        # ``dict`` accepts positional and keyword unpacking.  The first form
        # exercises an unpacked constructor argument nested under a dict
        # display; the second exercises both forms directly in the call.
        b"PRIVATE_EXPORT = {**dict(*[[('own_private_state', [101]), ('serial', 7)]])}\n",
        b"PRIVATE_EXPORT = dict(*[{'own_private_state': [101]}], **{'serial': 7})\n",
        # ``fromkeys`` is a closed static constructor too, including an
        # unpacked argument list.
        b"PRIVATE_EXPORT = dict.fromkeys(*[['own_private_state'], [101]])\n",
    ),
)
def test_runtime_source_rejects_static_private_payloads_with_literal_unpacking(
    source: bytes,
) -> None:
    with pytest.raises(SubmissionPrivacyError, match="local-only"):
        validate_submission_members(
            [("main.py", source)],
            allowed_members={"main.py"},
        )


@pytest.mark.parametrize(
    "source",
    (
        b"PRIVATE_EXPORT = {''.join(['own', '_private_state']): [101], 'serial': 7}\n",
        b"PRIVATE_EXPORT = {'{}{}'.format('own', '_private_state'): [101], 'serial': 7}\n",
        b"PRIVATE_EXPORT = {'own-private-state'.replace('-', '_'): [101], 'serial': 7}\n",
        b"PRIVATE_EXPORT = {'OWN_PRIVATE_STATE'.casefold(): [101], 'serial': 7}\n",
        b"PRIVATE_EXPORT = {('own_private_state',)[0]: [101], 'serial': 7}\n",
        b"PRIVATE_EXPORT = dict(zip(('own_private_state', 'serial'), ([101], 7)))\n",
        b"PRIVATE_EXPORT = '{\"own_private_state\":[%d],\"serial\":7}' % 101\n",
    ),
)
def test_runtime_source_rejects_private_payloads_with_computed_static_keys_or_json(
    source: bytes,
) -> None:
    with pytest.raises(SubmissionPrivacyError, match="static|local-only"):
        validate_submission_members(
            [("main.py", source)],
            allowed_members={"main.py"},
        )


def test_builder_rejects_computed_private_python_key_in_generated_runtime(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    generated_main = (PROJECT_ROOT / "main.py").read_bytes() + (
        b"\nPRIVATE_EXPORT = {''.join(['own', '_private_state']): [101], "
        b"'serial': 7}\n"
    )

    with pytest.raises(StudentArtifactError, match="privacy"):
        build_student_submission(
            _model(tmp_path / "student.json"),
            package,
            generated_main=generated_main,
        )

    assert not package.exists()


@pytest.mark.parametrize(
    "source",
    (
        b"K = 'own_private_state'\nPRIVATE_EXPORT = {K: [101]}\n",
        b"KEY = 'serial'\nPAYLOAD = {KEY: 7}\n",
        b"K = 'own_private_state'\nALIAS = K\nPRIVATE_EXPORT = {ALIAS: [101]}\n",
        b"K = 'own' + '_private_state'\nPRIVATE_EXPORT = {K: [101]}\n",
        b"K, KEY = ('own_private_state', 'serial')\nPRIVATE_EXPORT = {K: [101], KEY: 7}\n",
        b"K = 'own_private_state'\nPRIVATE_EXPORT = dict([(K, [101])])\n",
        b"K = 'own_private_state'\nPRIVATE_EXPORT = dict(zip((K,), ([101],)))\n",
        b"def export_private():\n"
        b"    K = 'own_private_state'\n"
        b"    return {K: [101]}\n",
        b"class ExportPrivate:\n"
        b"    KEY = 'serial'\n"
        b"    PAYLOAD = {KEY: 7}\n",
        b"K = 'own_private_state'\nPRIVATE_EXPORT = lambda: {K: [101]}\n",
    ),
)
def test_runtime_source_rejects_flow_proven_static_name_keys(
    source: bytes,
) -> None:
    with pytest.raises(SubmissionPrivacyError, match="local-only"):
        validate_submission_members(
            [("main.py", source)],
            allowed_members={"main.py"},
        )


@pytest.mark.parametrize(
    "source",
    (
        b"def render(key):\n"
        b"    K = 'own_private_state'\n"
        b"    K = key\n"
        b"    return {K: [101]}\n",
        b"def render(flag):\n"
        b"    K = 'own_private_state'\n"
        b"    if flag:\n"
        b"        K = 'safe_public_key'\n"
        b"    return {K: [101]}\n",
        b"def render(key):\n"
        b"    value = {K: [101]}\n"
        b"    K = key\n"
        b"    return value\n",
        b"K = 'own_private_state'\n"
        b"K = 'safe_public_key'\n"
        b"PAYLOAD = {K: [101]}\n",
        b"def render(suffix):\n"
        b"    K = 'own_private_state'\n"
        b"    K += suffix\n"
        b"    return {K: [101]}\n",
    ),
)
def test_flow_static_name_tracking_invalidates_runtime_or_ambiguous_keys(
    source: bytes,
) -> None:
    validate_submission_members(
        [("main.py", source)],
        allowed_members={"main.py"},
    )


@pytest.mark.parametrize(
    "source",
    (
        b"def export_private():\n"
        b"    return {K: [101]}\n"
        b"K = 'own_private_state'\n",
        b"export_private = lambda: {K: 7}\n"
        b"K = 'serial'\n",
        b"def export_private():\n"
        b"    return {K: [101], S: 7}\n"
        b"K, S = ('own_private_state', 'serial')\n",
        b"def export_private():\n"
        b"    return dict(zip((K, S), ([101], 7)))\n"
        b"K, S = ('own_private_state', 'serial')\n",
        b"class ExportPrivate:\n"
        b"    def payload(self):\n"
        b"        return {K: [101]}\n"
        b"K = 'own_private_state'\n",
        b"def outer():\n"
        b"    def inner():\n"
        b"        return {K: [101]}\n"
        b"    return inner\n"
        b"K = 'own_private_state'\n",
    ),
)
def test_runtime_source_rejects_forward_static_bindings_captured_by_nested_scopes(
    source: bytes,
) -> None:
    """Later immutable globals remain static at a Python closure's call site."""
    with pytest.raises(SubmissionPrivacyError, match="local-only"):
        validate_submission_members(
            [("main.py", source)],
            allowed_members={"main.py"},
        )


@pytest.mark.parametrize(
    "source",
    (
        b"K = 'own_private_state'\n"
        b"def export_private():\n"
        b"    return '{\\\"%s\\\":[101]}' % K\n",
        b"K = 'own_private_state'\n"
        b"def export_private():\n"
        b"    return f'{{\"{K}\":[101]}}'\n",
        b"K = 'own_private_state'\n"
        b"def export_private():\n"
        b"    return '{{\\\"{}\\\":[101]}}'.format(K)\n",
        b"K = 'own_private_state'\n"
        b"def export_private():\n"
        b"    return ''.join(('{\\\"', K, '\\\":[101]}'))\n",
    ),
)
def test_runtime_source_rejects_bound_name_static_json_composition(
    source: bytes,
) -> None:
    """Supported pure string composition must resolve already-bound names."""
    with pytest.raises(SubmissionPrivacyError, match="local-only"):
        validate_submission_members(
            [("main.py", source)],
            allowed_members={"main.py"},
        )


def test_builder_rejects_cross_statement_private_key_alias(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    generated_main = (PROJECT_ROOT / "main.py").read_bytes() + (
        b"\nPRIVATE_KEY = 'own_private_state'\n"
        b"PRIVATE_EXPORT = {PRIVATE_KEY: [101]}\n"
    )

    with pytest.raises(StudentArtifactError, match="privacy"):
        build_student_submission(
            _model(tmp_path / "student.json"),
            package,
            generated_main=generated_main,
        )

    assert not package.exists()


@pytest.mark.parametrize("payload", (b'{"metric":1e999}', b'{"metric":-1e999}'))
def test_json_members_reject_exponent_overflow_to_non_finite_float(payload: bytes) -> None:
    with pytest.raises(SubmissionPrivacyError, match="non-finite"):
        validate_submission_members(
            [("models/metadata.json", payload)],
            allowed_members={"models/metadata.json"},
        )


def test_kaggle_profile_with_its_two_supported_manifests_clean_room_verifies(
    tmp_path: Path,
) -> None:
    model_path = _model(tmp_path / "student.json")
    package = tmp_path / "package"

    build_student_submission(
        model_path,
        package,
        runtime_paths=KAGGLE_STUDENT_RUNTIME_PATHS,
        generated_main=render_student_entrypoint().encode("utf-8"),
        generated_files=_kaggle_generated_files(),
        extra_files={
            "student-model-manifest.json": b'{"artifact_purpose":"ACTUAL_TRAINED"}',
            "student-package-manifest.json": b'{"agent_kind":"student"}',
        },
    )

    assert verify_student_submission(package)["model_bytes"] == model_path.stat().st_size


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("serial", 777001),
        ("optionIndex", 3),
        ("option-indices", [3]),
        ("execution.index", 3),
        ("EXECUTION_INDICES", [3]),
    ),
)
def test_supported_kaggle_build_rejects_private_locator_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    artifact = tmp_path / "artifact"
    private_manifest = json.dumps(
        {"telemetry": {field: value}},
        separators=(",", ":"),
    ).encode("utf-8")

    with pytest.raises(StudentArtifactError, match="privacy"):
        build_student_submission(
            _model(tmp_path / "student.json"),
            artifact,
            runtime_paths=KAGGLE_STUDENT_RUNTIME_PATHS,
            generated_main=render_student_entrypoint().encode("utf-8"),
            generated_files=_kaggle_generated_files(),
            extra_files={
                "student-model-manifest.json": private_manifest,
                "student-package-manifest.json": b'{"agent_kind":"student"}',
            },
        )

    assert not artifact.exists()


def test_generic_index_field_is_not_treated_as_a_private_execution_index() -> None:
    validate_submission_members(
        [("models/metadata.json", b'{"index":3}')],
        allowed_members={"models/metadata.json"},
    )


@pytest.mark.parametrize(
    "payload",
    (
        b'{"safe":0,"safe":1}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
    ),
)
def test_json_members_fail_closed_for_duplicate_keys_and_non_finite_constants(
    payload: bytes,
) -> None:
    with pytest.raises(SubmissionPrivacyError):
        validate_submission_members(
            [("models/student-v0.json", payload)],
            allowed_members={"models/student-v0.json"},
        )


def test_json_parser_enforces_byte_and_object_pair_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_byte_bound = submission_privacy._MAX_JSON_BYTES
    monkeypatch.setattr(submission_privacy, "_MAX_JSON_BYTES", 1)
    with pytest.raises(SubmissionPrivacyError, match="byte bound"):
        validate_submission_members(
            [("models/student-v0.json", b"{}")],
            allowed_members={"models/student-v0.json"},
        )

    monkeypatch.setattr(submission_privacy, "_MAX_JSON_BYTES", original_byte_bound)
    monkeypatch.setattr(submission_privacy, "_MAX_JSON_OBJECT_PAIRS", 1)
    with pytest.raises(SubmissionPrivacyError, match="strict JSON"):
        validate_submission_members(
            [("models/student-v0.json", b'{"one":1,"two":2}')],
            allowed_members={"models/student-v0.json"},
        )


@pytest.mark.parametrize(
    "field",
    (
        "actor_payload",
        "actorPayload",
        "ACTOR-PAYLOAD",
        "actor.payload",
        "actor__payload",
    ),
)
def test_json_member_normalizes_local_key_spelling(field: str) -> None:
    with pytest.raises(SubmissionPrivacyError, match="local-only"):
        validate_submission_members(
            [("models/student-v0.json", json.dumps({field: []}).encode("utf-8"))],
            allowed_members={"models/student-v0.json"},
        )


@pytest.mark.parametrize("name", ("training-data.json", "dataset_dump.json"))
def test_auxiliary_training_file_names_are_rejected(name: str) -> None:
    with pytest.raises(SubmissionPrivacyError, match="auxiliary"):
        validate_submission_members(
            [(name, b"{}")],
            allowed_members={name},
        )


def test_c5_private_payload_is_rejected_at_extra_file_boundary(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"

    with pytest.raises(StudentArtifactError, match="privacy"):
        build_student_submission(
            _model(tmp_path / "student.json"),
            artifact,
            runtime_paths=KAGGLE_STUDENT_RUNTIME_PATHS,
            generated_main=render_student_entrypoint().encode("utf-8"),
            generated_files=_kaggle_generated_files(),
            extra_files={
                "student-model-manifest.json": b'{"own_private_state":[101]}',
                "student-package-manifest.json": b'{"agent_kind":"student"}',
            },
        )

    assert not artifact.exists()


def test_c5_schema_is_rejected_at_archive_extraction_boundary(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)

    _replace_archive_member(
        artifact,
        MODEL_MEMBER,
        b'{"schema_version":"canonical-decision-v1"}',
    )
    manifest_path = artifact / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_sha256"] = hashlib.sha256(
        (artifact / ARCHIVE_NAME).read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StudentArtifactError, match="privacy"):
        verify_student_submission(artifact)


def test_c5_record_is_rejected_after_extraction_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    manifest = json.loads((artifact / MANIFEST_NAME).read_text(encoding="utf-8"))
    expected_records = manifest["files"]
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    original_write_bytes = Path.write_bytes
    c5_record = (
        b'{"schema_version":"canonical-decision-v1","record_id":"local",'
        b'"own_private_state":[101]}'
    )

    def write_c5_record(target: Path, data: bytes) -> int:
        if target == extracted / MODEL_MEMBER:
            return original_write_bytes(target, c5_record)
        return original_write_bytes(target, data)

    monkeypatch.setattr(Path, "write_bytes", write_c5_record)

    with pytest.raises(StudentArtifactError, match="privacy"):
        student_submission._extract(
            artifact / ARCHIVE_NAME,
            extracted,
            expected_records,
        )


def test_c5_record_id_is_rejected_in_supported_kaggle_manifest_build(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"

    with pytest.raises(StudentArtifactError, match="privacy"):
        build_student_submission(
            _model(tmp_path / "student.json"),
            artifact,
            runtime_paths=KAGGLE_STUDENT_RUNTIME_PATHS,
            generated_main=render_student_entrypoint().encode("utf-8"),
            generated_files=_kaggle_generated_files(),
            extra_files={
                "student-model-manifest.json": (
                    b'{"public_audit":{"c5RecordId":"c5-local"}}'
                ),
                "student-package-manifest.json": b'{"agent_kind":"student"}',
            },
        )

    assert not artifact.exists()


def test_c5_record_id_is_rejected_inside_tampered_archive(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(
        _model(tmp_path / "student.json"),
        artifact,
        runtime_paths=KAGGLE_STUDENT_RUNTIME_PATHS,
        generated_main=render_student_entrypoint().encode("utf-8"),
        generated_files=_kaggle_generated_files(),
        extra_files={
            "student-model-manifest.json": b'{"artifact_purpose":"ACTUAL_TRAINED"}',
            "student-package-manifest.json": b'{"agent_kind":"student"}',
        },
    )

    _replace_archive_member(
        artifact,
        "student-model-manifest.json",
        b'{"public_audit":{"c5_record_id":"c5-local"}}',
    )
    manifest_path = artifact / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_sha256"] = hashlib.sha256(
        (artifact / ARCHIVE_NAME).read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StudentArtifactError, match="privacy"):
        verify_student_submission(artifact)


def test_c5_record_id_is_rejected_after_extraction_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(
        _model(tmp_path / "student.json"),
        artifact,
        runtime_paths=KAGGLE_STUDENT_RUNTIME_PATHS,
        generated_main=render_student_entrypoint().encode("utf-8"),
        generated_files=_kaggle_generated_files(),
        extra_files={
            "student-model-manifest.json": b'{"artifact_purpose":"ACTUAL_TRAINED"}',
            "student-package-manifest.json": b'{"agent_kind":"student"}',
        },
    )
    manifest = json.loads((artifact / MANIFEST_NAME).read_text(encoding="utf-8"))
    expected_records = manifest["files"]
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    original_write_bytes = Path.write_bytes
    c5_record_reference = b'{"public_audit":{"c5RecordId":"c5-local"}}'

    def write_c5_record_reference(target: Path, data: bytes) -> int:
        if target == extracted / "student-model-manifest.json":
            return original_write_bytes(target, c5_record_reference)
        return original_write_bytes(target, data)

    monkeypatch.setattr(Path, "write_bytes", write_c5_record_reference)

    with pytest.raises(StudentArtifactError, match="privacy"):
        student_submission._extract(
            artifact / ARCHIVE_NAME,
            extracted,
            expected_records,
        )


def _public_trace_observation() -> dict[str, object]:
    def player(owner: int) -> dict[str, object]:
        return {
            "active": [],
            "asleep": False,
            "bench": [],
            "benchMax": 5,
            "burned": False,
            "confused": False,
            "deckCount": 53,
            "discard": [],
            "hand": [{"id": 101 + owner, "serial": 201 + owner, "playerIndex": owner}],
            "handCount": 1,
            "paralyzed": False,
            "poisoned": False,
            "prize": [None] * 6,
        }

    return {
        "current": {
            "energyAttached": False,
            "firstPlayer": 0,
            "players": [player(0), player(1)],
            "result": -1,
            "retreated": False,
            "stadium": [],
            "stadiumPlayed": False,
            "supporterPlayed": False,
            "turn": 2,
            "turnActionCount": 3,
            "yourIndex": 0,
        },
        "select": {
            "context": 0,
            "maxCount": 1,
            "minCount": 1,
            "option": [{"type": 14}],
            "type": 0,
        },
        "step": 7,
    }


def test_real_redacted_decision_state_trace_payload_is_submission_safe() -> None:
    public_trace = build_decision_state(_public_trace_observation()).to_trace_payload()
    validate_submission_members(
        [("telemetry/public-trace.json", json.dumps(public_trace).encode("utf-8"))],
        allowed_members={"telemetry/public-trace.json"},
    )


def test_c5_public_action_marker_is_rejected_from_submission_json() -> None:
    with pytest.raises(SubmissionPrivacyError, match="local-only schema"):
        validate_submission_members(
            [
                (
                    "telemetry/public-trace.json",
                    b'{"privacy":{"redaction_version":"c5-public-action-v1"}}',
                )
            ],
            allowed_members={"telemetry/public-trace.json"},
        )


def test_builder_uses_the_single_validated_model_snapshot_when_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_path = _model(tmp_path / "student.json")
    original_read_bytes = Path.read_bytes
    original_model = original_read_bytes(model_path)
    replacement_path = tmp_path / "replacement.json"
    StudentV0Model(
        (0.0,) * MODEL_FEATURE_DIM,
        bias=1.0,
    ).export(replacement_path)
    replacement_model = original_read_bytes(replacement_path)
    changed = False

    original_read_bounded = student_submission._read_bounded_file

    def read_then_change_source(path: Path, **kwargs: object) -> bytes:
        nonlocal changed
        data = original_read_bounded(path, **kwargs)
        if path == model_path and not changed:
            changed = True
            path.write_bytes(replacement_model)
        return data

    monkeypatch.setattr(student_submission, "_read_bounded_file", read_then_change_source)
    artifact = tmp_path / "artifact"

    build_student_submission(model_path, artifact)

    assert changed is True
    assert original_read_bytes(artifact / MODEL_MEMBER) == original_model


def test_archive_member_bytes_must_match_the_root_manifest_record(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    archive_main = (artifact / "main.py").read_bytes() + b"\n# archive-only tamper\n"
    _replace_archive_member(artifact, "main.py", archive_main)
    manifest_path = artifact / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_sha256"] = hashlib.sha256(
        (artifact / ARCHIVE_NAME).read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StudentArtifactError, match="archive member.*manifest"):
        verify_student_submission(artifact)


def test_archive_with_trailing_private_payload_is_rejected_even_if_rehashed(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    archive_path = artifact / ARCHIVE_NAME
    with archive_path.open("ab") as archive:
        archive.write(b'{"own_private_state":[999],"serial":123}')
    manifest_path = artifact / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_sha256"] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StudentArtifactError, match="not canonical"):
        verify_student_submission(artifact)


def _private_python_payload_of_size(size: int) -> bytes:
    payload = b'PRIVATE_EXPORT = {"own_private_state": [101], "serial": 7}\n'
    assert len(payload) < size
    return payload + b"#" + b"x" * (size - len(payload) - 2) + b"\n"


def _computed_private_python_payload_of_size(size: int) -> bytes:
    payload = (
        b"PRIVATE_EXPORT = {''.join(['own', '_private_state']): [101], "
        b"'serial': 7}\n"
    )
    assert len(payload) < size
    return payload + b"#" + b"x" * (size - len(payload) - 2) + b"\n"


def test_private_python_literal_is_rejected_from_a_same_size_archive_member(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    _replace_archive_member(
        artifact,
        "main.py",
        _private_python_payload_of_size((artifact / "main.py").stat().st_size),
    )
    manifest_path = artifact / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_sha256"] = hashlib.sha256(
        (artifact / ARCHIVE_NAME).read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StudentArtifactError, match="privacy"):
        verify_student_submission(artifact)


def test_computed_private_python_key_is_rejected_from_verified_archive(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    _replace_archive_member(
        artifact,
        "main.py",
        _computed_private_python_payload_of_size(
            (artifact / "main.py").stat().st_size
        ),
    )
    manifest_path = artifact / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["archive_sha256"] = hashlib.sha256(
        (artifact / ARCHIVE_NAME).read_bytes()
    ).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StudentArtifactError, match="privacy"):
        verify_student_submission(artifact)


def test_private_python_literal_is_rejected_after_extraction_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    original_write_bytes = Path.write_bytes

    def inject_private_python_literal(target: Path, data: bytes) -> int:
        if target.name == "main.py" and "student-v0-clean-room-" in str(target):
            data = _private_python_payload_of_size(len(data))
        return original_write_bytes(target, data)

    monkeypatch.setattr(Path, "write_bytes", inject_private_python_literal)

    with pytest.raises(StudentArtifactError, match="privacy"):
        verify_student_submission(artifact)


def test_manifest_record_size_is_capped_before_member_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(student_submission, "_MAX_MEMBER_BYTES", 1)
    with pytest.raises(StudentArtifactError, match="size exceeds byte bound"):
        student_submission._validated_manifest_records(
            [{"path": MODEL_MEMBER, "sha256": "a" * 64, "size": 2}]
        )


def test_archive_size_is_preflighted_before_tar_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    monkeypatch.setattr(student_submission, "_MAX_ARCHIVE_BYTES", 1)

    def tar_open_must_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("tar parser must not receive an oversized archive")

    monkeypatch.setattr(student_submission.tarfile, "open", tar_open_must_not_run)
    with pytest.raises(StudentArtifactError, match="archive exceeds byte bound"):
        verify_student_submission(artifact)


def test_archive_extended_metadata_is_rejected_before_tarfile_parses_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PAX/GNU records must not be read by tarfile before the bounded profile."""
    pax = tarfile.TarInfo("pax-header")
    pax.type = tarfile.XHDTYPE
    pax.size = 512
    raw_tar = pax.tobuf(format=tarfile.USTAR_FORMAT) + (b"x" * pax.size) + (b"\0" * 1024)
    archive = gzip.compress(raw_tar, mtime=0)

    def tar_open_must_not_run(*args: object, **kwargs: object) -> object:
        raise AssertionError("tarfile must not receive PAX/GNU extension data")

    monkeypatch.setattr(student_submission.tarfile, "open", tar_open_must_not_run)
    with pytest.raises(StudentArtifactError, match="extended metadata"):
        student_submission._extract(
            archive,
            tmp_path / "extracted",
            [{"path": "main.py", "sha256": "a" * 64, "size": 0}],
        )


def test_post_write_member_bytes_are_rechecked_against_the_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    original_write_bytes = Path.write_bytes

    def alter_extracted_runtime(target: Path, data: bytes) -> int:
        if target.name == "main.py" and "student-v0-clean-room-" in str(target):
            data += b"\n# post-write tamper\n"
        return original_write_bytes(target, data)

    monkeypatch.setattr(Path, "write_bytes", alter_extracted_runtime)

    with pytest.raises(StudentArtifactError, match="post-extraction member.*manifest"):
        verify_student_submission(artifact)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("public_audit", {"c5_record_id": "c5-local"}),
        ("serial", 777001),
    ),
)
def test_outer_manifest_rejects_private_json_fields(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    manifest_path = artifact / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StudentArtifactError, match="manifest privacy"):
        verify_student_submission(artifact)


def test_outer_manifest_rejects_duplicate_top_level_keys(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    manifest_path = artifact / MANIFEST_NAME
    manifest_text = manifest_path.read_text(encoding="utf-8").rstrip()
    duplicate = (
        manifest_text[:-1]
        + ',"agent_identity":"student-v0-rule-v0-fallback"}'
    )
    manifest_path.write_text(duplicate, encoding="utf-8")

    with pytest.raises(StudentArtifactError, match="manifest.*strict JSON"):
        verify_student_submission(artifact)


def test_outer_manifest_rejects_non_finite_json_constants(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    manifest_path = artifact / MANIFEST_NAME
    manifest_text = manifest_path.read_text(encoding="utf-8").rstrip()
    manifest_path.write_text(
        manifest_text[:-1] + ',"diagnostic":NaN}',
        encoding="utf-8",
    )

    with pytest.raises(StudentArtifactError, match="manifest.*strict JSON"):
        verify_student_submission(artifact)


@pytest.mark.parametrize(
    ("mutation", "match"),
    (
        ({"unexpected": "field"}, "top-level"),
        ({"artifact_schema_version": 2}, "schema version"),
        ({"artifact_schema_version": True}, "schema version"),
        ({"archive_sha256": "A" * 64}, "outer manifest archive hash"),
    ),
)
def test_outer_manifest_requires_its_closed_versioned_shape(
    tmp_path: Path,
    mutation: dict[str, object],
    match: str,
) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    manifest_path = artifact / MANIFEST_NAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(mutation)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(StudentArtifactError, match=match):
        verify_student_submission(artifact)


def test_verifier_rejects_unmanifested_regular_file(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    (artifact / "local-record.jsonl").write_text(
        '{"schema_version":"canonical-specialist-decision-v2-local"}\n',
        encoding="utf-8",
    )

    with pytest.raises(StudentArtifactError, match="closed inventory"):
        verify_student_submission(artifact)


def test_verifier_rejects_unmanifested_symlink(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    (artifact / "local-record-link").symlink_to(artifact / "main.py")

    with pytest.raises(StudentArtifactError, match="closed inventory"):
        verify_student_submission(artifact)


def test_verifier_rejects_unmanifested_directory(tmp_path: Path) -> None:
    artifact = tmp_path / "artifact"
    build_student_submission(_model(tmp_path / "student.json"), artifact)
    (artifact / "unmanifested").mkdir()

    with pytest.raises(StudentArtifactError, match="closed inventory"):
        verify_student_submission(artifact)


@pytest.mark.parametrize(
    "manifest",
    (
        [],
        {
            "c1_schema_version": 2,
            "feature_schema_hash": "a" * 64,
        },
        {
            "featureDomain": "actor-visible-action-v1",
            "c1_schema_version": 2,
            "feature_schema_hash": "a" * 64,
        },
        {
            "feature_domain": "actor-visible-action-v1",
            "c1_schema_version": 1,
            "feature_schema_hash": "a" * 64,
        },
        {
            "feature_domain": "actor-visible-action-v1",
            "c1_schema_version": 2,
            "feature_schema_hash": "not-a-schema-hash",
        },
        {
            "feature_domain": "actor-visible-action-v1",
            "c1_schema_version": 2,
            "feature_schema_hash": "a" * 64,
            "unexpected": "field",
        },
    ),
)
def test_v2_model_manifest_requires_c1_v2_and_feature_schema_hash(
    manifest: object,
) -> None:
    with pytest.raises(SubmissionPrivacyError, match="v2 model manifest"):
        validate_submission_members(
            [("models/specialist-v2.json", json.dumps(manifest).encode("utf-8"))],
            allowed_members={"models/specialist-v2.json"},
            required_json_roles={
                "models/specialist-v2.json": SPECIALIST_V2_MODEL_MANIFEST_ROLE,
            },
        )


def test_specialist_v2_model_manifest_role_accepts_exact_closed_binding() -> None:
    validate_submission_members(
        [
            (
                "models/specialist-v2.json",
                json.dumps(
                    {
                        "feature_domain": "actor-visible-action-v1",
                        "c1_schema_version": 2,
                        "feature_schema_hash": "a" * 64,
                    }
                ).encode("utf-8"),
            )
        ],
        allowed_members={"models/specialist-v2.json"},
        required_json_roles={
            "models/specialist-v2.json": SPECIALIST_V2_MODEL_MANIFEST_ROLE,
        },
    )


def test_required_specialist_v2_manifest_role_cannot_name_an_absent_member() -> None:
    with pytest.raises(SubmissionPrivacyError, match="required JSON role"):
        validate_submission_members(
            [("main.py", b"# runtime\n")],
            allowed_members={"main.py", "models/specialist-v2.json"},
            required_json_roles={
                "models/specialist-v2.json": SPECIALIST_V2_MODEL_MANIFEST_ROLE,
            },
        )


def test_legacy_student_model_remains_allowed_without_specialist_role(
    tmp_path: Path,
) -> None:
    model_bytes = _model(tmp_path / "student.json").read_bytes()
    validate_submission_members(
        [(MODEL_MEMBER, model_bytes)],
        allowed_members={MODEL_MEMBER},
    )


@pytest.mark.parametrize("field", ("actor_payload", "looking"))
def test_json_member_rejects_actor_payload_or_reveal_fields(field: str) -> None:
    with pytest.raises(SubmissionPrivacyError, match="local-only"):
        validate_submission_members(
            [("models/student-v0.json", json.dumps({field: []}).encode("utf-8"))],
            allowed_members={"models/student-v0.json"},
        )
