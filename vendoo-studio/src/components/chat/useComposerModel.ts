import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { ListingProviderId, ModelFavorite, ProviderStatus } from "../../api/types";
import type { ModelEsque } from "./providerIconUtils";

/**
 * The data behind the composer's model picker: every provider, its models,
 * the one in use, its effort levels and the starred models.
 *
 * The provider, model and effort are the same settings Settings → Providers
 * edits. There is one source of truth: no per-conversation choice.
 */

export const EFFORT_LABELS: Record<string, string> = {
  default: "Default",
  none: "Off",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
};

export const PROVIDER_LABELS: Record<ListingProviderId, string> = {
  chatgpt: "ChatGPT",
  claude: "Claude",
  cursor: "Cursor",
  mimo: "Xiaomi MiMo",
};

const MIMO_LISTING_MODEL = "mimo-v2.5-pro";
const FALLBACK_CHATGPT_EFFORTS = ["none", "low", "medium", "high", "xhigh"];

/** One provider in the picker's rail, the role T3 Code's ProviderInstanceEntry plays. */
export interface ProviderInstanceEntry {
  instanceId: ListingProviderId;
  displayName: string;
  /** Signed in or keyed; a provider that is not ready cannot be picked. */
  ready: boolean;
  /** Why the provider cannot be picked, or why its models did not load. */
  message: string | null;
}

export interface EffortOption {
  value: string;
  label: string;
  isDefault?: boolean;
}

export interface EffortChoice {
  options: EffortOption[];
  value: string;
}

/** Which provider the server is actually using, in the order Settings picks. */
export function activeProviderId(status: Pick<ProviderStatus, "provider" | "primary"> | undefined): ListingProviderId | null {
  if (!status) return null;
  const name = status.provider;
  if (name === "xiaomi-mimo") return "mimo";
  if (name === "chatgpt" || name === "claude" || name === "cursor") return name;
  return status.primary ?? null;
}

/** Saving `next` as the primary keeps the old primary as the fallback, unless it is next itself. */
export function providerOrderFor(
  next: ListingProviderId,
  status: Pick<ProviderStatus, "primary" | "fallback">,
): { primary: ListingProviderId; fallback: ListingProviderId | "none" } {
  if (status.primary === next) return { primary: next, fallback: status.fallback };
  const fallback = status.fallback === next ? status.primary : status.fallback;
  return { primary: next, fallback };
}

const UPPERCASE_WORDS = new Set(["gpt", "oss"]);

/**
 * A readable name for a model id that has no display name of its own:
 * `gpt-5.3-codex` → `GPT-5.3-Codex`, `mimo-v2.5-pro` → `MiMo-V2.5-Pro`.
 */
export function modelNameFromSlug(slug: string): string {
  return slug
    .split("-")
    .map((part) => {
      const lower = part.toLowerCase();
      if (UPPERCASE_WORDS.has(lower)) return part.toUpperCase();
      if (lower === "mimo") return "MiMo";
      return part.charAt(0).toUpperCase() + part.slice(1);
    })
    .join("-");
}

export function effortOptions(values: readonly string[], withDefault: boolean): EffortOption[] {
  return [
    ...(withDefault ? [{ value: "default", label: EFFORT_LABELS.default, isDefault: true }] : []),
    ...values.map((value) => ({ value, label: EFFORT_LABELS[value] ?? value })),
  ];
}

/** Rows for one provider: the catalog, plus the saved model if the catalog no longer lists it. */
export function providerModelOptions(
  models: readonly { slug: string; name: string }[],
  saved: string,
): ModelEsque[] {
  const options: ModelEsque[] = models.map((model) => ({ slug: model.slug, name: model.name }));
  if (saved && !options.some((option) => option.slug === saved)) {
    options.push({ slug: saved, name: modelNameFromSlug(saved), isUnavailable: true });
  }
  return options;
}

function errorMessage(error: unknown): string | null {
  return error instanceof Error ? error.message : null;
}

const SETTINGS_UI_KEY = ["settings-ui"];

