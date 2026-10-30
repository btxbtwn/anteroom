// Providers settings laid out like T3 Code's Providers page
// (components/settings/ProviderSettingsPanel.tsx and ProviderInstanceCard.tsx,
// https://github.com/pingdotgg/t3code, MIT License, (c) 2026 T3 Tools Inc.):
// a provider list beside the selected provider's editor, with Studio's own
// sign-in flows, API keys, listing order and per-provider models mapped onto it.
import { type ReactNode, useCallback, useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckIcon, ExternalLinkIcon, KeyRoundIcon, LogInIcon, PlugZapIcon, TerminalIcon, Trash2Icon } from "lucide-react";
import { useWorkspaceRuntime } from "../../app/runtime";
import { api } from "../../api/client";
import type { AIKeyProvider, KeyProviderId, ListingProviderId, PhotoProviderChoice, ProviderStatus } from "../../api/types";
import { cn } from "../../lib/utils";
import { ClaudeLoginTerminal } from "../ClaudeLoginTerminal";
import { KEY_PROVIDER_IDS, KEY_PROVIDER_LABELS, LISTING_PROVIDER_IDS, isKeyProviderId, isListingProviderId } from "../keyProviders";
import { ProviderInstanceIcon, resolveProviderId } from "../chat/ProviderInstanceIcon";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { ScrollArea } from "../ui/scroll-area";
import { Spinner } from "../ui/spinner";
import {
  SettingsCode,
  SettingsGroup,
  SettingsNote,
  SettingsRow,
  SettingsSection,
  SettingsSelect,
  type SettingsSelectOption,
} from "./SettingsLayout";

type ListingFallbackId = ListingProviderId | "none";
type ModelOption = { value: string; label: string };
type StatusTone = "ready" | "idle" | "warning" | "error";

const PROVIDER_IDS = LISTING_PROVIDER_IDS;

const PROVIDER_LABELS: Record<ListingProviderId, { short: string; full: string }> = {
  chatgpt: { short: "ChatGPT", full: "ChatGPT" },
  claude: { short: "Claude", full: "Claude" },
  mimo: { short: "MiMo", full: "Xiaomi MiMo" },
  cursor: { short: "Cursor", full: "Cursor" },
  ...(Object.fromEntries(
    KEY_PROVIDER_IDS.map((id) => [id, { short: KEY_PROVIDER_LABELS[id], full: KEY_PROVIDER_LABELS[id] }]),
  ) as Record<KeyProviderId, { short: string; full: string }>),
};

const REASONING_LABELS: Record<string, string> = {
  none: "Off",
  minimal: "Minimal",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
};

const STATUS_DOT: Record<StatusTone, string> = {
  ready: "bg-success",
  idle: "bg-muted-foreground/40",
  warning: "bg-warning",
  error: "bg-destructive",
};

// T3's provider card height: tall enough for the editor, inside the viewport.
const PROVIDER_CARD_HEIGHT =
  "@min-[48rem]/providers:h-[min(44rem,calc(100dvh-11rem))] @min-[48rem]/providers:min-h-[32rem]";

function providerLabel(choice: ListingFallbackId): string {
  return choice === "none" ? "None" : PROVIDER_LABELS[choice].short;
}

function providerOption<T extends string>(value: T, id: ListingProviderId, full = true): SettingsSelectOption<T> {
  const label = full ? PROVIDER_LABELS[id].full : PROVIDER_LABELS[id].short;
  return {
    value,
    label: (
      <>
        <ProviderInstanceIcon driverKind={id} displayName={label} className="size-4" iconClassName="size-4" />
        <span className="truncate">{label}</span>
      </>
    ),
  };
}

/** T3's provider card title icon (ProviderInstanceCard.tsx). */
function ProviderIcon({ id }: { id: ListingProviderId }) {
  return (
    <ProviderInstanceIcon
      driverKind={id}
      displayName={PROVIDER_LABELS[id].full}
      className="size-5"
      iconClassName="size-4 text-foreground/80"
    />
  );
}

function TestResult({ result }: { result: string | null }) {
  if (!result) return null;
  const ok = result.includes("successful");
  return (
    <span className={cn("inline-flex items-center gap-1", ok ? "text-success-foreground" : "text-destructive-foreground")}>
      {ok ? <CheckIcon className="size-3" /> : null}
      {result}
    </span>
  );
}

function useConnectionTest(run: () => Promise<{ ok: boolean; error?: string | null }>) {
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const test = async () => {
    setTesting(true);
    setResult(null);
    try {
      const outcome = await run();
      setResult(outcome.ok ? "Connection successful" : outcome.error || "Connection failed");
    } catch (err) {
      setResult(`Error: ${err instanceof Error ? err.message : String(err)}`);
    }
    setTesting(false);
  };
  return { testing, result, test, clear: () => setResult(null) };
}

function TestButton({ testing, onClick }: { testing: boolean; onClick: () => void }) {
  return (
    <Button size="xs" variant="outline" onClick={onClick} disabled={testing}>
      {testing ? <Spinner /> : <PlugZapIcon />}
      {testing ? "Testing" : "Test"}
    </Button>
  );
}

/** Secret entry for a provider key. Keys go straight to the Keychain via the API; only a masked form comes back. */
function ApiKeyField({
  placeholder,
  saving,
  onSave,
  label,
}: {
  placeholder: string;
  saving: boolean;
  onSave: (key: string) => void;
  label: string;
}) {
  const [value, setValue] = useState("");
  const save = () => {
    if (!value.trim()) return;
    onSave(value);
    setValue("");
  };
  return (
    <form
      className="flex min-w-0 items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        save();
      }}
    >
      <Input
        size="sm"
        font="mono"
        type="password"
        aria-label={label}
        value={value}
        onChange={(event) => setValue(event.target.value)}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
      />
      <Button type="submit" size="xs" variant="outline" disabled={!value.trim() || saving}>
        {saving ? <Spinner /> : <KeyRoundIcon />}
        {saving ? "Saving" : "Save"}
      </Button>
    </form>
  );
}

