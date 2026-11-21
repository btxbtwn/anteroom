import json
import subprocess
from uuid import uuid4

import pytest

from anteroom.services import catalog_index as catalog
from anteroom.services.hosted_context import for_seller
from anteroom.services.service_context import use


def test_hosted_builder_runs_in_child_without_database_and_keeps_sellers_private(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTEROOM_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ANTEROOM_SKILLS_DIR', str(tmp_path / 'empty-skills'))
    monkeypatch.setenv('ANTEROOM_EXTENSION_DIR', str(tmp_path / 'empty-extension'))
    # A child that accidentally queries the database cannot complete this build.
    monkeypatch.setenv('ANTEROOM_DATABASE_URL', 'postgresql+psycopg://unavailable@127.0.0.1:9/unavailable')
    monkeypatch.setattr(catalog, '_build_unlocked', lambda *a, **kw: pytest.fail('built in the API interpreter'))
    catalog.reset_catalog_index_cache()

    shared_categories = [('ebay', 'shared-leaf', 'Clothing > Coats', 'Coats')]
    owners = [str(uuid4()), str(uuid4())]
    private_paths = ['Private > Amber Coat Schema', 'Private > Cobalt Shoe Schema']
    seen_shared_indexes = []
    worker_inputs = []
    real_run = subprocess.run

    def capture_run(*args, **kwargs):
        worker_inputs.append(json.loads(kwargs['input']))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(catalog.subprocess, 'run', capture_run)

    for owner, private_path, other_path in zip(owners, private_paths, reversed(private_paths), strict=True):
        schema_row = catalog._SchemaRow(
            id=f'schema-{owner}',
            marketplace='ebay',
            general_path=private_path,
            category_path=private_path,
            fields=[{'label': 'Color', 'options': ['Amber', 'Cobalt']}],
        )
        snapshot = catalog.CatalogSnapshot(
            {'trees': {'ebay': {'content_hash': 'hash-v1'}}, 'schemas': 1},
            shared_categories,
            [schema_row],
        )
        with use(for_seller(owner)):
            result = catalog.build_catalog_index(snapshot)
            assert result['ok']
            assert result['counts']['categories'] == 1
            docs_index = catalog._state().index
            assert docs_index is not None
            docs_contents = '\n'.join(chunk.content for chunk in docs_index.chunks)
            assert private_path in docs_contents
            assert other_path not in docs_contents
            # Seller docs index never duplicates public category leaves.
            assert 'shared-leaf' not in docs_contents
            assert catalog._SHARED_CATEGORIES.index is not None
            seen_shared_indexes.append(catalog._SHARED_CATEGORIES.index)
            assert catalog._state().fingerprint == json.dumps(snapshot.fingerprint, sort_keys=True)
            assert not list(catalog._catalog_root().glob('build-*'))

    # Second seller reuses the exact same in-memory shared category index without rebuilding it.
    assert seen_shared_indexes[0] is seen_shared_indexes[1]
    assert worker_inputs[0]['build_categories'] is True
    assert len(worker_inputs[0]['category_rows']) == 1
    assert worker_inputs[1]['build_categories'] is False
    assert worker_inputs[1]['category_rows'] == []

    # Shared category artifacts live once under data_root/catalog-index, never per seller.
    assert (tmp_path / 'catalog-index' / 'categories.semble').is_dir()
    assert (tmp_path / 'catalog-index' / 'category-docs').is_dir()
    for owner in owners:
        seller_root = tmp_path / 'sellers' / owner / 'catalog-index'
        assert seller_root.is_dir()
        assert not (seller_root / 'categories.semble').exists()
        assert not (seller_root / 'category-docs').exists()

    # After a process restart, the saved shared category index loads from disk without re-indexing.
    catalog.reset_catalog_index_cache()
    worker_inputs.clear()
    third_owner = str(uuid4())
    snapshot = catalog.CatalogSnapshot(
        {'trees': {'ebay': {'content_hash': 'hash-v1'}}, 'schemas': 0},
        shared_categories,
        [],
    )
    with use(for_seller(third_owner)):
        result = catalog.build_catalog_index(snapshot)
        assert result['ok']
        assert catalog._SHARED_CATEGORIES.index is not None
    assert worker_inputs[0]['build_categories'] is False
    assert worker_inputs[0]['category_rows'] == []


def test_failed_child_does_not_replace_published_index(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTEROOM_DATA_DIR', str(tmp_path))
    catalog.reset_catalog_index_cache()
    owner = str(uuid4())

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])

    monkeypatch.setattr(catalog.subprocess, 'run', fail)
    with use(for_seller(owner)):
        previous = object()
        previous_categories = object()
        catalog._state().index = previous
        catalog._state().fingerprint = 'previous'
        catalog._SHARED_CATEGORIES.index = previous_categories
        catalog._SHARED_CATEGORIES.fingerprint = 'previous-trees'
        catalog._SHARED_CATEGORIES.root = str(catalog.category_index_dir())
        with pytest.raises(subprocess.CalledProcessError):
            catalog.build_catalog_index(catalog.CatalogSnapshot({'trees': {}}, [], []))
        assert catalog._state().index is previous
        assert catalog._state().fingerprint == 'previous'
        assert catalog._SHARED_CATEGORIES.index is previous_categories
        assert catalog._SHARED_CATEGORIES.fingerprint == 'previous-trees'
        assert not list(catalog._catalog_root().glob('build-*'))


