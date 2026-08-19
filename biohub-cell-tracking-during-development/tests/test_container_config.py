from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROJECT_WORKSPACE = "/workspace/biohub-cell-tracking-during-development"


def test_dockerfile_uses_ubuntu_2404_and_long_running_container():
    dockerfile = (PROJECT_ROOT / "docker" / "Dockerfile").read_text()
    assert dockerfile.startswith("FROM ubuntu:24.04")
    assert f"WORKDIR {PROJECT_WORKSPACE}" in dockerfile
    assert 'CMD ["sleep", "infinity"]' in dockerfile


def test_pyproject_allows_tracksdata_direct_reference_and_uses_cpu_torch():
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
    assert "allow-direct-references = true" in pyproject
    assert 'name = "pytorch-cpu"' in pyproject
    assert 'url = "https://download.pytorch.org/whl/cpu"' in pyproject
    assert 'torch = { index = "pytorch-cpu" }' in pyproject


def test_compose_mounts_git_repository_root_and_uses_biohub_workdir():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text()
    assert "container_name: biohub-dev" in compose
    assert "- ..:/workspace" in compose
    assert f"working_dir: {PROJECT_WORKSPACE}" in compose
    assert "command: sleep infinity" in compose


def test_devcontainer_opens_biohub_inside_parent_git_repository():
    devcontainer = (PROJECT_ROOT / ".devcontainer" / "devcontainer.json").read_text()
    assert f'"workspaceFolder": "{PROJECT_WORKSPACE}"' in devcontainer
    assert '"git.openRepositoryInParentFolders": "always"' in devcontainer


def test_setup_script_exists():
    assert (PROJECT_ROOT / "setup.sh").is_file()