/** A provider's photo model, listing model and reasoning. */
function ProviderModelRows({
  provider,
  models,
  visionModel,
  listingModel,
  onModel,
  reasoning,
  reasoningDescription,
  pending,
  error,
}: {
  provider: ListingProviderId;
  models: ModelOption[];
  visionModel: string;
  listingModel: string;
  onModel: (model: { vision_model: string } | { listing_model: string }) => void;
  reasoning: { value: string; options: ModelOption[]; onChange: (value: string) => void } | null;
  reasoningDescription: string;
  pending: boolean;
  error?: string | null;
}) {
  const label = providerLabel(provider);
  const options = [...models];
  for (const value of [visionModel, listingModel]) {
    if (value && !options.some((option) => option.value === value)) options.push({ value, label: value });
  }
  const listingLabel = options.find((option) => option.value === listingModel)?.label ?? listingModel;
  const modelOptions = options.map((option) => ({ value: option.value, label: option.label }));
  return (
    <>
      <SettingsRow
        title="Photo model"
        description={`Reads product photos when ${label} does photo analysis.`}
        status={error ? <span className="text-destructive-foreground">{error}</span> : null}
        control={
          <SettingsSelect
            aria-label={`${label} photo model`}
            className="sm:w-56"
            value={visionModel}
            options={modelOptions}
            disabled={pending || options.length === 0}
            onChange={(value) => onModel({ vision_model: value })}
          />
        }
      />
      <SettingsRow
        title="Listing model"
        description={`Writes marketplace copy when ${label} is the listing AI.`}
        control={
          <SettingsSelect
            aria-label={`${label} listing model`}
            className="sm:w-56"
            value={listingModel}
            options={modelOptions}
            disabled={pending || options.length === 0}
            onChange={(value) => onModel({ listing_model: value })}
          />
        }
      />
      <SettingsRow
        title="Reasoning"
        description={reasoningDescription}
        control={
          reasoning ? (
            <SettingsSelect
              aria-label={`${label} reasoning`}
              className="sm:w-56"
              value={reasoning.value}
              options={reasoning.options}
              disabled={pending}
              onChange={reasoning.onChange}
            />
          ) : (
            <span className="text-xs text-muted-foreground">Not adjustable for {listingLabel}</span>
          )
        }
      />
    </>
  );
}

function BraveSearchSection({ chatgptSignedIn }: { chatgptSignedIn: boolean }) {
  const queryClient = useQueryClient();
  const connection = useConnectionTest(api.settings.testBrave);
  const { data: brave } = useQuery({
    queryKey: ["settings-brave"],
    queryFn: api.settings.brave,
  });
  const setKeyMutation = useMutation({
    mutationFn: (key: string) => api.settings.setBrave(key),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings-brave"] });
      connection.clear();
    },
  });
  const deleteKeyMutation = useMutation({
    mutationFn: () => api.settings.deleteBrave(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings-brave"] });
      connection.clear();
    },
  });
  const configured = Boolean(brave?.configured);

  return (
    <SettingsSection id="brave" title="Brave Search">
      <SettingsRow
        id="brave-api-key"
        title="API key"
        description={
          <>
            Used to look up sold comps when your listing model's web search fails, times out or finds no sales. Get a
            key at{" "}
            <a className="text-foreground underline-offset-4 hover:underline" href="https://api.search.brave.com" target="_blank" rel="noreferrer">
              api.search.brave.com
            </a>
            . Stored in macOS Keychain and never sent to the browser.
          </>
        }
        status={
          connection.result ? (
            <TestResult result={connection.result} />
          ) : setKeyMutation.isError ? (
            <span className="text-destructive-foreground">{(setKeyMutation.error as Error).message}</span>
          ) : configured ? (
            <span className="inline-flex items-center gap-1.5">
              <span className="size-1.5 rounded-full bg-success" aria-hidden />
              Fallback · <SettingsCode>{brave?.masked_key}</SettingsCode>
            </span>
          ) : chatgptSignedIn ? (
            "Fallback when your listing model's web search does not work"
          ) : (
            "Not configured — sold comps use your listing model's web search only"
          )
        }
        control={
          configured ? (
            <>
              <TestButton testing={connection.testing} onClick={connection.test} />
              <Button size="xs" variant="ghost-destructive" onClick={() => deleteKeyMutation.mutate()}>
                <Trash2Icon />
                Remove
              </Button>
            </>
          ) : null
        }
      >
        <ApiKeyField
          label="Brave Search API key"
          placeholder="BSA..."
          saving={setKeyMutation.isPending}
          onSave={(key) => setKeyMutation.mutate(key)}
        />
      </SettingsRow>
    </SettingsSection>
  );
}

type ProviderSummary = { tone: StatusTone; headline: string; detail?: string };

