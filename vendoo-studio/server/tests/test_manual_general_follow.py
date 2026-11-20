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


def test_depop_size_choice_survives_save_when_it_matches_general_and_a_blank_id_remains():
    for category in ('womenswear_tshirts', 'womenswear_jeans'):
        for captured_form in (False, True):
            previous = {'title': 'Example garment', 'description': 'Example', 'size': '0',
                        'direct_category_ids': {'depop': category},
                        'depop_specifics': {'size': '', 'nativeSizeId': ''},
                        'form': {'aspects': {f'{category}_size': '81', 'other_category_size': '99'}},
                        'seller_fields': ['depop_specifics.size', 'depop_specifics.nativeSizeId',
                                          f'form.aspects.{category}_size']}
            if captured_form:
                previous['_native_forms'] = {'depop': {}}
            submitted = copy.deepcopy(previous)
            submitted['depop_specifics']['size'] = '0'
            saved = prepared_listing(previous, submitted)
            specifics = saved['depop_specifics']
            assert marketplace_text(saved, 'depop', 'size') == '0'
            assert f'{category}_size' not in saved['form']['aspects']
            assert saved['form']['aspects']['other_category_size'] == '99'
            assert f'form.aspects.{category}_size' not in saved.get('seller_fields', [])
            # Posting checks a remaining native ID before falling back to General.
            # It is safe to retain it only while a label remains authoritative.
            assert 'size' in specifics or 'nativeSizeId' not in specifics
            if 'size' not in specifics:
                assert 'depop_specifics.nativeSizeId' not in saved.get('seller_fields', [])


def test_depop_explicit_size_clear_keeps_its_blank_and_lock():
    previous = {'title': 'Example garment', 'size': '0',
                'direct_category_ids': {'depop': 'womenswear_tshirts'},
                'depop_specifics': {'size': '0', 'nativeSizeId': ''}}
    submitted = copy.deepcopy(previous)
    submitted['depop_specifics']['size'] = ''
    saved = prepared_listing(previous, submitted)
    assert saved['depop_specifics']['size'] == ''
    assert 'depop_specifics.size' in saved['seller_fields']


def test_depop_non_general_size_override_keeps_its_coded_aspect():
    previous = {'title': 'Example garment', 'size': '0',
                'direct_category_ids': {'depop': 'womenswear_tshirts'},
                'depop_specifics': {'size': '', 'nativeSizeId': ''},
                'form': {'aspects': {'womenswear_tshirts_size': '81'}}}
    submitted = copy.deepcopy(previous)
    submitted['depop_specifics']['size'] = '00'
    saved = prepared_listing(previous, submitted)
    assert saved['depop_specifics']['size'] == '00'
    assert 'depop_specifics.size' in saved['seller_fields']
    assert saved['form']['aspects']['womenswear_tshirts_size'] == '81'
