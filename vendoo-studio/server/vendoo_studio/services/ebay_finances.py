"""Order proceeds from the production Finances API's granted transaction scope."""
from decimal import Decimal

from vendoo_studio.services.ebay_client import EbayClient


ORDER_TYPES = {"SALE", "REFUND", "CREDIT", "DISPUTE", "SHIPPING_LABEL", "NON_SALE_CHARGE", "ADJUSTMENT"}
FUNDING_TYPES = {"TRANSFER", "WITHDRAWAL", "PURCHASE", "LOAN_REPAYMENT"}


async def order_proceeds(client: EbayClient, order_id: str) -> Decimal | None:
    """Sum recorded order credits/debits; never subtract included sale fees twice.

    Funding movements are not income or expenses of the order. Unknown types,
    currencies, conflicting duplicate entries, or incomplete pagination leave
    proceeds unavailable rather than silently overstating profit.
    """
    total = Decimal(0)
    sales = False
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
            entry = {field: transaction.get(field) for field in ("orderId", "amount", "bookingEntry")}
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
            sales = sales or kind == "SALE"
        offset += len(transactions)
        count = body.get("total")
        more = bool(body.get("next")) or (isinstance(count, int) and offset < count)
        if not more:
            # A duplicate page can otherwise conceal missing transactions.
            if isinstance(count, int) and len(seen) != count:
                return None
            return total if sales else None
        if not transactions:
            return None
    return None
