from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_uses_ubuntu_2404_and_long_running_container():
    dockerfile = (PROJECT_ROOT / "docker" / "Dockerfile").read_text()
    assert dockerfile.startswith("FROM ubuntu:24.04")
    assert 'CMD ["sleep", "infinity"]' in dockerfile


def test_pyproject_allows_tracksdata_direct_reference_and_uses_cpu_torch():
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
    assert "allow-direct-references = true" in pyproject
    assert 'name = "pytorch-cpu"' in pyproject
    assert 'url = "https://download.pytorch.org/whl/cpu"' in pyproject
    assert 'torch = { index = "pytorch-cpu" }' in pyproject


def test_compose_exposes_named_persistent_development_container():
    compose = (PROJECT_ROOT / "docker-compose.yml").read_text()
    assert "container_name: biohub-dev" in compose
    assert "command: sleep infinity" in compose


def test_setup_script_exists():
    assert (PROJECT_ROOT / "setup.sh").is_file()