def test_search_catalog_combines_shared_categories_with_seller_schemas(tmp_path, monkeypatch):
    from sqlalchemy.orm import Session

    from anteroom.database import Base, load_models
    from anteroom.models.catalog import CategorySchema, CategoryTree, CategoryTreeNode
    from worker_sessions import memory_engine

    monkeypatch.setenv('ANTEROOM_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ANTEROOM_SKILLS_DIR', str(tmp_path / 'empty-skills'))
    monkeypatch.setenv('ANTEROOM_EXTENSION_DIR', str(tmp_path / 'empty-extension'))
    catalog.reset_catalog_index_cache()

    load_models()
    engine = memory_engine()
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            db.add(CategoryTree(marketplace='ebay', status='complete', roots_loaded=True, content_hash='h1'))
            db.add(CategoryTreeNode(
                marketplace='ebay', category_id='coat-leaf', parent_id='__root',
                path='Clothing > Women > Trench Coats', label='Trench Coats',
                is_leaf=True, has_children=False, children_loaded=True,
            ))
            db.add(CategorySchema(
                id='schema-1', marketplace='ebay',
                general_path='Clothing > Women > Trench Coats',
                category_path='Clothing > Women > Trench Coats',
                fields=[{'label': 'Material', 'options': ['Gabardine']}],
            ))
            db.commit()

            catalog.rebuild_catalog_index(db)

            cat_hits = catalog.search_catalog(db, 'women trench coat', kind='category', top_k=5, ensure=False)
            assert [hit['id'] for hit in cat_hits] == ['coat-leaf']

            schema_hits = catalog.search_catalog(db, 'women trench coat', kind='schema', top_k=5, ensure=False)
            assert [hit['id'] for hit in schema_hits] == ['schema-1']

            all_hits = catalog.search_catalog(db, 'women trench coat', kind='all', top_k=5, ensure=False)
            kinds = {hit['kind'] for hit in all_hits}
            assert {'category', 'schema'}.issubset(kinds)

            # Even if a seller's docs index is still cold while shared categories are warm,
            # kind='all' still supplements schema hits from the database.
            with catalog._INDEX_LOCK:
                catalog._state().index = None
            cold_docs_hits = catalog.search_catalog(db, 'women trench coat', kind='all', top_k=5, ensure=False)
            assert {'category', 'schema'}.issubset({hit['kind'] for hit in cold_docs_hits})
    finally:
        catalog.reset_catalog_index_cache()
        engine.dispose()


def test_failed_category_build_preserves_last_good_index_and_does_not_poison_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('ANTEROOM_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('ANTEROOM_SKILLS_DIR', str(tmp_path / 'empty-skills'))
    monkeypatch.setenv('ANTEROOM_EXTENSION_DIR', str(tmp_path / 'empty-extension'))
    catalog.reset_catalog_index_cache()

    v1_rows = [('ebay', 'leaf-1', 'Clothing > Coats', 'Coats')]
    v2_rows = [('ebay', 'leaf-1', 'Clothing > Winter Coats', 'Winter Coats')]
    v1_snap = catalog.CatalogSnapshot({'trees': {'ebay': {'content_hash': 'h1'}}, 'schemas': 0}, v1_rows, [])
    v2_snap = catalog.CatalogSnapshot({'trees': {'ebay': {'content_hash': 'h1'}}, 'schemas': 0}, v2_rows, [])

    # Path rename changes the trees version even when content_hash and ids are unchanged.
    assert catalog._category_trees_version(v1_snap.fingerprint['trees'], v1_rows) != (
        catalog._category_trees_version(v2_snap.fingerprint['trees'], v2_rows)
    )

    catalog._build_unlocked(v1_snap)
    first_index = catalog._SHARED_CATEGORIES.index
    first_fp = catalog._SHARED_CATEGORIES.fingerprint
    assert first_index is not None
    assert (catalog.category_index_dir() / 'studio-fingerprint.json').is_file()

    # If Semble fails on v2, keep v1 on disk and in memory without stamping v2 as cached.
    monkeypatch.setattr(catalog, '_load_semble_index', lambda _docs: (_ for _ in ()).throw(RuntimeError('boom')))
    catalog._build_unlocked(v2_snap)
    assert catalog._SHARED_CATEGORIES.index is first_index
    assert catalog._SHARED_CATEGORIES.fingerprint == first_fp
    assert (catalog.category_index_dir() / 'studio-fingerprint.json').is_file()

    # Corrupt stamp on disk falls back cleanly to rebuilding instead of raising.
    (catalog.category_index_dir() / 'studio-fingerprint.json').write_bytes(b'\xff\xfe')
    loaded, hit = catalog._load_saved_category_index(catalog.category_index_dir(), first_fp or '')
    assert (loaded, hit) == (None, False)
    catalog.reset_catalog_index_cache()
