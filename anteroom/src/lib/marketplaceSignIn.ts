import type { DirectMarketplace } from "../api/directConnections";

export interface MarketplaceSignInReturn {
  marketplace: DirectMarketplace;
  state: string;
  claim: string;
}

/**
 * After the seller approves eBay or Etsy, the relay sends this browser back
 * with a one-time claim. Take it, and drop it from the address bar so it is not
 * kept in history or sent anywhere else.
 */
export function takeMarketplaceSignIn(target: Window = window): MarketplaceSignInReturn | null {
  const url = new URL(target.location.href);
  const marketplace = url.searchParams.get("marketplace");
  const state = url.searchParams.get("state");
  const claim = url.searchParams.get("claim");
  if ((marketplace !== "ebay" && marketplace !== "etsy") || !state || !claim) return null;
  for (const name of ["marketplace", "state", "claim"]) url.searchParams.delete(name);
  target.history.replaceState(null, "", url.pathname + url.search + url.hash);
  return { marketplace, state, claim };
}
