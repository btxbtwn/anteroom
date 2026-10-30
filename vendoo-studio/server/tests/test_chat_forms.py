import asyncio
import json
from unittest.mock import AsyncMock, patch

from test_chat_field_context import context_db as context_db
from vendoo_studio.services.chat_forms import forms_prompt
from vendoo_studio.services.chat_prompts import build_chat_messages
from vendoo_studio.services.option_lookup import OptionCatalog


def test_every_native_field_is_visible_and_large_choices_are_searchable(context_db):
    db, conv_id, _ = context_db
    markets = ['ebay', 'poshmark', 'mercari', 'depop', 'sellwild']
    choices = [f'Choice {n}' for n in range(350)]
    forms = {market: {'fields': [{
        'path': f'{market}_specifics.field{n}', 'label': f'Field {n}',
        'value': '', 'options': choices, 'option_values': {label: str(i) for i, label in enumerate(choices)},
        'required': False, 'max_values': 2, 'multi': True, 'locked': n == 0,
        'value_constraints': {'Choice 349': [{'parentName': 'Size', 'parentValues': ['M']}]},
    } for n in range(45)]} for market in markets}
    catalog = OptionCatalog({})
    with patch('vendoo_studio.services.chat_forms.get_selected_marketplaces', return_value=markets), \
         patch('vendoo_studio.services.chat_forms.native_field_fill.load_forms', new=AsyncMock(return_value=forms)), \
         patch('vendoo_studio.services.chat_forms.vinted_browser.category_rows', return_value=[{'title': 'Straight-leg pants'}]):
        prompt = asyncio.run(forms_prompt(db, conv_id, catalog))
    data = json.loads(prompt.split('\n')[-1])
    for market in markets:
        assert len(data[market]['fields']) == 45
        last = data[market]['fields'][-1]
        assert 'options' not in last
        assert last['options_lookup']['count'] == 350
        found = catalog.search({'marketplace': market, 'field': last['path'], 'query': 'Choice 349'})
        assert found['results'][0]['value'] == '349'
        assert found['results'][0]['constraints'] == [{'parentName': 'Size', 'parentValues': ['M']}]
        assert data[market]['fields'][0]['locked']
    assert {row['path'] for row in data['general']['fields']} >= {'cost', 'sku', 'department', 'quantity'}
    assert {row['path'] for row in data['vinted']['fields']} >= {'vinted_specifics.category', 'vinted_specifics.material'}
    vinted = {row['path']: row for row in data['vinted']['fields']}
    assert vinted['vinted_specifics.title']['value'] == 'Shirt'
    assert 'title' not in vinted
    assert 'vinted_specifics.price' in vinted
    assert 'Category changes are allowed' in prompt
    assert 'Never publish' in prompt


def test_chat_refreshes_forms_even_without_marketplace_named(context_db):
    db, conv_id, _ = context_db
    with patch('vendoo_studio.services.chat_prompts.load_skill_rules', return_value=''), \
         patch('vendoo_studio.services.chat_forms.native_field_fill.load_forms', new=AsyncMock(return_value={})) as load, \
         patch('vendoo_studio.services.chat_forms.vinted_browser.category_rows', return_value=[{'title': 'Straight-leg pants'}]):
        messages = asyncio.run(build_chat_messages(conv_id, db, 'Fix these fields'))
    content = messages[0]['content']
    load.assert_awaited_once()
    assert 'Current editable forms' in content
    assert 'Straight-leg pants' in content
    assert 'Category is already selected; do not change it' not in content
    data = json.loads(content.split('Current editable forms ---\n')[1].split('\n')[1])
    assert data['ebay']['unavailable'] is True


def test_etsy_optional_fields_and_last_choice_are_available(context_db):
    db, conv_id, _ = context_db
    choices = [f'Pattern {n}' for n in range(20)]
    with patch('vendoo_studio.services.chat_forms.get_selected_marketplaces', return_value=['etsy']), \
         patch('vendoo_studio.services.chat_forms.native_field_fill.load_forms', new=AsyncMock(return_value={})), \
         patch('vendoo_studio.services.chat_forms.vinted_browser.category_rows', return_value=[]), \
         patch('vendoo_studio.services.direct_categories.overlay_live_fields', new=AsyncMock(return_value=[{
             'key': 'category_specifics.pattern', 'label': 'Pattern', 'options': choices,
         }])):
        data = json.loads(asyncio.run(forms_prompt(db, conv_id)).split('\n')[-1])
    fields = {row['path']: row for row in data['etsy']['fields']}
    assert fields['etsy_specifics.category_specifics.pattern']['options'] == choices
    assert 'etsy_specifics.whoMade' in fields
    assert 'etsy_specifics.category_specifics.sustainability' in fields
    category = next(row for row in data['vinted']['fields'] if row['path'] == 'vinted_specifics.category')
    assert 'options_unavailable' in category