/** Starred models, kept with the other UI preferences so every Studio sees the same stars. */
export function toggleFavorite(
  favorites: readonly ModelFavorite[],
  provider: ListingProviderId,
  model: string,
): ModelFavorite[] {
  const exists = favorites.some((favorite) => favorite.provider === provider && favorite.model === model);
  return exists
    ? favorites.filter((favorite) => !(favorite.provider === provider && favorite.model === model))
    : [...favorites, { provider, model }];
}

export function useModelFavorites() {
  const queryClient = useQueryClient();
  const prefs = useQuery({ queryKey: SETTINGS_UI_KEY, queryFn: api.settings.ui });
  type Prefs = typeof prefs.data;
  const favorites = prefs.data?.model_favorites ?? [];
  const save = useMutation({
    mutationFn: (next: ModelFavorite[]) => api.settings.setUi({ model_favorites: next }),
    onMutate: async (next) => {
      await queryClient.cancelQueries({ queryKey: SETTINGS_UI_KEY });
      const previous = queryClient.getQueryData<Prefs>(SETTINGS_UI_KEY);
      if (previous) queryClient.setQueryData(SETTINGS_UI_KEY, { ...previous, model_favorites: next });
      return { previous };
    },
    onError: (_error, _next, context) => {
      if (context?.previous) queryClient.setQueryData(SETTINGS_UI_KEY, context.previous);
    },
    onSuccess: (payload) => queryClient.setQueryData(SETTINGS_UI_KEY, payload),
  });
  return {
    favorites,
    toggle: (provider: ListingProviderId, model: string) => {
      const current = queryClient.getQueryData<Prefs>(SETTINGS_UI_KEY)?.model_favorites ?? favorites;
      save.mutate(toggleFavorite(current, provider, model));
    },
  };
}

