"""CLI: export or import marketplace category tree seeds."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from vendoo_studio.services.category_tree_seed import (
    default_db_path,
    default_seed_path,
    export_seed,
    import_seed,
)


def _postgres(name: str):
    import os

    from vendoo_studio.database import make_engine

    url = os.environ.get(name, "").strip()
    if not url.startswith(("postgresql://", "postgresql+psycopg://")):
        raise RuntimeError(f"{name} must hold a Postgres URL")
    return make_engine(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export or import Anteroom category_trees / category_tree_nodes only.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    export_parser = sub.add_parser("export", help="Write a seed from an Anteroom SQLite DB")
    export_parser.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Source SQLite path (default: packaged Anteroom DB)",
    )
    export_parser.add_argument(
        "--out",
        type=Path,
        default=default_seed_path(),
        help=f"Seed output path (default: {default_seed_path()})",
    )

    import_parser = sub.add_parser(
        "import",
        help="Replace category trees in an Anteroom SQLite DB from the seed",
    )
    target = import_parser.add_mutually_exclusive_group()
    target.add_argument(
        "--db",
        type=Path,
        default=default_db_path(),
        help=f"Target SQLite path (default: {default_db_path()})",
    )
    target.add_argument(
        "--database-url-env",
        metavar="NAME",
        help="Environment variable holding a Postgres URL with the migration login. "
             "The URL never appears in arguments or output.",
    )
    import_parser.add_argument(
        "--seed",
        type=Path,
        default=default_seed_path(),
        help=f"Seed path (default: {default_seed_path()})",
    )

    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            db = args.db or default_db_path()
            result = export_seed(db, args.out)
        elif args.database_url_env:
            result = import_seed(_postgres(args.database_url_env), args.seed)
            result["db"] = args.database_url_env
        else:
            result = import_seed(args.db, args.seed)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
