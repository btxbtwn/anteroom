"""Strict hash-only inventory protocol; shared by pytest and the aggregate job."""
import math
import re

PROVENANCE = {'commit', 'lock_sha256', 'run_id', 'attempt', 'python', 'pytest', 'pytest-xdist', 'pytest-split', 'worker_count'}


def validate_record(record):
    if not isinstance(record, dict) or set(record) != {'provenance', 'full', 'selected'}:
        raise ValueError('Invalid inventory schema')
    provenance = record['provenance']
    if not isinstance(provenance, dict) or set(provenance) != PROVENANCE:
        raise ValueError('Invalid provenance schema')
    for key, value in provenance.items():
        pattern = '[0-9a-f]{40}' if key == 'commit' else '[0-9a-f]{64}' if key == 'lock_sha256' else '[0-9]+' if key in ('run_id', 'attempt', 'worker_count') else '[0-9]+(?:[.][0-9]+)+(?:[a-z0-9.+-]*)'
        if not isinstance(value, str) or not re.fullmatch(pattern, value):
            raise ValueError('Invalid provenance value')
    if int(provenance['worker_count']) < 1:
        raise ValueError('Missing workers')
    for field in ('full', 'selected'):
        values = record[field]
        if not isinstance(values, list) or not values or any(not isinstance(v, str) or not re.fullmatch('[0-9a-f]{64}', v) for v in values):
            raise ValueError('Invalid collection hashes')
        if len(values) != len(set(values)):
            raise ValueError('Duplicate collection hashes')
    positions = {key: i for i, key in enumerate(record['full'])}
    if not set(record['selected']) <= positions.keys():
        raise ValueError('Selected unknown collection hash')
    indices = [positions[key] for key in record['selected']]
    if indices != sorted(indices):
        raise ValueError('Changed collection order')


def validate_workers(records):
    if not records:
        raise ValueError('Missing worker inventory')
    for record in records:
        validate_record(record)
    if any(record != records[0] for record in records[1:]):
        raise ValueError('Worker inventories disagree')
    return records[0]


def validate_profile(inventories, observations, expected):
    if len(inventories) != 2 or len(observations) != 2:
        raise ValueError('Both shard inventories and observations are required')
    for inventory in inventories:
        validate_record(inventory)
        if any(inventory['provenance'].get(key) != value for key, value in expected.items()):
            raise ValueError('Unexpected provenance')
    first, second = inventories
    if first['provenance'] != second['provenance'] or first['full'] != second['full']:
        raise ValueError('Shard provenance or full collection differs')
    left, right = set(first['selected']), set(second['selected'])
    if left & right or left | right != set(first['full']):
        raise ValueError('Shard union is incomplete or overlaps')
    for inventory, payload in zip(inventories, observations, strict=True):
        if not isinstance(payload, dict) or set(payload) != set(inventory['selected']):
            raise ValueError('Missing or unknown duration observations')
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0 for value in payload.values()):
            raise ValueError('Invalid observed duration')
    return len(first['full'])