export function useComposerModel() {
  const queryClient = useQueryClient();
  const status = useQuery({ queryKey: ["settings-provider"], queryFn: api.settings.provider, staleTime: 30_000 });
  const chatgptReady = Boolean(status.data?.chatgpt?.signed_in);
  const claudeReady = Boolean(status.data?.claude?.signed_in);
  const cursorReady = Boolean(status.data?.masked_cursor_key);
  const mimoReady = Boolean(status.data?.masked_key);
  // Same query keys as Settings → Providers, so either side refreshes the other.
  const chatgpt = useQuery({ queryKey: ["chatgpt-models"], queryFn: api.settings.chatgptModels, enabled: chatgptReady, staleTime: 5 * 60_000 });
  const claude = useQuery({ queryKey: ["claude-models"], queryFn: api.settings.claudeModels, enabled: claudeReady, staleTime: 5 * 60_000 });
  const cursor = useQuery({ queryKey: ["cursor-models"], queryFn: api.settings.cursorModels, enabled: cursorReady, staleTime: 5 * 60_000 });

  const active = activeProviderId(status.data);

  const entries: ProviderInstanceEntry[] = [
    {
      instanceId: "chatgpt",
      displayName: PROVIDER_LABELS.chatgpt,
      ready: chatgptReady,
      message: chatgptReady
        ? chatgpt.data?.error || errorMessage(chatgpt.error)
        : "Not ready. Sign in to ChatGPT in Settings → Providers.",
    },
    {
      instanceId: "claude",
      displayName: PROVIDER_LABELS.claude,
      ready: claudeReady,
      message: claudeReady
        ? claude.data?.error || errorMessage(claude.error)
        : "Not ready. Sign in to Claude in Settings → Providers.",
    },
    {
      instanceId: "cursor",
      displayName: PROVIDER_LABELS.cursor,
      ready: cursorReady,
      message: cursorReady
        ? cursor.data?.error || errorMessage(cursor.error)
        : "Not ready. Add a Cursor API key in Settings → Providers.",
    },
    {
      instanceId: "mimo",
      displayName: PROVIDER_LABELS.mimo,
      ready: mimoReady,
      message: mimoReady ? null : "Not ready. Add a Xiaomi MiMo API key in Settings → Providers.",
    },
  ];

  const savedModel: Record<ListingProviderId, string> = {
    chatgpt: chatgpt.data?.listing_model ?? "",
    claude: claude.data?.listing_model ?? "",
    cursor: cursor.data?.listing_model ?? "",
    mimo: MIMO_LISTING_MODEL,
  };

  const modelOptionsByInstance = new Map<ListingProviderId, ModelEsque[]>([
    [
      "chatgpt",
      chatgptReady
        ? providerModelOptions(
            (chatgpt.data?.models ?? []).map((slug) => ({ slug, name: modelNameFromSlug(slug) })),
            savedModel.chatgpt,
          )
        : [],
    ],
    [
      "claude",
      claudeReady
        ? providerModelOptions(
            (claude.data?.models ?? []).map(({ value, label }) => ({ slug: value, name: label })),
            savedModel.claude,
          )
        : [],
    ],
    [
      "cursor",
      cursorReady
        ? providerModelOptions(
            (cursor.data?.models ?? []).map((slug) => ({
              slug,
              name: cursor.data?.labels?.[slug] || (slug === "auto" ? "Auto" : modelNameFromSlug(slug)),
            })),
            savedModel.cursor,
          )
        : [],
    ],
    ["mimo", mimoReady ? [{ slug: MIMO_LISTING_MODEL, name: modelNameFromSlug(MIMO_LISTING_MODEL) }] : []],
  ]);

  const model = active ? savedModel[active] || status.data?.listing_model || "" : "";

  // Effort only offers what the chosen model supports.
  const effort: EffortChoice | null = (() => {
    if (active === "chatgpt" && chatgpt.data) {
      const values = chatgpt.data.reasoning_efforts?.length ? chatgpt.data.reasoning_efforts : FALLBACK_CHATGPT_EFFORTS;
      return { options: effortOptions(values, false), value: values.includes(chatgpt.data.reasoning_effort) ? chatgpt.data.reasoning_effort : values[0] };
    }
    if (active === "claude" && claude.data?.efforts.length) {
      return { options: effortOptions(claude.data.efforts, true), value: claude.data.effort ?? "default" };
    }
    if (active === "cursor" && cursor.data?.reasoning) {
      return { options: cursor.data.reasoning.options, value: cursor.data.reasoning.value };
    }
    return null;
  })();

  const refresh = () => {
    for (const key of ["settings-provider", "chatgpt-models", "claude-models", "cursor-models", "status"]) {
      void queryClient.invalidateQueries({ queryKey: [key] });
    }
  };

  const selectModel = useMutation({
    mutationFn: async ({ provider, model: next }: { provider: ListingProviderId; model: string }) => {
      if (status.data && provider !== active) {
        await api.settings.setPreferredProvider(providerOrderFor(provider, status.data));
      }
      if (provider === "chatgpt") await api.settings.setChatGPTModels({ listing_model: next });
      else if (provider === "claude") await api.settings.setClaudeModels({ listing_model: next });
      else if (provider === "cursor") await api.settings.setCursorModels({ listing_model: next });
    },
    onSettled: refresh,
  });

  const selectEffort = useMutation({
    mutationFn: async (value: string) => {
      if (active === "chatgpt") await api.settings.setChatGPTModels({ reasoning_effort: value });
      else if (active === "claude") await api.settings.setClaudeModels({ effort: value });
      else if (active === "cursor") await api.settings.setCursorModels({ reasoning_effort: value });
    },
    onSettled: refresh,
  });

  return {
    loading: status.isLoading,
    entries,
    modelOptionsByInstance,
    active,
    model,
    effort,
    selectModel: (provider: ListingProviderId, next: string) => selectModel.mutate({ provider, model: next }),
    selectEffort: (value: string) => selectEffort.mutate(value),
    pending: selectModel.isPending || selectEffort.isPending,
    error: errorMessage(selectModel.error) || errorMessage(selectEffort.error),
  };
}
