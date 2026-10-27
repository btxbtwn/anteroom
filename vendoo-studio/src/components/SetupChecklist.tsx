import type { ReactNode } from "react";
import { CheckIcon } from "lucide-react";
import { cn } from "../lib/utils";
import { useVendooEnabled } from "../vendooMode";
import { ConnectChromeButton } from "./ConnectChromeButton";
import { ExtensionLoadPath } from "./ExtensionLoadPath";
import { Button } from "./ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyTitle } from "./ui/empty";

type SetupChecklistProps = {
  providerConfigured: boolean;
  chromeAvailable: boolean;
  extensionConnected: boolean;
  creating?: boolean;
  onOpenSettings: () => void;
  onStartGuide?: () => void;
  onCreate: () => void;
};

function Step({
  index,
  done,
  label,
  children,
}: {
  index: number;
  done: boolean;
  label: string;
  children?: ReactNode;
}) {
  return (
    <li className="flex gap-3 px-4 py-3.5">
      <span
        aria-hidden="true"
        className={cn(
          "mt-px inline-flex size-5 shrink-0 items-center justify-center rounded-full text-2xs font-semibold tabular-nums",
          done ? "bg-success/16 text-success-foreground" : "bg-muted text-muted-foreground ring-1 ring-border ring-inset",
        )}
      >
        {done ? <CheckIcon className="size-3" strokeWidth={3} /> : index}
      </span>
      <div className="min-w-0 flex-1">
        <p className={cn("text-sm leading-5", done ? "text-muted-foreground" : "font-medium text-foreground")}>
          {done ? <span className="sr-only">Done: </span> : null}
          {label}
        </p>
        {children}
      </div>
    </li>
  );
}

function Note({ children }: { children: ReactNode }) {
  return <p className="mt-1 text-xs text-muted-foreground">{children}</p>;
}

/** First-run steps on the empty listings view, laid out as T3 Code's Empty card. */
export function SetupChecklist({
  providerConfigured,
  chromeAvailable,
  extensionConnected,
  creating,
  onOpenSettings,
  onStartGuide,
  onCreate,
}: SetupChecklistProps) {
  const vendooOn = useVendooEnabled();
  return (
    <Empty className="flex-1 overflow-y-auto">
      <div className="flex w-full max-w-md flex-col gap-6">
        <EmptyHeader className="max-w-none">
          <EmptyTitle>Turn product photos into marketplace-ready drafts</EmptyTitle>
          <EmptyDescription>Three steps and Anteroom is ready to list.</EmptyDescription>
        </EmptyHeader>
        <ol className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card text-left shadow-xs/5">
          <Step index={1} done={providerConfigured} label="Sign in with ChatGPT, or add a MiMo or Cursor API key.">
            {providerConfigured ? (
              <Note>Ready to generate listings.</Note>
            ) : (
              <div className="mt-2.5 flex flex-wrap gap-2">
                {onStartGuide ? (
                  <Button type="button" size="sm" onClick={onStartGuide}>
                    Start setup guide
                  </Button>
                ) : null}
                <Button type="button" variant="outline" size="sm" onClick={onOpenSettings}>
                  Open Settings
                </Button>
              </div>
            )}
          </Step>
          <Step
            index={2}
            done={extensionConnected}
            label={vendooOn
              ? "Connect Chrome, load the listing extension once, and sign in to Vendoo."
              : "Connect Chrome and load the listing extension once. Listing opens the marketplace in that Chrome."}
          >
            {!chromeAvailable ? (
              <Note>
                Install{" "}
                <a className="underline underline-offset-2" href="https://www.google.com/chrome" target="_blank" rel="noreferrer">
                  Google Chrome
                </a>
                , then come back here.
              </Note>
            ) : extensionConnected ? (
              <Note>Extension connected.</Note>
            ) : (
              <>
                <ConnectChromeButton className="mt-2.5" />
                <ExtensionLoadPath compact />
              </>
            )}
          </Step>
          <Step index={3} done={false} label="Create a listing and drop in photos.">
            <Button
              type="button"
              size="sm"
              className="mt-2.5"
              disabled={creating || !providerConfigured}
              onClick={onCreate}
            >
              Create a listing
            </Button>
            {!providerConfigured ? <Note>Sign in first so chat can generate the listing.</Note> : null}
          </Step>
        </ol>
      </div>
    </Empty>
  );
}
