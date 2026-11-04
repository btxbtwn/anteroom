import copy

from vendoo_studio.services.listing_editor import prepared_listing
from vendoo_studio.services.seller_overrides import MARKETPLACES, marketplace_text, price_for


def test_general_edit_reaches_native_forms_without_regeneration():
    previous = {'title': 'Old title', 'description': 'Old description', 'brand': 'Hanes', 'price': 25,
                '_native_forms': {market: {} for market in MARKETPLACES},
                **{f'{market}_specifics': {'title': 'Old title', 'description': 'Old description', 'brand': 'Hanes', 'price': 25}
                   for market in MARKETPLACES}}
    submitted = copy.deepcopy(previous)
    submitted.update(title='Corrected title', description='Corrected description', brand='Nike', price=30)
    saved = prepared_listing(previous, submitted)
    for market in MARKETPLACES:
        assert price_for(saved, market) == 30
        for field in ('title', 'description', 'brand'):
            assert marketplace_text(saved, market, field) == submitted[field], (market, field)


def test_deliberate_native_title_edit_remains_independent():
    previous = {'title': 'General title', '_native_forms': {'poshmark': {}},
                'poshmark_specifics': {'title': 'Old Poshmark title'}}
    submitted = copy.deepcopy(previous)
    submitted['poshmark_specifics']['title'] = 'General title'
    saved = prepared_listing(previous, submitted)
    assert saved['poshmark_specifics']['title'] == 'General title'
    assert 'poshmark_specifics.title' in saved['seller_fields']
