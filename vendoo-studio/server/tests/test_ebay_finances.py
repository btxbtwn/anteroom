from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from vendoo_studio.services.ebay_finances import order_financials

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def transaction(kind="SALE", value="40.00", booking="CREDIT", identifier="sale-1", **changes):
    return {"transactionId": identifier, "transactionType": kind, "orderId": "order-1", "bookingEntry": booking,
            "amount": {"currency": "USD", "value": value}, **changes}


async def proceeds(transactions, **changes):
    client = AsyncMock()
    client.request.return_value = {"transactions": transactions, "total": len(transactions), **changes}
    result = await order_financials(client, "order-1")
    return result.net if result else None


async def test_recorded_sale_amount_already_includes_selling_fees():
    assert await proceeds([transaction(totalFeeAmount={"currency": "USD", "value": "10.00"})]) == Decimal("40.00")


async def test_refunds_fees_labels_disputes_and_reversals_use_their_booking_entry():
    entries = [transaction(), transaction("REFUND", "12", "DEBIT", "refund"),
               transaction("SHIPPING_LABEL", "5", "DEBIT", "label"), transaction("SHIPPING_LABEL", "1", "CREDIT", "label-credit"),
               transaction("NON_SALE_CHARGE", "3", "DEBIT", "fee"), transaction("NON_SALE_CHARGE", ".50", "CREDIT", "fee-credit"),
               transaction("CREDIT", "1", "CREDIT", "credit"), transaction("DISPUTE", "4", "DEBIT", "dispute"),
               transaction("DISPUTE", "2", "CREDIT", "dispute-credit"), transaction("ADJUSTMENT", ".25", "DEBIT", "adjustment")]
    assert await proceeds(entries) == Decimal("20.25")


@pytest.mark.parametrize("kind", ["TRANSFER", "WITHDRAWAL", "PURCHASE", "LOAN_REPAYMENT"])
async def test_funding_movements_are_not_deducted_from_order_proceeds(kind):
    assert await proceeds([transaction(), transaction(kind, "100", "DEBIT", "funding", orderId=None)]) == Decimal("40.00")


@pytest.mark.parametrize("changes", [
    {"amount": {"currency": "EUR", "value": "40"}}, {"amount": {"currency": "USD", "value": "NaN"}},
    {"amount": {"currency": "USD", "value": "-40"}}, {"amount": {"currency": "USD", "value": "bad"}},
    {"amount": {"currency": "USD"}}, {"bookingEntry": "UNKNOWN"}, {"transactionType": "UNKNOWN"},
    {"transactionId": ""}, {"orderId": "another-order"},
])
async def test_incomplete_or_unrecognised_financial_data_never_becomes_profit(changes):
    assert await proceeds([transaction(**changes)]) is None


async def test_missing_sale_is_not_reported_as_zero_earnings():
    assert await proceeds([]) is None
    assert await proceeds([transaction("REFUND", "5", "DEBIT")]) is None
    assert await proceeds([transaction(value="0")]) == Decimal(0)


async def test_duplicates_do_not_double_count_and_conflicts_remain_unknown():
    sale = transaction()
    assert await proceeds([sale, sale], total=1) == Decimal("40")
    assert await proceeds([sale, sale]) is None  # Two advertised entries, only one unique entry.
    assert await proceeds([sale, transaction(value="50")], total=1) is None


async def test_all_transaction_pages_use_fixed_paths_and_numeric_offsets():
    client = AsyncMock()
    client.request.side_effect = [
        {"transactions": [transaction()], "total": 2, "next": "https://untrusted.example/next"},
        {"transactions": [transaction("REFUND", "5", "DEBIT", "refund")], "total": 2},
    ]
    assert (await order_financials(client, "order-1")).net == Decimal("35")
    assert [call.args for call in client.request.call_args_list] == [("GET", "/sell/finances/v1/transaction")] * 2
    assert [call.kwargs["params"] for call in client.request.call_args_list] == [
        {"filter": "orderId:{order-1}", "limit": 1000, "offset": 0},
        {"filter": "orderId:{order-1}", "limit": 1000, "offset": 1},
    ]


async def test_empty_intermediate_page_does_not_return_partial_proceeds():
    client = AsyncMock()
    client.request.side_effect = [{"transactions": [transaction()], "total": 2}, {"transactions": [], "total": 2}]
    assert await order_financials(client, "order-1") is None


async def test_reported_original_fees_are_separate_from_net_proceeds():
    client = AsyncMock()
    client.request.return_value = {"total": 1, "transactions": [transaction(totalFeeAmount={"currency": "USD", "value": "10.00"})]}
    result = await order_financials(client, "order-1")
    assert result.net == Decimal("40") and result.original_sale_fees == Decimal("10")


@pytest.mark.parametrize("fee", [None, {}, {"currency": "EUR", "value": "10"}, {"currency": "USD", "value": "NaN"}, {"currency": "USD", "value": "-1"}])
async def test_missing_or_unverified_fees_never_become_zero_or_reduce_net_again(fee):
    client = AsyncMock()
    client.request.return_value = {"total": 1, "transactions": [transaction(totalFeeAmount=fee)]}
    result = await order_financials(client, "order-1")
    assert result.net == Decimal("40") and result.original_sale_fees is None


async def test_multiple_sale_records_do_not_double_an_order_level_fee_total():
    client = AsyncMock()
    client.request.return_value = {"total": 2, "transactions": [transaction(identifier="one", value="20", totalFeeAmount={"currency": "USD", "value": "10"}),
                                                          transaction(identifier="two", value="20", totalFeeAmount={"currency": "USD", "value": "10"})]}
    result = await order_financials(client, "order-1")
    assert result.net == Decimal("40") and result.original_sale_fees is None
