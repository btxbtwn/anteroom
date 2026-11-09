"""The hosted Regenerate dialog uses the same price research as the Mac."""
from vendoo_studio.hosted import create_app
from vendoo_studio.routes.listings import preview_price_drop, stream_price_drop_comps


def test_hosted_registers_regenerate_research_routes():
    app = create_app('https://example.supabase.co')
    routes = {route.path: route for route in app.routes if hasattr(route, 'path')}
    for suffix, handler in [('preview', preview_price_drop), ('comps', stream_price_drop_comps)]:
        path = '/api/conversations/{conv_id}/price-drop/' + suffix
        assert path in routes
        assert routes[path].endpoint is handler
        assert routes[path].methods == {'POST'}
