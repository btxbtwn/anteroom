/**
 * The Usage page: how much of each provider's subscription is left, with
 * the reset times. Laid out like T3 Code's Usage page in its Limits view
 * (apps/web/src/components/usage/UsagePage.tsx and UsageLimitsPooled.tsx,
 * MIT, © 2026 T3 Tools Inc.).
 */
import { RefreshCwIcon } from "lucide-react";
import { useState } from "react";

import { cn } from "../lib/utils";
import { Button } from "./ui/button";
import { Spinner } from "./ui/spinner";
import { ProviderUsageSection } from "./usage/UsageLimits";
import { useUsageLimits } from "./usage/useUsageLimits";
import { PageHeader, WorkspacePage } from "./WorkspacePage";

export function UsagePage() {
  const usage = useUsageLimits();
  // The page advances `now` on refresh rather than ticking, as T3's does.
  const [now, setNow] = useState(() => Date.now());
  const providers = usage.data?.providers ?? [];
  const withLimits = providers.filter((provider) => provider.windows.length > 0);
  const withoutLimits = providers.filter((provider) => provider.windows.length === 0);

  return (
    <WorkspacePage>
      <PageHeader
        title="Usage"
        description="How much of each subscription is left, and when it resets. Hover or tap a bar for details."
        actions={
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Refresh limits"
          aria-busy={usage.refreshing}
          disabled={usage.refreshing}
          onClick={() => {
            void usage.refresh().finally(() => setNow(Date.now()));
          }}
        >
          <RefreshCwIcon className={cn(usage.refreshing && "animate-spin")} />
        </Button>
        }
      />
      {usage.isLoading ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner className="size-4" /> Reading usage…
        </p>
      ) : usage.error ? (
        <p className="text-sm text-muted-foreground">Could not read usage: {(usage.error as Error).message}</p>
      ) : (
        <>
          {withLimits.length === 0 ? (
            <p className="text-sm text-muted-foreground">No provider reports subscription limits.</p>
          ) : null}
          {[...withLimits, ...withoutLimits].map((limits) => (
            <ProviderUsageSection key={limits.provider} limits={limits} now={now} />
          ))}
        </>
      )}
    </WorkspacePage>
  );
}
