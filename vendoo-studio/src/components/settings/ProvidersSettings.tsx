import { type ReactNode, useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { ListingProviderId, PhotoProviderChoice } from "../../api/types";
import { ClaudeLoginTerminal } from "../ClaudeLoginTerminal";
import { ProviderLogo, resolveProviderLogoId } from "../ProviderLogo";
import { SettingsRow, SettingsSection } from "./SettingsLayout";

type ListingFallbackId = ListingProviderId | "none";

function providerLabel(choice: ListingFallbackId): string {
  if (choice === "chatgpt") return "ChatGPT";
  if (choice === "claude") return "Claude";
  if (choice === "mimo") return "MiMo";
  if (choice === "cursor") return "Cursor";
  return "None";
}

function providerTitle(choice: ListingProviderId, full = false): ReactNode {
  const label =
    choice === "chatgpt"
      ? "ChatGPT"
      : choice === "claude"
        ? "Claude"
        : choice === "mimo"
          ? (full ? "Xiaomi MiMo" : "MiMo")
          : "Cursor";
  return (
    <>
      <ProviderLogo id={choice} label={label} size={18} />
      <span>{label}</span>
    </>
  );
}

function activeProviderChoice(providerName: string | undefined): ListingProviderId | null {
  return resolveProviderLogoId(providerName);
}

type ModelOption = { value: string; label: string };

/** A provider's photo model, listing model and reasoning, kept in its own section. */
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
  const modelSelect = (role: "vision_model" | "listing_model", value: string, ariaLabel: string) => (
    <select
      className="input settings-model-select"
      aria-label={`${label} ${ariaLabel}`}
      value={value}
      disabled={pending || options.length === 0}
      onChange={(event) =>
        onModel(
          role === "vision_model"
            ? { vision_model: event.target.value }
            : { listing_model: event.target.value },
        )
      }
    >
      {options.map((option) => (
        <option key={`${role}-${option.value}`} value={option.value}>
          {option.label}
        </option>
      ))}
    </select>
  );
  return (
    <>
      <SettingsRow
        title="Photo model"
        description={`Reads product photos when ${label} does photo analysis.`}
        status={error ? <span className="text-error">{error}</span> : null}
        control={modelSelect("vision_model", visionModel, "photo model")}
      />
      <SettingsRow
        title="Listing model"
        description={`Writes marketplace copy when ${label} is the listing AI.`}
        control={modelSelect("listing_model", listingModel, "listing model")}
      />
      <SettingsRow
        title="Reasoning"
        description={reasoningDescription}
        control={
          reasoning ? (
            <select
              className="input settings-model-select"
              aria-label={`${label} reasoning`}
              value={reasoning.value}
              disabled={pending}
              onChange={(event) => reasoning.onChange(event.target.value)}
            >
              {reasoning.options.map((option) => (
                <option key={`reasoning-${option.value}`} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          ) : (
            <span className="settings-row-value">Not adjustable for {listingLabel}</span>
          )
        }
      />
    </>
  );
}

const REASONING_LABELS: Record<string, string> = {
  none: "Off",
  low: "Low",
  medium: "Medium",
  high: "High",
  xhigh: "Extra high",
  max: "Max",
};

function BraveSearchSection({ chatgptSignedIn }: { chatgptSignedIn: boolean }) {
  const queryClient = useQueryClient();
  const [apiKey, setApiKey] = useState("");
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const { data: brave } = useQuery({
    queryKey: ["settings-brave"],
    queryFn: api.settings.brave,
  });
  const setKeyMutation = useMutation({
    mutationFn: (key: string) => api.settings.setBrave(key),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings-brave"] });
      setApiKey("");
      setTestResult(null);
    },
  });
  const deleteKeyMutation = useMutation({
    mutationFn: () => api.settings.deleteBrave(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings-brave"] });
      setTestResult(null);
    },
  });
  const configured = Boolean(brave?.configured);
  const saveKey = () => {
    if (!apiKey.trim()) return;
    setKeyMutation.mutate(apiKey);
  };
  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.settings.testBrave();
      setTestResult(result.ok ? "Connection successful" : result.error || "Connection failed");
    } catch (err) {
      setTestResult(`Error: ${err instanceof Error ? err.message : String(err)}`);
    }
    setTesting(false);
  };

  return (
    <SettingsSection id="brave" title="Brave Search">
      <SettingsRow
        id="brave-api-key"
        title="API key"
        description={
          <>
            Used to look up sold comps when your listing model's web search fails, times out or finds no sales. Get a key at{" "}
            <a href="https://api.search.brave.com" target="_blank" rel="noreferrer">
              api.search.brave.com
            </a>
            . Stored in macOS Keychain and never sent to the browser.
          </>
        }
      >
        <div className="settings-row-field">
          <input
            className="input font-mono"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") saveKey();
            }}
            placeholder="BSA..."
            autoComplete="off"
            spellCheck={false}
          />
          <button type="button" className="btn btn-sm btn-outline" onClick={saveKey} disabled={!apiKey.trim() || setKeyMutation.isPending}>
            {setKeyMutation.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </SettingsRow>
      <SettingsRow
        title="Status"
        description={
          configured
            ? `Fallback · ${brave?.masked_key}`
            : chatgptSignedIn
              ? "Fallback when your listing model's web search does not work"
              : "Not configured — sold comps use your listing model's web search only"
        }
        status={
          testResult ? (
            <span className={testResult.includes("successful") ? "text-success" : "text-error"}>{testResult}</span>
          ) : setKeyMutation.isError ? (
            <span className="text-error">{(setKeyMutation.error as Error).message}</span>
          ) : null
        }
        control={
          configured ? (
            <>
              <button type="button" className="btn btn-sm btn-outline" onClick={handleTest} disabled={testing}>
                {testing ? "Testing…" : "Test"}
              </button>
              <button type="button" className="btn btn-sm btn-ghost settings-danger" onClick={() => deleteKeyMutation.mutate()}>
                Remove
              </button>
            </>
          ) : null
        }
      />
    </SettingsSection>
  );
}

