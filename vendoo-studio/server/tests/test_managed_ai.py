"""Company-funded inference must stay explicit, server-only and seller scoped."""
from dataclasses import replace

from test_service_context import runtime
from vendoo_studio.services import keychain, listing_provider, service_context, user_settings
from vendoo_studio.services.hosted_provider import provider_for


def test_new_hosted_seller_gets_luna_without_a_personal_key(monkeypatch):
    monkeypatch.setenv("ANTEROOM_AI_API_KEY", "company-test-key")
    seller = replace(runtime("alice"), provider_factory=provider_for)
    with service_context.use(seller):
        assert user_settings.get_listing_provider_order() == ("anteroom", "none")
        provider = listing_provider.get_listing_provider()
        assert provider.name == "anteroom"
        assert provider.listing_model == provider.vision_model == "gpt-6-luna"
        assert listing_provider.provider_is_configured()
        assert not hasattr(provider, "api_key")
        assert seller.secrets.value == {}


def test_existing_personal_provider_is_preserved_and_never_charged_to_company(monkeypatch):
    monkeypatch.setenv("ANTEROOM_AI_API_KEY", "company-test-key")
    seller = replace(runtime("alice"), provider_factory=provider_for)
    with service_context.use(seller):
        user_settings.set_listing_provider_order("openai", "none")
        assert listing_provider.get_listing_provider() is None
        keychain.set_ai_key("openai", "personal-test-key")
        assert listing_provider.get_listing_provider().name == "openai"


def test_managed_ai_never_falls_back_to_personal_billing(monkeypatch):
    monkeypatch.delenv("ANTEROOM_AI_API_KEY", raising=False)
    seller = replace(runtime("alice"), provider_factory=provider_for)
    with service_context.use(seller):
        keychain.set_ai_key("openai", "personal-test-key")
        user_settings.set_listing_provider_order("anteroom")
        assert user_settings.get_listing_provider_order() == ("anteroom", "none")
        assert listing_provider.get_listing_provider() is None


def test_company_key_is_not_available_to_desktop(monkeypatch):
    monkeypatch.setenv("ANTEROOM_AI_API_KEY", "company-test-key")
    assert listing_provider._ready_provider("anteroom") is None


def test_existing_automatic_personal_provider_is_preserved(monkeypatch):
    monkeypatch.setenv("ANTEROOM_AI_API_KEY", "company-test-key")
    seller = replace(runtime("alice"), provider_factory=provider_for)
    with service_context.use(seller):
        keychain.set_ai_key("openai", "personal-test-key")
        assert listing_provider.get_listing_provider().name == "openai"


def test_managed_request_uses_luna_and_disables_response_storage(monkeypatch):
    from vendoo_studio.services import managed_ai
    from vendoo_studio.services.provider_deadlines import unwrap

    monkeypatch.setenv("ANTEROOM_AI_API_KEY", "company-test-key")
    with service_context.use(runtime("alice")):
        wrapped = unwrap(managed_ai.provider())
        payload = wrapped._provider._payload([{"role": "user", "content": "Describe this item"}], managed_ai.MODEL, True)
        assert payload["model"] == "gpt-6-luna"
        assert payload["store"] is False


def test_managed_cost_is_a_token_only_estimate():
    from vendoo_studio.services import model_usage

    with model_usage.capture() as calls:
        with model_usage.observe("anteroom", "gpt-6-luna") as call:
            call.usage({"input_tokens": 20_000, "output_tokens": 3_000,
                        "input_tokens_details": {"cached_tokens": 10_000}})
    assert calls[0]["cost_usd"] == 0.0026
    assert calls[0]["cost_source"] == "token_rate_estimate"


def test_managed_status_requires_auth_and_never_returns_company_key(monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from vendoo_studio.hosted import create_app
    from vendoo_studio.services import hosted_context

    monkeypatch.setenv("ANTEROOM_AI_API_KEY", "company-test-key")
    contexts = {owner: replace(runtime(owner), provider_factory=provider_for) for owner in ("alice", "bob")}
    monkeypatch.setattr(hosted_context, "for_seller", lambda owner, _token: contexts[owner])
    with TestClient(create_app("https://project.supabase.co")) as client:
        client.app.state.session_verifier = SimpleNamespace(verify=lambda token: SimpleNamespace(user_id=token))
        assert client.get("/api/settings/provider").status_code == 401
        for owner in contexts:
            response = client.get("/api/settings/provider", headers={"Authorization": f"Bearer {owner}"})
            assert response.status_code == 200
            assert response.json()["provider"] == "anteroom"
            assert response.json()["managed_ai_available"] is True
            assert "company-test-key" not in response.text
            assert contexts[owner].secrets.value == {}
