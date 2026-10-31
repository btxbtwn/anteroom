"""Export and import the six marketplace category trees (no listings or secrets).

Each function takes the database as an Engine, or as a SQLite file path for
the CLI, which works on the packaged Mac app's file.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime, UTC
from pathlib import Path
from typing import cast

from sqlalchemy import Table, Engine, create_engine, delete, func, inspect, insert, select

from vendoo_studio.services.category_tree import MARKETPLACES

SEED_VERSION = 1
SEED_NAME = "category-trees-seed.json.gz"
PACKAGED_DB = Path.home() / "Library" / "Application Support" / "Anteroom" / "vendoo_studio.db"
INSERT_BATCH = 5000


def default_seed_path() -> Path:
    from vendoo_studio.config import resource_root

    bundled = resource_root() / "data" / SEED_NAME
    if bundled.is_file():
        return bundled
    return Path(__file__).resolve().parents[3] / "data" / SEED_NAME


def default_db_path() -> Path:
    """Packaged Mac app DB path (CLI default)."""
    return PACKAGED_DB


def _tables() -> tuple[Table, Table]:
    from vendoo_studio.models.catalog import CategoryTree, CategoryTreeNode

    return cast(Table, CategoryTree.__table__), cast(Table, CategoryTreeNode.__table__)


def _engine(target: Engine | Path | None) -> Engine:
    if target is None:
        from vendoo_studio.database import engine

        return engine
    if isinstance(target, Engine):
        return target
    return create_engine(f"sqlite:///{target}")


def _has_tables(engine: Engine, *names: str) -> bool:
    existing = set(inspect(engine).get_table_names())
    return all(name in existing for name in names)


def trees_complete(target: Engine | Path | None = None) -> bool:
    if isinstance(target, Path) and not target.is_file():
        return False
    engine = _engine(target)
    if not _has_tables(engine, "category_trees"):
        return False
    trees, _nodes = _tables()
    with engine.connect() as connection:
        rows = dict(connection.execute(
            select(trees.c.marketplace, trees.c.status).where(trees.c.marketplace.in_(MARKETPLACES))
        ).all())
    return all(rows.get(marketplace) == "complete" for marketplace in MARKETPLACES)


def ensure_seeded_category_trees(
    target: Engine | Path | None = None,
    seed_path: Path | None = None,
) -> dict | None:
    """Import the bundled seed when any of the six trees is missing or incomplete."""
    if trees_complete(target):
        return None
    seed = seed_path or default_seed_path()
    if not seed.is_file():
        return None
    return import_seed(target, seed)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _moment(value) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return _utcnow()


def _read_seed(path: Path) -> dict:
    raw = path.read_bytes()
    if path.suffix == ".gz" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    payload = json.loads(raw.decode("utf-8"))
    if payload.get("version") != SEED_VERSION:
        raise ValueError(f"Unsupported seed version: {payload.get('version')!r}")
    trees = payload.get("category_trees")
    nodes = payload.get("category_tree_nodes")
    if not isinstance(trees, list) or not isinstance(nodes, list):
        raise ValueError("Seed must include category_trees and category_tree_nodes lists")
    return payload


def export_seed(source: Engine | Path, seed_path: Path) -> dict:
    """Write only category_trees + category_tree_nodes for the six marketplaces."""
    if isinstance(source, Path) and not source.is_file():
        raise FileNotFoundError(f"Database not found: {source}")
    engine = _engine(source)
    if not _has_tables(engine, "category_trees", "category_tree_nodes"):
        raise RuntimeError(f"{engine.url} has no category tree tables")
    trees_table, nodes_table = _tables()
    with engine.connect() as connection:
        trees = []
        for row in connection.execute(
            select(
                trees_table.c.marketplace, trees_table.c.roots_loaded, trees_table.c.status,
                trees_table.c.error, trees_table.c.source, trees_table.c.updated_at,
            ).where(trees_table.c.marketplace.in_(MARKETPLACES)).order_by(trees_table.c.marketplace)
        ).mappings():
            item = dict(row)
            item["roots_loaded"] = bool(item["roots_loaded"])
            stamp = item["updated_at"]
            item["updated_at"] = stamp.isoformat(sep=" ") if isinstance(stamp, datetime) else stamp
            trees.append(item)
        found = {tree["marketplace"] for tree in trees}
        missing = [mp for mp in MARKETPLACES if mp not in found]
        if missing:
            raise RuntimeError("Missing marketplaces in category_trees: " + ", ".join(missing))
        incomplete = [tree["marketplace"] for tree in trees if tree["status"] != "complete"]
        if incomplete:
            raise RuntimeError("Incomplete marketplaces: " + ", ".join(incomplete))
        nodes = []
        for row in connection.execute(
            select(
                nodes_table.c.marketplace, nodes_table.c.category_id, nodes_table.c.parent_id,
                nodes_table.c.path, nodes_table.c.label, nodes_table.c.is_leaf,
                nodes_table.c.has_children, nodes_table.c.children_loaded,
            ).where(nodes_table.c.marketplace.in_(MARKETPLACES))
            .order_by(nodes_table.c.marketplace, nodes_table.c.path)
        ).mappings():
            item = dict(row)
            for key in ("is_leaf", "has_children", "children_loaded"):
                item[key] = bool(item[key])
            nodes.append(item)
    counts = {mp: 0 for mp in MARKETPLACES}
    for node in nodes:
        counts[node["marketplace"]] += 1
    empty = [mp for mp, count in counts.items() if count == 0]
    if empty:
        raise RuntimeError("Marketplaces with zero nodes: " + ", ".join(empty))
    payload = {
        "version": SEED_VERSION,
        "marketplaces": list(MARKETPLACES),
        "exported_at": _utcnow().isoformat(sep=" "),
        "node_counts": counts,
        "category_trees": trees,
        "category_tree_nodes": nodes,
    }

    seed_path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if seed_path.suffix == ".gz" or str(seed_path).endswith(".json.gz"):
        seed_path.write_bytes(gzip.compress(body, compresslevel=9))
    else:
        seed_path.write_bytes(body)
    return {"seed": str(seed_path), "node_counts": counts, "nodes": len(nodes)}


def import_seed(target: Engine | Path | None, seed_path: Path | None = None) -> dict:
    """Replace category_trees / category_tree_nodes and mark all six marketplaces complete."""
    seed_path = seed_path or default_seed_path()
    if not seed_path.is_file():
        raise FileNotFoundError(f"Seed not found: {seed_path}")
    payload = _read_seed(seed_path)
    trees = payload["category_trees"]
    nodes = payload["category_tree_nodes"]
    by_marketplace = {tree["marketplace"]: tree for tree in trees}
    missing = [mp for mp in MARKETPLACES if mp not in by_marketplace]
    if missing:
        raise RuntimeError("Seed missing marketplaces: " + ", ".join(missing))

    if isinstance(target, Path):
        target.parent.mkdir(parents=True, exist_ok=True)
    engine = _engine(target)
    trees_table, nodes_table = _tables()
    # The app's database already has these from its migrations; a bare file
    # the CLI points at may not.
    trees_table.metadata.create_all(engine, tables=[trees_table, nodes_table])
    now = _utcnow()
    rows = [
        {
            "marketplace": node["marketplace"],
            "category_id": node["category_id"],
            "parent_id": node["parent_id"],
            "path": node["path"],
            "label": node["label"],
            "is_leaf": bool(node["is_leaf"]),
            "has_children": bool(node["has_children"]),
            "children_loaded": bool(node.get("children_loaded", True)),
        }
        for node in nodes
        if node["marketplace"] in MARKETPLACES
    ]
    with engine.begin() as connection:
        connection.execute(delete(nodes_table))
        connection.execute(delete(trees_table))
        connection.execute(insert(trees_table), [
            {
                "marketplace": marketplace,
                "roots_loaded": bool(by_marketplace[marketplace].get("roots_loaded", True)),
                "status": "complete",
                "error": None,
                "source": by_marketplace[marketplace].get("source"),
                "updated_at": _moment(by_marketplace[marketplace].get("updated_at") or now),
            }
            for marketplace in MARKETPLACES
        ])
        for start in range(0, len(rows), INSERT_BATCH):
            connection.execute(insert(nodes_table), rows[start:start + INSERT_BATCH])
        counts = dict(connection.execute(
            select(nodes_table.c.marketplace, func.count()).group_by(nodes_table.c.marketplace)
        ).all())
        statuses = dict(connection.execute(select(trees_table.c.marketplace, trees_table.c.status)).all())
    counts = {mp: counts.get(mp, 0) for mp in MARKETPLACES}
    statuses = {mp: statuses.get(mp) for mp in MARKETPLACES}

    incomplete = [mp for mp, status in statuses.items() if status != "complete"]
    if incomplete:
        raise RuntimeError("Import left incomplete marketplaces: " + ", ".join(incomplete))
    return {"db": str(engine.url), "seed": str(seed_path), "node_counts": counts, "statuses": statuses}
