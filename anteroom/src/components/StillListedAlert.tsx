import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CircleAlertIcon } from "lucide-react";
import { api } from "../api/client";
import type { StillListedItem } from "../api/directConnections";
import { pollMs } from "../api/polling";
import { marketplaceName } from "./marketplaceNames";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";

const STILL_LISTED_POLL_MS = 60_000;
const SHOWN = 3;

function listJoin(names: string[]): string {
  if (names.length <= 1) return names.join("");
  return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

export function stillListedLine(item: StillListedItem): string {
  const where = listJoin(item.stillListed.map((entry) => marketplaceName(entry.marketplace)));
  const failed = item.stillListed.filter((entry) => entry.state === "failed").length;
  return `Sold on ${marketplaceName(item.soldOn)}, still on ${where}${failed ? ` (${failed === 1 ? "delist failed" : `${failed} delists failed`})` : ""}`;
}

/** Recently sold items still listed on another marketplace. */
export function StillListedAlert({ onOpen }: { onOpen: (convId: string) => void }) {
  const [expanded, setExpanded] = useState(false);
  const { data } = useQuery({
    queryKey: ["marketplace-sales", "still-listed"],
    queryFn: ({ signal }) => api.marketplaceSales.stillListed({ signal }),
    refetchInterval: () => pollMs(STILL_LISTED_POLL_MS),
  });
  const items = data?.items ?? [];
  if (!items.length) return null;
  const more = items.length - SHOWN;
  const shown = expanded ? items : items.slice(0, SHOWN);
  return (
    <Alert variant="error" role="alert" className="mx-(--sidebar-content-inset) shrink-0">
      <CircleAlertIcon />
      <AlertTitle>
        {items.length === 1 ? "A sold item is still listed" : `${items.length} sold items are still listed`}
      </AlertTitle>
      <AlertDescription className="gap-1.5">
        <ul className="flex max-h-60 flex-col gap-1 overflow-y-auto">
          {shown.map((item) => (
            <li key={item.conversationId}>
              <button type="button" className="text-left underline-offset-2 hover:underline" onClick={() => onOpen(item.conversationId)}>
                <span className="font-medium text-foreground">{item.title || "Untitled item"}</span>
                <span className="block text-xs">{stillListedLine(item)}</span>
                <span className="block text-xs">
                  {data?.autodelist && item.detected
                    ? "Anteroom retries eligible delists for up to two weeks after the sale."
                    : "Delist this item under Marketplaces."}
                </span>
              </button>
            </li>
          ))}
        </ul>
        {more > 0 ? (
          <button type="button" aria-expanded={expanded} className="self-start text-xs underline"
            onClick={() => setExpanded((value) => !value)}>
            {expanded ? "Show fewer items" : `Show ${more} more ${more === 1 ? "item" : "items"}`}
          </button>
        ) : null}
      </AlertDescription>
    </Alert>
  );
}
