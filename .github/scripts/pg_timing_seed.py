"""Expand reviewed file averages to the node IDs collected by this checkout."""
import json
import os
from pathlib import Path


def pytest_collection_finish(session):
    if not session.config.option.collectonly or session.testsfailed:
        return
    profile_path = Path(__file__).with_name('pg-file-durations.json')
    profile = json.loads(profile_path.read_text())
    timings = {}
    for item in session.items:
        name = item.path.relative_to(session.config.rootpath).as_posix()
        if name in profile:
            timings[item.nodeid] = profile[name]
    with Path(os.environ['PG_DURATIONS_PATH']).open('x') as output:
        json.dump(timings, output)
