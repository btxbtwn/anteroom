import { createContext } from "react";

/** Messages already displayed above the selected marketplace's form. */
export const MarketplaceMessagesContext = createContext<readonly string[]>([]);

export function uniqueMarketplaceMessages(messages: readonly (string | null | undefined)[], shown: readonly string[] = []): string[] {
  const key = (message: string) => message.trim().replace(/\s+/g, " ").replace(/^(?:eBay|Etsy|Poshmark|Mercari|Depop|Sellwild|Vinted|Facebook) said:\s*/i, "");
  const seen = new Set(shown.map(key));
  return messages.filter((message): message is string => {
    if (!message?.trim() || seen.has(key(message))) return false;
    seen.add(key(message));
    return true;
  });
}
