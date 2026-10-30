"""Shared settings handlers whose dependencies are seller scoped.

Desktop sign-in terminals, filesystem utilities and machine settings are not
mounted here. Their hosted equivalents require a registered worker.
"""
from fastapi import APIRouter

from vendoo_studio.routes import ai_keys, settings

router = APIRouter(prefix='/api/settings', tags=['settings'])

router.add_api_route('/provider', settings.get_provider, methods=['GET'])
router.add_api_route('/provider', settings.set_provider, methods=['PUT'])
router.add_api_route('/provider/preferred', settings.set_preferred_provider, methods=['PUT'])
router.add_api_route('/provider/photos', settings.set_photo_provider, methods=['PUT'])
router.add_api_route('/provider/key', settings.delete_key, methods=['DELETE'])
router.add_api_route('/provider/test', settings.test_connection, methods=['POST'])
router.add_api_route('/provider/mimo/test', settings.test_mimo, methods=['POST'])

# The API-key router contains no local OAuth callbacks or browser launches.
keys_router = ai_keys.key_router
