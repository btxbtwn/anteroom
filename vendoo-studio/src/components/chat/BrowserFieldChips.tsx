import { XIcon } from "lucide-react";
import type { BrowserField } from "../../api/client";
import { cn } from "../../lib/utils";
import { useVendooEnabled } from "../../vendooMode";
import { Button } from "../ui/button";

const MARKET_LABELS: Record<string, string> = {
  general: "Vendoo",
  ebay: "eBay",
  etsy: "Etsy",
  poshmark: "Poshmark",
  mercari: "Mercari",
  depop: "Depop",
};

/** Fields picked in Studio's browser; they go with the next message. */
export function BrowserFieldChips({
  fields,
  onChange,
}: {
  fields: BrowserField[] | undefined;
  onChange: ((fields: BrowserField[]) => void) | undefined;
}) {
  const vendooOn = useVendooEnabled();
  if (!fields?.length) return null;
  const labels = vendooOn ? MARKET_LABELS : { ...MARKET_LABELS, general: "General" };
  return (
    <div className="mb-3 flex flex-col gap-1.5" aria-label={vendooOn ? "Fields pointed at in the Vendoo browser" : "Fields pointed at in the browser"}>
      <div className="flex items-center justify-between gap-2 text-muted-foreground text-xs">
        <span>
          {fields.length} field{fields.length === 1 ? "" : "s"} from the browser go with your message
        </span>
        <Button type="button" size="xs" variant="ghost-muted" onClick={() => onChange?.([])}>
          Clear
        </Button>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {fields.map((field) => (
          <span
            key={`${field.marketplace}:${field.key || field.label}`}
            className={cn(
              "inline-flex h-6 max-w-full min-w-0 items-center gap-1 rounded-md border border-border/80 bg-background ps-2 pe-0.5 text-xs",
              field.account_managed && "opacity-60",
            )}
            title={field.value || "empty"}
          >
            <span className="truncate">
              {labels[field.marketplace] || field.marketplace} / {field.label}
            </span>
            {!field.filled ? <span className="text-muted-foreground text-2xs">empty</span> : null}
            <Button
              type="button"
              size="icon-micro"
              variant="ghost-muted"
              aria-label={`Remove ${field.label}`}
              onClick={() => onChange?.(fields.filter((item) => item !== field))}
            >
              <XIcon />
            </Button>
          </span>
        ))}
      </div>
    </div>
  );
}
