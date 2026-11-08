"""Anteroom-funded inference. The company credential exists only on the server."""
from __future__ import annotations

import os

MODEL = "gpt-6-luna"


def available() -> bool:
    from vendoo_studio.services.service_context import current

    return current() is not None and bool(os.environ.get("ANTEROOM_AI_API_KEY", "").strip())


def provider():
    from vendoo_studio.providers.openai_api import OpenAIAPIProvider
    from vendoo_studio.services.hosted_provider import HostedProvider

    if not available():
        return None
    instance = OpenAIAPIProvider(os.environ["ANTEROOM_AI_API_KEY"], MODEL, MODEL)
    instance.name = "anteroom"
    return HostedProvider(instance)
