"""CPU-only catalog builder. Its input is an already-materialized seller snapshot."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from vendoo_studio.services import catalog_index
from vendoo_studio.services.hosted_context import for_seller
from vendoo_studio.services.service_context import use


def main():
    owner, directory = sys.argv[1:]
    # Index construction yields CPU to the API and lease-renewal workers.
    os.nice(10)
    raw = json.load(sys.stdin)
    snapshot = catalog_index.CatalogSnapshot(
        raw['fingerprint'], raw['category_rows'],
        [catalog_index._SchemaRow(**row) for row in raw['schema_rows']],
    )
    output = Path(directory)
    with use(for_seller(owner)):
        result = catalog_index._build_unlocked(snapshot)
        index = catalog_index._state().index
        if index is not None:
            index.save(output / 'index')
        result['indexed'] = index is not None
        (output / 'result.json').write_text(json.dumps(result))


if __name__ == '__main__':
    main()
