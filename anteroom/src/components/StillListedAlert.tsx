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

/** Sold items still listed on another marketplace, until each is delisted or marked not listed. */
export function StillListedAlert({ onOpen }: { onOpen: (convId: string) => void }) {
  const { data } = useQuery({
    queryKey: ["marketplace-sales", "still-listed"],
    queryFn: ({ signal }) => api.marketplaceSales.stillListed({ signal }),
    refetchInterval: () => pollMs(STILL_LISTED_POLL_MS),
  });
  const items = data?.items ?? [];
  if (!items.length) return null;
  const more = items.length - SHOWN;
  return (
    <Alert variant="error" role="alert" className="mx-(--sidebar-content-inset) shrink-0">
      <CircleAlertIcon />
      <AlertTitle>
        {items.length === 1 ? "A sold item is still listed" : `${items.length} sold items are still listed`}
      </AlertTitle>
      <AlertDescription className="gap-1.5">
        <ul className="flex flex-col gap-1">
          {items.slice(0, SHOWN).map((item) => (
            <li key={item.conversationId}>
              <button type="button" className="text-left underline-offset-2 hover:underline" onClick={() => onOpen(item.conversationId)}>
                <span className="font-medium text-foreground">{item.title || "Untitled item"}</span>
                <span className="block text-xs">{stillListedLine(item)}</span>
              </button>
            </li>
          ))}
        </ul>
        {more > 0 ? <p className="text-xs">and {more} more</p> : null}
        <p className="text-xs">
          {data?.autodelist
            ? "Anteroom keeps trying to delist them. Open an item to see why a delist failed."
            : "Open each item and delist it under Marketplaces."}
        </p>
      </AlertDescription>
    </Alert>
  );
}
