"""Match Vendoo's single item sale to its direct marketplace order."""
from datetime import UTC, datetime

from vendoo_studio.models.marketplace_sale import MarketplaceSale


def matching_sale_index(lines: list[MarketplaceSale], marketplace: str, sold_at: datetime | None) -> int | None:
    """Vendoo has no order ID: use its marketplace and nearest sale date."""
    candidates = [i for i, line in enumerate(lines) if marketplace == "unknown" or line.marketplace == marketplace]
    if not candidates:
        return None

    def distance(index: int) -> float:
        moment = lines[index].sold_at or lines[index].received_at
        if moment and sold_at:
            moment = moment if moment.tzinfo else moment.replace(tzinfo=UTC)
            return abs((moment - sold_at).total_seconds())
        return float(index)

    return min(candidates, key=distance)
