import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { BUSY_POLL_MS, IDLE_POLL_MS, pollMs } from "../api/polling";
import { dismissSetupGuide } from "../onboarding";
import { ClaudeLoginTerminal } from "./ClaudeLoginTerminal";
import { ConnectChromeButton } from "./ConnectChromeButton";
import { ExtensionLoadPath } from "./ExtensionLoadPath";
import { KEY_PROVIDER_IDS, KEY_PROVIDER_LABELS, isKeyProviderId, isListingProviderId } from "./keyProviders";
import { useVendooEnabled } from "../vendooMode";
import type { KeyProviderId, ListingProviderId } from "../api/types";

export const SETUP_GUIDE_STEPS = [
  { id: "welcome", label: "Welcome" },
  { id: "listing-ai", label: "Listing AI" },
  { id: "brave", label: "Brave Search" },
  { id: "chrome", label: "Connect Chrome" },
  { id: "tour", label: "How Anteroom works" },
  { id: "updates", label: "Updating live listings" },
  { id: "ready", label: "Create a listing" },
] as const;

type StepId = (typeof SETUP_GUIDE_STEPS)[number]["id"];
type ListingChoice = ListingProviderId;

type FirstRunGuideProps = {
  providerConfigured: boolean;
  chromeAvailable: boolean;
  extensionConnected: boolean;
  creating?: boolean;
  onClose: () => void;
  onCreateListing: () => boolean;
};

