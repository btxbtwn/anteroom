"""The shared category trees, loaded by the operator command, back the web pickers."""
import json

from test_hosted_drafts import BOB, cloud, headers  # noqa: F401
from test_seller_ownership import migration_engine  # noqa: F401
from test_seller_sessions import jwks, signing_keys  # noqa: F401
from anteroom import category_trees_cli
from anteroom.services import catalog_index
from anteroom.services.category_tree import MARKETPLACES
from anteroom.services.category_tree_seed import SEED_VERSION

LEAVES = {
    'general': ('g-coat', 'Women > Clothing > Coats'),
    'ebay': ('57988', "Clothing, Shoes & Accessories > Women > Women's Clothing > Coats, Jackets & Vests"),
    'depop': ('womenswear_coats', 'Womenswear > Coats'),
}


def seed(tmp_path):
    nodes = []
    for marketplace in MARKETPLACES:
        category_id, path = LEAVES.get(marketplace, (marketplace + '-leaf', marketplace.title() + ' > Coats'))
        parent = path.split(' > ')[0]
        nodes += [
            {'marketplace': marketplace, 'category_id': marketplace + '-root', 'parent_id': '__root', 'path': parent,
             'label': parent, 'is_leaf': False, 'has_children': True},
            {'marketplace': marketplace, 'category_id': category_id, 'parent_id': marketplace + '-root', 'path': path,
             'label': path.split(' > ')[-1], 'is_leaf': True, 'has_children': False},
        ]
    path = tmp_path / 'seed.json'
    path.write_text(json.dumps({'version': SEED_VERSION, 'category_tree_nodes': nodes,
                                'category_trees': [{'marketplace': name, 'updated_at': '2026-01-01 00:00:00',
                                                    'source': 'test'} for name in MARKETPLACES]}))
    return path


def test_loaded_trees_enable_verified_category_pickers_and_bulk_writes(cloud, signing_keys, tmp_path, monkeypatch, capsys):  # noqa: F811
    client, engine = cloud
    alice = headers(signing_keys)
    # Keep the background Semble build out of this test; search uses the verified lexical path.
    monkeypatch.setattr(catalog_index, '_kick_rebuild', lambda: None)
    assert 'catalog_categories' not in client.get('/api/runtime', headers=alice).json()['features']
    assert client.get('/api/catalog/categories?marketplace=depop').status_code == 401

    monkeypatch.setenv('ANTEROOM_TEST_MIGRATION_URL', engine.url.render_as_string(hide_password=False))
    assert category_trees_cli.main(['import', '--database-url-env', 'ANTEROOM_TEST_MIGRATION_URL', '--seed', str(seed(tmp_path))]) == 0
    output = capsys.readouterr().out
    assert 'postgresql' not in output and json.loads(output)['db'] == 'ANTEROOM_TEST_MIGRATION_URL'

    assert 'catalog_categories' in client.get('/api/runtime', headers=alice).json()['features']
    tree = client.get('/api/catalog/categories?marketplace=ebay', headers=alice)
    assert tree.status_code == 200, tree.text
    assert tree.json()['complete'] and LEAVES['ebay'][0] in {node['id'] for node in tree.json()['nodes']}
    found = client.get('/api/catalog/search', headers=alice, params={'q': 'womenswear coats', 'marketplace': 'depop', 'kind': 'category'})
    assert found.status_code == 200, found.text
    assert [row['id'] for row in found.json()['results']] == [LEAVES['depop'][0]]

    identity = client.post('/api/conversations', headers=alice, json={'title': 'Coat'}).json()['id']
    assert client.put('/api/conversations/' + identity + '/listing', headers=alice, json={
        'listing': {'title': 'Coat', 'price': 40}, 'expected_revision_id': ''}).status_code == 200
    ebay_path = LEAVES['ebay'][1].replace(' > ', ' › ')
    edits = {'category': {'general': LEAVES['general'][0], 'depop': LEAVES['depop'][0],
                          'ebay': {'id': LEAVES['ebay'][0], 'path': ebay_path}}}
    endpoint = '/api/listings/bulk-edit'
    for forged in ({'ebay': {'id': '123', 'path': 'Unverified category'}},
                   {'ebay': {'id': LEAVES['ebay'][0], 'path': 'Some other path'}},
                   {'general': 'general-root'}):
        assert client.post(endpoint + '/preview', headers=alice, json={
            'conversation_ids': [identity], 'edits': {'category': forged}}).status_code == 400
    preview = client.post(endpoint + '/preview', headers=alice, json={'conversation_ids': [identity], 'edits': edits})
    assert preview.status_code == 200, preview.text
    row = preview.json()['rows'][0]
    assert not row['skip_reason'], row
    fields = ('conversation_id', 'expected_revision_id', 'expected_basis', 'expected_result')
    applied = client.post(endpoint + '/apply', headers=alice, json={'edits': edits, 'rows': [{key: row[key] for key in fields}]})
    assert applied.status_code == 200 and len(applied.json()['applied']) == 1, applied.text
    listing = client.get('/api/conversations/' + identity + '/listing', headers=alice).json()['listing']
    assert listing['category_path'] == LEAVES['general'][1]
    assert listing['direct_category_ids']['ebay'] == LEAVES['ebay'][0]
    assert listing['direct_category_ids']['depop'] == LEAVES['depop'][0]


