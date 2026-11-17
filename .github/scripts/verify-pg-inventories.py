"""Validate both exact-current-run shard inventories and duration observations."""
import argparse
import hashlib
import json
from pathlib import Path

from pg_inventory_contract import validate_profile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--commit', required=True)
    parser.add_argument('--lock', type=Path, required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--attempt', required=True)
    args = parser.parse_args()
    try:
        inventories, observations = [], []
        for group in (1, 2):
            suffix = f'{args.run_id}-{args.attempt}-{group}.json'
            directory = args.directory / str(group)
            inventory = directory / f'pg-inventory-{suffix}'
            durations = directory / f'pg-duration-hashes-{suffix}'
            if set(directory.iterdir()) != {inventory, durations}:
                raise ValueError('Unexpected artifact files')
            inventories.append(json.loads(inventory.read_text()))
            observations.append(json.loads(durations.read_text()))
        count = validate_profile(inventories, observations, {
            'commit': args.commit, 'lock_sha256': hashlib.sha256(args.lock.read_bytes()).hexdigest(),
            'run_id': args.run_id, 'attempt': args.attempt})
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        parser.exit(1, 'Postgres collection/observation verification failed.\n')
    print(f'Verified {count} collection hashes: exact disjoint union, matching provenance and observations.')


if __name__ == '__main__':
    main()
