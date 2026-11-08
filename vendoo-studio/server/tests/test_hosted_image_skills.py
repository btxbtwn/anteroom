"""The hosted image must carry every skill folder the server reads at runtime."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SERVER = ROOT / 'vendoo-studio/server/vendoo_studio'


def _skills_read_by_server() -> set[str]:
    pattern = re.compile(r'skills_dir\(\)\s*/\s*"([^"/]+)')
    return {name for path in SERVER.rglob('*.py') for name in pattern.findall(path.read_text(encoding='utf-8'))}


def test_hosted_image_copies_every_skill_the_server_reads():
    skills = _skills_read_by_server()
    assert {'list-this', 'measure-this', 'box-scout'} <= skills
    dockerfile = (ROOT / 'deploy/fly/Dockerfile').read_text(encoding='utf-8')
    dockerignore = (ROOT / '.dockerignore').read_text(encoding='utf-8').splitlines()
    for name in sorted(skills):
        assert re.search(rf'^COPY skills/{re.escape(name)}/\S* /app/skills/{re.escape(name)}/', dockerfile, re.M), name
        assert f'!skills/{name}/' in dockerignore, name
