import { describe, expect, it } from "vitest";
import { KEY_PROVIDER_IDS, LISTING_PROVIDER_IDS, isKeyProviderId, isListingProviderId } from "./keyProviders";

describe("provider ids", () => {
  it("lists every API-key provider once among the listing providers", () => {
    for (const id of KEY_PROVIDER_IDS) expect(LISTING_PROVIDER_IDS.filter((other) => other === id)).toHaveLength(1);
    expect(LISTING_PROVIDER_IDS).toHaveLength(new Set(LISTING_PROVIDER_IDS).size);
  });

  it("tells key providers from sign-ins and unknown names", () => {
    expect(isKeyProviderId("anthropic")).toBe(true);
    expect(isKeyProviderId("claude")).toBe(false);
    expect(isListingProviderId("claude")).toBe(true);
    expect(isListingProviderId("xiaomi-mimo")).toBe(false);
    expect(isListingProviderId(undefined)).toBe(false);
  });
});
