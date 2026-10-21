import { describe, expect, it } from "vitest";
import type { ListingProviderId } from "../../api/types";
import {
  adjacentModelPickerProvider,
  modelPickerCommandFromEvent,
  modelPickerJumpLabel,
} from "./ModelPickerContent";
import { modelPickerModelKey, parseModelPickerModelKey } from "./modelPickerKeys";
import { sortProviderModelItems, providerModelKey } from "./modelOrdering";
import {
  activeProviderId,
  effortOptions,
  modelNameFromSlug,
  providerModelOptions,
  providerOrderFor,
  toggleFavorite,
  type ProviderInstanceEntry,
} from "./useComposerModel";

function entry(instanceId: ListingProviderId, ready = true): ProviderInstanceEntry {
  return { instanceId, displayName: instanceId, ready, message: ready ? null : "Not ready." };
}

const key = (event: Partial<KeyboardEvent>) => ({
  key: "",
  metaKey: false,
  ctrlKey: false,
  altKey: false,
  shiftKey: false,
  ...event,
});

describe("model names", () => {
  it("names catalog slugs that come without a display name", () => {
    expect(modelNameFromSlug("gpt-5.5")).toBe("GPT-5.5");
    expect(modelNameFromSlug("gpt-5.3-codex")).toBe("GPT-5.3-Codex");
    expect(modelNameFromSlug("mimo-v2.5-pro")).toBe("MiMo-V2.5-Pro");
  });

  it("keeps a saved model the catalog no longer lists, marked unavailable", () => {
    const options = providerModelOptions([{ slug: "auto", name: "Auto" }], "grok-4.5");
    expect(options).toEqual([
      { slug: "auto", name: "Auto" },
      { slug: "grok-4.5", name: "Grok-4.5", isUnavailable: true },
    ]);
    expect(providerModelOptions([{ slug: "auto", name: "Auto" }], "auto")).toHaveLength(1);
  });
});

describe("favorites", () => {
  it("stars and unstars one provider's model without touching the others", () => {
    const starred = toggleFavorite([], "cursor", "grok-4.7");
    expect(starred).toEqual([{ provider: "cursor", model: "grok-4.7" }]);
    const both = toggleFavorite(starred, "claude", "grok-4.7");
    expect(both).toHaveLength(2);
    expect(toggleFavorite(both, "cursor", "grok-4.7")).toEqual([{ provider: "claude", model: "grok-4.7" }]);
  });

  it("lists favorites first inside a provider, in catalog order otherwise", () => {
    const items = ["auto", "grok-4.7", "composer-2.5"].map((slug) => ({ slug, instanceId: "cursor" as const }));
    const sorted = sortProviderModelItems(items, {
      favoriteModelKeys: new Set([providerModelKey("cursor", "composer-2.5")]),
      groupFavorites: true,
    });
    expect(sorted.map((item) => item.slug)).toEqual(["composer-2.5", "auto", "grok-4.7"]);
  });

  it("orders the favorites tab by provider", () => {
    const items = [
      { slug: "auto", instanceId: "cursor" as const },
      { slug: "gpt-5.5", instanceId: "chatgpt" as const },
    ];
    const sorted = sortProviderModelItems(items, { instanceOrder: ["chatgpt", "claude", "cursor", "mimo"] });
    expect(sorted.map((item) => item.slug)).toEqual(["gpt-5.5", "auto"]);
  });
});

describe("picker keyboard", () => {
  it("labels the first nine rows with T3's jump shortcuts", () => {
    expect(modelPickerJumpLabel(0, true)).toBe("⌘1");
    expect(modelPickerJumpLabel(8, false)).toBe("Ctrl+9");
  });

  it("reads mod+1…9 as picks and mod+shift+arrows as provider switches", () => {
    expect(modelPickerCommandFromEvent(key({ key: "3", metaKey: true }), true)).toEqual({ kind: "jump", index: 2 });
    expect(modelPickerCommandFromEvent(key({ key: "3", ctrlKey: true }), true)).toBeNull();
    expect(modelPickerCommandFromEvent(key({ key: "3", ctrlKey: true }), false)).toEqual({ kind: "jump", index: 2 });
    expect(modelPickerCommandFromEvent(key({ key: "0", metaKey: true }), true)).toBeNull();
    expect(modelPickerCommandFromEvent(key({ key: "ArrowDown", metaKey: true, shiftKey: true }), true)).toEqual({
      kind: "provider",
      direction: 1,
    });
    expect(modelPickerCommandFromEvent(key({ key: "a" }), true)).toBeNull();
  });

  it("cycles favorites and ready providers, skipping the ones not set up", () => {
    const entries = [entry("chatgpt", false), entry("claude"), entry("cursor"), entry("mimo", false)];
    expect(adjacentModelPickerProvider({ entries, selectedInstanceId: "favorites", direction: 1 })).toBe("claude");
    expect(adjacentModelPickerProvider({ entries, selectedInstanceId: "cursor", direction: 1 })).toBe("favorites");
    expect(adjacentModelPickerProvider({ entries, selectedInstanceId: "favorites", direction: -1 })).toBe("cursor");
  });

  it("round-trips a model key", () => {
    expect(parseModelPickerModelKey(modelPickerModelKey("cursor", "claude-opus-5.5"))).toEqual({
      instanceId: "cursor",
      slug: "claude-opus-5.5",
    });
  });
});

describe("provider settings", () => {
  it("reads the provider in use from the status, falling back to the primary", () => {
    expect(activeProviderId({ provider: "xiaomi-mimo", primary: "chatgpt" })).toBe("mimo");
    expect(activeProviderId({ provider: "none", primary: "cursor" })).toBe("cursor");
  });

  it("keeps the old primary as the fallback when switching", () => {
    expect(providerOrderFor("cursor", { primary: "claude", fallback: "none" })).toEqual({
      primary: "cursor",
      fallback: "none",
    });
    expect(providerOrderFor("cursor", { primary: "claude", fallback: "cursor" })).toEqual({
      primary: "cursor",
      fallback: "claude",
    });
  });

  it("offers Default only where the provider has one", () => {
    expect(effortOptions(["low", "high"], true).map((option) => option.value)).toEqual(["default", "low", "high"]);
    expect(effortOptions(["low"], false)).toEqual([{ value: "low", label: "Low" }]);
  });
});
