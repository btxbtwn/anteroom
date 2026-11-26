import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { SubscriptionPlanOption, SubscriptionSummary } from "../../api/types";
import { useWorkspaceRuntime } from "../../app/runtime";
import { cn } from "../../lib/utils";
import { PageError, PageSkeleton } from "../WorkspacePage";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Toggle, ToggleGroup } from "../ui/toggle-group";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

const currency = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

function formatPriceCents(cents: number): string {
  if (cents <= 0) return "$0";
  return currency.format(cents / 100);
}

function formatResetDate(iso: string): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    timeZone: "UTC",
  }).format(parsed);
}

function UsageMeter({ used, limit }: { used: number; limit: number }) {
  const ratio = limit > 0 ? Math.min(1, used / limit) : 1;
  const pct = Math.round(ratio * 100);
  const exhausted = limit > 0 && used >= limit;
  const nearLimit = !exhausted && ratio >= 0.8;

  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
      role="progressbar"
      aria-valuenow={used}
      aria-valuemin={0}
      aria-valuemax={limit}
    >
      <div
        className={cn(
          "h-full rounded-full transition-[width] duration-200",
          exhausted ? "bg-destructive" : nearLimit ? "bg-warning" : "bg-primary",
        )}
        style={{ width: `${pct}%` }}
      />
    </div>
  );
}

function PlanPriceLabel({ plan, interval }: { plan: SubscriptionPlanOption; interval: "monthly" | "annual" }) {
  if (plan.monthly_price_cents === 0) {
    return <span className="text-sm font-medium tabular-nums">Free</span>;
  }
  if (interval === "annual") {
    const monthlyEquivalent = Math.round(plan.annual_price_cents / 12);
    return (
      <div className="text-right">
        <div className="text-sm font-medium tabular-nums">{formatPriceCents(monthlyEquivalent)}/mo</div>
        <div className="text-xs text-muted-foreground tabular-nums">
          {formatPriceCents(plan.annual_price_cents)}/yr
        </div>
      </div>
    );
  }
  return <span className="text-sm font-medium tabular-nums">{formatPriceCents(plan.monthly_price_cents)}/mo</span>;
}

function formatPlanSummary(plan: SubscriptionPlanOption): string {
  const items =
    plan.monthly_listed_items === null
      ? "Unlimited items listed"
      : `${plan.monthly_listed_items.toLocaleString()} items listed/mo`;
  const credits =
    plan.monthly_credits === null
      ? "Unlimited Smart Credits"
      : `${plan.monthly_credits.toLocaleString()} Smart Credits/mo`;
  return `${items} · ${credits}`;
}

function PlanBreakdown({ data }: { data: SubscriptionSummary }) {
  const { ai_listing, photo_cutout, photo_studio, photo_refund } = data.credits.breakdown;
  const parts: string[] = [];
  if (ai_listing.count > 0) {
    parts.push(`${ai_listing.count.toLocaleString()} AI listing${ai_listing.count === 1 ? "" : "s"} (${ai_listing.credits.toLocaleString()} cr)`);
  }
  if (photo_cutout.count > 0) {
    parts.push(`${photo_cutout.count.toLocaleString()} background cut${photo_cutout.count === 1 ? "" : "s"} (${photo_cutout.credits.toLocaleString()} cr)`);
  }
  if (photo_studio.count > 0) {
    parts.push(`${photo_studio.count.toLocaleString()} AI Studio edit${photo_studio.count === 1 ? "" : "s"} (${photo_studio.credits.toLocaleString()} cr)`);
  }
  if (photo_refund.count > 0) {
    parts.push(`${photo_refund.count.toLocaleString()} refunded (${photo_refund.credits.toLocaleString()} cr)`);
  }
  if (parts.length === 0) {
    return <SettingsNote>No Smart Credits spent yet this month.</SettingsNote>;
  }
  return <SettingsNote>{parts.join(" · ")}</SettingsNote>;
}

