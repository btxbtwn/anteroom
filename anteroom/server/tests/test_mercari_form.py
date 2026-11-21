from __future__ import annotations

import copy
import unittest
from unittest.mock import AsyncMock, patch

from anteroom.services import mercari_form


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

    def test_a_brand_missing_from_the_catalog_copy_is_not_shown_as_no_brand(self):
        # AM TEES XL (draft 0b945d2660ec): General brand "AM Tees", no Mercari brand of its own.
        # The bundled catalog is a dated copy, so absence there is not a confirmed no-match.
        listing = {'brand': 'AM Tees', 'condition': 'Pre-Owned - Good', 'price': 20}
        before = copy.deepcopy(listing)
        row = next(row for row in mercari_form.form(listing)['fields'] if row['path'] == 'mercari_specifics.brand')
        self.assertEqual(row['value'], 'AM Tees')
        self.assertIn("isn't in Anteroom's copy of Mercari's brand list", row['help'])
        self.assertIn('otherwise the item is listed with No brand', row['help'])
        self.assertEqual(listing, before)

    def test_recognized_blank_and_seller_brands_are_kept(self):
        row = next(row for row in mercari_form.form({'brand': 'nike'})['fields'] if row['path'] == 'mercari_specifics.brand')
        self.assertEqual(row['value'], 'Nike')
        self.assertNotIn('help', row)
        for listing in ({'brand': ''}, {'brand': 'Unbranded'}, {'brand': 'AM Tees', 'mercari_specifics': {'noBrand': True}}):
            row = next(row for row in mercari_form.form(listing)['fields'] if row['path'] == 'mercari_specifics.brand')
            self.assertEqual(row['value'], mercari_form.NO_BRAND, listing)
            self.assertNotIn('help', row)
        locked = {'brand': 'Other', 'mercari_specifics': {'brand': 'AM Tees'},
                  'seller_fields': ['mercari_specifics.brand']}
        row = next(row for row in mercari_form.form(locked)['fields'] if row['path'] == 'mercari_specifics.brand')
        self.assertEqual(row['value'], 'AM Tees')

    def test_an_unconfirmed_brand_does_not_block_list_before_mercaris_live_check(self):
        from anteroom.services import native_contract

        def brand_gaps(listing):
            listing = {**listing, native_contract.KEY: native_contract.capture(
                listing, {'mercari': mercari_form.form(listing)}, ['mercari'])}
            return [gap for gap in native_contract.gaps(listing, 'mercari') if gap['field'] == 'mercari_specifics.brand']

        found = brand_gaps({'brand': 'AM Tees', 'price': 20})
        self.assertEqual(len(found), 1)
        self.assertFalse(found[0]['blocking'])
        self.assertIn("isn't in Anteroom's copy of Mercari's brand list", found[0]['message'])
        self.assertEqual(brand_gaps({'brand': 'Nike', 'price': 20}), [])

    def test_an_unreadable_brand_catalog_is_an_error_not_no_brand(self):
        with patch.object(mercari_form, 'catalog', side_effect=OSError('catalog unreadable')):
            with self.assertRaises(OSError):
                mercari_form.form({'brand': 'AM Tees'})

    async def test_shipping_requires_the_sellers_shoebox_answer_and_uses_live_fees(self):
        listing = {'direct_category_ids': {'mercari': '275'}, 'weight_lb': 1, 'package_dimensions_in': '13x10x3'}
        quote = {'id': 2552, 'fee': 799, 'standardFee': 899, 'etaForSeller': '1 - 7 days',
                 'weightRange': {'min': 13, 'max': 16}}
        with patch('anteroom.services.mercari_api.shipping_quotes', new=AsyncMock(return_value=[quote])) as read:
            await mercari_form.editor_form(listing)
            read.assert_not_awaited()
            listing['mercari_specifics'] = {'package': {'fitsShoebox': 'yes'}}
            result = await mercari_form.editor_form(listing)
            read.assert_awaited_once_with(275, 16, (14, 10, 5))
        field = next(row for row in result['fields'] if row['path'] == 'mercari_specifics.shippingClassId')
        self.assertEqual(field['option_values'], {'USPS Ground Advantage': '2552'})
        self.assertIn('$7.99', field['option_help']['USPS Ground Advantage'])
        self.assertEqual(result['notes'], [])
        self.assertFalse(result.get('reload_recommended', False))

    async def test_shipping_failure_or_unknown_label_keeps_product_fields_and_never_invents_rates(self):
        listing = {'direct_category_ids': {'mercari': '275'}, 'weight_lb': 1,
                   'mercari_specifics': {'package': {'fitsShoebox': 'yes'}}}
        for quote in (AsyncMock(side_effect=RuntimeError('unavailable')),
                      AsyncMock(return_value=[{'id': 999999, 'fee': 1, 'weightRange': {'max': 16}}])):
            with patch('anteroom.services.mercari_api.shipping_quotes', new=quote):
                result = await mercari_form.editor_form(listing)
            fields = {row['path']: row for row in result['fields']}
            self.assertIn('Nike', fields['mercari_specifics.brand']['options'])
            self.assertEqual(fields['mercari_specifics.shippingClassId']['options'], [])
            self.assertTrue(result['notes'])
            self.assertTrue(result['reload_recommended'])

    async def test_a_capitalized_shoebox_answer_still_loads_the_labels(self):
        # Generation saved the radio's label ("Yes") rather than its value ("yes").
        listing = {'direct_category_ids': {'mercari': '165'}, 'weight_oz': 12,
                   'mercari_specifics': {'package': {'fitsShoebox': 'Yes', 'ounces': 12, 'pounds': 0}}}
        self.assertEqual(mercari_form.package(listing)[1], (14, 10, 5))
        quote = {'id': 2551, 'fee': 739, 'etaForSeller': '1 - 7 days', 'weightRange': {'min': 9, 'max': 12}}
        with patch('anteroom.services.mercari_api.shipping_quotes', new=AsyncMock(return_value=[quote])) as read:
            result = await mercari_form.editor_form(listing)
        read.assert_awaited_once_with(165, 12, (14, 10, 5))
        field = next(row for row in result['fields'] if row['path'] == 'mercari_specifics.shippingClassId')
        self.assertEqual(field['option_values'], {'USPS Ground Advantage': '2551'})
        self.assertEqual(field['value'], '2551')

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

    def test_general_letter_size_matches_ranged_regular_size_not_tall(self):
        # Men > Tops > T-shirts (303) spells Regular sizes "S (34-36)" and "M (38-40)"
        # while Tall sizes are plain "M", "L", "XL".
        def size_value(listing):
            return next(
                row for row in mercari_form.form(listing)['fields']
                if row['path'] == 'mercari_specifics.nativeSizeId'
            )['value']

        self.assertEqual(size_value({'direct_category_ids': {'mercari': '303'}, 'size': 'S'}), '9')
        self.assertEqual(size_value({'direct_category_ids': {'mercari': '303'}, 'size': 'M'}), '10')
        self.assertEqual(size_value({'direct_category_ids': {'mercari': '303'}, 'size': 'M', 'sizeType': 'Tall'}), '340')
        self.assertEqual(size_value({'direct_category_ids': {'mercari': '303'}, 'size': '2XL'}), '13')
        self.assertEqual(size_value({'direct_category_ids': {'mercari': '275'}, 'size': 'S'}), '3')
        self.assertEqual(size_value({'direct_category_ids': {'mercari': '275'}, 'size': 'S', 'sizeType': 'Petite'}), '292')
        self.assertEqual(size_value({'direct_category_ids': {'mercari': '303'}, 'size': 'S',
                                     'mercari_specifics': {'nativeSizeId': ''}}), '')
        self.assertEqual(size_value({'direct_category_ids': {'mercari': '303'}, 'size': 'S',
                                     'mercari_specifics': {'size': ''}}), '')

    def test_brand_uses_reference_options_source_so_editor_skips_contradictory_alert(self):
        row = next(row for row in mercari_form.form({'brand': 'Hind Performance'})['fields']
                   if row['path'] == 'mercari_specifics.brand')
        self.assertEqual(row['value'], 'Hind Performance')
        self.assertEqual(row['options_source'], 'reference')
        self.assertTrue(row['selection_only'])
        self.assertIn("isn't in Anteroom's copy of Mercari's brand list", row['help'])

    def test_mercari_followed_size_and_condition_have_no_empty_gaps(self):
        from anteroom.services import native_contract

        listing = {
            'title': 'Hind Performance S Vintage Tee',
            'description': 'Vintage long sleeve graphic tee.',
            'brand': 'Hind Performance',
            'condition': 'Good',
            'size': 'S',
            'price': 18,
            'direct_category_ids': {'mercari': '303'},
            'mercari_specifics': {'shippingMethod': 'SOYO', 'shipsFrom': '70115',
                                  'smartPricing': {'enabled': False}},
        }
        listing[native_contract.KEY] = native_contract.capture(
            listing, {'mercari': mercari_form.form(listing)}, ['mercari'],
        )
        blocking = [gap['field'] for gap in native_contract.gaps(listing, 'mercari') if gap.get('blocking')]
        self.assertEqual(blocking, [])

        cleared = copy.deepcopy(listing)
        cleared['mercari_specifics']['nativeSizeId'] = ''
        cleared['mercari_specifics']['condition'] = ''
        cleared[native_contract.KEY] = native_contract.capture(
            cleared, {'mercari': mercari_form.form(cleared)}, ['mercari'],
        )
        cleared_blocking = {gap['field'] for gap in native_contract.gaps(cleared, 'mercari') if gap.get('blocking')}
        self.assertEqual(cleared_blocking, {'mercari_specifics.nativeSizeId', 'mercari_specifics.condition'})

        # Explicitly clearing mercari_specifics.size without nativeSizeId also stays empty and blocks.
        cleared_size_key = copy.deepcopy(listing)
        cleared_size_key['mercari_specifics']['size'] = ''
        cleared_size_key[native_contract.KEY] = native_contract.capture(
            cleared_size_key, {'mercari': mercari_form.form(cleared_size_key)}, ['mercari'],
        )
        cleared_size_blocking = {
            gap['field'] for gap in native_contract.gaps(cleared_size_key, 'mercari') if gap.get('blocking')
        }
        self.assertEqual(cleared_size_blocking, {'mercari_specifics.nativeSizeId'})
