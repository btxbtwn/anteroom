// Ported from T3 Code (https://github.com/pingdotgg/t3code), MIT License,
// (c) 2026 T3 Tools Inc.: apps/web/src/components/usage/UsageLimits.tsx
// (PaceIcon, the pace mark, WindowBar, LimitWindows) and UsageLimitsPooled.tsx
// (PoolSection, PoolWindowCard, the segment and its details). Studio has one
// account per provider, so each window is a single segment and its details
// open on hover; reset credits, hubs and multi-environment pooling are left out.
import { Fragment, useLayoutEffect, useState, type ReactNode } from "react";
import { AlertTriangleIcon, GaugeIcon, TrendingDownIcon, TrendingUpIcon } from "lucide-react";

import type { ListingProviderId, ProviderUsageLimits, ProviderUsageWindow } from "../../api/types";
import { cn } from "../../lib/utils";
import { ProviderInstanceIcon } from "../chat/ProviderInstanceIcon";
import { Tooltip, TooltipPopup, TooltipTrigger } from "../ui/tooltip";
import {
  formatResetsIn,
  formatUpcomingTimestamp,
  limitsNotice,
  paceOf,
  paceRemainingPercent,
  remainingPercent,
  type LimitPace,
} from "./limitWindows";

const PACE: Record<LimitPace, { readonly label: string; readonly icon: typeof GaugeIcon }> = {
  ahead: { label: "Ahead of pace: spending faster than the window elapses", icon: TrendingUpIcon },
  on: { label: "On pace with the window", icon: GaugeIcon },
  under: { label: "Under pace: headroom left for the rest of the window", icon: TrendingDownIcon },
};

/** T3's usage colours (components/usage/usageProviders.ts); MiMo reports no bars. */
const PROVIDER_COLOR: Record<ListingProviderId, string> = {
  chatgpt: "var(--contrast-foreground)",
  claude: "#d97757",
  cursor: "#8b8b8b",
  mimo: "color-mix(in oklab, var(--contrast-foreground) 72%, var(--background))",
};

export function barColor(provider: ListingProviderId): string {
  return PROVIDER_COLOR[provider] ?? "var(--foreground)";
}

export function PaceIcon({ pace }: { readonly pace: LimitPace }) {
  const Icon = PACE[pace].icon;
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <span
            role="img"
            aria-label={PACE[pace].label}
            className="inline-flex text-muted-foreground"
          />
        }
      >
        <Icon className="size-3.5" aria-hidden />
      </TooltipTrigger>
      <TooltipPopup side="top">{PACE[pace].label}</TooltipPopup>
    </Tooltip>
  );
}

// The tick sits in a transparent gap cut from the bar, clamped so it stays inside at 0% and 100%.
// The gap is a clip-path slot rather than a mask: a mask composites its layer and re-antialiases
// the bar's rounded ends, and the polygon reaches 1px past the box so only the slot edges clip.
function paceMarkStyles(timeLeft: number | null) {
  if (timeLeft === null) return null;
  const at = `clamp(3px, ${timeLeft}%, calc(100% - 3px))`;
  const [left, right] = [`calc(${at} - 3px)`, `calc(${at} + 3px)`];
  const [near, far] = ["-1px", "calc(100% + 1px)"];
  return {
    tick: { left: at },
    gap: {
      clipPath: `polygon(${near} ${near}, ${left} ${near}, ${left} ${far}, ${right} ${far}, ${right} ${near}, ${far} ${near}, ${far} ${far}, ${near} ${far})`,
    },
    // For a track painted on an element that also draws focus rings, which a clip would cut.
    gappedTrack: {
      backgroundColor: "transparent",
      backgroundImage: `linear-gradient(to right, var(--color-muted) ${left}, transparent ${left}, transparent ${right}, var(--color-muted) ${right})`,
    },
  };
}

/**
 * Pace mark styles for the bar given the returned ref. Below 6px there is no room for the gap
 * and tick, so the bar draws neither, as the mobile bars do.
 */
export function usePaceMark(timeLeft: number | null) {
  const [bar, setBar] = useState<HTMLElement | null>(null);
  const [roomy, setRoomy] = useState(false);
  useLayoutEffect(() => {
    if (!bar) return;
    const measure = () => setRoomy(bar.getBoundingClientRect().width >= 6);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(bar);
    return () => observer.disconnect();
  }, [bar]);
  return [setBar, roomy ? paceMarkStyles(timeLeft) : null] as const;
}

