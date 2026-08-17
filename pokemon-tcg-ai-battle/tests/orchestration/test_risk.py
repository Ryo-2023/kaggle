import subprocess
from pathlib import Path

import pytest

from scripts.orchestration.risk import PatchMetadata, classify_risk, inspect_patch_metadata


def _metadata(**changes) -> PatchMetadata:
    values = dict(
        added_lines=1,
        deleted_lines=0,
        diff_lines=1,
        binary=False,
        symlink=False,
        submodule=False,
        large_deletion=False,
    )
    values.update(changes)
    return PatchMetadata(**values)


@pytest.mark.parametrize(
    ("path", "finding"),
    [
        ("scripts/orchestration/provider.py", "sensitive-path"),
        ("requirements.txt", "sensitive-path"),
        (".github/workflows/test.yml", "sensitive-path"),
        ("private/secret.txt", "sensitive-path"),
        ("src/outside.py", "outside-allowed-path"),
        ("docs/locked/a.md", "protected-path"),
        ("forbidden/a.py", "forbidden-path"),
    ],
)
def test_risk_uses_glob_aware_path_policy(path: str, finding: str) -> None:
    result = classify_risk(
        [path],
        allowed_paths=["safe/**"],
        forbidden_paths=["forbidden/**"],
        protected_paths=["docs/locked/**"],
        metadata=_metadata(),
    )
    assert result.level == "HIGH"
    assert finding in result.findings


@pytest.mark.parametrize(
    "field",
    ["binary", "symlink", "submodule", "large_deletion"],
)
def test_git_metadata_forbids_auto_integration(field: str) -> None:
    result = classify_risk(
        ["safe/a.py"],
        allowed_paths=["safe/**"],
        forbidden_paths=[],
        protected_paths=[],
        metadata=_metadata(**{field: True}),
    )
    assert result.level == "HIGH"
    assert field.replace("_", "-") in result.findings


def test_metadata_counts_diff_lines_and_detects_symlink(repository: Path) -> None:
    link = repository / "link.py"
    link.symlink_to("fixture.py")
    subprocess.run(["git", "add", "-N", "link.py"], cwd=repository, check=True)
    metadata = inspect_patch_metadata(repository)
    assert metadata.symlink is True
