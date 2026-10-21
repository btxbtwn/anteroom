import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { ListingProviderId, ProviderStatus } from "../../api/types";

/**
 * The provider, model and effort the listing chat, generation and the
 * assistant use, read and saved through the same settings Settings → Providers
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
  mimo: "MiMo",
};

const PROVIDER_ORDER: ListingProviderId[] = ["chatgpt", "claude", "cursor", "mimo"];
const MIMO_LISTING_MODEL = "mimo-v2.5-pro";
const FALLBACK_CHATGPT_EFFORTS = ["none", "low", "medium", "high", "xhigh"];

export interface ModelOption {
  value: string;
  label: string;
}

export interface ProviderModels {
  id: ListingProviderId;
  label: string;
  /** Signed in or keyed; a provider that is not ready cannot be picked. */
  ready: boolean;
  models: ModelOption[];
  /** The saved listing model for this provider. */
  model: string;
  loading: boolean;
  error: string | null;
}

export interface EffortChoice {
  options: ModelOption[];
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

export function modelLabel(provider: ListingProviderId, value: string): string {
  if (provider === "cursor" && value === "auto") return "Auto";
  return value;
}

/** The composer offers one current model from each of these, newest first within a family. */
const CURSOR_PICKER_FAMILIES = ["auto", "composer", "grok", "claude-sonnet", "claude-opus", "gpt"] as const;

function cursorPickerFamily(id: string): (typeof CURSOR_PICKER_FAMILIES)[number] | null {
  if (id === "auto") return "auto";
  // Fast, mini and coding variants stay available in Settings, not this menu.
  if (/fast|mini|nano|flash|codex|haiku/i.test(id)) return null;
  if (id.startsWith("composer")) return "composer";
  if (id.startsWith("grok")) return "grok";
  if (id.startsWith("claude-sonnet")) return "claude-sonnet";
  if (id.startsWith("claude-opus")) return "claude-opus";
  if (id.startsWith("gpt-")) return "gpt";
  return null;
}

function newerModelId(left: string, right: string): string {
  const leftParts = (left.match(/\d+/g) ?? []).map(Number);
  const rightParts = (right.match(/\d+/g) ?? []).map(Number);
  const length = Math.max(leftParts.length, rightParts.length);
  for (let index = 0; index < length; index += 1) {
    const diff = (leftParts[index] ?? 0) - (rightParts[index] ?? 0);
    if (diff !== 0) return diff > 0 ? left : right;
  }
  return left;
}

/**
 * The Cursor catalog is every model the account can call. The composer shows
 * one current model per family, plus whatever is already selected.
 */
export function cursorPickerModels(models: readonly ModelOption[], selected: string): ModelOption[] {
  const best = new Map<string, ModelOption>();
  for (const model of models) {
    const family = cursorPickerFamily(model.value);
    if (!family) continue;
    const current = best.get(family);
    if (!current || newerModelId(model.value, current.value) === model.value) best.set(family, model);
  }
  const picked = CURSOR_PICKER_FAMILIES.flatMap((family) => {
    const model = best.get(family);
    return model ? [model] : [];
  });
  if (selected && !picked.some((model) => model.value === selected)) {
    const saved = models.find((model) => model.value === selected);
    if (saved) picked.push(saved);
  }
  return picked;
}

export function effortOptions(values: readonly string[], withDefault: boolean): ModelOption[] {
  return [
    ...(withDefault ? [{ value: "default", label: EFFORT_LABELS.default }] : []),
    ...values.map((value) => ({ value, label: EFFORT_LABELS[value] ?? value })),
  ];
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
  const providers: ProviderModels[] = PROVIDER_ORDER.map((id) => {
    switch (id) {
      case "chatgpt":
        return {
          id,
          label: PROVIDER_LABELS[id],
          ready: chatgptReady,
          models: (chatgpt.data?.models ?? []).map((value) => ({ value, label: value })),
          model: chatgpt.data?.listing_model ?? "",
          loading: chatgpt.isLoading,
          error: chatgpt.data?.error || (chatgpt.error as Error | null)?.message || null,
        };
      case "claude":
        return {
          id,
          label: PROVIDER_LABELS[id],
          ready: claudeReady,
          models: (claude.data?.models ?? []).map(({ value, label }) => ({ value, label })),
          model: claude.data?.listing_model ?? "",
          loading: claude.isLoading,
          error: claude.data?.error || (claude.error as Error | null)?.message || null,
        };
      case "cursor":
        return {
          id,
          label: PROVIDER_LABELS[id],
          ready: cursorReady,
          models: cursorPickerModels(
            (cursor.data?.models ?? []).map((value) => ({ value, label: modelLabel("cursor", value) })),
            cursor.data?.listing_model ?? "",
          ),
          model: cursor.data?.listing_model ?? "",
          loading: cursor.isLoading,
          error: cursor.data?.error || (cursor.error as Error | null)?.message || null,
        };
      case "mimo":
        return {
          id,
          label: PROVIDER_LABELS[id],
          ready: mimoReady,
          models: [{ value: MIMO_LISTING_MODEL, label: MIMO_LISTING_MODEL }],
          model: MIMO_LISTING_MODEL,
          loading: false,
          error: null,
        };
    }
  });
  const current = providers.find((provider) => provider.id === active) ?? null;
  const currentModel = current ? current.model || status.data?.listing_model || "" : "";

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
    mutationFn: async ({ provider, model }: { provider: ListingProviderId; model: string }) => {
      if (status.data && provider !== active) {
        await api.settings.setPreferredProvider(providerOrderFor(provider, status.data));
      }
      if (provider === "chatgpt") await api.settings.setChatGPTModels({ listing_model: model });
      else if (provider === "claude") await api.settings.setClaudeModels({ listing_model: model });
      else if (provider === "cursor") await api.settings.setCursorModels({ listing_model: model });
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
    providers,
    active,
    current,
    currentModel,
    effort,
    selectModel: (provider: ListingProviderId, model: string) => selectModel.mutate({ provider, model }),
    selectEffort: (value: string) => selectEffort.mutate(value),
    pending: selectModel.isPending || selectEffort.isPending,
    error: (selectModel.error as Error | null)?.message || (selectEffort.error as Error | null)?.message || null,
  };
}