function summarizeProvider(
  id: ListingProviderId,
  provider: ProviderStatus | undefined,
  keyProvider: AIKeyProvider | undefined,
): ProviderSummary {
  if (isKeyProviderId(id)) {
    if (!keyProvider) return { tone: "idle", headline: "Checking" };
    return keyProvider.masked_key
      ? { tone: "ready", headline: keyProvider.sign_in ? "Connected" : "API key saved", detail: keyProvider.masked_key }
      : { tone: "idle", headline: keyProvider.sign_in ? "Not connected" : "No API key" };
  }
  if (!provider) return { tone: "idle", headline: "Checking" };
  if (id === "chatgpt") {
    const chatgpt = provider.chatgpt;
    if (chatgpt?.signed_in) return { tone: "ready", headline: "Signed in", detail: chatgpt.email ?? undefined };
    if (chatgpt?.pending) return { tone: "warning", headline: "Waiting for sign-in" };
    if (chatgpt?.error) return { tone: "error", headline: "Sign-in failed", detail: chatgpt.error };
    return { tone: "idle", headline: "Not signed in" };
  }
  if (id === "claude") {
    const claude = provider.claude;
    if (claude?.signed_in) return { tone: "ready", headline: "Signed in", detail: claude.email ?? undefined };
    if (claude && !claude.installed) return { tone: "warning", headline: "Needs Claude Code" };
    return { tone: "idle", headline: "Not signed in" };
  }
  const masked = id === "mimo" ? provider.masked_key : provider.masked_cursor_key;
  return masked ? { tone: "ready", headline: "API key saved", detail: masked } : { tone: "idle", headline: "No API key" };
}

function RoleBadges({
  id,
  primary,
  fallback,
  photoProvider,
}: {
  id: ListingProviderId;
  primary: ListingProviderId;
  fallback: ListingFallbackId;
  photoProvider: PhotoProviderChoice;
}) {
  return (
    <>
      {primary === id ? (
        <Badge variant="info" size="sm">
          Primary
        </Badge>
      ) : fallback === id ? (
        <Badge variant="outline" size="sm">
          Fallback
        </Badge>
      ) : null}
      {photoProvider === id ? (
        <Badge variant="outline" size="sm">
          Photos
        </Badge>
      ) : null}
    </>
  );
}

/** One row of the provider list, after T3's ProviderInstanceCard list mode. */
function ProviderListRow({
  id,
  summary,
  selected,
  onSelect,
  badges,
}: {
  id: ListingProviderId;
  summary: ProviderSummary;
  selected: boolean;
  onSelect: () => void;
  badges: ReactNode;
}) {
  const label = PROVIDER_LABELS[id].full;
  return (
    <div
      data-slot="settings-row"
      className={cn(
        "group relative flex min-h-16 items-center gap-3 px-3 py-3 transition-colors sm:px-4",
        selected ? "bg-muted/45" : "hover:bg-muted/25",
      )}
    >
      <button
        type="button"
        id={id}
        className="absolute inset-0 z-10 cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        onClick={onSelect}
        onFocus={onSelect}
        aria-label={`Select ${label}`}
        aria-pressed={selected}
      />
      <div className={cn("flex min-w-0 flex-1 items-start gap-3", summary.tone === "idle" && !selected && "opacity-70 group-hover:opacity-100")}>
        <ProviderIcon id={id} />
        <span className="min-w-0 flex-1">
          <span className="flex min-w-0 items-center gap-2">
            <span className="truncate text-sm font-medium text-foreground">{label}</span>
          </span>
          <span className="mt-0.5 flex items-center gap-1.5 text-xs leading-normal text-muted-foreground/80">
            <span className={cn("size-1.5 shrink-0 rounded-full", STATUS_DOT[summary.tone])} aria-hidden />
            <span className="truncate">{summary.headline}</span>
          </span>
        </span>
      </div>
      <span className="flex shrink-0 items-center gap-1">{badges}</span>
    </div>
  );
}

