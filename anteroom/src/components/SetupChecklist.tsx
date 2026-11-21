import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckIcon } from "lucide-react";
import { api } from "../api/client";
import { cn } from "../lib/utils";
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

type HostedSetupChecklistViewProps = {
  managedAI: boolean;
  providerConfigured: boolean;
  ebayConnected: boolean;
  etsyConnected: boolean;
  creating?: boolean;
  onOpenProviders: () => void;
  onOpenConnections: () => void;
  onCreate: () => void;
};

type HostedSetupChecklistProps = Omit<HostedSetupChecklistViewProps, "ebayConnected" | "etsyConnected">;

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

/** Hosted web first-run checklist shown to new sellers before their first listing. */
export function HostedSetupChecklistView({
  managedAI,
  providerConfigured,
  ebayConnected,
  etsyConnected,
  creating,
  onOpenProviders,
  onOpenConnections,
  onCreate,
}: HostedSetupChecklistViewProps) {
  let stepNumber = 1;
  const aiStep = !managedAI ? stepNumber++ : null;
  const directStep = stepNumber++;
  const chromeStep = stepNumber++;
  const createStep = stepNumber++;
  const directDone = ebayConnected || etsyConnected;
  const directNote = ebayConnected && etsyConnected
    ? "eBay and Etsy connected."
    : ebayConnected
      ? "eBay connected. You can also connect Etsy anytime in Settings."
      : etsyConnected
        ? "Etsy connected. You can also connect eBay anytime in Settings."
        : "Sign in directly from your browser — no extension needed for eBay or Etsy.";

  return (
    <Empty className="flex-1 overflow-y-auto">
      <div className="flex w-full max-w-md flex-col gap-6">
        <EmptyHeader className="max-w-none">
          <EmptyTitle>Turn product photos into marketplace-ready drafts</EmptyTitle>
          <EmptyDescription>
            Create your first listing now, or connect marketplaces first so you’re ready to cross-list.
          </EmptyDescription>
        </EmptyHeader>
        <ol className="divide-y divide-border overflow-hidden rounded-xl border border-border bg-card text-left shadow-xs/5">
          {aiStep !== null ? (
            <Step index={aiStep} done={providerConfigured} label="Add an AI provider key to generate listings.">
              {providerConfigured ? (
                <Note>AI provider ready.</Note>
              ) : (
                <div className="mt-2.5 flex flex-wrap gap-2">
                  <Button type="button" variant="outline" size="sm" onClick={onOpenProviders}>
                    Open AI settings
                  </Button>
                </div>
              )}
            </Step>
          ) : null}
          <Step index={directStep} done={directDone} label="Connect eBay or Etsy in Settings.">
            <Note>{directNote}</Note>
            {!(ebayConnected && etsyConnected) ? (
              <div className="mt-2.5 flex flex-wrap gap-2">
                <Button type="button" variant="outline" size="sm" onClick={onOpenConnections}>
                  Open Connections
                </Button>
              </div>
            ) : null}
          </Step>
          <Step
            index={chromeStep}
            done={false}
            label="Optional: connect Chrome with Anteroom Bridge for Poshmark, Mercari, and Depop."
          >
            <Note>
              Install Anteroom Bridge in Chrome on a computer when you want to list on those marketplaces, then connect it from your account menu.
            </Note>
          </Step>
          <Step index={createStep} done={false} label="Create a listing and drop in product photos.">
            <Button
              type="button"
              size="sm"
              className="mt-2.5"
              disabled={creating}
              onClick={onCreate}
            >
              Create a listing
            </Button>
            {!providerConfigured ? (
              <Note>You can add photos now and configure an AI provider key before generating.</Note>
            ) : null}
          </Step>
        </ol>
      </div>
    </Empty>
  );
}

/** Queries the seller's direct marketplace connections for the hosted onboarding checklist. */
export function HostedSetupChecklist(props: HostedSetupChecklistProps) {
  const { data } = useQuery({
    queryKey: ["direct-connections"],
    queryFn: api.directConnections.status,
    staleTime: 30_000,
    retry: false,
  });
  return (
    <HostedSetupChecklistView
      {...props}
      ebayConnected={Boolean(data?.accounts?.ebay?.connected)}
      etsyConnected={Boolean(data?.accounts?.etsy?.connected)}
    />
  );
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
  return (
    <Empty className="flex-1 overflow-y-auto">
      <div className="flex w-full max-w-md flex-col gap-6">
        <EmptyHeader className="max-w-none">
          <EmptyTitle>Turn product photos into marketplace-ready drafts</EmptyTitle>
          <EmptyDescription>Create and generate listings without Chrome. Connect marketplaces when you’re ready to list.</EmptyDescription>
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
            label="Optional: connect desktop Chrome with the Bridge extension for Poshmark, Mercari and Depop."
          >
            <Note>eBay and Etsy use connected accounts and do not need the extension.</Note>
            {!chromeAvailable ? (
              <Note>
                Install{" "}
                <a className="underline underline-offset-2" href="https://www.google.com/chrome" target="_blank" rel="noreferrer">
                  Google Chrome
                </a>
                {" "}on a computer when you want to list on those marketplaces. You can create drafts now.
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