def test_seller_index_state_persists_and_hosted_lookups_never_build_inline(monkeypatch):
    import uuid

    from anteroom.services.hosted_context import for_seller
    from anteroom.services.service_context import use

    alice, bob = str(uuid.uuid4()), str(uuid.uuid4())
    assert for_seller(alice).catalog is for_seller(alice).catalog
    assert for_seller(alice).catalog is not for_seller(bob).catalog
    kicked = []
    monkeypatch.setattr(catalog_index, 'fingerprint', lambda db: {'categories': 1})
    monkeypatch.setattr(catalog_index, '_kick_rebuild', lambda: kicked.append(True))
    monkeypatch.setattr(catalog_index, 'rebuild_catalog_index', lambda db: (_ for _ in ()).throw(AssertionError('built inline')))
    with use(for_seller(alice)):
        assert catalog_index.ensure_catalog_index(None)['rebuilt'] is False
    assert kicked == [True]


def test_complete_category_trees_are_cached_per_version_and_invalidated_on_reimport(
    cloud, signing_keys, tmp_path, monkeypatch,  # noqa: F811
):
    from sqlalchemy import event

    client, engine = cloud
    alice = headers(signing_keys)
    monkeypatch.setenv('ANTEROOM_TEST_MIGRATION_URL', engine.url.render_as_string(hide_password=False))
    seed_path = seed(tmp_path)
    assert category_trees_cli.main(['import', '--database-url-env', 'ANTEROOM_TEST_MIGRATION_URL', '--seed', str(seed_path)]) == 0

    node_queries: list[str] = []

    def count_node_queries(_conn, _cursor, statement, *_args):
        if 'category_tree_nodes' in statement.lower():
            node_queries.append(statement)

    event.listen(engine, 'before_cursor_execute', count_node_queries)
    try:
        first = client.get('/api/catalog/categories?marketplace=ebay', headers=alice)
        assert first.status_code == 200, first.text
        etag = first.headers.get('etag')
        assert etag
        assert len(node_queries) == 1

        second = client.get('/api/catalog/categories?marketplace=ebay', headers=alice)
        assert second.status_code == 200
        assert second.json() == first.json()
        assert second.headers.get('etag') == etag
        assert len(node_queries) == 1  # served from cache without scanning category_tree_nodes

        not_modified = client.get(
            '/api/catalog/categories?marketplace=ebay',
            headers={**alice, 'If-None-Match': etag},
        )
        assert not_modified.status_code == 304
        assert len(node_queries) == 1

        # An identical import keeps the response cached, even across CLI engines.
        assert category_trees_cli.main(['import', '--database-url-env', 'ANTEROOM_TEST_MIGRATION_URL', '--seed', str(seed_path)]) == 0
        unchanged = client.get('/api/catalog/categories?marketplace=ebay',
                               headers={**alice, 'If-None-Match': etag})
        assert unchanged.status_code == 304
        assert len(node_queries) == 1

        # Changed nodes invalidate the cache even with identical seed metadata.
        updated = json.loads(seed_path.read_text())
        updated['category_tree_nodes'].append({
            'marketplace': 'ebay',
            'category_id': '999999',
            'parent_id': 'ebay-root',
            'path': 'Clothing, Shoes & Accessories > New Leaf',
            'label': 'New Leaf',
            'is_leaf': True,
            'has_children': False,
        })
        seed_path2 = tmp_path / 'seed2.json'
        seed_path2.write_text(json.dumps(updated))
        assert category_trees_cli.main(['import', '--database-url-env', 'ANTEROOM_TEST_MIGRATION_URL', '--seed', str(seed_path2)]) == 0

        refreshed = client.get(
            '/api/catalog/categories?marketplace=ebay',
            headers={**alice, 'If-None-Match': etag},
        )
        assert refreshed.status_code == 200
        assert refreshed.headers.get('etag') != etag
        assert '999999' in {node['id'] for node in refreshed.json()['nodes']}
        assert len(node_queries) == 2
    finally:
        event.remove(engine, 'before_cursor_execute', count_node_queries)
