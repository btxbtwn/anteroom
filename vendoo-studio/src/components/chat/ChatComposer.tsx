import type { RefObject } from "react";
import type { BrowserField } from "../../api/client";
import { ComposerPromptEditor, type ComposerPromptEditorHandle } from "../ComposerPromptEditor";

const MARKET_LABELS: Record<string, string> = {
  general: "Vendoo",
  ebay: "eBay",
  etsy: "Etsy",
  poshmark: "Poshmark",
  mercari: "Mercari",
  depop: "Depop",
};

interface Props {
  composerRef: RefObject<ComposerPromptEditorHandle | null>;
  input: string;
  onInputChange: (value: string) => void;
  placeholder: string;
  /** Fields picked in Studio's browser; they go with the next message. */
  browserFields: BrowserField[] | undefined;
  onBrowserFieldsChange: ((fields: BrowserField[]) => void) | undefined;
  busy: boolean;
  stopping: boolean;
  onSend: () => void;
  onCancel: () => Promise<void>;
}

export function ChatComposer({
  composerRef,
  input,
  onInputChange,
  placeholder,
  browserFields,
  onBrowserFieldsChange,
  busy,
  stopping,
  onSend,
  onCancel,
}: Props) {
  const canSubmit = Boolean(input.trim() || browserFields?.length);
  return (
    <div className="chat-composer">
      {browserFields && browserFields.length > 0 && (
        <div className="chat-browser-fields" aria-label="Fields pointed at in the Vendoo browser">
          <div className="chat-browser-fields-head">
            <span>
              {browserFields.length} field{browserFields.length === 1 ? "" : "s"} from the browser go with your message
            </span>
            <button type="button" className="btn btn-ghost btn-sm" onClick={() => onBrowserFieldsChange?.([])}>
              Clear
            </button>
          </div>
          <div className="chat-browser-fields-list">
            {browserFields.map((field) => (
              <span
                key={`${field.marketplace}:${field.key || field.label}`}
                className={`browser-chip${field.account_managed ? " is-muted" : ""}`}
                title={field.value || "empty"}
              >
                {MARKET_LABELS[field.marketplace] || field.marketplace} / {field.label}
                {!field.filled && <span className="browser-chip-empty">empty</span>}
                <button
                  type="button"
                  aria-label={`Remove ${field.label}`}
                  onClick={() => onBrowserFieldsChange?.(browserFields.filter((item) => item !== field))}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        </div>
      )}
      <div className="chat-composer-pill">
        <ComposerPromptEditor
          handleRef={composerRef}
          value={input}
          onChange={onInputChange}
          onSubmit={onSend}
          placeholder={placeholder}
        />
        {busy ? (
          <>
            <button
              type="button"
              className="chat-send chat-send-cancel"
              onClick={() => { void onCancel(); }}
              disabled={stopping}
              aria-label="Stop"
              title="Stop everything running for this listing"
            >
              <svg width="10" height="10" viewBox="0 0 10 10" fill="currentColor" aria-hidden="true">
                <rect x="1" y="1" width="8" height="8" rx="1" />
              </svg>
            </button>
            {canSubmit ? (
              <button
                type="button"
                className="chat-send"
                onClick={onSend}
                aria-label="Queue message"
                title="Queue for when the current run finishes"
              >
                <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <path d="M8 12.5V3.5M8 3.5L3.5 8M8 3.5L12.5 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            ) : null}
          </>
        ) : (
          <button
            type="button"
            className="chat-send"
            onClick={onSend}
            disabled={!canSubmit}
            aria-label="Send"
          >
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
              <path d="M8 12.5V3.5M8 3.5L3.5 8M8 3.5L12.5 8" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
        )}
      </div>
    </div>
  );
}