export function FirstRunGuide({
  providerConfigured,
  chromeAvailable,
  extensionConnected,
  creating,
  onClose,
  onCreateListing,
}: FirstRunGuideProps) {
  const vendooOn = useVendooEnabled();
  const queryClient = useQueryClient();
  const [step, setStep] = useState<StepId>("welcome");
  const [listingChoice, setListingChoice] = useState<ListingChoice | null>(null);
  const [mimoKey, setMimoKey] = useState("");
  const [cursorKey, setCursorKey] = useState("");
  const [claudeTerminalOpen, setClaudeTerminalOpen] = useState(false);
  const [braveKey, setBraveKey] = useState("");
  const [mimoMessage, setMimoMessage] = useState<string | null>(null);
  const [cursorMessage, setCursorMessage] = useState<string | null>(null);
  const [braveMessage, setBraveMessage] = useState<string | null>(null);
  const [keyProvider, setKeyProvider] = useState<KeyProviderId>("openai");
  const [aiKey, setAIKey] = useState("");
  const [aiKeyMessage, setAIKeyMessage] = useState<string | null>(null);
  const [openRouterWaiting, setOpenRouterWaiting] = useState(false);

  const { data: provider } = useQuery({
    queryKey: ["settings-provider"],
    queryFn: api.settings.provider,
    // Fast only while a ChatGPT or Claude sign-in waits on the browser; key saves invalidate it.
    refetchInterval: (query) =>
      pollMs(query.state.data?.chatgpt?.pending ? BUSY_POLL_MS : IDLE_POLL_MS),
  });
  const { data: aiKeys } = useQuery({
    queryKey: ["ai-keys"],
    queryFn: api.settings.aiKeys,
    // Fast only while OpenRouter's sign-in waits on the browser for its key.
    refetchInterval: (query) =>
      openRouterWaiting && !query.state.data?.providers.some((entry) => entry.id === "openrouter" && entry.masked_key)
        ? BUSY_POLL_MS
        : false,
  });
  const savedKeyIds = (aiKeys?.providers ?? []).filter((entry) => entry.masked_key).map((entry) => entry.id);
  const keyEntry = aiKeys?.providers.find((entry) => entry.id === keyProvider);
  const { data: brave } = useQuery({
    queryKey: ["settings-brave"],
    queryFn: api.settings.brave,
  });

  const chatgptSignedIn = Boolean(provider?.chatgpt?.signed_in);
  const chatgptPending = provider?.chatgpt?.pending;
  const claudeSignedIn = Boolean(provider?.claude?.signed_in);
  const mimoConfigured = Boolean(provider?.masked_key);
  const cursorConfigured = Boolean(provider?.masked_cursor_key);
  const braveConfigured = Boolean(brave?.configured);
  const preferred = isListingProviderId(provider?.primary) ? provider.primary : null;
  const selectedListing =
    listingChoice ??
    preferred ??
    (chatgptSignedIn
      ? "chatgpt"
      : claudeSignedIn
        ? "claude"
        : mimoConfigured
          ? "mimo"
          : cursorConfigured
            ? "cursor"
            : (savedKeyIds[0] ?? "chatgpt"));
  const keyCardSelected = isKeyProviderId(selectedListing);

  const refreshProvider = () => {
    queryClient.invalidateQueries({ queryKey: ["settings-provider"] });
    queryClient.invalidateQueries({ queryKey: ["status"] });
  };

  const setPreferred = useMutation({
    mutationFn: (primary: ListingChoice) =>
      api.settings.setPreferredProvider({
        primary,
        fallback: primary === "chatgpt" ? "mimo" : "chatgpt",
      }),
    onSuccess: refreshProvider,
  });

  const chooseListing = (choice: ListingChoice) => {
    setListingChoice(choice);
    setPreferred.mutate(choice);
  };

  const chatgptLogin = useMutation({
    mutationFn: () => api.settings.chatgptLogin(),
    onSuccess: () => {
      setPreferred.mutate("chatgpt");
      refreshProvider();
    },
  });
  const chatgptCancel = useMutation({
    mutationFn: () => api.settings.chatgptCancelLogin(),
    onSuccess: refreshProvider,
  });
  const saveMimo = useMutation({
    mutationFn: (key: string) => api.settings.setProvider(key),
    onSuccess: () => {
      setMimoKey("");
      setMimoMessage("MiMo key saved in Keychain.");
      setPreferred.mutate("mimo");
      refreshProvider();
    },
    onError: (err: Error) => setMimoMessage(err.message),
  });
  const saveCursor = useMutation({
    mutationFn: (key: string) => api.settings.setCursor(key),
    onSuccess: () => {
      setCursorKey("");
      setCursorMessage("Cursor key saved in Keychain.");
      setPreferred.mutate("cursor");
      refreshProvider();
    },
    onError: (err: Error) => setCursorMessage(err.message),
  });
  const saveAIKey = useMutation({
    mutationFn: ({ id, key }: { id: KeyProviderId; key: string }) => api.settings.setAIKey(id, key),
    onSuccess: (_result, { id }) => {
      setAIKey("");
      setAIKeyMessage(`${KEY_PROVIDER_LABELS[id]} key saved in Keychain.`);
      setPreferred.mutate(id);
      queryClient.invalidateQueries({ queryKey: ["ai-keys"] });
      refreshProvider();
    },
    onError: (err: Error) => setAIKeyMessage(err.message),
  });
  const openRouterSignIn = useMutation({
    mutationFn: () => api.settings.openRouterSignIn(),
    onSuccess: () => {
      setOpenRouterWaiting(true);
      setPreferred.mutate("openrouter");
    },
    onError: (err: Error) => setAIKeyMessage(err.message),
  });
  const chooseKeyProvider = (id: KeyProviderId) => {
    setKeyProvider(id);
    setAIKeyMessage(null);
    setListingChoice(id);
    if (savedKeyIds.includes(id)) setPreferred.mutate(id);
  };
  const saveBrave = useMutation({
    mutationFn: (key: string) => api.settings.setBrave(key),
    onSuccess: () => {
      setBraveKey("");
      setBraveMessage("Brave key saved in Keychain.");
      queryClient.invalidateQueries({ queryKey: ["settings-brave"] });
    },
    onError: (err: Error) => setBraveMessage(err.message),
  });

  const steps: readonly { id: StepId; label: string }[] = vendooOn
    ? SETUP_GUIDE_STEPS
    : SETUP_GUIDE_STEPS.filter((item) => item.id !== "updates");
  const activeStep: StepId = !vendooOn && step === "updates" ? "tour" : step;
  const stepIndex = steps.findIndex((item) => item.id === activeStep);
  const isLast = activeStep === "ready";
  const listingReady =
    chatgptSignedIn || claudeSignedIn || mimoConfigured || cursorConfigured || savedKeyIds.length > 0 || providerConfigured;
  const canCreateListing = true;
  const chromeReady = extensionConnected;
  const canAdvance =
    activeStep === "listing-ai" ? listingReady : true;

  useEffect(() => {
    if (!chatgptSignedIn && !claudeSignedIn && !mimoConfigured && !cursorConfigured) return;
    queryClient.invalidateQueries({ queryKey: ["status"] });
  }, [chatgptSignedIn, claudeSignedIn, mimoConfigured, cursorConfigured, queryClient]);

  const finish = () => {
    dismissSetupGuide();
    onClose();
  };

  const goNext = () => {
    if (isLast) {
      finish();
      return;
    }
    setStep(steps[stepIndex + 1].id);
  };

  const goBack = () => {
    if (stepIndex <= 0) return;
    setStep(steps[stepIndex - 1].id);
  };

  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = dialogRef.current;
    if (!root) return;
    const focusable = () => Array.from(root.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
    )).filter((el) => !el.hasAttribute("disabled"));
    const first = focusable()[0];
    first?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        dismissSetupGuide();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const firstItem = items[0];
      const lastItem = items[items.length - 1];
      if (event.shiftKey && document.activeElement === firstItem) {
        event.preventDefault();
        lastItem.focus();
      } else if (!event.shiftKey && document.activeElement === lastItem) {
        event.preventDefault();
        firstItem.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown, true);
    return () => window.removeEventListener("keydown", onKeyDown, true);
  }, [onClose, step]);

  const doneFor = (id: StepId) => {
    if (id === "welcome") return stepIndex > 0;
    if (id === "listing-ai") return listingReady;
    if (id === "brave") return braveConfigured || stepIndex > steps.findIndex((item) => item.id === "brave");
    if (id === "chrome") return chromeReady;
    if (id === "tour") return stepIndex > steps.findIndex((item) => item.id === "tour");
    if (id === "updates") return stepIndex > steps.findIndex((item) => item.id === "updates");
    return false;
  };

  return (
    <div className="setup-guide" role="presentation">
      <div className="setup-guide-backdrop" />
      <div
        ref={dialogRef}
        className="setup-guide-popup"
        role="dialog"
        aria-modal="true"
        aria-labelledby="setup-guide-title"
      >
        <ol className="setup-guide-rail" aria-label="Setup steps">
          {steps.map((item, index) => {
            const current = item.id === activeStep;
            const done = doneFor(item.id);
            return (
              <li key={item.id}>
                <button
                  type="button"
                  className={`setup-guide-rail-item${current ? " current" : ""}${done ? " done" : ""}`}
                  onClick={() => setStep(item.id)}
                >
                  <span className="setup-guide-rail-mark" aria-hidden="true">
                    {done && !current ? "✓" : index + 1}
                  </span>
                  <span>{item.label}</span>
                </button>
              </li>
            );
          })}
        </ol>

        <div className="setup-guide-main">
          <div className="setup-guide-body">
            {activeStep === "welcome" ? (
              <>
                <h2 id="setup-guide-title" className="setup-guide-title">{vendooOn ? "Turn photos into Vendoo drafts" : "Turn photos into listings"}</h2>
                <p className="setup-guide-copy">
                  {vendooOn
                    ? "Anteroom stays on this Mac. You add product photos, chat writes the listing, then Anteroom fills a Vendoo draft. It never publishes. You review and send live yourself."
                    : "Anteroom stays on this Mac. You add product photos, chat writes the listing, then you list it on the marketplace. Anteroom never publishes until you confirm."}
                </p>
                <ul className="setup-guide-points">
                  <li>Choose ChatGPT, a Xiaomi MiMo key, or a Cursor API key so chat can read photos.</li>
                  <li>Optionally add Brave Search for sold-price comps.</li>
                  <li>Connect everyday Chrome once, then create a listing.</li>
                </ul>
              </>
            ) : null}

            {activeStep === "listing-ai" ? (
              <>
                <h2 id="setup-guide-title" className="setup-guide-title">Choose listing AI</h2>
                <p className="setup-guide-copy">
                  Pick a primary. The other becomes the fallback automatically; you can change both later in Settings.
                </p>
                <div className="setup-guide-choices" role="radiogroup" aria-label="Listing AI">
                  <button
                    type="button"
                    role="radio"
                    aria-checked={selectedListing === "chatgpt"}
                    className={`setup-guide-choice${selectedListing === "chatgpt" ? " selected" : ""}`}
                    onClick={() => chooseListing("chatgpt")}
                  >
                    <strong>ChatGPT</strong>
                    <span>Sign in. Best if you already pay for ChatGPT.</span>
                    {chatgptSignedIn ? <em>Signed in{provider?.chatgpt?.email ? ` as ${provider.chatgpt.email}` : ""}</em> : null}
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={selectedListing === "claude"}
                    className={`setup-guide-choice${selectedListing === "claude" ? " selected" : ""}`}
                    onClick={() => chooseListing("claude")}
                  >
                    <strong>Claude</strong>
                    <span>Sign in. Best if you already pay for Claude Pro or Max.</span>
                    {claudeSignedIn ? <em>Signed in{provider?.claude?.email ? ` as ${provider.claude.email}` : ""}</em> : null}
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={selectedListing === "mimo"}
                    className={`setup-guide-choice${selectedListing === "mimo" ? " selected" : ""}`}
                    onClick={() => chooseListing("mimo")}
                  >
                    <strong>Xiaomi MiMo</strong>
                    <span>Paste an API key if you are not using ChatGPT.</span>
                    {mimoConfigured ? <em>Key saved · {provider?.masked_key}</em> : null}
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={selectedListing === "cursor"}
                    className={`setup-guide-choice${selectedListing === "cursor" ? " selected" : ""}`}
                    onClick={() => chooseListing("cursor")}
                  >
                    <strong>Cursor</strong>
                    <span>Paste a Cursor API key to use Composer for listings.</span>
                    {cursorConfigured ? <em>Key saved · {provider?.masked_cursor_key}</em> : null}
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={keyCardSelected}
                    className={`setup-guide-choice${keyCardSelected ? " selected" : ""}`}
                    onClick={() => chooseKeyProvider(isKeyProviderId(selectedListing) ? selectedListing : keyProvider)}
                  >
                    <strong>Your own API key</strong>
                    <span>OpenAI, Anthropic, Google Gemini, xAI, Mistral or OpenRouter. The provider bills you per use.</span>
                    {savedKeyIds.length ? <em>Key saved · {savedKeyIds.map((id) => KEY_PROVIDER_LABELS[id]).join(", ")}</em> : null}
                  </button>
                </div>
                {selectedListing === "chatgpt" ? (
                  <div className="setup-guide-task">
                    {chatgptSignedIn ? (
                      <p className="setup-guide-copy">ChatGPT is ready. Continue when you want.</p>
                    ) : chatgptPending ? (
                      <>
                        <p className="setup-guide-copy">
                          Approve Anteroom in your browser, and leave token sharing on.{" "}
                          <a href={chatgptPending.authorize_url} target="_blank" rel="noreferrer">
                            Open the sign-in page again
                          </a>
                        </p>
                        <button
                          type="button"
                          className="btn btn-sm btn-outline"
                          onClick={() => chatgptCancel.mutate()}
                          disabled={chatgptCancel.isPending}
                        >
                          Cancel sign-in
                        </button>
                      </>
                    ) : (
                      <button
                        type="button"
                        className="btn btn-primary btn-sm"
                        onClick={() => chatgptLogin.mutate()}
                        disabled={chatgptLogin.isPending}
                      >
                        {chatgptLogin.isPending ? "Starting…" : "Sign in with ChatGPT"}
                      </button>
                    )}
                    {chatgptLogin.isError ? (
                      <p className="setup-guide-error">{(chatgptLogin.error as Error).message}</p>
                    ) : null}
                  </div>
                ) : selectedListing === "claude" ? (
                  <div className="setup-guide-task">
                    {claudeSignedIn ? (
                      <p className="setup-guide-copy">Claude is ready. Continue when you want.</p>
                    ) : claudeTerminalOpen ? (
                      <ClaudeLoginTerminal
                        onClose={(signedIn) => {
                          setClaudeTerminalOpen(false);
                          if (signedIn) setPreferred.mutate("claude");
                          refreshProvider();
                        }}
                      />
                    ) : (
                      <>
                        {provider?.claude && !provider.claude.installed ? (
                          <p className="setup-guide-copy">
                            Needs Claude Code. Install it in Terminal with{" "}
                            <code>{provider.claude.install_command}</code>, then sign in.
                          </p>
                        ) : null}
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          onClick={() => setClaudeTerminalOpen(true)}
                          disabled={provider?.claude ? !provider.claude.installed : true}
                        >
                          Sign in with Claude
                        </button>
                      </>
                    )}
                  </div>
                ) : isKeyProviderId(selectedListing) ? (
                  <div className="setup-guide-task">
                    <label className="setup-guide-field">
                      <span>Provider</span>
                      <select
                        className="input"
                        value={selectedListing}
                        onChange={(e) => chooseKeyProvider(e.target.value as KeyProviderId)}
                      >
                        {KEY_PROVIDER_IDS.map((id) => (
                          <option key={id} value={id}>
                            {KEY_PROVIDER_LABELS[id]}
                          </option>
                        ))}
                      </select>
                    </label>
                    {keyEntry?.masked_key && keyEntry.id === selectedListing ? (
                      <p className="setup-guide-copy">
                        {KEY_PROVIDER_LABELS[selectedListing]} is ready · {keyEntry.masked_key}. Continue when you want.
                      </p>
                    ) : (
                      <>
                        {selectedListing === "openrouter" ? (
                          <button
                            type="button"
                            className="btn btn-primary btn-sm"
                            onClick={() => openRouterSignIn.mutate()}
                            disabled={openRouterSignIn.isPending || openRouterWaiting}
                          >
                            {openRouterWaiting ? "Waiting for the browser…" : "Sign in with OpenRouter"}
                          </button>
                        ) : null}
                        <label className="setup-guide-field">
                          <span>{KEY_PROVIDER_LABELS[selectedListing]} API key</span>
                          <input
                            className="input font-mono"
                            type="password"
                            value={aiKey}
                            onChange={(e) => setAIKey(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" && aiKey.trim()) saveAIKey.mutate({ id: selectedListing, key: aiKey });
                            }}
                            placeholder={keyEntry?.key_hint || "API key"}
                            autoComplete="off"
                            spellCheck={false}
                          />
                        </label>
                        <button
                          type="button"
                          className="btn btn-primary btn-sm"
                          disabled={!aiKey.trim() || saveAIKey.isPending}
                          onClick={() => saveAIKey.mutate({ id: selectedListing, key: aiKey })}
                        >
                          {saveAIKey.isPending ? "Saving…" : "Save key"}
                        </button>
                        {keyEntry?.key_url ? (
                          <p className="setup-guide-note">
                            Get a key at{" "}
                            <a href={keyEntry.key_url} target="_blank" rel="noreferrer">
                              {new URL(keyEntry.key_url).host}
                            </a>
                            . Usage is billed by the provider, apart from any chat subscription.
                          </p>
                        ) : null}
                      </>
                    )}
                    {aiKeyMessage ? <p className="setup-guide-note">{aiKeyMessage}</p> : null}
                  </div>
                ) : selectedListing === "cursor" ? (
                  <div className="setup-guide-task">
                    <label className="setup-guide-field">
                      <span>Cursor API key</span>
                      <input
                        className="input font-mono"
                        type="password"
                        value={cursorKey}
                        onChange={(e) => setCursorKey(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && cursorKey.trim()) saveCursor.mutate(cursorKey);
                        }}
                        placeholder="cursor_..."
                        autoComplete="off"
                        spellCheck={false}
                      />
                    </label>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      disabled={!cursorKey.trim() || saveCursor.isPending}
                      onClick={() => saveCursor.mutate(cursorKey)}
                    >
                      {saveCursor.isPending ? "Saving…" : "Save key"}
                    </button>
                    {cursorMessage ? <p className="setup-guide-note">{cursorMessage}</p> : null}
                  </div>
                ) : (
                  <div className="setup-guide-task">
                    <label className="setup-guide-field">
                      <span>MiMo API key</span>
                      <input
                        className="input font-mono"
                        type="password"
                        value={mimoKey}
                        onChange={(e) => setMimoKey(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && mimoKey.trim()) saveMimo.mutate(mimoKey);
                        }}
                        placeholder="sk-..."
                        autoComplete="off"
                        spellCheck={false}
                      />
                    </label>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      disabled={!mimoKey.trim() || saveMimo.isPending}
                      onClick={() => saveMimo.mutate(mimoKey)}
                    >
                      {saveMimo.isPending ? "Saving…" : "Save key"}
                    </button>
                    {mimoMessage ? <p className="setup-guide-note">{mimoMessage}</p> : null}
                  </div>
                )}
              </>
            ) : null}

            {activeStep === "brave" ? (
              <>
                <h2 id="setup-guide-title" className="setup-guide-title">Brave Search (optional)</h2>
                <p className="setup-guide-copy">
                  Sold-price comps use ChatGPT web search first. A Brave API key is the fallback when that misses. Skip if you only use ChatGPT, or get a key at{" "}
                  <a href="https://api.search.brave.com" target="_blank" rel="noreferrer">
                    api.search.brave.com
                  </a>
                  . Stored in Keychain.
                </p>
                {braveConfigured ? (
                  <p className="setup-guide-note">Brave is configured · {brave?.masked_key}</p>
                ) : (
                  <div className="setup-guide-task">
                    <label className="setup-guide-field">
                      <span>Brave API key</span>
                      <input
                        className="input font-mono"
                        type="password"
                        value={braveKey}
                        onChange={(e) => setBraveKey(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" && braveKey.trim()) saveBrave.mutate(braveKey);
                        }}
                        placeholder="BSA..."
                        autoComplete="off"
                        spellCheck={false}
                      />
                    </label>
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      disabled={!braveKey.trim() || saveBrave.isPending}
                      onClick={() => saveBrave.mutate(braveKey)}
                    >
                      {saveBrave.isPending ? "Saving…" : "Save key"}
                    </button>
                    {braveMessage ? <p className="setup-guide-note">{braveMessage}</p> : null}
                  </div>
                )}
              </>
            ) : null}

            {activeStep === "chrome" ? (
              <>
                <h2 id="setup-guide-title" className="setup-guide-title">Connect Chrome</h2>
                <p className="setup-guide-copy">
                  {vendooOn
                    ? "Anteroom uses Chrome for a signed-in Vendoo session. Load the listing extension from the folder below once, then sign in to Vendoo. After that, Send to Vendoo creates or updates a draft over the API. It never publishes."
                    : "Anteroom uses Chrome to open each marketplace. Load the listing extension from the folder below once. Listing on a marketplace opens that marketplace in this Chrome. Anteroom never publishes until you confirm."}
                </p>
                {!chromeAvailable ? (
                  <p className="setup-guide-copy">
                    Install{" "}
                    <a href="https://www.google.com/chrome" target="_blank" rel="noreferrer">
                      Google Chrome
                    </a>
                    , then come back.
                  </p>
                ) : chromeReady ? (
                  <p className="setup-guide-note">Extension connected.</p>
                ) : (
                  <div className="setup-guide-task">
                    <ConnectChromeButton className="btn btn-primary btn-sm" />
                    <ExtensionLoadPath />
                  </div>
                )}
              </>
            ) : null}

            {activeStep === "tour" ? (
              <>
                <h2 id="setup-guide-title" className="setup-guide-title">How the app is laid out</h2>
                <ul className="setup-guide-points">
                  <li>
                    <strong>Sidebar</strong> — your listings. Compose starts a new one. Settings is ChatGPT, MiMo, Brave, and Chrome.
                  </li>
                  <li>
                    <strong>Photos</strong> — drag product pictures into the tray. Order matters; the first photo is the hero.
                  </li>
                  <li>
                    <strong>Item details</strong> — cost, flaws, and measurements you already know. Chat uses these.
                  </li>
                  <li>
                    <strong>Chat</strong> — ask it to write the listing from the photos. Edit in conversation instead of starting over.
                  </li>
                  <li>
                    <strong>Listing panel</strong> — {vendooOn
                      ? "the draft fields for Vendoo, eBay, Poshmark, Mercari, Depop, and Etsy. Review here before send."
                      : "the draft fields for eBay, Poshmark, Mercari, Depop, and Etsy. Review here before you list."}
                  </li>
                  <li>
                    {vendooOn ? (
                      <><strong>Send to Vendoo</strong> — creates or updates the Vendoo form over Vendoo's API. Listing and relisting stay human clicks in Vendoo.</>
                    ) : (
                      <><strong>Marketplaces</strong> — list on eBay, Poshmark, and the others from here when you confirm. Anteroom never publishes on its own.</>
                    )}
                  </li>
                  <li>
                    <strong>Footer</strong> — extension and listing-AI status. If either is off, fix it in Settings before generating.
                  </li>
                </ul>
              </>
            ) : null}

            {activeStep === "updates" ? (
              <>
                <h2 id="setup-guide-title" className="setup-guide-title">Editing something already listed</h2>
                <p className="setup-guide-copy">
                  Anteroom writes the Vendoo <em>form</em>. Vendoo only carries a form change onto a
                  live marketplace listing when you delist and list it again — so a regenerated
                  listing is live on its old copy until you do that, in Vendoo.
                </p>
                <ul className="setup-guide-points">
                  <li>
                    <strong>1. Regenerate here.</strong> The listing wears a blue <em>unsent</em> chip until you press <strong>Update Vendoo</strong> — until then even the Vendoo form is on the old copy.
                  </li>
                  <li>
                    <strong>2. Update Vendoo, then open the item there.</strong> The Vendoo form now matches Anteroom; the live listings still do not. The ⋮ menu beside Vendoo Form → Delist Item takes it off every marketplace at once.
                  </li>
                  <li>
                    <strong>3. List it again from Vendoo.</strong> The new listing carries the updated copy, and its listing date resets.
                  </li>
                  <li>
                    <strong>Anteroom tracks every step for you.</strong> A blue <em>unsent</em> chip until the edit reaches Vendoo, then an amber <em>relist</em> chip until the listing is back up — and Filters collects each set. The relist chip stays through the middle, where the item reads as a draft because it really is live nowhere, and clears itself once Vendoo reports the new listing date.
                  </li>
                </ul>
              </>
            ) : null}

            {activeStep === "ready" ? (
              <>
                <h2 id="setup-guide-title" className="setup-guide-title">Create your first listing</h2>
                <p className="setup-guide-copy">
                  Drop in photos, add any notes, then ask chat to generate. Replay this guide anytime from Settings → General.
                </p>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={creating || !canCreateListing}
                  onClick={() => {
                    if (onCreateListing()) finish();
                  }}
                >
                  Create a listing
                </button>
                {!canCreateListing ? (
                  <p className="setup-guide-note">
                    {listingReady
                      ? "Waiting for Anteroom to see ChatGPT, MiMo, or Cursor before creating a listing."
                      : "Sign in with ChatGPT or save a MiMo or Cursor key first."}
                  </p>
                ) : null}
              </>
            ) : null}
          </div>

          <div className="setup-guide-footer">
            <button type="button" className="btn btn-ghost btn-sm" onClick={finish}>
              Skip
            </button>
            <div className="setup-guide-footer-nav">
              <button type="button" className="btn btn-outline btn-sm" onClick={goBack} disabled={stepIndex === 0}>
                Back
              </button>
              <button
                type="button"
                className="btn btn-primary btn-sm"
                onClick={goNext}
                disabled={!canAdvance}
                title={!canAdvance ? "Set up a listing AI first" : undefined}
              >
                {isLast ? "Done" : (activeStep === "brave" && !braveConfigured) || (activeStep === "chrome" && !chromeReady) ? "Skip for now" : "Continue"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
