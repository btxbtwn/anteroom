from sqlalchemy import event, insert
from sqlalchemy.orm import Session

from test_seller_ownership import migration_engine  # noqa: F401
from vendoo_studio.models.catalog import CategoryTree, CategoryTreeNode
from vendoo_studio.services import schema_migrations
from vendoo_studio.services.agent_categories import tree_leaves


def test_tree_leaves_returns_terminal_values_without_hydrating_the_tree(migration_engine):  # noqa: F811
    schema_migrations.ensure_schema(migration_engine)
    with Session(migration_engine) as db:
        db.add_all([CategoryTree(marketplace=market) for market in ('ebay', 'etsy', 'depop')])
        db.execute(insert(CategoryTreeNode), [
            {'marketplace': market, 'category_id': identity, 'parent_id': '',
             'path': path, 'label': path, 'is_leaf': leaf, 'has_children': children}
            for market, identity, path, leaf, children in [
                ('ebay', 'leaf', 'Clothing > Shirts', True, False),
                ('ebay', 'flagged', 'Clothing > Tops', True, True),
                ('ebay', 'terminal', 'Clothing > Tees', False, False),
                ('etsy', 'other', 'Clothing > Other', True, False),
                ('mercari', 'orphan', 'Missing tree > Leaf', True, False),
                *[('ebay', f'branch-{number}', f'Branch {number}', False, True) for number in range(1000)],
            ]
        ])
        db.commit()

    loaded = []

    def record_load(node, _context):
        loaded.append(node.category_id)

    event.listen(CategoryTreeNode, 'load', record_load)
    try:
        with Session(migration_engine) as db:
            assert sorted(tree_leaves(db, 'ebay'), key=lambda row: row['id']) == [
                {'id': 'flagged', 'path': 'Clothing > Tops'},
                {'id': 'leaf', 'path': 'Clothing > Shirts'},
                {'id': 'terminal', 'path': 'Clothing > Tees'},
            ]
            assert tree_leaves(db, 'etsy') == [{'id': 'other', 'path': 'Clothing > Other'}]
            assert tree_leaves(db, 'depop') == []
            assert tree_leaves(db, 'mercari') == []
    finally:
        event.remove(CategoryTreeNode, 'load', record_load)
    assert loaded == [], 'Concurrent worker reads must not hydrate every category node just to return leaf IDs and paths'