export function ProvidersSettings() {
  const queryClient = useQueryClient();
  const [apiKey, setApiKey] = useState("");
  const [cursorKey, setCursorKey] = useState("");
  const [claudeTerminalOpen, setClaudeTerminalOpen] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const [mimoTesting, setMimoTesting] = useState(false);
  const [mimoTestResult, setMimoTestResult] = useState<string | null>(null);
  const [cursorTesting, setCursorTesting] = useState(false);
  const [cursorTestResult, setCursorTestResult] = useState<string | null>(null);

  const { data: provider } = useQuery({
    queryKey: ["settings-provider"],
    queryFn: api.settings.provider,
  });
  const chatgptSignedIn = Boolean(provider?.chatgpt?.signed_in);
  const claudeSignedIn = Boolean(provider?.claude?.signed_in);
  const cursorConfigured = Boolean(provider?.masked_cursor_key);
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

  const refreshProvider = () => {
    queryClient.invalidateQueries({ queryKey: ["settings-provider"] });
    queryClient.invalidateQueries({ queryKey: ["chatgpt-models"] });
    queryClient.invalidateQueries({ queryKey: ["claude-models"] });
    queryClient.invalidateQueries({ queryKey: ["cursor-models"] });
    queryClient.invalidateQueries({ queryKey: ["status"] });
  };

  const setKeyMutation = useMutation({
    mutationFn: (key: string) => api.settings.setProvider(key),
    onSuccess: () => {
      refreshProvider();
      setApiKey("");
      setMimoTestResult(null);
    },
  });

  const deleteKeyMutation = useMutation({
    mutationFn: () => api.settings.deleteKey(),
    onSuccess: () => {
      refreshProvider();
      setMimoTestResult(null);
    },
  });

  const setCursorMutation = useMutation({
    mutationFn: (key: string) => api.settings.setCursor(key),
    onSuccess: () => {
      refreshProvider();
      setCursorKey("");
    },
  });

  const deleteCursorMutation = useMutation({
    mutationFn: () => api.settings.deleteCursor(),
    onSuccess: refreshProvider,
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
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings-provider"] });
      queryClient.invalidateQueries({ queryKey: ["chatgpt-models"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
  const setCursorModelsMutation = useMutation({
    mutationFn: (models: { vision_model?: string; listing_model?: string; reasoning_effort?: string }) =>
      api.settings.setCursorModels(models),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings-provider"] });
      queryClient.invalidateQueries({ queryKey: ["cursor-models"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
  });
  const setClaudeModelsMutation = useMutation({
    mutationFn: (models: { vision_model?: string; listing_model?: string; effort?: string }) =>
      api.settings.setClaudeModels(models),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["settings-provider"] });
      queryClient.invalidateQueries({ queryKey: ["claude-models"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    },
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
  const pendingCode = chatgptPending?.user_code;
  const claude = provider?.claude;
  const mimoConfigured = Boolean(provider?.masked_key);
  const primary: ListingProviderId =
    provider?.primary === "claude" || provider?.primary === "mimo" || provider?.primary === "cursor"
      ? provider.primary
      : "chatgpt";
  const fallback: ListingFallbackId =
    provider?.fallback === "chatgpt" ||
    provider?.fallback === "claude" ||
    provider?.fallback === "mimo" ||
    provider?.fallback === "cursor" ||
    provider?.fallback === "none"
      ? provider.fallback
      : primary === "chatgpt"
        ? "mimo"
        : "chatgpt";
  const activeChoice = activeProviderChoice(provider?.provider);
  const choiceReady = (choice: ListingFallbackId) =>
    choice === "chatgpt"
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

  const saveOrder = (nextPrimary: ListingProviderId, nextFallback: ListingFallbackId) => {
    let fallbackValue = nextFallback;
    if (fallbackValue === nextPrimary) {
      fallbackValue = "none";
    }
    setPreferredMutation.mutate({ primary: nextPrimary, fallback: fallbackValue });
  };

  const chatgptReasoning = chatgptModels?.reasoning_efforts?.length
    ? chatgptModels.reasoning_efforts
    : ["none", "low", "medium", "high", "xhigh"];

  useEffect(() => {
    if (!pendingCode) return;
    const id = window.setInterval(() => {
      queryClient.invalidateQueries({ queryKey: ["settings-provider"] });
      queryClient.invalidateQueries({ queryKey: ["status"] });
    }, 2000);
    return () => window.clearInterval(id);
  }, [pendingCode, queryClient]);

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.settings.testConnection();
      setTestResult(result.ok ? "Connection successful" : (result.error || "Connection failed"));
    } catch (err) {
      setTestResult(`Error: ${err instanceof Error ? err.message : String(err)}`);
    }
    setTesting(false);
  };

  const handleMimoTest = async () => {
    setMimoTesting(true);
    setMimoTestResult(null);
    try {
      const result = await api.settings.testMimo();
      setMimoTestResult(result.ok ? "Connection successful" : (result.error || "Connection failed"));
    } catch (err) {
      setMimoTestResult(`Error: ${err instanceof Error ? err.message : String(err)}`);
    }
    setMimoTesting(false);
  };

  const handleCursorTest = async () => {
    setCursorTesting(true);
    setCursorTestResult(null);
    try {
      const result = await api.settings.testCursor();
      setCursorTestResult(result.ok ? "Connection successful" : (result.error || "Connection failed"));
    } catch (err) {
      setCursorTestResult(`Error: ${err instanceof Error ? err.message : String(err)}`);
    }
    setCursorTesting(false);
  };

  const saveKey = () => {
    if (!apiKey.trim()) return;
    setKeyMutation.mutate(apiKey);
  };

  const saveCursorKey = () => {
    if (!cursorKey.trim()) return;
    setCursorMutation.mutate(cursorKey);
  };

  return (
    <>
      <SettingsSection id="listing-ai" title="Listing AI">
        <SettingsRow title="In use">
          <p className="settings-row-desc settings-provider-in-use">
            {provider?.configured && activeChoice ? (
              <>
                <ProviderLogo id={activeChoice} label={providerLabel(activeChoice)} size={16} />
                <span>
                  {providerLabel(activeChoice)} is active
                  {activeChoice === primary ? " (primary)" : " (fallback)"}.
                </span>
              </>
            ) : primaryReady ? (
              <>
                <ProviderLogo id={primary} label={providerLabel(primary)} size={16} />
                <span>{providerLabel(primary)} is ready.</span>
              </>
            ) : fallback !== "none" && fallbackReady ? (
              <>
                <ProviderLogo id={fallback} label={providerLabel(fallback)} size={16} />
                <span>
                  {providerLabel(primary)} is not ready — {providerLabel(fallback)} will be used.
                </span>
              </>
            ) : (
              <span>
                Configure {providerLabel(primary)}
                {fallback !== "none" ? ` or ${providerLabel(fallback)}` : ""} below.
              </span>
            )}
          </p>
        </SettingsRow>
        <SettingsRow
          title="Primary"
          description="Writes listings, and reads photos unless Photo analysis names another. Each provider's models are set in its section below."
          control={
            <div className="settings-provider-select">
              <ProviderLogo id={primary} label={providerLabel(primary)} size={16} />
              <select
                className="input settings-model-select"
                aria-label="Primary listing AI"
                value={primary}
                disabled={setPreferredMutation.isPending}
                onChange={(event) => saveOrder(event.target.value as ListingProviderId, fallback)}
              >
                <option value="chatgpt">ChatGPT</option>
                <option value="claude">Claude</option>
                <option value="mimo">Xiaomi MiMo</option>
                <option value="cursor">Cursor</option>
              </select>
            </div>
          }
        />
        <SettingsRow
          title="Fallback"
          description="Used only when the primary provider is not ready."
          control={
            <div className="settings-provider-select">
              {fallback !== "none" && fallback !== primary ? (
                <ProviderLogo id={fallback} label={providerLabel(fallback)} size={16} />
              ) : null}
              <select
                className="input settings-model-select"
                aria-label="Fallback listing AI"
                value={fallback === primary ? "none" : fallback}
                disabled={setPreferredMutation.isPending}
                onChange={(event) =>
                  saveOrder(primary, event.target.value as ListingFallbackId)
                }
              >
                <option value="none">None</option>
                {primary !== "chatgpt" ? <option value="chatgpt">ChatGPT</option> : null}
                {primary !== "claude" ? <option value="claude">Claude</option> : null}
                {primary !== "mimo" ? <option value="mimo">Xiaomi MiMo</option> : null}
                {primary !== "cursor" ? <option value="cursor">Cursor</option> : null}
              </select>
            </div>
          }
        />
        <SettingsRow
          title="Photo analysis"
          description="Reads product photos before the listing is written, with that provider's photo model. A provider that is not ready falls back to the listing AI."
          control={
            <div className="settings-provider-select">
              {photoProvider !== "same" ? (
                <ProviderLogo id={photoProvider} label={providerLabel(photoProvider)} size={16} />
              ) : null}
              <select
                className="input settings-model-select"
                aria-label="Photo analysis AI"
                value={photoProvider}
                disabled={setPhotoProviderMutation.isPending}
                onChange={(event) =>
                  setPhotoProviderMutation.mutate(event.target.value as PhotoProviderChoice)
                }
              >
                <option value="same">Same as listing AI</option>
                <option value="chatgpt">ChatGPT</option>
                <option value="claude">Claude</option>
                <option value="mimo">Xiaomi MiMo</option>
                <option value="cursor">Cursor</option>
              </select>
            </div>
          }
        />
        {photoProvider !== "same" && !choiceReady(photoProvider) ? (
          <SettingsRow title="Photo analysis status">
            <p className="settings-row-desc">
              {providerLabel(photoProvider)} is not ready, so the listing AI reads photos. Configure it below.
            </p>
          </SettingsRow>
        ) : null}
      </SettingsSection>

      <SettingsSection id="chatgpt" title={providerTitle("chatgpt")}>
        <SettingsRow
          title="Sign in with ChatGPT"
          description="Uses your ChatGPT subscription to generate listings and look up sold comps. Usage counts against Codex quota, not a Platform API key."
          status={
            chatgptSignedIn && testResult && provider?.provider === "chatgpt" ? (
              <span className={testResult.includes("successful") ? "text-success" : "text-error"}>{testResult}</span>
            ) : chatgpt?.error ? (
              <span className="text-error">{chatgpt.error}</span>
            ) : null
          }
          control={
            chatgptSignedIn ? (
              <>
                {provider?.provider === "chatgpt" ? (
                  <button type="button" className="btn btn-sm btn-outline" onClick={handleTest} disabled={testing}>
                    {testing ? "Testing…" : "Test"}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="btn btn-sm btn-ghost settings-danger"
                  onClick={() => chatgptLogoutMutation.mutate()}
                  disabled={chatgptLogoutMutation.isPending}
                >
                  Sign out
                </button>
              </>
            ) : chatgptPending ? (
              <button
                type="button"
                className="btn btn-sm btn-outline"
                onClick={() => chatgptCancelMutation.mutate()}
                disabled={chatgptCancelMutation.isPending}
              >
                Cancel
              </button>
            ) : (
              <button
                type="button"
                className="btn btn-sm btn-primary"
                onClick={() => chatgptLoginMutation.mutate()}
                disabled={chatgptLoginMutation.isPending}
              >
                {chatgptLoginMutation.isPending ? "Starting…" : "Sign in"}
              </button>
            )
          }
        >
          {chatgptSignedIn ? (
            <p className="settings-row-desc">
              Signed in{chatgpt?.email ? ` as ${chatgpt.email}` : ""}
              {chatgpt?.plan ? ` · ${chatgpt.plan}` : ""}.
              {primary === "chatgpt"
                ? " Primary for listings."
                : fallback === "chatgpt"
                  ? " Fallback for listings."
                  : " Not in the listing order."}
            </p>
          ) : chatgptPending ? (
            <p className="settings-row-desc">
              Open{" "}
              <a href={chatgptPending.verification_url} target="_blank" rel="noreferrer">
                {chatgptPending.verification_url}
              </a>{" "}
              and enter code <code className="settings-row-code">{chatgptPending.user_code}</code>
            </p>
          ) : chatgptLoginMutation.isError ? (
            <p className="settings-row-desc text-error">{(chatgptLoginMutation.error as Error).message}</p>
          ) : null}
        </SettingsRow>
        {chatgptSignedIn ? (
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
            reasoningDescription="Higher uses more Codex quota and takes longer. Default is Low. Applies to both models."
            pending={setChatGPTModelsMutation.isPending}
            error={chatgptModels?.error || setChatGPTModelsMutation.error?.message}
          />
        ) : null}
      </SettingsSection>

      <SettingsSection id="claude" title={providerTitle("claude")}>
        <SettingsRow
          title="Sign in with Claude"
          description="Uses your Claude Pro or Max subscription through Claude Code. Usage counts against your Claude plan, not an API key."
          status={
            claudeSignedIn && testResult && provider?.provider === "claude" ? (
              <span className={testResult.includes("successful") ? "text-success" : "text-error"}>{testResult}</span>
            ) : null
          }
          control={
            claudeSignedIn ? (
              provider?.provider === "claude" ? (
                <button type="button" className="btn btn-sm btn-outline" onClick={handleTest} disabled={testing}>
                  {testing ? "Testing…" : "Test"}
                </button>
              ) : null
            ) : claudeTerminalOpen ? null : (
              <button
                type="button"
                className="btn btn-sm btn-primary"
                onClick={() => setClaudeTerminalOpen(true)}
                disabled={claude ? !claude.installed : true}
              >
                Sign in
              </button>
            )
          }
        >
          {claudeSignedIn ? (
            <p className="settings-row-desc">
              Signed in{claude?.email ? ` as ${claude.email}` : ""}
              {claude?.plan ? ` · ${claude.plan.charAt(0).toUpperCase()}${claude.plan.slice(1)}` : ""}.
              {primary === "claude"
                ? " Primary for listings."
                : fallback === "claude"
                  ? " Fallback for listings."
                  : " Not in the listing order."}
              {" "}Signing out of Claude Code (<code className="settings-row-code">claude auth logout</code> in
              Terminal) signs Studio out too; Sign in here brings it back.
            </p>
          ) : claudeTerminalOpen ? (
            <ClaudeLoginTerminal
              onClose={() => {
                setClaudeTerminalOpen(false);
                refreshProvider();
              }}
            />
          ) : claude && !claude.installed ? (
            <p className="settings-row-desc">
              Needs Claude Code. Install it in Terminal with{" "}
              <code className="settings-row-code">{claude.install_command}</code>, then sign in.
            </p>
          ) : null}
        </SettingsRow>
        {claudeSignedIn ? (
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
        ) : null}
      </SettingsSection>

      <SettingsSection id="mimo" title={providerTitle("mimo", true)}>
        <SettingsRow
          title="API key"
          description="Stored in macOS Keychain and never sent to the browser. When MiMo is your listing model, sold comps use its web search, which needs the Web Search plugin turned on in the MiMo console (Plugin Management)."
        >
          <div className="settings-row-field">
            <input
              className="input font-mono"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveKey();
              }}
              placeholder="sk-..."
              autoComplete="off"
              spellCheck={false}
            />
            <button type="button" className="btn btn-sm btn-outline" onClick={saveKey} disabled={!apiKey.trim() || setKeyMutation.isPending}>
              {setKeyMutation.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </SettingsRow>
        <SettingsRow
          title="Status"
          description={
            mimoConfigured
              ? primary === "mimo"
                ? `Primary · ${provider?.masked_key}`
                : fallback === "mimo"
                  ? `Fallback · ${provider?.masked_key}`
                  : `Saved · ${provider?.masked_key}`
              : primary === "mimo"
                ? "Primary — add a key to use MiMo"
                : fallback === "mimo"
                  ? "Fallback — add a key if ChatGPT is unavailable"
                  : "Not configured"
          }
          status={
            mimoTestResult ? (
              <span className={mimoTestResult.includes("successful") ? "text-success" : "text-error"}>{mimoTestResult}</span>
            ) : null
          }
          control={
            mimoConfigured ? (
              <>
                <button type="button" className="btn btn-sm btn-outline" onClick={handleMimoTest} disabled={mimoTesting}>
                  {mimoTesting ? "Testing…" : "Test"}
                </button>
                <button type="button" className="btn btn-sm btn-ghost settings-danger" onClick={() => deleteKeyMutation.mutate()}>
                  Remove
                </button>
              </>
            ) : null
          }
        />
        <SettingsRow
          title="Models"
          description="MiMo reads photos with mimo-v2.5 and writes listings with mimo-v2.5-pro. These are fixed."
        />
      </SettingsSection>

      <SettingsSection id="cursor" title={providerTitle("cursor")}>
        <SettingsRow
          title="API key"
          description="From Cursor Dashboard → Integrations. Stored in Keychain; listing runs use the local Cursor SDK against an empty scratch folder. When Cursor is your listing model it also searches the web for sold comps."
        >
          <div className="settings-row-field">
            <input
              className="input font-mono"
              type="password"
              value={cursorKey}
              onChange={(e) => setCursorKey(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") saveCursorKey();
              }}
              placeholder="cursor_..."
              autoComplete="off"
              spellCheck={false}
            />
            <button
              type="button"
              className="btn btn-sm btn-outline"
              onClick={saveCursorKey}
              disabled={!cursorKey.trim() || setCursorMutation.isPending}
            >
              {setCursorMutation.isPending ? "Saving…" : "Save"}
            </button>
          </div>
        </SettingsRow>
        <SettingsRow
          title="Status"
          description={
            cursorConfigured
              ? primary === "cursor"
                ? `Primary · ${provider?.masked_cursor_key}`
                : fallback === "cursor"
                  ? `Fallback · ${provider?.masked_cursor_key}`
                  : `Saved · ${provider?.masked_cursor_key}`
              : primary === "cursor"
                ? "Primary — add a Cursor API key"
                : fallback === "cursor"
                  ? "Fallback — add a key if other providers are unavailable"
                  : "Not configured"
          }
          status={
            cursorTestResult ? (
              <span className={cursorTestResult.includes("successful") ? "text-success" : "text-error"}>
                {cursorTestResult}
              </span>
            ) : null
          }
          control={
            cursorConfigured ? (
              <>
                <button
                  type="button"
                  className="btn btn-sm btn-outline"
                  onClick={handleCursorTest}
                  disabled={cursorTesting}
                >
                  {cursorTesting ? "Testing…" : "Test"}
                </button>
                <button
                  type="button"
                  className="btn btn-sm btn-ghost settings-danger"
                  onClick={() => deleteCursorMutation.mutate()}
                >
                  Remove
                </button>
              </>
            ) : null
          }
        />
        {cursorConfigured ? (
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
        ) : null}
      </SettingsSection>

      <BraveSearchSection chatgptSignedIn={chatgptSignedIn} />
    </>
  );
}
