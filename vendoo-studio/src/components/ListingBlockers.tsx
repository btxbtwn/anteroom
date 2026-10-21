import { ChevronRightIcon } from "lucide-react";
import { fieldLabel, blockerTarget } from "./listingChanges";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";

export function ListingBlockers({ issues, onSelect }: {
  issues: { field?: string; message?: string }[];
  onSelect: (field: string) => void;
}) {
  const groups = new Map<string, typeof issues>();
  for (const issue of issues) {
    if (!issue.message) continue;
    const target = blockerTarget(issue.field || "");
    const group = target.reviewTab === "input" ? "Photos" : target.tab === "general" ? "General" : marketplaceName(target.tab);
    groups.set(group, [...(groups.get(group) || []), issue]);
  }
  if (!groups.size) return null;
  return (
    <div className="mt-2 flex w-full flex-col gap-0.5 text-left" aria-label="Fields to fix before sending">
      {[...groups].map(([group, entries]) => (
        <details key={group} className="group rounded-lg">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-md px-1.5 py-1 font-medium text-foreground text-xs hover:bg-accent [&::-webkit-details-marker]:hidden">
            <ChevronRightIcon className="size-3.5 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
            {group} · {entries.length} {entries.length === 1 ? "issue" : "issues"}
          </summary>
          <div className="flex flex-col gap-1.5 py-1 ps-6 pe-1">
            {entries.map((issue, index) => (
              <div key={`${issue.field}:${index}`} className="flex items-start justify-between gap-2 text-muted-foreground text-xs">
                <span className="min-w-0 pt-0.5">{issue.message}</span>
                {issue.field && (
                  <Button variant="outline" size="xs" className="shrink-0" onClick={() => onSelect(issue.field!)}>
                    Fix {fieldLabel(issue.field)}
                  </Button>
                )}
              </div>
            ))}
          </div>
        </details>
      ))}
    </div>
  );
}
