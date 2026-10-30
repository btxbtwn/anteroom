/** Only these reviewed browser operations may leave the authenticated HTTP API. */
import type { MarketplaceForm } from "./types";
export interface HostedBrowser {
  form: (conversationId: string, marketplace: string, revisionId?: string | null) => Promise<MarketplaceForm>;
}
let browser: HostedBrowser | null = null;
export function installHostedBrowser(value: HostedBrowser) {
  browser = value;
  return () => { if (browser === value) browser = null; };
}
export function hostedBrowser() { return browser; }
