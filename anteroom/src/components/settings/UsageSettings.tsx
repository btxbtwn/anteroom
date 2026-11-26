import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { useWorkspaceRuntime } from "../../app/runtime";
import { PageError, PageSkeleton } from "../WorkspacePage";
import { Button } from "../ui/button";
import { SettingsRow, SettingsSection } from "./SettingsLayout";

const dollars = new Intl.NumberFormat("en-US", {
  style: "currency", currency: "USD", maximumFractionDigits: 6,
});

export function UsageSettings() {
  const { account } = useWorkspaceRuntime();
  const usage = useQuery({
    queryKey: ["managed-usage", account?.id],
    queryFn: api.settings.managedUsage,
  });
  const data = usage.data;
  const empty = data && data.openrouter.count + data.photoroom.count === 0;

  return (
    <SettingsSection
      id="managed-usage"
      title="Last 30 days"
      headerAction={
        <Button variant="outline" size="sm" disabled={usage.isFetching} onClick={() => void usage.refetch()}>
          Refresh
        </Button>
      }
    >
      {usage.isPending && <PageSkeleton label="Loading usage" cards={2} />}
      {usage.isError && <PageError message={`Couldn’t load usage. ${usage.error.message}`} />}
      {data && <>
        <SettingsRow title="Total metered usage" description={account?.email} className="wrap-anywhere" inlineControl
          control={<strong className="text-2xl font-semibold tabular-nums">{dollars.format(data.openrouter.usd + data.photoroom.usd)}</strong>}
        />
        <SettingsRow title="OpenRouter" description={`${data.openrouter.count.toLocaleString()} AI call${data.openrouter.count === 1 ? "" : "s"}`} inlineControl
          control={<span className="text-sm tabular-nums">{dollars.format(data.openrouter.usd)}</span>}
        />
        <SettingsRow title="Photoroom" description={`${data.photoroom.count.toLocaleString()} photo edit${data.photoroom.count === 1 ? "" : "s"}`} inlineControl
          control={<span className="text-sm tabular-nums">{dollars.format(data.photoroom.usd)}</span>}
        />
        <p className="px-3 py-3 text-xs text-muted-foreground sm:px-4">
          {empty && "No metered usage recorded in this period. "}
          Costs in USD cover Anteroom-managed services. Your own API keys are billed separately.
        </p>
      </>}
    </SettingsSection>
  );
}
