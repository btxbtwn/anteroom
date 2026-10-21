import { SegmentedTabs } from "./ListingFormControls";

export type ListingReviewTab = "input" | "forms";

const TABS: { id: ListingReviewTab; label: string }[] = [
  { id: "input", label: "Input" },
  { id: "forms", label: "Forms" },
];

/** Input / Forms as T3's segmented control. */
export function ListingReviewTabs({
  value,
  onChange,
}: {
  value: ListingReviewTab;
  onChange: (tab: ListingReviewTab) => void;
}) {
  return <SegmentedTabs tabs={TABS} value={value} onChange={onChange} label="Listing review" />;
}