/** Setup and models for a provider the seller connects with their own API key. */
function KeyProviderEditor({
  id,
  entry,
  role,
  onChanged,
}: {
  id: KeyProviderId;
  entry: AIKeyProvider | undefined;
  role: string;
  onChanged: () => void;
}) {
  const { hosted } = useWorkspaceRuntime();
  const queryClient = useQueryClient();
  const label = KEY_PROVIDER_LABELS[id];
  const configured = Boolean(entry?.masked_key);
  const test = useConnectionTest(() => api.settings.testAIKey(id));
  const [signInUrl, setSignInUrl] = useState<string | null>(null);
  const changed = () => {
    test.clear();
    onChanged();
  };
  const setKey = useMutation({ mutationFn: (key: string) => api.settings.setAIKey(id, key), onSuccess: changed });
  const deleteKey = useMutation({ mutationFn: () => api.settings.deleteAIKey(id), onSuccess: changed });
  const signIn = useMutation({
    mutationFn: () => api.settings.openRouterSignIn(),
    onSuccess: ({ authorize_url }) => setSignInUrl(authorize_url),
  });
  const { data: models } = useQuery({
    queryKey: ["ai-key-models", id],
    queryFn: () => api.settings.aiKeyModels(id),
    enabled: configured,
    staleTime: 5 * 60_000,
  });
  const setModels = useMutation({
    mutationFn: (change: { vision_model?: string; listing_model?: string; reasoning_effort?: string }) =>
      api.settings.setAIKeyModels(id, change),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["ai-key-models", id] });
      onChanged();
    },
  });

  // The browser finishes the sign-in; check back until the key arrives.
  const waiting = Boolean(signInUrl) && !configured;
  useEffect(() => {
    if (!waiting) return;
    const timer = window.setInterval(onChanged, 2000);
    const stop = window.setTimeout(() => setSignInUrl(null), 10 * 60_000);
    return () => {
      window.clearInterval(timer);
      window.clearTimeout(stop);
    };
  }, [waiting, onChanged]);

  const error = setKey.error ?? deleteKey.error ?? signIn.error;
  const searchNote = entry?.web_search
    ? ` When ${label} writes your listings it also searches the web for sold comps.`
    : hosted ? " Sold comps require a provider with web search." : " Sold comps need a Brave Search key, since this provider does not search the web here.";

  return (
    <>
      <SettingsSection title="Setup">
        {entry?.sign_in && !hosted ? (
          <SettingsRow
            title={`Sign in with ${label}`}
            description={`Approve Anteroom in your browser and ${label} hands it a key that bills your own account.`}
            status={
              waiting ? (
                <span className="inline-flex flex-wrap items-center gap-1.5">
                  <Spinner /> Waiting for you to approve in the browser.
                  <a className="underline" href={signInUrl ?? undefined} target="_blank" rel="noreferrer">
                    Open the page again
                  </a>
                </span>
              ) : null
            }
            control={
              configured ? null : (
                <Button size="xs" variant="outline" onClick={() => signIn.mutate()} disabled={signIn.isPending || waiting}>
                  {signIn.isPending ? <Spinner /> : <LogInIcon />}
                  Sign in
                </Button>
              )
            }
          />
        ) : null}
        <SettingsRow
          title="API key"
          description={
            <>
              {hosted ? "Encrypted in your account’s private vault." : "Stored in macOS Keychain and never sent to the browser."} Usage is billed by {label} to your account, apart
              from any chat subscription.{searchNote}{" "}
              <a className="inline-flex items-center gap-0.5 underline" href={entry?.key_url} target="_blank" rel="noreferrer">
                Get a key
                <ExternalLinkIcon className="size-3" />
              </a>
            </>
          }
          status={
            test.result ? (
              <TestResult result={test.result} />
            ) : error ? (
              <span className="text-destructive-foreground">{(error as Error).message}</span>
            ) : configured ? (
              <span className="inline-flex items-center gap-1.5">
                Saved · <SettingsCode>{entry?.masked_key}</SettingsCode> · {role}
              </span>
            ) : (
              "Not configured"
            )
          }
          control={
            configured ? (
              <>
                <TestButton testing={test.testing} onClick={test.test} />
                <Button size="xs" variant="ghost-destructive" onClick={() => deleteKey.mutate()}>
                  <Trash2Icon />
                  Remove
                </Button>
              </>
            ) : null
          }
        >
          <ApiKeyField
            label={`${label} API key`}
            placeholder={entry?.key_hint || "API key"}
            saving={setKey.isPending}
            onSave={(key) => setKey.mutate(key)}
          />
        </SettingsRow>
      </SettingsSection>
      {configured ? (
        <SettingsSection title="Models">
          <ProviderModelRows
            provider={id}
            models={(models?.models ?? []).map((slug) => ({
              value: slug,
              label: models?.labels?.[slug] || slug,
            }))}
            visionModel={models?.vision_model ?? entry?.vision_model ?? ""}
            listingModel={models?.listing_model ?? entry?.listing_model ?? ""}
            onModel={(model) => setModels.mutate(model)}
            reasoning={
              models?.reasoning
                ? {
                    value: models.reasoning.value,
                    options: models.reasoning.options.map((option) => ({
                      value: option.value,
                      label: REASONING_LABELS[option.value] ?? option.label,
                    })),
                    onChange: (value) => setModels.mutate({ reasoning_effort: value }),
                  }
                : null
            }
            reasoningDescription={
              id === "openrouter"
                ? "Higher uses more reasoning tokens and takes longer. Options come from the listing model; Default leaves the choice to OpenRouter. Applies to the listing model."
                : ""
            }
            pending={setModels.isPending}
            error={models?.error || setModels.error?.message}
          />
        </SettingsSection>
      ) : null}
    </>
  );
}

