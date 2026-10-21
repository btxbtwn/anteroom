import { ChevronRightIcon } from "lucide-react";
import type { ListingChange } from "../api/types";
import { changeValue, fieldLabel } from "./listingChanges";

export function ListingChangeList({ changes, beforeLabel = "Before", afterLabel = "After" }: {
  changes: ListingChange[];
  beforeLabel?: string;
  afterLabel?: string;
}) {
  if (!changes.length) return <p className="text-muted-foreground">No field changes.</p>;
  return (
    <div className="flex flex-col divide-y overflow-hidden rounded-xl border">
      {changes.map((change) => (
        <details key={change.field} className="group" open={changes.length <= 6}>
          <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-2 font-medium text-sm hover:bg-accent/50 [&::-webkit-details-marker]:hidden">
            <ChevronRightIcon className="size-3.5 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
            {fieldLabel(change.field)}
          </summary>
          <div className="grid grid-cols-2 gap-3 px-3 pb-3 max-sm:grid-cols-1">
            {([[beforeLabel, change.before], [afterLabel, change.after]] as const).map(([label, value]) => (
              <div key={label} className="min-w-0">
                <span className="mb-1 block font-medium text-muted-foreground text-xs">{label}</span>
                <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-md bg-muted px-2 py-1.5 font-mono text-xs">{changeValue(value)}</pre>
              </div>
            ))}
          </div>
        </details>
      ))}
    </div>
  );
}
