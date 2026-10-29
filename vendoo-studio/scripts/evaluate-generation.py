#!/usr/bin/env python3
"""Score captured model outputs: evaluate-generation.py --outputs outputs.json.

Outputs are a JSON object mapping case ids to full listing objects. No model
calls, credentials, marketplace actions or private listings are needed.
"""
import argparse
import asyncio
import json
from pathlib import Path

from vendoo_studio.services.generation_eval import evaluate_outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--outputs", type=Path)
    source.add_argument("--run", action="store_true", help="Generate live drafts with the configured provider")
    parser.add_argument("--output-dir", type=Path, help="New directory for private live outputs and metrics")
    parser.add_argument("--cases", type=Path, default=Path(__file__).resolve().parents[1]
                        / "server/tests/fixtures/generation_cases.json")
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text())
    if args.run:
        if args.output_dir is None:
            parser.error("--run requires --output-dir pointing to a new directory")
        from vendoo_studio.services.listing_provider import get_listing_provider
        from vendoo_studio.services.generation_eval_run import run_cases

        provider = get_listing_provider()
        if provider is None:
            parser.error("Connect a listing provider in Anteroom before running live evaluations")
        result = asyncio.run(run_cases(provider, cases, args.output_dir))
    else:
        result = evaluate_outputs(cases, json.loads(args.outputs.read_text()))
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
