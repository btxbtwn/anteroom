import type { KeyProviderId, ListingProviderId } from "../api/types";

/** API-key providers in the order Settings lists them; mirrors server ai_key_catalog.py. */
export const KEY_PROVIDER_IDS: readonly KeyProviderId[] = ["openai", "anthropic", "gemini", "xai", "mistral", "openrouter"];

export const KEY_PROVIDER_LABELS: Record<KeyProviderId, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  gemini: "Google Gemini",
  xai: "xAI Grok",
  mistral: "Mistral",
  openrouter: "OpenRouter",
};

/** Every listing provider: sign-ins first, then API keys, then the SDK and MiMo keys. */
export const LISTING_PROVIDER_IDS: readonly ListingProviderId[] = ["chatgpt", "claude", ...KEY_PROVIDER_IDS, "cursor", "mimo"];

export function isKeyProviderId(value: unknown): value is KeyProviderId {
  return typeof value === "string" && (KEY_PROVIDER_IDS as readonly string[]).includes(value);
}

export function isListingProviderId(value: unknown): value is ListingProviderId {
  return typeof value === "string" && (LISTING_PROVIDER_IDS as readonly string[]).includes(value);
}
