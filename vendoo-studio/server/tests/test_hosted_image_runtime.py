"""The hosted image runs the Python that CI tests and the type checker targets.

Dependabot's #1547 moved the image from python:3.12-slim to 3.15-slim while CI
kept testing 3.12. tree-sitter has no 3.15 wheel and the slim image has no
compiler, so every deploy failed at "Build the image" (run 43811611551), and a
build that got through would have shipped an untested interpreter.
"""

import re
from pathlib import Path

import tomllib
import yaml

ROOT = Path(__file__).resolve().parents[3]


def _image_pythons() -> set[str]:
    dockerfile = (ROOT / 'deploy/fly/Dockerfile').read_text(encoding='utf-8')
    return set(re.findall(r'^FROM python:(\d+\.\d+)[-@:]', dockerfile, re.M))


def _ci_pythons() -> set[str]:
    workflow = yaml.safe_load((ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8'))
    found = set()
    for job in workflow['jobs'].values():
        for step in job.get('steps', []):
            if 'setup-python' in str(step.get('uses', '')):
                found.add(str(step['with']['python-version']))
    return found


def test_hosted_image_runs_the_python_ci_tests():
    image = _image_pythons()
    assert len(image) == 1, f'every Python stage of the image uses one version: {image}'
    assert image == _ci_pythons(), f'image {image} must be the Python CI tests ({_ci_pythons()})'
    pyproject = tomllib.loads((ROOT / 'vendoo-studio/pyproject.toml').read_text(encoding='utf-8'))
    assert {pyproject['tool']['basedpyright']['pythonVersion']} == image


def test_dependabot_keeps_the_image_python_and_moves_only_its_digest():
    config = yaml.safe_load((ROOT / '.github/dependabot.yml').read_text(encoding='utf-8'))
    docker = [entry for entry in config['updates']
              if entry['package-ecosystem'] == 'docker' and entry['directory'] == '/deploy/fly']
    assert len(docker) == 1
    ignored = {rule['dependency-name']: set(rule.get('update-types', [])) for rule in docker[0].get('ignore', [])}
    # A new Python is a deliberate move of CI, the type checker and the image together.
    assert {'version-update:semver-major', 'version-update:semver-minor'} <= ignored.get('python', set())
