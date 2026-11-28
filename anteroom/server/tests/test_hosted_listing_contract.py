import pytest
from fastapi import HTTPException

from anteroom.services.hosted_listing_contract import editable_listing, stored_listing_fields


def test_the_website_reads_which_prices_the_seller_set():
    # The editor shows a marketplace price that follows General unless it is locked (#2007).
    stored = {'title': 'Tee', 'price': 12, 'depop_specifics': {'price': 10.8},
              'seller_fields': ['depop_specifics.price', 'poshmark_specifics.poshmarkPrice']}
    assert stored_listing_fields(stored)['seller_fields'] == ['depop_specifics.price', 'poshmark_specifics.poshmarkPrice']


def test_an_odd_saved_lock_never_stops_the_listing_opening():
    read = stored_listing_fields({'title': 'Tee', 'seller_fields': ['price', 7, '', 'x' * 300]})
    assert read['seller_fields'] == ['price']
    assert stored_listing_fields({'title': 'Tee', 'seller_fields': 'price'})['seller_fields'] == []


@pytest.mark.parametrize('locks', ['price', [1], [''], ['x' * 300], ['price'] * 2001])
def test_a_save_sends_locks_only_as_field_names(locks):
    with pytest.raises(HTTPException):
        editable_listing({'title': 'Tee', 'seller_fields': locks})


def test_the_package_weight_keeps_its_pounds():
    """#2304: 1 lb 2 oz read back as 2 oz, so the Mercari package (1 lb 2 oz) looked wrong."""
    stored = {'title': 'Chinos', 'weight_lb': 1, 'weight_oz': 2,
              'mercari_specifics': {'package': {'pounds': 1, 'ounces': 2}}}

    listing = stored_listing_fields(stored)

    assert (listing['weight_lb'], listing['weight_oz']) == (1, 2)
    # A client sends the pounds back on save.
    assert editable_listing(dict(listing))['weight_lb'] == 1


def test_a_save_refuses_negative_pounds():
    with pytest.raises(HTTPException):
        editable_listing({'title': 'Chinos', 'weight_lb': -1})
