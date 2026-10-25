import type { AnalyticsStaleListing } from "../api/types";
import { formatDays, formatMoney } from "./analyticsFormat";
import { EmptyRow, ListRow, PageSection, RowList } from "./WorkspacePage";

/**
 * Listings 60+ days old with no sale: the ones to cut past the everyday 25% in
 * the next sale event, as deep as their cost after fees allows.
 */
export function DiscountSuggestions({
  listings,
  onOpenListing,
}: {
  listings: AnalyticsStaleListing[];
  onOpenListing: (id: string) => void;
}) {
  return (
    <PageSection
      aria-label="Discount deeper"
      title="Discount deeper"
      description={<>
        Active listings up 60 days or more, oldest first. Give these 35–40% off in the next sale instead of 25%, as deep
        as still covers what the item cost after 20% fees. Items with no cost recorded get 35%.
      </>}
    >
      {listings.length === 0 ? (
        <EmptyRow>Nothing has been listed 60 days without selling.</EmptyRow>
      ) : (
        <RowList>
            {listings.map((listing) => (
              <ListRow
                key={listing.conversation_id}
                title={listing.title}
                meta={<>
                  {formatDays(listing.days_listed)} · {formatMoney(listing.price)}
                  {listing.lowest_price != null ? ` · lowest ${formatMoney(listing.lowest_price)}` : ""}
                </>}
                value={listing.discount_percent == null || listing.sale_price == null
                  ? "Keep full price"
                  : `${listing.discount_percent}% off → ${formatMoney(listing.sale_price)}`}
                onClick={() => onOpenListing(listing.conversation_id)}
              />
            ))}
        </RowList>
      )}
    </PageSection>
  );
}
