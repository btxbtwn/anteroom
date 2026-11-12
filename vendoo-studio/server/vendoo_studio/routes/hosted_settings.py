"""Shared settings handlers whose dependencies are seller scoped.

Desktop sign-in terminals, filesystem utilities and machine settings are not
mounted here. Their hosted equivalents require a registered worker.
"""
from fastapi import APIRouter, Depends, HTTPException
from vendoo_studio.repositories.queries import ConversationRepo
from vendoo_studio.services.hosted_database import get_seller_db

from vendoo_studio.routes import ai_keys, settings

router = APIRouter(prefix='/api/settings', tags=['settings'])

router.add_api_route('/provider', settings.get_provider, methods=['GET'])
router.add_api_route('/usage-limits', settings.usage_limits, methods=['GET'])
router.add_api_route('/provider', settings.set_provider, methods=['PUT'])
router.add_api_route('/provider/preferred', settings.set_preferred_provider, methods=['PUT'])
router.add_api_route('/provider/photos', settings.set_photo_provider, methods=['PUT'])
router.add_api_route('/provider/key', settings.delete_key, methods=['DELETE'])
router.add_api_route('/provider/test', settings.test_connection, methods=['POST'])
router.add_api_route('/provider/mimo/test', settings.test_mimo, methods=['POST'])

# The API-key router contains no local OAuth callbacks or browser launches.
keys_router = ai_keys.key_router


@router.get('/{provider}/models')
def subscription_models(provider: str):
    from vendoo_studio.routes.mac_inference import _pinned, relay
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
):
    router.add_api_route(path, handler, methods=methods)


def require_conversation(db, identity):
    if identity and ConversationRepo(db).get(identity) is None:
        raise HTTPException(404, 'Listing not found')


@router.get('/hidden-fields')
def hidden_fields(conversation_id: str | None = None, db=Depends(get_seller_db)):
    require_conversation(db, conversation_id)
    return settings.get_hidden_fields(conversation_id)


@router.put('/hidden-fields')
def hide_field(body: settings.HiddenFieldConfig, db=Depends(get_seller_db)):
    require_conversation(db, body.conversation_id)
    return settings.hide_hidden_field(body)


@router.delete('/hidden-fields')
def restore_field(body: settings.HiddenFieldConfig, db=Depends(get_seller_db)):
    require_conversation(db, body.conversation_id)
    return settings.restore_hidden_field(body)


@router.post('/hidden-fields/restore-all')
def restore_all(body: settings.RestoreAllHiddenFields, db=Depends(get_seller_db)):
    require_conversation(db, body.conversation_id)
    return settings.restore_all_hidden_fields(body)


@router.post('/hidden-fields/restore-matching')
def restore_matching(body: settings.RestoreMatchingHiddenFields, db=Depends(get_seller_db)):
    require_conversation(db, body.conversation_id)
    return settings.restore_matching_hidden_fields(body)
