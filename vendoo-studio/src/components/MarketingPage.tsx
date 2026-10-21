import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { AnalyticsStaleListing } from "../api/types";
import { formatDays, formatMoney } from "./analyticsFormat";
import { AdSpend } from "./AdSpend";
import { SaleCalendar } from "./SaleCalendar";
import { EmptyRow, ListRow, PageError, PageHeader, PageSection, RowList, WorkspacePage } from "./WorkspacePage";
import { Skeleton } from "./ui/skeleton";

interface Props {
  onOpenListing: (id: string) => void;
}

/** Sales events and paid ads: what the seller runs to move stock, beside what it earned. */
export function MarketingPage({ onOpenListing }: Props) {
  // Stale stock does not depend on the sales period; this shares Ads' default query.
  const query = useQuery({ queryKey: ["analytics", "12m"], queryFn: () => api.analytics.get("12m") });

  return (
    <WorkspacePage width="expanded">
      <PageHeader
        title="Marketing"
        description="Plan marketplace sales, decide how deep to discount, and track what you spend on ads against what sold."
      />
      <SaleCalendar onOpenListing={onOpenListing} />
      {query.isLoading ? (
        <div className="flex flex-col gap-3" role="status" aria-label="Loading listings…">
          <Skeleton className="h-4 w-32" />
          <Skeleton shape="card" className="h-32" />
        </div>
      ) : null}
      {query.data ? <DiscountDeeper listings={query.data.stale} onOpenListing={onOpenListing} /> : null}
      {query.isError ? <PageError message={(query.error as Error).message || "Could not load listings."} /> : null}
      <AdSpend />
    </WorkspacePage>
  );
}

/**
 * Listings 60+ days old with no sale: the ones to cut past the everyday 25% in
 * the next sale event, as deep as their cost after fees allows.
 */
function DiscountDeeper({
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
