import gzip
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from anteroom import category_trees_cli
from anteroom.services.category_tree import MARKETPLACES
from anteroom.services.category_tree_seed import (
    ensure_seeded_category_trees,
    export_seed,
    import_seed,
    seed_matches,
    trees_complete,
)


class CategoryTreeSeedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "source.db"
        self.target = self.root / "target.db"
        self.seed = self.root / "seed.json.gz"
        import sqlite3

        con = sqlite3.connect(self.source)
        con.executescript(
            """
            CREATE TABLE category_trees (
                marketplace VARCHAR NOT NULL PRIMARY KEY,
                roots_loaded BOOLEAN NOT NULL,
                status VARCHAR NOT NULL,
                error VARCHAR,
                source VARCHAR,
                updated_at DATETIME,
                content_hash VARCHAR
            );
            CREATE TABLE category_tree_nodes (
                marketplace VARCHAR NOT NULL,
                category_id VARCHAR NOT NULL,
                parent_id VARCHAR NOT NULL,
                path VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                is_leaf BOOLEAN NOT NULL,
                has_children BOOLEAN NOT NULL,
                children_loaded BOOLEAN NOT NULL,
                PRIMARY KEY (marketplace, category_id)
            );
            """
        )
        for marketplace in MARKETPLACES:
            con.execute(
                "INSERT INTO category_trees VALUES (?, 1, 'complete', NULL, 'seed-test', '2026-01-01', NULL)",
                (marketplace,),
            )
            con.execute(
                "INSERT INTO category_tree_nodes VALUES (?, ?, '__root', ?, ?, 1, 0, 1)",
                (marketplace, f"{marketplace}-root", marketplace.title(), marketplace.title()),
            )
        con.commit()
        con.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_export_requires_complete_trees(self):
        import sqlite3

        con = sqlite3.connect(self.source)
        con.execute("UPDATE category_trees SET status='paused' WHERE marketplace='ebay'")
        con.commit()
        con.close()
        with self.assertRaisesRegex(RuntimeError, "Incomplete marketplaces: ebay"):
            export_seed(self.source, self.seed)

    def test_round_trip_replaces_trees_and_marks_complete(self):
        result = export_seed(self.source, self.seed)
        self.assertEqual(result["nodes"], len(MARKETPLACES))
        self.assertTrue(self.seed.is_file())
        payload = json.loads(gzip.decompress(self.seed.read_bytes()))
        self.assertEqual(payload["version"], 1)
        self.assertEqual(set(payload["node_counts"]), set(MARKETPLACES))

        import sqlite3

        con = sqlite3.connect(self.target)
        con.execute(
            "CREATE TABLE category_trees (marketplace VARCHAR PRIMARY KEY, roots_loaded BOOLEAN, "
            "status VARCHAR, error VARCHAR, source VARCHAR, updated_at DATETIME, content_hash VARCHAR)"
        )
        con.execute(
            "CREATE TABLE category_tree_nodes (marketplace VARCHAR, category_id VARCHAR, "
            "parent_id VARCHAR, path VARCHAR, label VARCHAR, is_leaf BOOLEAN, "
            "has_children BOOLEAN, children_loaded BOOLEAN, PRIMARY KEY (marketplace, category_id))"
        )
        con.execute(
            "INSERT INTO category_trees VALUES ('ebay', 0, 'pending', 'old', NULL, NULL, NULL)"
        )
        con.execute(
            "INSERT INTO category_tree_nodes VALUES ('ebay', 'stale', '__root', 'Stale', 'Stale', 1, 0, 1)"
        )
        con.commit()
        con.close()

        imported = import_seed(self.target, self.seed)
        self.assertTrue(all(status == "complete" for status in imported["statuses"].values()))
        self.assertEqual(imported["node_counts"]["ebay"], 1)

        con = sqlite3.connect(self.target)
        stale = con.execute(
            "SELECT COUNT(*) FROM category_tree_nodes WHERE category_id='stale'"
        ).fetchone()[0]
        self.assertEqual(stale, 0)
        self.assertEqual(
            con.execute("SELECT COUNT(*) FROM category_trees").fetchone()[0],
            len(MARKETPLACES),
        )
        con.close()

    def test_changed_nodes_invalidate_response_with_unchanged_seed_metadata(self):
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session

        from anteroom.routes.catalog import categories, _serialize_tree_nodes

        export_seed(self.source, self.seed)
        import_seed(self.target, self.seed)
        engine = create_engine(f"sqlite:///{self.target}")

        def response(etag=None):
            with Session(engine) as db:
                return categories("ebay", etag, db)

        try:
            with patch("anteroom.routes.catalog._serialize_tree_nodes", wraps=_serialize_tree_nodes) as serialize:
                first = response()
                etag = first.headers["etag"]
                import_seed(self.target, self.seed)
                self.assertEqual(response(etag).status_code, 304)
                self.assertEqual(serialize.call_count, 1)

                payload = json.loads(gzip.decompress(self.seed.read_bytes()))
                for node in payload["category_tree_nodes"]:
                    if node["marketplace"] == "ebay":
                        node["label"] = "Changed label"
                self.seed.write_bytes(gzip.compress(json.dumps(payload).encode()))
                import_seed(self.target, self.seed)
                refreshed = response(etag)
                self.assertEqual(refreshed.status_code, 200)
                self.assertNotEqual(refreshed.headers["etag"], etag)
                self.assertEqual(json.loads(refreshed.body)["nodes"][0]["label"], "Changed label")
                self.assertEqual(serialize.call_count, 2)
                self.assertTrue(seed_matches(self.target, self.seed))
        finally:
            engine.dispose()

    def test_ensure_imports_when_incomplete_and_skips_when_complete(self):
        export_seed(self.source, self.seed)
        self.assertIsNone(ensure_seeded_category_trees(self.source, self.seed))
        self.assertTrue(trees_complete(self.source))

        result = ensure_seeded_category_trees(self.target, self.seed)
        self.assertIsNotNone(result)
        self.assertTrue(trees_complete(self.target))
        self.assertIsNone(ensure_seeded_category_trees(self.target, self.seed))

    def test_seed_matches_compares_the_database_rows_with_the_seed(self):
        import sqlite3

        export_seed(self.source, self.seed)
        self.assertFalse(seed_matches(self.target, self.seed))
        import_seed(self.target, self.seed)
        self.assertTrue(seed_matches(self.target, self.seed))

        # A changed label, a changed tree timestamp or an extra node all count.
        for statement in (
            "UPDATE category_tree_nodes SET label='Renamed' WHERE marketplace='ebay'",
            "UPDATE category_trees SET updated_at='2026-02-02 00:00:00.000000' WHERE marketplace='etsy'",
            "INSERT INTO category_tree_nodes VALUES ('ebay', 'extra', '__root', 'Extra', 'Extra', 1, 0, 1)",
        ):
            import_seed(self.target, self.seed)
            con = sqlite3.connect(self.target)
            con.execute(statement)
            con.commit()
            con.close()
            self.assertFalse(seed_matches(self.target, self.seed), statement)

    def test_import_if_changed_leaves_a_matching_database_alone(self):
        export_seed(self.source, self.seed)
        argv = ["import", "--if-changed", "--db", str(self.target), "--seed", str(self.seed)]
        with patch("anteroom.category_trees_cli.import_seed", wraps=import_seed) as imported:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(category_trees_cli.main(argv), 0)
            self.assertEqual(imported.call_count, 1)
            with redirect_stdout(io.StringIO()) as out:
                self.assertEqual(category_trees_cli.main(argv), 0)
            self.assertEqual(imported.call_count, 1)
        self.assertTrue(json.loads(out.getvalue())["unchanged"])


if __name__ == "__main__":
    unittest.main()
