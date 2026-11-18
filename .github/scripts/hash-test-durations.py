"""Publish duration observations keyed by SHA-256 of the full pytest nodeid."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = json.loads(args.input.read_text())
        if not isinstance(payload, dict) or not payload:
            raise ValueError
        safe = {}
        for nodeid, duration in payload.items():
            if not isinstance(nodeid, str) or not nodeid:
                raise ValueError
            if type(duration) not in (int, float) or not math.isfinite(duration) or duration < 0:
                raise ValueError
            digest = hashlib.sha256(nodeid.encode('utf-8')).hexdigest()
            if digest in safe:
                raise ValueError
            safe[digest] = duration
        # Never overwrite an existing observation; no output is created on invalid input.
        with args.output.open('x') as target:
            json.dump(safe, target, sort_keys=True, allow_nan=False)
            target.write('\n')
    except (OSError, ValueError, TypeError, OverflowError):
        # Payloads and decoding exceptions may contain private nodeids. Do not echo them.
        parser.exit(1, 'Invalid duration observations or unavailable output path.\n')


if __name__ == '__main__':
    main()
