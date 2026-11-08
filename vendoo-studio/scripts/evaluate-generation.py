#!/usr/bin/env python3
"""Score captured model outputs: evaluate-generation.py --outputs outputs.json.

Outputs are a JSON object mapping case ids to full listing objects. No model
calls, credentials, marketplace actions or private listings are needed.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

from vendoo_studio.services.generation_eval import evaluate_outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--outputs", type=Path)
    source.add_argument("--run", action="store_true", help="Generate live drafts with the configured provider")
    parser.add_argument("--provider", choices=("openai", "openrouter"),
                        help="Evaluate an API provider without changing the app's settings")
    parser.add_argument("--model", help="Exact model id for --provider")
    parser.add_argument("--api-key-file", type=Path, help="Private key file for --provider; never printed")
    parser.add_argument("--output-dir", type=Path, help="New directory for private live outputs and metrics")
    parser.add_argument("--cases", type=Path, default=Path(__file__).resolve().parents[1]
                        / "server/tests/fixtures/generation_cases.json")
    args = parser.parse_args()
    if args.provider and (not args.run or not args.model):
        parser.error("--provider requires --run and --model")
    if not args.provider and (args.model or args.api_key_file):
        parser.error("--model and --api-key-file require --provider")
    cases = json.loads(args.cases.read_text())
    if args.run:
        if args.output_dir is None:
            parser.error("--run requires --output-dir pointing to a new directory")
        from vendoo_studio.services.listing_provider import get_listing_provider
        from vendoo_studio.services.generation_eval_run import run_cases

        if args.provider:
            from vendoo_studio.providers.openai_api import OpenAIAPIProvider
            from vendoo_studio.providers.chat_completions import OpenRouterProvider
            from vendoo_studio.services.ai_key_catalog import KEY_PROVIDERS
            from vendoo_studio.services.provider_deadlines import with_deadlines

            key = (args.api_key_file.read_text().strip() if args.api_key_file else
                   os.environ.get("OPENAI_API_KEY" if args.provider == "openai" else "OPENROUTER_API_KEY", "").strip())
            if not key:
                parser.error("Provide --api-key-file or the provider's API-key environment variable")
            provider = with_deadlines(
                OpenAIAPIProvider(key, args.model, args.model) if args.provider == "openai" else
                OpenRouterProvider(KEY_PROVIDERS["openrouter"], key, args.model, args.model, "low")
            )
        else:
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