function WindowBar({
  color,
  window,
  now,
}: {
  readonly color: string;
  readonly window: ProviderUsageWindow;
  readonly now: number;
}) {
  const remaining = remainingPercent(window);
  const timeLeft = paceRemainingPercent(window, now);
  const [barRef, paceMark] = usePaceMark(timeLeft);
  const resetsIn = formatResetsIn(window, now);
  const resetsAt = window.resets_at ? formatUpcomingTimestamp(window.resets_at, now) : null;
  const summary = `${window.label}: ${remaining}% left${
    timeLeft === null ? "" : `, ${timeLeft}% of the window left`
  }${resetsIn ? `, ${resetsIn}` : ""}`;

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <div
            ref={barRef}
            role="img"
            aria-label={summary}
            tabIndex={0}
            className="relative h-6 cursor-default rounded-full outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          />
        }
      >
        {/* Clip both layers so the gap exposes even a translucent composer surface. */}
        <div className="absolute inset-0" style={paceMark?.gap}>
          <div className="absolute inset-x-0 inset-y-1.5 rounded-full bg-muted" />
          {remaining > 0 ? (
            <div
              className="absolute inset-y-1.5 left-0 rounded-full"
              style={{ width: `${remaining}%`, backgroundColor: color }}
            />
          ) : null}
        </div>
        {paceMark ? (
          <span
            aria-hidden
            className="absolute inset-y-1 w-0.5 -translate-x-1/2 rounded-full bg-foreground"
            style={paceMark.tick}
          />
        ) : null}
      </TooltipTrigger>
      <TooltipPopup side="top">
        <div className="flex flex-col gap-0.5">
          <span className="text-foreground">
            {remaining}% left{timeLeft !== null ? ` · ${timeLeft}% of the window left` : ""}
          </span>
          {timeLeft !== null ? (
            <span className="text-muted-foreground">The line is where even spending would be.</span>
          ) : null}
          {resetsAt ? (
            <span className="text-muted-foreground">
              Resets {resetsAt}
              {resetsIn ? ` · ${resetsIn}` : ""}
            </span>
          ) : null}
        </div>
      </TooltipPopup>
    </Tooltip>
  );
}

/** The compact rows T3 shows in the composer's usage-limits banner. */
export function LimitWindows({
  provider,
  windows,
  now,
  compact = false,
}: {
  readonly provider: ListingProviderId;
  readonly windows: ReadonlyArray<ProviderUsageWindow>;
  readonly now: number;
  readonly compact?: boolean;
}) {
  const color = barColor(provider);
  return (
    <div
      className={
        compact
          ? "grid grid-cols-[minmax(0,9rem)_minmax(3rem,1fr)_auto] gap-x-3 gap-y-0.5"
          : "grid grid-cols-[11rem_minmax(0,1fr)_7rem] gap-x-4 gap-y-1"
      }
    >
      {windows.map((window) => {
        const pace = paceOf(window, now);
        const resetsIn = formatResetsIn(window, now);
        return (
          <Fragment key={window.id}>
            <span className="flex min-w-0 items-center gap-2 text-xs">
              <span className="truncate text-muted-foreground">{window.label}</span>
              <span className="ms-auto shrink-0 font-medium text-foreground tabular-nums">
                {remainingPercent(window)}% left
              </span>
            </span>
            <WindowBar color={color} window={window} now={now} />
            <span className="flex items-center gap-2 text-xs whitespace-nowrap text-muted-foreground tabular-nums">
              {pace ? <PaceIcon pace={pace} /> : null}
              <span className="ms-auto shrink-0">{resetsIn ?? ""}</span>
            </span>
          </Fragment>
        );
      })}
    </div>
  );
}

function Row({ label, children }: { readonly label: string; readonly children: ReactNode }) {
  return (
    <div className="grid grid-cols-[4.5rem_minmax(0,1fr)] gap-x-3">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 text-foreground tabular-nums">{children}</span>
    </div>
  );
}

function SegmentDetails({
  limits,
  window,
  now,
}: {
  readonly limits: ProviderUsageLimits;
  readonly window: ProviderUsageWindow;
  readonly now: number;
}) {
  const remaining = remainingPercent(window);
  const timeLeft = paceRemainingPercent(window, now);
  const resetsIn = formatResetsIn(window, now);
  return (
    <div className="flex w-72 max-w-[calc(100vw-3rem)] flex-col gap-2.5 text-xs">
      <span className="flex items-center gap-2 text-sm font-medium text-foreground">
        <ProviderInstanceIcon
          driverKind={limits.provider}
          displayName={limits.label}
          className="size-5"
          iconClassName="size-4 text-foreground/80"
        />
        <span className="truncate">{limits.label}</span>
      </span>
      {limits.plan ? (
        <div className="flex flex-col gap-1 border-t border-border/60 pt-2.5">
          <Row label="Plan">{limits.plan}</Row>
        </div>
      ) : null}
      <div className="flex flex-col gap-1 border-t border-border/60 pt-2.5">
        <Row label="Left">{remaining}%</Row>
        {window.resets_at ? (
          <Row label="Resets">
            {formatUpcomingTimestamp(window.resets_at, now)}
            {resetsIn ? ` · ${resetsIn.replace("resets in ", "in ")}` : ""}
          </Row>
        ) : null}
        {timeLeft !== null ? <Row label="Even pace">{timeLeft}% left by now</Row> : null}
      </div>
    </div>
  );
}

