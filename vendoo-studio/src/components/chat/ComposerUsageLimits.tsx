/**
 * The composer's `/usage-limits` panel: typing the command and sending it shows
 * the active provider's usage instead of sending a message, as T3 Code does.
 * It is answered locally and closes on the next send or on dismiss.
 *
 * Adapted from T3 Code (MIT, © 2026 T3 Tools Inc.):
 * apps/web/src/components/chat/ComposerUsageLimits.tsx and
 * useUsageLimitsPanel.ts, and the command from packages/shared/src/usageLimits.ts.
 */
import { useQuery } from "@tanstack/react-query";
import { GaugeIcon, XIcon } from "lucide-react";

import { api } from "../../api/client";
import { Button } from "../ui/button";
import { LimitWindows } from "../usage/UsageLimits";
import { limitsNotice } from "../usage/limitWindows";
import { USAGE_LIMITS_QUERY_KEY } from "../usage/useUsageLimits";
import { activeProviderId } from "./useComposerModel";

export function isUsageLimitsCommand(prompt: string): boolean {
  return prompt.trim().toLowerCase() === "/usage-limits";
}

export function ComposerUsageLimits({ onDismiss }: { onDismiss: () => void }) {
  const status = useQuery({ queryKey: ["settings-provider"], queryFn: api.settings.provider, staleTime: 30_000 });
  const usage = useQuery({ queryKey: USAGE_LIMITS_QUERY_KEY, queryFn: () => api.settings.usageLimits() });
  const active = activeProviderId(status.data);
  const limits = usage.data?.providers.find((provider) => provider.provider === active) ?? null;
  // T3 reads the bars against the moment the panel opened; the read time stands in for it.
  const now = limits ? Date.parse(limits.checked_at) : 0;
  const notice = limits ? limitsNotice(limits) : null;
  return (
    <div
      data-composer-usage-limits="true"
      className="mb-2 flex flex-col gap-1.5 rounded-2xl border border-border/70 bg-muted/30 px-3 py-2"
    >
      <div className="flex min-w-0 items-center gap-2 text-xs">
        <GaugeIcon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="font-medium text-foreground">Usage limits</span>
        {limits ? (
          <span className="min-w-0 truncate text-muted-foreground">
            {limits.label}
            {limits.plan ? ` · ${limits.plan}` : null}
          </span>
        ) : null}
        <Button
          size="icon-xs"
          variant="ghost-muted"
          className="ms-auto -me-1"
          aria-label="Dismiss usage limits"
          onClick={onDismiss}
        >
          <XIcon />
        </Button>
      </div>
      {usage.isLoading ? (
        <span className="text-xs text-muted-foreground">Reading usage…</span>
      ) : !limits ? (
        <span className="text-xs text-muted-foreground">Usage limits are unavailable for this provider.</span>
      ) : notice ? (
        <span className="text-xs text-muted-foreground">{notice}</span>
      ) : (
        <LimitWindows compact provider={limits.provider} windows={limits.windows} now={now} />
      )}
    </div>
  );
}