export function ProvidersSettings() {
  const { hosted } = useWorkspaceRuntime();
  const providerIds = hosted ? PROVIDER_IDS.filter((id) => isKeyProviderId(id) || id === "mimo") : PROVIDER_IDS;
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<ListingProviderId | null>(null);
  const [claudeTerminalOpen, setClaudeTerminalOpen] = useState(false);
  const activeTest = useConnectionTest(api.settings.testConnection);
  const mimoTest = useConnectionTest(api.settings.testMimo);
  const cursorTest = useConnectionTest(api.settings.testCursor);

  const { data: provider } = useQuery({
    queryKey: ["settings-provider"],
    queryFn: api.settings.provider,
  });
  const chatgptSignedIn = Boolean(provider?.chatgpt?.signed_in);
  const claudeSignedIn = Boolean(provider?.claude?.signed_in);
  const cursorConfigured = Boolean(provider?.masked_cursor_key);
  const mimoConfigured = Boolean(provider?.masked_key);
  const { data: chatgptModels } = useQuery({
    queryKey: ["chatgpt-models"],
    queryFn: api.settings.chatgptModels,
    enabled: chatgptSignedIn,
  });
  const { data: claudeModels } = useQuery({
    queryKey: ["claude-models"],
    queryFn: api.settings.claudeModels,
    enabled: claudeSignedIn,
    // Listing the account's models starts Claude Code, so don't redo it on every focus.
    staleTime: 5 * 60_000,
  });
  const { data: cursorModels } = useQuery({
    queryKey: ["cursor-models"],
    queryFn: api.settings.cursorModels,
    enabled: cursorConfigured,
  });
  const { data: aiKeys } = useQuery({ queryKey: ["ai-keys"], queryFn: api.settings.aiKeys });
  const keyEntries = new Map((aiKeys?.providers ?? []).map((entry) => [entry.id, entry]));

  // Stable, so the OpenRouter sign-in's polling effect does not restart on every render.
  const refreshProvider = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["settings-provider"] });
    queryClient.invalidateQueries({ queryKey: ["chatgpt-models"] });
    queryClient.invalidateQueries({ queryKey: ["claude-models"] });
    queryClient.invalidateQueries({ queryKey: ["cursor-models"] });
    queryClient.invalidateQueries({ queryKey: ["ai-keys"] });
    queryClient.invalidateQueries({ queryKey: ["ai-key-models"] });
    queryClient.invalidateQueries({ queryKey: ["status"] });
  }, [queryClient]);
  const refreshModels = (key: string) => () => {
    queryClient.invalidateQueries({ queryKey: ["settings-provider"] });
    queryClient.invalidateQueries({ queryKey: [key] });
    queryClient.invalidateQueries({ queryKey: ["status"] });
  };

  const setKeyMutation = useMutation({
    mutationFn: (key: string) => api.settings.setProvider(key),
    onSuccess: () => {
      refreshProvider();
      mimoTest.clear();
    },
  });
  const deleteKeyMutation = useMutation({
    mutationFn: () => api.settings.deleteKey(),
    onSuccess: () => {
      refreshProvider();
      mimoTest.clear();
    },
  });
  const setCursorMutation = useMutation({
    mutationFn: (key: string) => api.settings.setCursor(key),
    onSuccess: () => {
      refreshProvider();
      cursorTest.clear();
    },
  });
  const deleteCursorMutation = useMutation({
    mutationFn: () => api.settings.deleteCursor(),
    onSuccess: () => {
      refreshProvider();
      cursorTest.clear();
    },
  });
  const chatgptLoginMutation = useMutation({
    mutationFn: () => api.settings.chatgptLogin(),
    onSuccess: refreshProvider,
  });
  const chatgptCancelMutation = useMutation({
    mutationFn: () => api.settings.chatgptCancelLogin(),
    onSuccess: refreshProvider,
  });
  const chatgptLogoutMutation = useMutation({
    mutationFn: () => api.settings.chatgptLogout(),
    onSuccess: refreshProvider,
  });
  const setChatGPTModelsMutation = useMutation({
    mutationFn: (models: { vision_model?: string; listing_model?: string; reasoning_effort?: string }) =>
      api.settings.setChatGPTModels(models),
    onSuccess: refreshModels("chatgpt-models"),
  });
  const setCursorModelsMutation = useMutation({
    mutationFn: (models: { vision_model?: string; listing_model?: string; reasoning_effort?: string }) =>
      api.settings.setCursorModels(models),
    onSuccess: refreshModels("cursor-models"),
  });
  const setClaudeModelsMutation = useMutation({
    mutationFn: (models: { vision_model?: string; listing_model?: string; effort?: string }) =>
      api.settings.setClaudeModels(models),
    onSuccess: refreshModels("claude-models"),
  });
  const setPhotoProviderMutation = useMutation({
    mutationFn: (choice: PhotoProviderChoice) => api.settings.setPhotoProvider(choice),
    onSuccess: refreshProvider,
  });
  const setPreferredMutation = useMutation({
    mutationFn: (order: { primary: ListingProviderId; fallback: ListingFallbackId }) =>
      api.settings.setPreferredProvider(order),
    onSuccess: refreshProvider,
  });

  const chatgpt = provider?.chatgpt;
  const chatgptPending = chatgpt?.pending;
  const pendingSignIn = chatgptPending?.authorize_url;
  const claude = provider?.claude;
  const primary: ListingProviderId = isListingProviderId(provider?.primary) ? provider.primary : "chatgpt";
  const fallback: ListingFallbackId =
    provider?.fallback === "none" || isListingProviderId(provider?.fallback)
      ? provider.fallback
      : primary === "chatgpt"
        ? "mimo"
        : "chatgpt";
  const activeChoice = resolveProviderId(provider?.provider);
  const choiceReady = (choice: ListingFallbackId) =>
    isKeyProviderId(choice)
      ? Boolean(keyEntries.get(choice)?.masked_key)
      : choice === "chatgpt"
      ? chatgptSignedIn
      : choice === "claude"
        ? claudeSignedIn
        : choice === "mimo"
          ? mimoConfigured
          : choice === "cursor"
            ? cursorConfigured
            : false;
  const photoProvider: PhotoProviderChoice = provider?.photo_provider ?? "same";
  const primaryReady = choiceReady(primary);
  const fallbackReady = choiceReady(fallback);
  const selected = selectedId ?? primary;

  const saveOrder = (nextPrimary: ListingProviderId, nextFallback: ListingFallbackId) => {
    setPreferredMutation.mutate({
      primary: nextPrimary,
      fallback: nextFallback === nextPrimary ? "none" : nextFallback,
    });
  };

  const chatgptReasoning = chatgptModels?.reasoning_efforts?.length
    ? chatgptModels.reasoning_efforts
    : ["none", "low", "medium", "high", "xhigh"];

  useEffect(() => {
    if (!pendingSignIn) return;
    const id = window.setInterval(() => {
      queryClient.invalidateQueries({ queryKey: ["settings-provider"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    }, 2000);
    return () => window.clearInterval(id);
  }, [pendingSignIn, queryClient]);

  const roleSentence = (id: ListingProviderId) =>
    primary === id ? "Primary for listings." : fallback === id ? "Fallback for listings." : "Not in the listing order.";

  const inUse: ReactNode =
    provider?.configured && activeChoice ? (
      <>
        <ProviderInstanceIcon driverKind={activeChoice} displayName={providerLabel(activeChoice)} className="size-4" iconClassName="size-4" />
        {providerLabel(activeChoice)} is active {activeChoice === primary ? "(primary)" : "(fallback)"}.
      </>
    ) : primaryReady ? (
      <>
        <ProviderInstanceIcon driverKind={primary} displayName={providerLabel(primary)} className="size-4" iconClassName="size-4" />
        {providerLabel(primary)} is ready.
      </>
    ) : fallback !== "none" && fallbackReady ? (
      <>
        <ProviderInstanceIcon driverKind={fallback} displayName={providerLabel(fallback)} className="size-4" iconClassName="size-4" />
        {providerLabel(primary)} is not ready — {providerLabel(fallback)} will be used.
      </>
    ) : (
      <>
        <span className="size-1.5 rounded-full bg-warning" aria-hidden />
        Set up {providerLabel(primary)}
        {fallback !== "none" ? ` or ${providerLabel(fallback)}` : ""} below.
      </>
    );

  const activeTestStatus = (id: ListingProviderId) =>
    activeTest.result && provider?.provider === id ? <TestResult result={activeTest.result} /> : null;

  const editors: Record<ListingProviderId, ReactNode> = {
    ...(Object.fromEntries(
      KEY_PROVIDER_IDS.map((id) => [
        id,
        <KeyProviderEditor key={id} id={id} entry={keyEntries.get(id)} role={roleSentence(id)} onChanged={refreshProvider} />,
      ]),
    ) as Record<KeyProviderId, ReactNode>),
    chatgpt: (
      <>
        <SettingsSection title="Account">
          <SettingsRow
            title="Sign in with ChatGPT"
            description={
              chatgptSignedIn ? (
                <>
                  Signed in{chatgpt?.email ? ` as ${chatgpt.email}` : ""}
                  {chatgpt?.plan ? ` · ${chatgpt.plan}` : ""}. {roleSentence("chatgpt")}
                </>
              ) : (
                "Uses your ChatGPT plan to generate listings and look up sold comps. You approve Anteroom in your browser, and usage counts against your plan, not an API key."
              )
            }
            status={
              activeTestStatus("chatgpt") ??
              (chatgpt?.error ? <span className="text-destructive-foreground">{chatgpt.error}</span> : null)
            }
            control={
              chatgptSignedIn ? (
                <>
                  {provider?.provider === "chatgpt" ? (
                    <TestButton testing={activeTest.testing} onClick={activeTest.test} />
                  ) : null}
                  <Button
                    size="xs"
                    variant="ghost"
                    onClick={() => chatgptLogoutMutation.mutate()}
                    disabled={chatgptLogoutMutation.isPending}
                  >
                    Sign out
                  </Button>
                </>
              ) : chatgptPending ? (
                <Button
                  size="xs"
                  variant="ghost-muted"
                  onClick={() => chatgptCancelMutation.mutate()}
                  disabled={chatgptCancelMutation.isPending}
                >
                  Cancel
                </Button>
              ) : (
                <Button size="xs" variant="outline" onClick={() => chatgptLoginMutation.mutate()} disabled={chatgptLoginMutation.isPending}>
                  {chatgptLoginMutation.isPending ? <Spinner /> : <LogInIcon />}
                  {chatgptLoginMutation.isPending ? "Starting" : chatgpt?.error ? "Retry sign-in" : "Sign in"}
                </Button>
              )
            }
          >
            {chatgptPending ? (
              <div className="grid max-w-xl gap-2 rounded-lg border border-border/60 bg-muted/30 p-3 text-sm">
                <p className="flex items-center gap-2 text-muted-foreground">
                  <Spinner size="sm" tone="muted" />
                  Approve Anteroom in your browser, and leave token sharing on.
                </p>
                <Button
                  size="xs"
                  variant="outline"
                  className="w-fit"
                  render={<a href={chatgptPending.authorize_url} target="_blank" rel="noreferrer" />}
                >
                  <ExternalLinkIcon />
                  Open the sign-in page again
                </Button>
              </div>
            ) : chatgptLoginMutation.isError ? (
              <SettingsNote tone="error">{(chatgptLoginMutation.error as Error).message}</SettingsNote>
            ) : null}
          </SettingsRow>
        </SettingsSection>
        {chatgptSignedIn ? (
          <SettingsSection title="Models">
            <ProviderModelRows
              provider="chatgpt"
              models={(chatgptModels?.models ?? []).map((slug) => ({ value: slug, label: slug }))}
              visionModel={chatgptModels?.vision_model ?? ""}
              listingModel={chatgptModels?.listing_model ?? ""}
              onModel={(model) => setChatGPTModelsMutation.mutate(model)}
              reasoning={
                chatgptModels
                  ? {
                      value: chatgptReasoning.includes(chatgptModels.reasoning_effort)
                        ? chatgptModels.reasoning_effort
                        : chatgptReasoning[0],
                      options: chatgptReasoning.map((effort) => ({
                        value: effort,
                        label: REASONING_LABELS[effort] || effort,
                      })),
                      onChange: (value) => setChatGPTModelsMutation.mutate({ reasoning_effort: value }),
                    }
                  : null
              }
              reasoningDescription="Higher uses more of your ChatGPT plan and takes longer. Default is Low. Applies to both models."
              pending={setChatGPTModelsMutation.isPending}
              error={chatgptModels?.error || setChatGPTModelsMutation.error?.message}
            />
          </SettingsSection>
        ) : null}
      </>
    ),
    claude: (
      <>
        <SettingsSection title="Account">
          <SettingsRow
            title="Sign in with Claude"
            description={
              claudeSignedIn ? (
                <>
                  Signed in{claude?.email ? ` as ${claude.email}` : ""}
                  {claude?.plan ? ` · ${claude.plan.charAt(0).toUpperCase()}${claude.plan.slice(1)}` : ""}.{" "}
                  {roleSentence("claude")} Signing out of Claude Code (<SettingsCode>claude auth logout</SettingsCode> in
                  Terminal) signs Anteroom out too; Sign in here brings it back.
                </>
              ) : (
                "Uses your Claude Pro or Max subscription through Claude Code. Usage counts against your Claude plan, not an API key."
              )
            }
            status={activeTestStatus("claude")}
            control={
              claudeSignedIn ? (
                provider?.provider === "claude" ? (
                  <TestButton testing={activeTest.testing} onClick={activeTest.test} />
                ) : null
              ) : claudeTerminalOpen ? null : (
                <Button
                  size="xs"
                  variant="outline"
                  onClick={() => setClaudeTerminalOpen(true)}
                  disabled={claude ? !claude.installed : true}
                >
                  <TerminalIcon />
                  Sign in
                </Button>
              )
            }
          >
            {!claudeSignedIn && claudeTerminalOpen ? (
              <ClaudeLoginTerminal
                onClose={() => {
                  setClaudeTerminalOpen(false);
                  refreshProvider();
                }}
              />
            ) : !claudeSignedIn && claude && !claude.installed ? (
              <div className="grid max-w-xl gap-1.5 rounded-lg border border-warning/32 bg-warning-surface p-3 text-xs">
                <p className="font-medium text-warning-foreground">Claude Code is not installed</p>
                <p className="text-muted-foreground">Install it in Terminal, then sign in:</p>
                <SettingsCode className="w-fit select-all">{claude.install_command}</SettingsCode>
              </div>
            ) : null}
          </SettingsRow>
        </SettingsSection>
        {claudeSignedIn ? (
          <SettingsSection title="Models">
            <ProviderModelRows
              provider="claude"
              models={claudeModels?.models ?? []}
              visionModel={claudeModels?.vision_model ?? ""}
              listingModel={claudeModels?.listing_model ?? ""}
              onModel={(model) => setClaudeModelsMutation.mutate(model)}
              reasoning={
                claudeModels?.efforts.length
                  ? {
                      value: claudeModels.effort ?? "default",
                      options: [
                        { value: "default", label: "Default" },
                        ...claudeModels.efforts.map((effort) => ({
                          value: effort,
                          label: REASONING_LABELS[effort] || effort,
                        })),
                      ],
                      onChange: (value) => setClaudeModelsMutation.mutate({ effort: value }),
                    }
                  : null
              }
              reasoningDescription="Higher uses more of your Claude plan and takes longer. Options come from the listing model, and apply to both models."
              pending={setClaudeModelsMutation.isPending}
              error={claudeModels?.error || setClaudeModelsMutation.error?.message}
            />
          </SettingsSection>
        ) : null}
      </>
    ),
    mimo: (
      <>
        <SettingsSection title="Setup">
          <SettingsRow
            title="API key"
            description={hosted ? "Encrypted in your account’s private vault. Usage is billed to your MiMo account." : "Stored in macOS Keychain and never sent to the browser. When MiMo is your listing model, sold comps use its web search, which needs the Web Search plugin turned on in the MiMo console (Plugin Management)."}
            status={
              mimoTest.result ? (
                <TestResult result={mimoTest.result} />
              ) : setKeyMutation.isError ? (
                <span className="text-destructive-foreground">{(setKeyMutation.error as Error).message}</span>
              ) : mimoConfigured ? (
                <span className="inline-flex items-center gap-1.5">
                  Saved · <SettingsCode>{provider?.masked_key}</SettingsCode> · {roleSentence("mimo")}
                </span>
              ) : primary === "mimo" ? (
                "Primary — add a key to use MiMo"
              ) : fallback === "mimo" ? (
                "Fallback — add a key if ChatGPT is unavailable"
              ) : (
                "Not configured"
              )
            }
            control={
              mimoConfigured ? (
                <>
                  <TestButton testing={mimoTest.testing} onClick={mimoTest.test} />
                  <Button size="xs" variant="ghost-destructive" onClick={() => deleteKeyMutation.mutate()}>
                    <Trash2Icon />
                    Remove
                  </Button>
                </>
              ) : null
            }
          >
            <ApiKeyField
              label="MiMo API key"
              placeholder="sk-..."
              saving={setKeyMutation.isPending}
              onSave={(key) => setKeyMutation.mutate(key)}
            />
          </SettingsRow>
        </SettingsSection>
        <SettingsSection title="Models">
          <SettingsRow
            title="Photo and listing models"
            description="MiMo reads photos with mimo-v2.5 and writes listings with mimo-v2.5-pro. These are fixed."
            control={
              <span className="flex items-center gap-1.5">
                <SettingsCode>mimo-v2.5</SettingsCode>
                <SettingsCode>mimo-v2.5-pro</SettingsCode>
              </span>
            }
          />
        </SettingsSection>
      </>
    ),
    cursor: (
      <>
        <SettingsSection title="Setup">
          <SettingsRow
            title="API key"
            description="From Cursor Dashboard → Integrations. Stored in Keychain; listing runs use the local Cursor SDK against an empty scratch folder. When Cursor is your listing model it also searches the web for sold comps."
            status={
              cursorTest.result ? (
                <TestResult result={cursorTest.result} />
              ) : setCursorMutation.isError ? (
                <span className="text-destructive-foreground">{(setCursorMutation.error as Error).message}</span>
              ) : cursorConfigured ? (
                <span className="inline-flex items-center gap-1.5">
                  Saved · <SettingsCode>{provider?.masked_cursor_key}</SettingsCode> · {roleSentence("cursor")}
                </span>
              ) : primary === "cursor" ? (
                "Primary — add a Cursor API key"
              ) : fallback === "cursor" ? (
                "Fallback — add a key if other providers are unavailable"
              ) : (
                "Not configured"
              )
            }
            control={
              cursorConfigured ? (
                <>
                  <TestButton testing={cursorTest.testing} onClick={cursorTest.test} />
                  <Button size="xs" variant="ghost-destructive" onClick={() => deleteCursorMutation.mutate()}>
                    <Trash2Icon />
                    Remove
                  </Button>
                </>
              ) : null
            }
          >
            <ApiKeyField
              label="Cursor API key"
              placeholder="cursor_..."
              saving={setCursorMutation.isPending}
              onSave={(key) => setCursorMutation.mutate(key)}
            />
          </SettingsRow>
        </SettingsSection>
        {cursorConfigured ? (
          <SettingsSection title="Models">
            <ProviderModelRows
              provider="cursor"
              models={(cursorModels?.models ?? []).map((slug) => ({
                value: slug,
                label: slug === "auto" ? "Auto" : slug,
              }))}
              visionModel={cursorModels?.vision_model ?? ""}
              listingModel={cursorModels?.listing_model ?? ""}
              onModel={(model) => setCursorModelsMutation.mutate(model)}
              reasoning={
                cursorModels?.reasoning
                  ? {
                      value: cursorModels.reasoning.value,
                      options: cursorModels.reasoning.options,
                      onChange: (value) => setCursorModelsMutation.mutate({ reasoning_effort: value }),
                    }
                  : null
              }
              reasoningDescription="Higher uses more Cursor usage and takes longer. Options come from the listing model, and apply wherever that model is used. Auto lets Cursor pick the model."
              pending={setCursorModelsMutation.isPending}
              error={cursorModels?.error || setCursorModelsMutation.error?.message}
            />
          </SettingsSection>
        ) : null}
      </>
    ),
  };

  const fallbackOptions: SettingsSelectOption<ListingFallbackId>[] = [
    { value: "none", label: "None" },
    ...(fallback !== "none" && !providerIds.includes(fallback) ? [providerOption<ListingFallbackId>(fallback, fallback)] : []),
    ...providerIds.filter((id) => id !== primary).map((id) => providerOption<ListingFallbackId>(id, id)),
  ];

  return (
    <>
      <SettingsSection id="listing-ai" title="Listing AI">
        <SettingsRow
          title="In use"
          description={<span className="inline-flex flex-wrap items-center gap-2">{inUse}</span>}
        />
        <SettingsRow
          title="Primary"
          description="Writes listings, and reads photos unless Photo analysis names another. Each provider's models are set in its editor below."
          control={
            <SettingsSelect<ListingProviderId>
              aria-label="Primary listing AI"
              value={primary}
              disabled={setPreferredMutation.isPending}
              onChange={(value) => saveOrder(value, fallback)}
              options={[...(!providerIds.includes(primary) ? [providerOption(primary, primary)] : []), ...providerIds.map((id) => providerOption(id, id))]}
            />
          }
        />
        <SettingsRow
          title="Fallback"
          description="Used only when the primary provider is not ready."
          control={
            <SettingsSelect<ListingFallbackId>
              aria-label="Fallback listing AI"
              value={fallback === primary ? "none" : fallback}
              disabled={setPreferredMutation.isPending}
              onChange={(value) => saveOrder(primary, value)}
              options={fallbackOptions}
            />
          }
        />
        <SettingsRow
          title="Photo analysis"
          description="Reads product photos before the listing is written, with that provider's photo model. A provider that is not ready falls back to the listing AI."
          status={
            photoProvider !== "same" && !choiceReady(photoProvider) ? (
              <span className="inline-flex items-center gap-1.5 text-warning-foreground">
                <span className="size-1.5 rounded-full bg-warning" aria-hidden />
                {providerLabel(photoProvider)} is not ready, so the listing AI reads photos. Set it up below.
              </span>
            ) : null
          }
          control={
            <SettingsSelect<PhotoProviderChoice>
              aria-label="Photo analysis AI"
              value={photoProvider}
              disabled={setPhotoProviderMutation.isPending}
              onChange={(value) => setPhotoProviderMutation.mutate(value)}
              options={[{ value: "same", label: "Same as listing AI" }, ...(photoProvider !== "same" && !providerIds.includes(photoProvider) ? [providerOption(photoProvider, photoProvider)] : []), ...providerIds.map((id) => providerOption(id, id))]}
            />
          }
        />
      </SettingsSection>

      <SettingsSection id="providers" title="Providers" variant="plain">
        <SettingsGroup
          divided={false}
          className={cn(
            PROVIDER_CARD_HEIGHT,
            "overflow-hidden @min-[48rem]/providers:grid @min-[48rem]/providers:grid-cols-[17rem_minmax(0,1fr)]",
          )}
        >
          <div className="border-b border-border/60 bg-muted/10 @min-[48rem]/providers:flex @min-[48rem]/providers:min-h-0 @min-[48rem]/providers:flex-col @min-[48rem]/providers:border-r @min-[48rem]/providers:border-b-0">
            <ScrollArea scrollFade chainVerticalScroll className="@min-[48rem]/providers:min-h-0 @min-[48rem]/providers:flex-1">
              <div className="divide-y divide-border/50" role="group" aria-label="Providers">
                {providerIds.map((id) => (
                  <ProviderListRow
                    key={id}
                    id={id}
                    summary={summarizeProvider(id, provider, isKeyProviderId(id) ? keyEntries.get(id) : undefined)}
                    selected={selected === id}
                    onSelect={() => setSelectedId(id)}
                    badges={<RoleBadges id={id} primary={primary} fallback={fallback} photoProvider={photoProvider} />}
                  />
                ))}
              </div>
            </ScrollArea>
          </div>
          <div className="min-w-0 @min-[48rem]/providers:min-h-0">
            <ScrollArea scrollFade chainVerticalScroll className="@min-[48rem]/providers:h-full">
              <div className="space-y-6 p-4" key={selected}>
                <div className="flex min-h-7 items-center justify-between gap-4 px-3 sm:px-4">
                  <h3 className="flex min-w-0 items-center gap-2 text-sm font-medium text-foreground">
                    <ProviderIcon id={selected} />
                    {PROVIDER_LABELS[selected].full}
                  </h3>
                  <span className="flex items-center gap-1">
                    <RoleBadges id={selected} primary={primary} fallback={fallback} photoProvider={photoProvider} />
                  </span>
                </div>
                {hosted && !providerIds.includes(selected) ? <SettingsNote>This sign-in requires a connected Mac worker. Choose an API-key provider below and select it as your primary listing AI to run in the web app.</SettingsNote> : editors[selected]}
              </div>
            </ScrollArea>
          </div>
        </SettingsGroup>
      </SettingsSection>

      {!hosted && <BraveSearchSection chatgptSignedIn={chatgptSignedIn} />}
    </>
  );
}