function PoolSegment({
  limits,
  window,
  color,
  now,
}: {
  readonly limits: ProviderUsageLimits;
  readonly window: ProviderUsageWindow;
  readonly color: string;
  readonly now: number;
}) {
  const remaining = remainingPercent(window);
  const timeLeft = paceRemainingPercent(window, now);
  const [segmentRef, paceMark] = usePaceMark(timeLeft);
  const resetsIn = formatResetsIn(window, now);
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            ref={segmentRef}
            type="button"
            style={paceMark?.gappedTrack}
            aria-label={`${limits.label} ${window.label}: ${remaining}% left${resetsIn ? `, ${resetsIn}` : ""}${timeLeft === null ? "" : `, ${timeLeft}% of the window left`}`}
            className="relative block h-8 w-full min-w-0 cursor-default rounded-md bg-muted text-start outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          />
        }
      >
        <div aria-hidden className="absolute inset-0 overflow-hidden rounded-md">
          <div className="absolute inset-0" style={paceMark?.gap}>
            <div
              aria-hidden
              className="absolute inset-y-0 left-0 rounded-md opacity-35"
              style={{ width: `${remaining}%`, backgroundColor: color }}
            />
          </div>
        </div>
        {paceMark ? (
          <span
            aria-hidden
            className="absolute inset-y-0.5 w-0.5 -translate-x-1/2 rounded-full bg-foreground"
            style={paceMark.tick}
          />
        ) : null}
        <div className="relative flex h-full min-w-0 items-center gap-1.5 overflow-hidden rounded-md px-2 text-xs">
          <span className="shrink-0 font-semibold text-foreground tabular-nums">{remaining}%</span>
          {resetsIn ? (
            <span className="ms-auto flex min-w-0 items-center gap-1.5 overflow-hidden rounded-sm bg-background/85 px-1.5 py-0.5 text-2xs whitespace-nowrap text-foreground tabular-nums">
              {resetsIn.replace("resets in ", "↻ ")}
            </span>
          ) : null}
        </div>
      </TooltipTrigger>
      <TooltipPopup side="top" sideOffset={6}>
        <SegmentDetails limits={limits} window={window} now={now} />
      </TooltipPopup>
    </Tooltip>
  );
}

function PoolWindowCard({
  limits,
  window,
  color,
  now,
}: {
  readonly limits: ProviderUsageLimits;
  readonly window: ProviderUsageWindow;
  readonly color: string;
  readonly now: number;
}) {
  const pace = paceOf(window, now);
  return (
    <div className="grid items-center gap-x-6 gap-y-3 rounded-lg border border-border/60 p-4 md:grid-cols-[11rem_minmax(0,1fr)]">
      <div className="flex flex-col gap-1">
        <span className="text-sm font-medium text-foreground">{window.label}</span>
        <span className="flex items-baseline gap-2">
          <span className="text-3xl font-semibold text-foreground tabular-nums">
            {remainingPercent(window)}%
          </span>
          <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
            <span>left</span>
            {pace ? <PaceIcon pace={pace} /> : null}
          </span>
        </span>
      </div>
      <div className="min-w-0">
        <PoolSegment limits={limits} window={window} color={color} now={now} />
      </div>
      {window.description ? (
        <p className="text-xs text-muted-foreground md:col-span-2">{window.description}</p>
      ) : null}
    </div>
  );
}

/** One provider: its windows, or T3's honest "no limits" line when it reports none. */
export function ProviderUsageSection({
  limits,
  now,
}: {
  readonly limits: ProviderUsageLimits;
  readonly now: number;
}) {
  const color = barColor(limits.provider);
  const notice = limitsNotice(limits);
  return (
    <section className="flex flex-col gap-3" data-usage-provider={limits.provider}>
      <h2 className="flex items-center gap-2 text-sm font-medium text-foreground">
        <ProviderInstanceIcon
          driverKind={limits.provider}
          displayName={limits.label}
          className="size-5"
          iconClassName="size-4 text-foreground/80"
        />
        {limits.label}
        {limits.plan ? <span className="font-normal text-muted-foreground">· {limits.plan}</span> : null}
      </h2>
      {notice ? (
        <div
          className={cn(
            "flex items-start gap-2 rounded-lg border border-border/60 p-4 text-xs text-muted-foreground",
            limits.unavailable?.reason === "probeFailed" && "border-warning/40",
          )}
        >
          {limits.unavailable?.reason === "probeFailed" ? (
            <AlertTriangleIcon className="mt-px size-3.5 shrink-0 text-warning" aria-hidden />
          ) : null}
          <span>{notice}</span>
        </div>
      ) : (
        limits.windows.map((window) => (
          <PoolWindowCard
            key={`${window.kind}:${window.id}`}
            limits={limits}
            window={window}
            color={color}
            now={now}
          />
        ))
      )}
    </section>
  );
}
