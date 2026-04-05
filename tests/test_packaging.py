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
        ROOT / ".github" / "workflows" / "ci.yml",
    ]
    for path in required:
        assert path.exists(), f"missing required public repo file: {path}"


def test_pyproject_exposes_publish_metadata() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    assert project["name"] == "opencac"
    assert project["readme"] == "README.md"
    assert project["license"]["file"] == "LICENSE"
    assert "Homepage" in project["urls"]
    assert "Repository" in project["urls"]
    assert "Issues" in project["urls"]
