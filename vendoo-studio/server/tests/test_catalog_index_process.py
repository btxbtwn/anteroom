import json
import subprocess
from uuid import uuid4

import pytest

from vendoo_studio.services import catalog_index as catalog
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use


def test_hosted_builder_runs_in_child_without_database_and_keeps_sellers_private(tmp_path, monkeypatch):
    monkeypatch.setenv('VENDOO_STUDIO_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('VENDOO_STUDIO_SKILLS_DIR', str(tmp_path / 'empty-skills'))
    monkeypatch.setenv('VENDOO_STUDIO_EXTENSION_DIR', str(tmp_path / 'empty-extension'))
    # A child that accidentally queries the database cannot complete this build.
    monkeypatch.setenv('ANTEROOM_DATABASE_URL', 'postgresql+psycopg://unavailable@127.0.0.1:9/unavailable')
    monkeypatch.setattr(catalog, '_build_unlocked', lambda _: pytest.fail('built in the API interpreter'))
    owners = [str(uuid4()), str(uuid4())]
    labels = ['Private amber coat', 'Private cobalt shoe']
    for owner, label, other in zip(owners, labels, reversed(labels), strict=True):
        snapshot = catalog.CatalogSnapshot({'trees': {label: 1}}, [('ebay', 'private-leaf', label, label)], [])
        with use(for_seller(owner)):
            result = catalog.build_catalog_index(snapshot)
            assert result['ok']
            index = catalog._state().index
            assert index is not None
            contents = '\n'.join(chunk.content for chunk in index.chunks)
            assert label in contents
            assert other not in contents
            assert catalog._state().fingerprint == json.dumps(snapshot.fingerprint, sort_keys=True)
            assert not list(catalog._catalog_root().glob('build-*'))
    assert (tmp_path / 'sellers' / owners[0] / 'catalog-index').is_dir()
    assert (tmp_path / 'sellers' / owners[1] / 'catalog-index').is_dir()


def test_failed_child_does_not_replace_published_index(tmp_path, monkeypatch):
    monkeypatch.setenv('VENDOO_STUDIO_DATA_DIR', str(tmp_path))
    owner = str(uuid4())

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(catalog.subprocess, 'run', fail)
    with use(for_seller(owner)):
        previous = object()
        catalog._state().index = previous
        catalog._state().fingerprint = 'previous'
        with pytest.raises(subprocess.CalledProcessError):
            catalog.build_catalog_index(catalog.CatalogSnapshot({'trees': {}}, [], []))
        assert catalog._state().index is previous
        assert catalog._state().fingerprint == 'previous'
        assert not list(catalog._catalog_root().glob('build-*'))
