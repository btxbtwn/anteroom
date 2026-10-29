from __future__ import annotations

import copy
import unittest
from unittest.mock import AsyncMock, patch

from vendoo_studio.services import mercari_form


class MercariFormTest(unittest.IsolatedAsyncioTestCase):
    def test_native_catalog_references_are_complete(self):
        data = mercari_form.catalog()
        self.assertEqual(len(data['itemCategories']), 3233)
        self.assertEqual(len(data['itemBrands']), 28853)
        groups = {row['id']: row for row in data['itemSizeGroups']}
        sizes = {row['id']: row for row in data['itemSizes']}
        self.assertEqual(len(sizes), 384)
        for category in data['itemCategories']:
            if category['itemSizeGroupId']:
                self.assertIn(category['itemSizeGroupId'], groups)
        for group in groups.values():
            self.assertTrue(group['sizeIds'])
            for size_id in group['sizeIds']:
                self.assertIn(size_id, sizes)

    def test_duplicate_sizes_remain_distinct_under_the_native_group_headings(self):
        listing = {'direct_category_ids': {'mercari': '275'}, 'size': 'M (8-10)'}
        result = mercari_form.form(listing)
        size = next(row for row in result['fields'] if row['path'] == 'mercari_specifics.nativeSizeId')
        self.assertEqual(size['option_values']['M (8-10) · Standard'], '4')
        self.assertNotEqual(size['option_values']['M (8-10) · Petite'], '4')
        self.assertEqual(size['value'], '4')
        self.assertEqual(size['option_groups']['M (8-10) · Petite'], 'Petite')
        listing['mercari_specifics'] = {'nativeSizeId': ''}
        cleared = next(row for row in mercari_form.form(listing)['fields'] if row['path'] == size['path'])
        self.assertEqual(cleared['value'], '')

    def test_categories_without_sizes_do_not_receive_a_fallback_size_field(self):
        for category_id in ('703', '3558'):
            result = mercari_form.form({'direct_category_ids': {'mercari': category_id}})
            self.assertTrue(result['known'])
            self.assertNotIn('mercari_specifics.nativeSizeId', [row['path'] for row in result['fields']])
        self.assertFalse(mercari_form.form({'direct_category_ids': {'mercari': 'invented'}})['known'])

    def test_seller_clears_and_disabled_price_controls_are_preserved(self):
        listing = {'brand': 'Nike', 'price': 20, 'mercari_specifics': {
            'brand': '', 'description': '', 'shippingMethod': 'SOYO', 'smartPricing': {'enabled': False}}}
        before = copy.deepcopy(listing)
        fields = {row['path']: row for row in mercari_form.form(listing)['fields']}
        self.assertEqual(fields['mercari_specifics.brand']['value'], mercari_form.NO_BRAND)
        self.assertEqual(fields['mercari_specifics.description']['value'], '')
        self.assertFalse(fields['mercari_specifics.smartPricing.minimumPrice']['required'])
        self.assertFalse(fields['mercari_specifics.shippingClassId']['required'])
        self.assertFalse(fields['mercari_specifics.package.fitsShoebox']['required'])
        self.assertEqual(listing, before)

    async def test_shipping_requires_the_sellers_shoebox_answer_and_uses_live_fees(self):
        listing = {'direct_category_ids': {'mercari': '275'}, 'weight_lb': 1, 'package_dimensions_in': '13x10x3'}
        quote = {'id': 2552, 'fee': 799, 'standardFee': 899, 'etaForSeller': '1 - 7 days',
                 'weightRange': {'min': 13, 'max': 16}}
        with patch('vendoo_studio.services.mercari_api.shipping_quotes', new=AsyncMock(return_value=[quote])) as read:
            await mercari_form.editor_form(listing)
            read.assert_not_awaited()
            listing['mercari_specifics'] = {'package': {'fitsShoebox': 'yes'}}
            result = await mercari_form.editor_form(listing)
            read.assert_awaited_once_with(275, 16, (14, 10, 5))
        field = next(row for row in result['fields'] if row['path'] == 'mercari_specifics.shippingClassId')
        self.assertEqual(field['option_values'], {'USPS Ground Advantage': '2552'})
        self.assertIn('$7.99', field['option_help']['USPS Ground Advantage'])
        self.assertEqual(result['notes'], [])

    async def test_shipping_failure_or_unknown_label_keeps_product_fields_and_never_invents_rates(self):
        listing = {'direct_category_ids': {'mercari': '275'}, 'weight_lb': 1,
                   'mercari_specifics': {'package': {'fitsShoebox': 'yes'}}}
        for quote in (AsyncMock(side_effect=RuntimeError('unavailable')),
                      AsyncMock(return_value=[{'id': 999999, 'fee': 1, 'weightRange': {'max': 16}}])):
            with patch('vendoo_studio.services.mercari_api.shipping_quotes', new=quote):
                result = await mercari_form.editor_form(listing)
            fields = {row['path']: row for row in result['fields']}
            self.assertIn('Nike', fields['mercari_specifics.brand']['options'])
            self.assertEqual(fields['mercari_specifics.shippingClassId']['options'], [])
            self.assertTrue(result['notes'])

    def test_package_skip_and_explicit_weight_match_the_native_question(self):
        listing = {'weight_lb': 3, 'weight_oz': 8, 'package_dimensions_in': '13x10x3',
                   'mercari_specifics': {'package': {'fitsShoebox': 'no', 'ounces': 6.1, 'skipDimensions': True}}}
        weight, dimensions, _ = mercari_form.package(listing)
        self.assertEqual(weight, 7)
        self.assertEqual(dimensions, (0, 0, 0))

    def test_ship_from_uses_the_configured_native_zip_and_preserves_a_valid_override(self):
        listing = {'form': {'ships_from_zip': '70112'}, 'mercari_specifics': {'shipsFrom': '12'}}
        field = next(row for row in mercari_form.form(listing)['fields'] if row['path'] == 'mercari_specifics.shipsFrom')
        self.assertEqual(field['value'], '70112')
        listing['mercari_specifics']['shipsFrom'] = '90210'
        field = next(row for row in mercari_form.form(listing)['fields'] if row['path'] == 'mercari_specifics.shipsFrom')
        self.assertEqual(field['value'], '90210')
