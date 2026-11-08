import { Skeleton } from "../ui/skeleton";

// Title widths vary a little, so the placeholder reads as a list rather than a grid.
const TITLE_WIDTHS = ["w-3/4", "w-2/3", "w-4/5", "w-1/2", "w-3/5", "w-2/3"];

/** The sidebar's listing rows while the inventory loads: same height, thumbnail and text lines as `ListingRow`. */
export function ListingRowsSkeleton({ count = 6 }: { count?: number }) {
  return (
    <div role="status" aria-label="Loading listings" aria-busy="true">
      {Array.from({ length: count }, (_, index) => (
        <div
          key={index}
          aria-hidden="true"
          className="mb-px flex min-h-22 items-center gap-4 px-3 py-2 max-[900px]:min-h-24 max-[900px]:py-3"
        >
          <Skeleton shape="card" className="size-14 flex-none max-[900px]:size-16" />
          <div className="flex min-w-0 flex-1 flex-col gap-2">
            <Skeleton className={`h-4 ${TITLE_WIDTHS[index % TITLE_WIDTHS.length]}`} />
            <Skeleton className="h-3 w-16" />
          </div>
        </div>
      ))}
    </div>
  );
}
