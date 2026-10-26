"""Order proceeds from the production Finances API's granted transaction scope."""
from dataclasses import dataclass
from decimal import Decimal

from vendoo_studio.services.ebay_client import EbayClient


ORDER_TYPES = {"SALE", "REFUND", "CREDIT", "DISPUTE", "SHIPPING_LABEL", "NON_SALE_CHARGE", "ADJUSTMENT"}
FUNDING_TYPES = {"TRANSFER", "WITHDRAWAL", "PURCHASE", "LOAN_REPAYMENT"}


@dataclass(frozen=True)
class OrderFinancials:
    net: Decimal
    original_sale_fees: Decimal | None


def _fee(value) -> Decimal | None:
    if not isinstance(value, dict) or value.get("currency") != "USD":
        return None
    try:
        amount = Decimal(str(value["value"]))
    except (KeyError, ValueError, ArithmeticError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


async def order_financials(client: EbayClient, order_id: str) -> OrderFinancials | None:
    """Sum recorded order credits/debits; never subtract included sale fees twice.

    Funding movements are not income or expenses of the order. Unknown types,
    currencies, conflicting duplicate entries, or incomplete pagination leave
    proceeds unavailable rather than silently overstating profit.
    """
    total = Decimal(0)
    sale_fees = []
    seen = {}
    offset = 0
    while offset < 10000:
        body = await client.request("GET", "/sell/finances/v1/transaction", params={
            "filter": f"orderId:{{{order_id}}}", "limit": 1000, "offset": offset,
        })
        transactions = body.get("transactions", [])
        if not isinstance(transactions, list):
            return None
        for transaction in transactions:
            kind = transaction.get("transactionType")
            identifier = transaction.get("transactionId")
            if not isinstance(kind, str) or not isinstance(identifier, str) or not identifier:
                return None
            key = (identifier, kind)
            entry = {field: transaction.get(field) for field in ("orderId", "amount", "bookingEntry", "totalFeeAmount")}
            if key in seen:
                if seen[key] != entry:
                    return None
                continue
            seen[key] = entry
            if kind in FUNDING_TYPES:
                continue
            if kind not in ORDER_TYPES or transaction.get("orderId") != order_id:
                return None
            money = transaction.get("amount") or {}
            if money.get("currency") != "USD" or transaction.get("bookingEntry") not in ("CREDIT", "DEBIT"):
                return None
            try:
                value = Decimal(str(money["value"]))
            except (KeyError, ValueError, ArithmeticError):
                return None
            if not value.is_finite() or value < 0:
                return None
            total += value if transaction["bookingEntry"] == "CREDIT" else -value
            if kind == "SALE":
                sale_fees.append(_fee(transaction.get("totalFeeAmount")) if transaction["bookingEntry"] == "CREDIT" else None)
        offset += len(transactions)
        count = body.get("total")
        more = bool(body.get("next")) or (isinstance(count, int) and offset < count)
        if not more:
            # A duplicate page can otherwise conceal missing transactions.
            if isinstance(count, int) and len(seen) != count:
                return None
            if not sale_fees:
                return None
            # totalFeeAmount is the order's fee total. Multiple SALE records
            # may repeat it; do not sum or guess an allocation across them.
            fees = sale_fees[0] if len(sale_fees) == 1 else None
            return OrderFinancials(net=total, original_sale_fees=fees)
        if not transactions:
            return None
    return None
