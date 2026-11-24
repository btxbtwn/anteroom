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
