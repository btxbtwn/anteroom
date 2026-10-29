#!/usr/bin/env python3
"""Score captured model outputs: evaluate-generation.py --outputs outputs.json.

Outputs are a JSON object mapping case ids to full listing objects. No model
calls, credentials, marketplace actions or private listings are needed.
"""
import argparse
import json
from pathlib import Path

from vendoo_studio.services.generation_eval import evaluate_outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outputs", type=Path, required=True)
    parser.add_argument("--cases", type=Path, default=Path(__file__).resolve().parents[1]
                        / "server/tests/fixtures/generation_cases.json")
    args = parser.parse_args()
    result = evaluate_outputs(json.loads(args.cases.read_text()), json.loads(args.outputs.read_text()))
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
