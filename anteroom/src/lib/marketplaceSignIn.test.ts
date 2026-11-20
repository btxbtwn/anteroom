import { describe, expect, it, vi } from "vitest";
import { takeMarketplaceSignIn } from "./marketplaceSignIn";

function page(href: string) {
  const replaceState = vi.fn();
  const target = { location: new URL(href), history: { replaceState } };
  return { target: target as unknown as Window, replaceState };
}

describe("returning from eBay or Etsy sign-in", () => {
  it("takes the claim and removes it from the address bar", () => {
    const { target, replaceState } = page("https://myanteroom.com/?marketplace=etsy&state=s1&claim=c1&listing=7#top");
    expect(takeMarketplaceSignIn(target)).toEqual({ marketplace: "etsy", state: "s1", claim: "c1" });
    expect(replaceState).toHaveBeenCalledWith(null, "", "/?listing=7#top");
  });

  it("ignores other links and unknown marketplaces", () => {
    for (const href of [
      "https://myanteroom.com/",
      "https://myanteroom.com/?listing=7",
      "https://myanteroom.com/?marketplace=ebay&state=s1",
      "https://myanteroom.com/?marketplace=poshmark&state=s1&claim=c1",
    ]) {
      const { target, replaceState } = page(href);
      expect(takeMarketplaceSignIn(target)).toBeNull();
      expect(replaceState).not.toHaveBeenCalled();
    }
  });
});
