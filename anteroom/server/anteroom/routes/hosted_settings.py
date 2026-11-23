"""Shared settings handlers whose dependencies are seller scoped.

Desktop sign-in terminals, filesystem utilities and machine settings are not
mounted here. Their hosted equivalents require a registered worker.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from anteroom.repositories.queries import ConversationRepo
from anteroom.services.hosted_database import get_seller_db

from anteroom.routes import ai_keys, settings

router = APIRouter(prefix='/api/settings', tags=['settings'])

def require_seller_providers():
    from anteroom.services.hosted_provider import managed_ai_enabled
    if managed_ai_enabled():
        raise HTTPException(403, 'AI is managed automatically for hosted accounts.')


def require_seller_photoroom():
    from anteroom.services.photoroom import managed_photoroom_enabled
    if managed_photoroom_enabled():
        raise HTTPException(403, 'AI Studio is managed automatically for hosted accounts.')


_seller_provider_route = [Depends(require_seller_providers)]
_seller_photoroom_route = [Depends(require_seller_photoroom)]

router.add_api_route('/provider', settings.get_provider, methods=['GET'])
router.add_api_route('/usage-limits', settings.usage_limits, methods=['GET'])
router.add_api_route('/provider', settings.set_provider, methods=['PUT'], dependencies=_seller_provider_route)
router.add_api_route('/provider/preferred', settings.set_preferred_provider, methods=['PUT'], dependencies=_seller_provider_route)
router.add_api_route('/provider/photos', settings.set_photo_provider, methods=['PUT'], dependencies=_seller_provider_route)
router.add_api_route('/provider/key', settings.delete_key, methods=['DELETE'], dependencies=_seller_provider_route)
router.add_api_route('/provider/test', settings.test_connection, methods=['POST'], dependencies=_seller_provider_route)
router.add_api_route('/provider/mimo/test', settings.test_mimo, methods=['POST'], dependencies=_seller_provider_route)

# The API-key router contains no local OAuth callbacks or browser launches.
keys_router = ai_keys.key_router


@router.get('/{provider}/models', dependencies=_seller_provider_route)
def subscription_models(provider: str):
    from anteroom.routes.mac_inference import _pinned, relay
    if provider not in ('chatgpt', 'claude'):
        raise HTTPException(404, 'Not found')
    if pinned := _pinned('mac', claim_route=False):
        return pinned
    connection = relay()
    models = connection.models.get(provider, {}) if connection.ready(provider) else {}
    vision, listing = models.get('vision_model', ''), models.get('listing_model', '')
    slugs = [listing] if listing else []
    return {
        'models': [{'value': slug, 'label': slug, 'efforts': []} for slug in slugs] if provider == 'claude' else slugs,
        'vision_model': vision, 'listing_model': listing,
        'reasoning_efforts': [], 'efforts': [],
        'error': None if models else 'Connect this subscription in Anteroom on your computer and keep it awake and online.',
    }


# These handlers use only the execution's owned settings store. They do not
# start browser sessions, write marketplace listings, or use machine settings.
for path, handler, methods in (
    ('/marketplaces', settings.get_marketplaces, ['GET']),
    ('/seller-region', settings.get_seller_region, ['GET']),
    ('/seller-region', settings.put_seller_region, ['PUT']),
    ('/marketplaces', settings.set_marketplaces, ['PUT']),
    ('/package-dimensions', settings.get_package_dimensions, ['GET']),
    ('/package-dimensions', settings.set_package_dimensions, ['PUT']),
    ('/ships-from-zip', settings.get_ships_from_zip, ['GET']),
    ('/ships-from-zip', settings.set_ships_from_zip, ['PUT']),
    ('/listing-defaults', settings.get_listing_defaults, ['GET']),
    ('/listing-defaults', settings.set_listing_default, ['PUT']),
    ('/listing-defaults', settings.clear_listing_default, ['DELETE']),
    ('/ebay-inventory-location', settings.get_ebay_inventory_location, ['GET']),
    ('/formulas', settings.get_listing_formulas, ['GET']),
    ('/formulas', settings.put_listing_formulas, ['PUT']),
    ('/sale-detection', settings.get_sale_detection_route, ['GET']),
    ('/sale-detection', settings.put_sale_detection, ['PUT']),
    # AI Studio's Photoroom key (seller Vault when unmanaged).
    ('/photoroom', settings.get_photoroom, ['GET']),
    ('/pricing-rules', settings.get_pricing_rules, ['GET']),
    ('/pricing-rules', settings.put_pricing_rules, ['PUT']),
    ('/pricing-rules/apply', settings.apply_pricing_rules, ['POST']),
    ('/shipping-presets', settings.get_shipping_presets, ['GET']),
    ('/shipping-presets', settings.create_shipping_preset, ['POST']),
    ('/shipping-presets/{preset_id}', settings.update_shipping_preset, ['PUT']),
    ('/shipping-presets/{preset_id}', settings.delete_shipping_preset, ['DELETE']),
    ('/shipping-presets/{preset_id}/apply', settings.apply_shipping_preset, ['POST']),
    ('/listing-warnings', settings.get_listing_warnings_route, ['GET']),
    ('/listing-warnings', settings.put_listing_warnings, ['PUT']),
):
    router.add_api_route(path, handler, methods=methods)

router.add_api_route('/photoroom', settings.set_photoroom, methods=['PUT'], dependencies=_seller_photoroom_route)
router.add_api_route('/photoroom', settings.delete_photoroom, methods=['DELETE'], dependencies=_seller_photoroom_route)


def require_conversation(db, identity):
    if identity and ConversationRepo(db).get(identity) is None:
        raise HTTPException(404, 'Listing not found')


@router.get('/hidden-fields')
def hidden_fields(conversation_id: str | None = None, db=Depends(get_seller_db, scope="function")):
    require_conversation(db, conversation_id)
    return settings.get_hidden_fields(conversation_id)


@router.put('/hidden-fields')
def hide_field(body: settings.HiddenFieldConfig, db=Depends(get_seller_db, scope="function")):
    require_conversation(db, body.conversation_id)
    return settings.hide_hidden_field(body)


@router.delete('/hidden-fields')
def restore_field(body: settings.HiddenFieldConfig, db=Depends(get_seller_db, scope="function")):
    require_conversation(db, body.conversation_id)
    return settings.restore_hidden_field(body)


@router.post('/hidden-fields/restore-all')
def restore_all(body: settings.RestoreAllHiddenFields, db=Depends(get_seller_db, scope="function")):
    require_conversation(db, body.conversation_id)
    return settings.restore_all_hidden_fields(body)


@router.post('/hidden-fields/restore-matching')
def restore_matching(body: settings.RestoreMatchingHiddenFields, db=Depends(get_seller_db, scope="function")):
    require_conversation(db, body.conversation_id)
    return settings.restore_matching_hidden_fields(body)


@router.get('/agent', response_model=settings.AgentConnectionStatus)
def hosted_agent_connection(request: Request):
    from anteroom.services.agent_connection import describe_hosted

    forwarded_proto = (request.headers.get('x-forwarded-proto') or request.url.scheme or 'https').split(',')[0].strip()
    host = (request.headers.get('x-forwarded-host') or request.headers.get('host') or 'myanteroom.com').split(',')[0].strip()
    return describe_hosted(f"{forwarded_proto}://{host}")


router.add_api_route('/mcp-tokens', settings.list_mcp_tokens, methods=['GET'])
router.add_api_route('/mcp-tokens', settings.create_mcp_token, methods=['POST'], status_code=201)
router.add_api_route('/mcp-tokens/{token_id}', settings.revoke_mcp_token, methods=['DELETE'])
router.add_api_route('/mcp-tokens/{token_id}', settings.update_mcp_token_permissions, methods=['PATCH'])


# Apps connected by signing in (Claude, ChatGPT, Gemini). Hosted only: Supabase
# runs their sign-in. A connected app's own token never reaches these routes.
class McpConnectionApproval(BaseModel):
    client_name: str | None = Field(default=None, max_length=200)
    permissions: dict[str, str] | None = None
    # Turns a disconnected app back on; sent once Supabase accepted the approval.
    enable: bool = False


@router.get('/mcp-connections')
def list_mcp_connections(db=Depends(get_seller_db, scope="function")):
    from anteroom.services import mcp_oauth

    return {"connections": mcp_oauth.list_connections(db)}


@router.put('/mcp-connections/{client_id}')
def approve_mcp_connection(client_id: str, body: McpConnectionApproval,
                           db=Depends(get_seller_db, scope="function")):
    from anteroom.services import mcp_oauth

    try:
        return mcp_oauth.approve(db, client_id, client_name=body.client_name, permissions=body.permissions,
                                 enable=body.enable)
    except mcp_oauth.McpConnectionError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch('/mcp-connections/{client_id}')
def update_mcp_connection(client_id: str, body: settings.McpTokenPermissionsConfig,
                          db=Depends(get_seller_db, scope="function")):
    from anteroom.services import mcp_oauth

    try:
        updated = mcp_oauth.update_permissions(db, client_id, body.permissions)
    except mcp_oauth.McpConnectionError as exc:
        raise HTTPException(422, str(exc)) from exc
    if updated is None:
        raise HTTPException(404, "Connected app not found")
    return updated


@router.delete('/mcp-connections/{client_id}')
async def revoke_mcp_connection(client_id: str, request: Request):
    from anteroom.services import mcp_oauth
    from anteroom.services.hosted_database import in_seller_session

    # Off in Anteroom first: the app's next MCP call is refused whatever Supabase says.
    try:
        revoked = await in_seller_session(request, mcp_oauth.revoke, client_id)
    except mcp_oauth.McpConnectionError as exc:
        raise HTTPException(422, str(exc)) from exc
    if revoked is None:
        raise HTTPException(404, "Connected app not found")
    # Then end the app's sign-in with Supabase, so its saved sign-in stops working too.
    signed_out = await mcp_oauth.end_supabase_grant(request.headers.get("authorization"), revoked["client_id"])
    if signed_out:
        revoked = await in_seller_session(request, mcp_oauth.mark_signed_out, client_id) or revoked
    # Not confirmed: Settings offers to finish disconnecting, which repeats this.
    return {**revoked, "signed_out": signed_out}
