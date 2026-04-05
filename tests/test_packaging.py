from __future__ import annotations

from pathlib import Path

import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_public_repo_files_exist() -> None:
    required = [
        ROOT / "README.md",
        ROOT / "LICENSE",
        ROOT / "CONTRIBUTING.md",
        ROOT / "CODE_OF_CONDUCT.md",
        ROOT / "SECURITY.md",
        ROOT / ".gitignore",
        ROOT / "Dockerfile",
        ROOT / ".dockerignore",
        ROOT / "opencac",
        ROOT / "scripts" / "opencac.sh",
        ROOT / ".github" / "workflows" / "ci.yml",
        ROOT / ".github" / "workflows" / "release.yml",
        ROOT / ".github" / "workflows" / "docker-publish.yml",
        ROOT / "compose.yaml",
    ]
    for path in required:
        assert path.exists(), f"missing required public repo file: {path}"
    assert not (ROOT / "a2a").exists(), "legacy a2a launcher should not exist"


def test_pyproject_exposes_publish_metadata() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    assert project["name"] == "opencac"
    assert project["readme"] == "README.md"
    assert project["license"] == "MIT"
    assert "Homepage" in project["urls"]
    assert "Repository" in project["urls"]
    assert "Issues" in project["urls"]
    assert data["project"]["scripts"]["opencac"] == "opencac.cli:main"


def test_gitignore_covers_runtime_artifacts() -> None:
    content = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".opencac/" in content
    assert "dist/" in content
    assert "build/" in content


def test_release_and_docker_workflows_exist() -> None:
    release = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    docker = (ROOT / ".github" / "workflows" / "docker-publish.yml").read_text(encoding="utf-8")
    assert "pypa/gh-action-pypi-publish@release/v1" in release
    assert "actions/upload-artifact@v4" in release
    assert "python -m build" in release
    assert "python -m twine check dist/*" in release
    assert "PYPI_API_TOKEN" in release
    assert "docker/build-push-action@v6" in docker
    assert "docker/metadata-action@v5" in docker
    assert "ghcr.io/lpoee/opencac" in docker


def test_compose_file_wires_opencac_service() -> None:
    compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "services:" in compose
    assert "opencac:" in compose
    assert "host.docker.internal:18101" in compose
    assert "host.docker.internal:18102" in compose
    assert "host.docker.internal:18103" in compose
    assert "A2A_CLOUD_FALLBACK_LOCAL" in compose


def test_public_launchers_target_opencac_cli() -> None:
    root_launcher = (ROOT / "opencac").read_text(encoding="utf-8")
    script_launcher = (ROOT / "scripts" / "opencac.sh").read_text(encoding="utf-8")
    assert 'exec "$SCRIPT_DIR/scripts/opencac.sh" "$@"' in root_launcher
    assert "python3 -m opencac.cli" in script_launcher
