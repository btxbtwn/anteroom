"""Provider dependencies must ship without the desktop or development extras."""

from pathlib import Path
import tomllib


def test_jsonschema_is_a_direct_runtime_dependency():
    project = Path(__file__).resolve().parents[2] / "pyproject.toml"
    dependencies = tomllib.loads(project.read_text())["project"]["dependencies"]
    assert any(dependency.startswith("jsonschema>=") for dependency in dependencies)