export function PlanSettings() {
  const { account } = useWorkspaceRuntime();
  const [interval, setInterval] = useState<"monthly" | "annual">("monthly");
  const subscription = useQuery({
    queryKey: ["subscription-plan", account?.id],
    queryFn: api.settings.subscription,
  });
  const data = subscription.data;

  const creditLimit = data?.credits.limit ?? null;
  const creditExhausted = creditLimit !== null && (data?.credits.used ?? 0) >= creditLimit;
  const creditNearLimit = !creditExhausted && creditLimit !== null && creditLimit > 0 && (data?.credits.used ?? 0) / creditLimit >= 0.8;

  const itemLimit = data?.listed_items.limit ?? null;
  const itemExhausted = itemLimit !== null && (data?.listed_items.used ?? 0) >= itemLimit;

  return (
    <>
      <SettingsSection
        id="subscription-plan"
        title="Plan & Smart Credits"
        headerAction={
          <Button
            variant="outline"
            size="sm"
            disabled={subscription.isFetching}
            onClick={() => void subscription.refetch()}
          >
            Refresh
          </Button>
        }
      >
        {subscription.isPending && <PageSkeleton label="Loading subscription plan" cards={2} />}
        {subscription.isError && <PageError message={`Couldn’t load your plan. ${subscription.error.message}`} />}
        {data && (
          <>
            <SettingsRow
              title={
                <>
                  <span>{data.plan.name}</span>
                  {data.plan.unlimited ? (
                    <Badge variant="secondary" size="sm">
                      Unlimited
                    </Badge>
                  ) : data.plan.billing_interval === "annual" && data.plan.annual_price_cents > 0 ? (
                    <Badge variant="outline" size="sm">
                      Annual
                    </Badge>
                  ) : null}
                </>
              }
              description={data.plan.tagline}
              inlineControl
              control={
                <strong className="text-lg font-semibold tabular-nums">
                  {data.plan.unlimited
                    ? "Unmetered"
                    : data.plan.monthly_price_cents === 0
                      ? "Free"
                      : data.plan.billing_interval === "annual" && data.plan.annual_price_cents > 0
                        ? `${formatPriceCents(data.plan.annual_price_cents)}/yr`
                        : `${formatPriceCents(data.plan.monthly_price_cents)}/mo`}
                </strong>
              }
            />

            <SettingsRow
              title="Smart Credits"
              description={`Resets ${formatResetDate(data.resets_at)} (UTC)`}
              inlineControl
              control={
                <span
                  className={cn(
                    "text-sm font-medium tabular-nums",
                    creditExhausted
                      ? "text-destructive-foreground"
                      : creditNearLimit
                        ? "text-warning-foreground"
                        : "text-foreground",
                  )}
                >
                  {creditLimit === null
                    ? `${data.credits.used.toLocaleString()} used · Unlimited`
                    : `${data.credits.used.toLocaleString()} / ${creditLimit.toLocaleString()}`}
                </span>
              }
            >
              {creditLimit !== null && <UsageMeter used={data.credits.used} limit={creditLimit} />}
              <PlanBreakdown data={data} />
              {creditExhausted && (
                <SettingsNote tone="error">
                  Monthly Smart Credits are used up. Manual listing, editing, updates, and delisting still work.
                </SettingsNote>
              )}
            </SettingsRow>

            <SettingsRow
              title="Items listed this month"
              description={
                itemLimit === null
                  ? "Manual listing, imports, and bulk cross-listing never use Smart Credits."
                  : "Cross-listing the same item to additional marketplaces counts once."
              }
              inlineControl
              control={
                <span
                  className={cn(
                    "text-sm font-medium tabular-nums",
                    itemExhausted ? "text-destructive-foreground" : "text-foreground",
                  )}
                >
                  {itemLimit === null
                    ? `${data.listed_items.used.toLocaleString()} · Unlimited`
                    : `${data.listed_items.used.toLocaleString()} / ${itemLimit.toLocaleString()}`}
                </span>
              }
            >
              {itemLimit !== null && <UsageMeter used={data.listed_items.used} limit={itemLimit} />}
              {itemExhausted && (
                <SettingsNote tone="error">
                  You’ve reached this month’s {itemLimit}-item Free limit. Upgrade to Lister ($14.99/mo) for unlimited
                  manual and bulk cross-listing.
                </SettingsNote>
              )}
            </SettingsRow>

            <p className="px-3 py-3 text-xs text-muted-foreground sm:px-4">
              Smart Credit rates: AI listing {data.credit_weights.ai_listing} credits · Background removal{" "}
              {data.credit_weights.photo_cutout} credit · AI Studio edit {data.credit_weights.photo_studio} credits ·
              Manual &amp; bulk cross-listing 0 credits.
            </p>
          </>
        )}
      </SettingsSection>

      {data && (
        <SettingsSection
          id="subscription-tiers"
          title="Available plans"
          headerAction={
            <ToggleGroup
              aria-label="Billing interval"
              value={[interval]}
              onValueChange={(values) => {
                const next = values[0];
                if (next === "monthly" || next === "annual") setInterval(next);
              }}
            >
              <Toggle value="monthly">Monthly</Toggle>
              <Toggle value="annual">Annual (2 mo free)</Toggle>
            </ToggleGroup>
          }
        >
          {data.plans.map((tier) => {
            const isCurrent = data.plan.id === tier.id;
            return (
              <SettingsRow
                key={tier.id}
                className={isCurrent ? "bg-accent/50" : undefined}
                title={
                  <>
                    <span>{tier.name}</span>
                    {isCurrent ? (
                      <Badge variant="default" size="sm">
                        Current
                      </Badge>
                    ) : tier.recommended ? (
                      <Badge variant="outline" size="sm">
                        Popular
                      </Badge>
                    ) : null}
                  </>
                }
                description={formatPlanSummary(tier)}
                inlineControl
                control={<PlanPriceLabel plan={tier} interval={interval} />}
              />
            );
          })}
          <p className="px-3 py-3 text-xs text-muted-foreground sm:px-4">
            Every paid plan includes all 5 marketplaces, bulk cross-listing, auto-delist, auto-relist, MCP, and
            analytics. Unused Smart Credits reset on the 1st of each UTC month.
          </p>
        </SettingsSection>
      )}
    </>
  );
}
