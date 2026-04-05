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
