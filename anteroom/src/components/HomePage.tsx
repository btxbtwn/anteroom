import { useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlarmClockIcon,
  ArrowRightIcon,
  CheckCircle2Icon,
  CheckIcon,
  PencilIcon,
  PlusIcon,
  RefreshCwIcon,
  RotateCcwIcon,
  TargetIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react";
import { api } from "../api/client";
import {
  applyGoalHomeSnapshot,
  browserTimeZone,
  patchHomeAfterDismiss,
  patchHomeAfterResolve,
  patchHomeAfterRestore,
  resolveLabel,
  type AttentionItem,
  type AttentionKind,
  type AttentionTarget,
  type GoalMetric,
  type GoalPace,
  type GoalPeriod,
  type HomeSummaryResponse,
  type SellerGoalProgress,
} from "../api/home";
import { homeQuery } from "../api/pageQueries";
import { cn } from "../lib/utils";
import { confirmDialog } from "../ui/confirmDialog";
import { Stat, StatGrid } from "./AnalyticsStat";
import { WorkspaceTabs } from "./ebay/WorkspaceTabs";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { Spinner } from "./ui/spinner";
import { toastManager } from "./ui/toast";
import {
  EmptyRow,
  FormError,
  FormField,
  PageError,
  PageSection,
  PageSkeleton,
  Panel,
  RowList,
  WorkspacePage,
} from "./WorkspacePage";

const PERIOD_OPTIONS: readonly { value: GoalPeriod; label: string }[] = [
  { value: "weekly", label: "Weekly" },
  { value: "monthly", label: "Monthly" },
  { value: "quarterly", label: "Quarterly" },
  { value: "yearly", label: "Yearly" },
];

const METRIC_OPTIONS: readonly { value: GoalMetric; label: string; placeholder: string }[] = [
  { value: "profit", label: "Profit ($)", placeholder: "2000" },
  { value: "sales", label: "Sales ($)", placeholder: "5000" },
  { value: "listings", label: "Listings created", placeholder: "100" },
  { value: "sell_through_rate", label: "Sell-through rate (%)", placeholder: "35" },
];

const ATTENTION_TABS = ["Active", "Snoozed", "Resolved"] as const;
type AttentionTab = (typeof ATTENTION_TABS)[number];

const KIND_LABELS: Record<AttentionKind, string> = {
  still_listed: "Still listed",
  expired_session: "Session",
  failed_job: "Publish error",
  unsent_edits: "Unsent edits",
  pending_approval: "Approval",
  relist_gap: "Relist gap",
  duplicate_listing: "Duplicate",
};

const PACE_LABELS: Record<GoalPace, string> = {
  achieved: "Achieved",
  ahead: "Ahead",
  on_track: "On track",
  behind: "Behind pace",
};

function formatCurrency(value: number | null): string {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  return `${sign}$${abs.toLocaleString(undefined, { minimumFractionDigits: abs % 1 === 0 ? 0 : 2, maximumFractionDigits: 2 })}`;
}

function formatGoalValue(value: number, unit: SellerGoalProgress["unit"]): string {
  if (unit === "currency") return formatCurrency(value);
  if (unit === "percent") return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 });
}

function formatWindow(start: string, end: string): string {
  const fmt = (iso: string) => {
    const parsed = new Date(`${iso}T12:00:00Z`);
    if (Number.isNaN(parsed.getTime())) return iso;
    return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric", timeZone: "UTC" });
  };
  return `${fmt(start)} – ${fmt(end)}`;
}

function formatHeaderDate(isoDate: string): string {
  const parsed = new Date(`${isoDate}T12:00:00Z`);
  if (Number.isNaN(parsed.getTime())) return isoDate;
  return parsed.toLocaleDateString(undefined, {
    weekday: "long",
    month: "long",
    day: "numeric",
    timeZone: "UTC",
  });
}

function formatRelativeTime(iso: string | null | undefined): string | null {
  if (!iso) return null;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return null;
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function paceBadgeVariant(pace: GoalPace): "success" | "secondary" | "warning" {
  if (pace === "achieved" || pace === "ahead") return "success";
  if (pace === "behind") return "warning";
  return "secondary";
}

function priorityBadgeVariant(priority: AttentionItem["priority"]): "error" | "warning" | "outline" {
  if (priority === "high") return "error";
  if (priority === "medium") return "warning";
  return "outline";
}

export interface HomePageProps {
  onNavigateAttention: (target: AttentionTarget) => void;
}

export function HomePage({ onNavigateAttention }: HomePageProps) {
  const timezone = useMemo(() => browserTimeZone(), []);
  const queryClient = useQueryClient();
  const queryDef = useMemo(() => homeQuery(timezone), [timezone]);

  const { data, isPending, isFetching, error, refetch } = useQuery<HomeSummaryResponse>(queryDef);

  const [attentionTab, setAttentionTab] = useState<AttentionTab>("Active");
  const [goalFormOpen, setGoalFormOpen] = useState(false);
  const goalFormVersion = useRef(0);
  const [editingGoalId, setEditingGoalId] = useState<string | null>(null);
  const [goalPeriod, setGoalPeriod] = useState<GoalPeriod>("monthly");
  const [goalMetric, setGoalMetric] = useState<GoalMetric>("profit");
  const [goalTarget, setGoalTarget] = useState("");
  const [goalLabel, setGoalLabel] = useState("");
  const [goalFormError, setGoalFormError] = useState<string | null>(null);

  const syncGoalsHome = async (next: HomeSummaryResponse | undefined) => {
    await queryClient.cancelQueries({ queryKey: queryDef.queryKey, exact: true });
    if (next) {
      const local = queryClient.getQueryData<HomeSummaryResponse>(queryDef.queryKey);
      queryClient.setQueryData(queryDef.queryKey, applyGoalHomeSnapshot(next, local));
    } else {
      await queryClient.invalidateQueries({ queryKey: queryDef.queryKey, exact: true });
    }
  };

  const patchHomeCache = (patch: (current: HomeSummaryResponse) => HomeSummaryResponse) => {
    const current = queryClient.getQueryData<HomeSummaryResponse>(queryDef.queryKey);
    if (!current) return undefined;
    const next = patch(current);
    queryClient.setQueryData(queryDef.queryKey, next);
    return current;
  };

  // Goals share one queue so overlapping edits stay ordered. Dismiss/restore
  // stay off that queue and patch the cache immediately — a full Today rebuild
  // used to stall every Dismiss on large inventories. Same-alert dismiss then
  // restore still runs in order so a late dismiss cannot undo a newer restore.
  // Goal responses only refresh today/goals so they cannot wipe those patches.
  const goalMutationScope = { id: "today-goals" };
  const attentionQueues = useRef(new Map<string, Promise<unknown>>());
  const attentionEpoch = useRef(new Map<string, number>());

  const bumpAttentionEpoch = (key: string) => {
    const next = (attentionEpoch.current.get(key) ?? 0) + 1;
    attentionEpoch.current.set(key, next);
    return next;
  };

  const runAttentionOp = <T,>(key: string, op: () => Promise<T>): Promise<T> => {
    const prior = attentionQueues.current.get(key) ?? Promise.resolve();
    const next = prior.catch(() => undefined).then(op);
    attentionQueues.current.set(key, next.then(() => undefined, () => undefined));
    return next;
  };

  const attentionOpIsCurrent = (key: string, epoch: number | undefined) =>
    epoch != null && attentionEpoch.current.get(key) === epoch;

  const saveGoal = useMutation({
    scope: goalMutationScope,
    mutationFn: ({ id, body }: {
      id: string | null;
      body: Parameters<typeof api.home.upsertGoal>[0];
      formVersion: number;
    }) => id ? api.home.updateGoal(id, body, timezone) : api.home.upsertGoal(body, timezone),
    onSuccess: async (res, { formVersion }) => {
      await syncGoalsHome(res.home);
      if (goalFormVersion.current !== formVersion) return;
      setGoalFormOpen(false);
      setEditingGoalId(null);
      setGoalTarget("");
      setGoalLabel("");
      setGoalFormError(null);
    },
    onError: (err, { formVersion }) => {
      if (goalFormVersion.current !== formVersion) return;
      setGoalFormError(err instanceof Error ? err.message : "Could not save goal.");
    },
  });

  const removeGoal = useMutation({
    scope: goalMutationScope,
    mutationFn: (goalId: string) => api.home.deleteGoal(goalId, timezone),
    onSuccess: (res) => syncGoalsHome(res.home),
    onError: () => { toastManager.add({ type: "error", title: "Could not delete goal" }); },
  });

  const dismissItem = useMutation({
    mutationFn: (item: AttentionItem) =>
      runAttentionOp(item.key, () => api.home.dismissAttention(item.key, item.kind, timezone)),
    onMutate: async (item) => {
      await queryClient.cancelQueries({ queryKey: queryDef.queryKey, exact: true });
      const epoch = bumpAttentionEpoch(item.key);
      patchHomeCache((current) => patchHomeAfterDismiss(current, item));
      return { key: item.key, epoch };
    },
    onSuccess: (res, item, ctx) => {
      if (!attentionOpIsCurrent(item.key, ctx?.epoch)) return;
      patchHomeCache((current) => patchHomeAfterDismiss(current, item, res.dismissal));
    },
    onError: async (_err, item, ctx) => {
      if (!attentionOpIsCurrent(item.key, ctx?.epoch)) return;
      toastManager.add({ type: "error", title: "Could not snooze alert" });
      await queryClient.invalidateQueries({ queryKey: queryDef.queryKey, exact: true });
    },
  });

  const resolveItem = useMutation({
    mutationFn: (item: AttentionItem) =>
      runAttentionOp(item.key, () => api.home.resolveAttention(item)),
    onMutate: async (item) => {
      await queryClient.cancelQueries({ queryKey: queryDef.queryKey, exact: true });
      const epoch = bumpAttentionEpoch(item.key);
      patchHomeCache((current) => patchHomeAfterResolve(current, item));
      return { key: item.key, epoch };
    },
    onSuccess: (res, item, ctx) => {
      if (!attentionOpIsCurrent(item.key, ctx?.epoch)) return;
      patchHomeCache((current) => patchHomeAfterResolve(current, item, res.resolution));
    },
    onError: async (_err, item, ctx) => {
      if (!attentionOpIsCurrent(item.key, ctx?.epoch)) return;
      toastManager.add({ type: "error", title: "Could not resolve alert" });
      await queryClient.invalidateQueries({ queryKey: queryDef.queryKey, exact: true });
    },
  });

  const restoreItem = useMutation({
    mutationFn: (itemKey: string) =>
      runAttentionOp(itemKey, () => api.home.restoreAttention(itemKey, timezone)),
    onMutate: async (itemKey) => {
      await queryClient.cancelQueries({ queryKey: queryDef.queryKey, exact: true });
      const epoch = bumpAttentionEpoch(itemKey);
      patchHomeCache((current) => patchHomeAfterRestore(current, itemKey));
      return { key: itemKey, epoch };
    },
    onSuccess: (res, itemKey, ctx) => {
      if (!attentionOpIsCurrent(itemKey, ctx?.epoch)) return;
      patchHomeCache((current) => patchHomeAfterRestore(current, res.item_key ?? itemKey));
    },
    onError: async (_err, itemKey, ctx) => {
      if (!attentionOpIsCurrent(itemKey, ctx?.epoch)) return;
      toastManager.add({ type: "error", title: "Could not restore alert" });
      await queryClient.invalidateQueries({ queryKey: queryDef.queryKey, exact: true });
    },
  });

  const openNewGoalForm = () => {
    goalFormVersion.current++;
    setEditingGoalId(null);
    setGoalPeriod("monthly");
    setGoalMetric("profit");
    setGoalTarget("");
    setGoalLabel("");
    setGoalFormError(null);
    setGoalFormOpen(true);
  };

  const openEditGoalForm = (goal: SellerGoalProgress) => {
    goalFormVersion.current++;
    setEditingGoalId(goal.id);
    setGoalPeriod(goal.period);
    setGoalMetric(goal.metric);
    setGoalTarget(String(goal.target));
    setGoalLabel(goal.custom_label ?? "");
    setGoalFormError(null);
    setGoalFormOpen(true);
  };

  const cancelGoalForm = () => {
    goalFormVersion.current++;
    setGoalFormOpen(false);
    setEditingGoalId(null);
    setGoalFormError(null);
  };

  const handleGoalSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    if (saveGoal.isPending) return;
    const target = Number(goalTarget);
    if (!Number.isFinite(target) || target <= 0) {
      setGoalFormError("Enter a target greater than zero.");
      return;
    }
    if (goalMetric === "sell_through_rate" && target > 100) {
      setGoalFormError("Sell-through rate target cannot exceed 100%.");
      return;
    }
    setGoalFormError(null);
    saveGoal.mutate({
      id: editingGoalId,
      body: { period: goalPeriod, metric: goalMetric, target, label: goalLabel.trim() || null },
      formVersion: goalFormVersion.current,
    });
  };

  const metricOption = METRIC_OPTIONS.find((opt) => opt.value === goalMetric) ?? METRIC_OPTIONS[0]!;
  const periodOption = PERIOD_OPTIONS.find((opt) => opt.value === goalPeriod) ?? PERIOD_OPTIONS[1]!;

  if (isPending && !data) {
    return (
      <WorkspacePage width="expanded">
        <h1 className="sr-only">Today</h1>
        <PageSkeleton label="Loading Today…" />
      </WorkspacePage>
    );
  }

  if (error && !data) {
    return (
      <WorkspacePage width="expanded">
        <h1 className="sr-only">Today</h1>
        <PageError message={error.message} />
      </WorkspacePage>
    );
  }

  const today = data?.today;
  const goals = data?.goals ?? [];
  const activeAttention = data?.needs_attention ?? [];
  const dismissedAttention = data?.dismissed ?? [];
  const resolvedAttention = data?.resolved ?? [];

  const profitHint =
    today && today.units_sold > 0
      ? today.profit_known === today.units_sold
        ? "All today's sales have cost recorded"
        : `${today.profit_known} of ${today.units_sold} sales with cost recorded`
      : "Recorded profit today";

  const automationHint = today
    ? `${today.shares_sent.toLocaleString()} ${today.shares_sent === 1 ? "share" : "shares"} · ${today.offers_sent.toLocaleString()} ${today.offers_sent === 1 ? "offer" : "offers"}`
    : undefined;

  return (
    <WorkspacePage width="expanded">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="sr-only">Today</h1>
        {today ? <p className="text-sm text-muted-foreground">{formatHeaderDate(today.date)}</p> : null}
        <Button type="button" variant="outline" size="sm" onClick={() => void refetch()} disabled={isFetching}>
          {isFetching ? <Spinner /> : <RefreshCwIcon />}
          Refresh
        </Button>
      </header>

      {error ? <PageError message={error.message} /> : null}

      <PageSection
        title={
          <span className="flex items-center gap-2">
            <span>Needs attention</span>
            <Badge variant={activeAttention.length > 0 ? "secondary" : "outline"} size="sm">
              {activeAttention.length}
            </Badge>
          </span>
        }
        description="Review alerts that may need action."
      >
        <WorkspaceTabs
          label="Needs attention views"
          items={ATTENTION_TABS}
          value={attentionTab}
          onChange={(next) => setAttentionTab(next as AttentionTab)}
        >
          {attentionTab === "Active" ? (
            activeAttention.length === 0 ? (
              <EmptyRow>
                <span className="inline-flex items-center gap-1.5">
                  <CheckCircle2Icon className="size-3.5 text-success" />
                  All caught up. Nothing needs attention right now.
                </span>
              </EmptyRow>
            ) : (
              <RowList>
                {activeAttention.map((item) => (
                  <li key={item.key} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm">
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant={priorityBadgeVariant(item.priority)} size="sm">
                          {KIND_LABELS[item.kind]}
                        </Badge>
                        <span className="truncate font-medium text-foreground">{item.title}</span>
                        {formatRelativeTime(item.occurred_at) ? (
                          <span className="text-xs text-muted-foreground tabular-nums">
                            {formatRelativeTime(item.occurred_at)}
                          </span>
                        ) : null}
                      </div>
                      <p className="text-xs text-muted-foreground">{item.detail}</p>
                    </div>
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="xs"
                        onClick={() => onNavigateAttention(item.target)}
                      >
                        {item.action_label}
                        <ArrowRightIcon />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="xs"
                        onClick={() => resolveItem.mutate(item)}
                        aria-label={`${resolveLabel(item.kind)}: ${item.title}`}
                        title={item.kind === "duplicate_listing"
                          ? "Never flag this pair again"
                          : "Hide until something changes"}
                      >
                        <CheckIcon />
                        {resolveLabel(item.kind)}
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="xs"
                        onClick={() => dismissItem.mutate(item)}
                        aria-label={`Snooze ${item.title} for 7 days`}
                        title="Hide for 7 days"
                      >
                        <AlarmClockIcon />
                        Snooze 7 days
                      </Button>
                    </div>
                  </li>
                ))}
              </RowList>
            )
          ) : attentionTab === "Resolved" ? (
            resolvedAttention.length === 0 ? (
              <EmptyRow>
                No resolved items. A resolved alert stays here until its situation changes, like another sale or a new error. Pairs marked not duplicates stay here for good.
              </EmptyRow>
            ) : (
              <RowList>
                {resolvedAttention.map((item) => (
                  <li key={item.key} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm">
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="outline" size="sm">
                          {KIND_LABELS[item.kind]}
                        </Badge>
                        <span className="truncate font-medium text-foreground">{item.title}</span>
                        {formatRelativeTime(item.resolved_at) ? (
                          <span className="text-xs text-muted-foreground tabular-nums">
                            {item.kind === "duplicate_listing" ? "Not duplicates" : "Resolved"} {formatRelativeTime(item.resolved_at)}
                          </span>
                        ) : null}
                      </div>
                      <p className="text-xs text-muted-foreground">{item.detail}</p>
                    </div>
                    <div className="flex min-w-0 flex-wrap items-center gap-2">
                      <Button
                        type="button"
                        variant="ghost"
                        size="xs"
                        onClick={() => restoreItem.mutate(item.key)}
                        aria-label={`Restore ${item.title}`}
                      >
                        <RotateCcwIcon />
                        Restore
                      </Button>
                    </div>
                  </li>
                ))}
              </RowList>
            )
          ) : dismissedAttention.length === 0 ? (
            <EmptyRow>
              No snoozed items. Snoozed alerts stay here for 7 days, then come back if they still apply.
            </EmptyRow>
          ) : (
            <RowList>
              {dismissedAttention.map((item) => (
                <li key={item.key} className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 text-sm">
                  <div className="flex min-w-0 flex-1 flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline" size="sm">
                        {KIND_LABELS[item.kind]}
                      </Badge>
                      <span className="truncate font-medium text-foreground">{item.title}</span>
                      {item.reappears_in_days != null ? (
                        <span className="text-xs text-muted-foreground tabular-nums">
                          Reappears in {item.reappears_in_days} {item.reappears_in_days === 1 ? "day" : "days"}
                        </span>
                      ) : null}
                    </div>
                    <p className="text-xs text-muted-foreground">{item.detail}</p>
                  </div>
                  <div className="flex min-w-0 flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="xs"
                      onClick={() => onNavigateAttention(item.target)}
                    >
                      {item.action_label}
                      <ArrowRightIcon />
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="xs"
                      onClick={() => restoreItem.mutate(item.key)}
                      aria-label={`Restore ${item.title}`}
                    >
                      <RotateCcwIcon />
                      Restore
                    </Button>
                  </div>
                </li>
              ))}
            </RowList>
          )}
        </WorkspaceTabs>
      </PageSection>

      <PageSection title="Today's summary">
        <StatGrid className="grid-cols-2 @3xl/page:grid-cols-5">
          <Stat
            label="Units listed"
            value={String(today?.units_listed ?? 0)}
            hint={
              today && today.drafts_created > 0
                ? `${today.drafts_created} ${today.drafts_created === 1 ? "draft" : "drafts"} created today`
                : "New listings today"
            }
          />
          <Stat
            label="Units sold"
            value={String(today?.units_sold ?? 0)}
            hint="Paid orders today"
          />
          <Stat
            label="Revenue"
            value={formatCurrency(today?.revenue ?? 0)}
            hint="Gross sales today"
          />
          <Stat
            label="Profit"
            value={today?.profit == null ? "—" : formatCurrency(today.profit)}
            negative={typeof today?.profit === "number" && today.profit < 0}
            hint={profitHint}
          />
          <Stat
            label="Automations"
            value={String(today?.automation_actions ?? 0)}
            hint={automationHint}
          />
        </StatGrid>
      </PageSection>

      <PageSection
        title="Goals"
        description="Track weekly, monthly, quarterly, or yearly profit, sales, listing volume, or sell-through rate."
        actions={
          <Button type="button" variant="outline" size="sm" onClick={openNewGoalForm}>
            <PlusIcon />
            Add goal
          </Button>
        }
      >
        {goalFormOpen ? (
          <Panel divided={false} className="p-4">
            <form onSubmit={handleGoalSubmit} className="flex flex-col gap-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-foreground">
                  {editingGoalId ? "Edit goal" : "New goal"}
                </span>
                <Button type="button" variant="ghost" size="xs" onClick={cancelGoalForm} aria-label="Close goal form">
                  <XIcon />
                </Button>
              </div>
              <div className="grid grid-cols-1 gap-3 @xl/page:grid-cols-4">
                <FormField label="Period">
                  <Select value={goalPeriod} onValueChange={(v) => setGoalPeriod((v as GoalPeriod) ?? "monthly")}>
                    <SelectTrigger size="sm" aria-label="Goal period">
                      <SelectValue>{periodOption.label}</SelectValue>
                    </SelectTrigger>
                    <SelectPopup>
                      {PERIOD_OPTIONS.map((opt) => (
                        <SelectItem key={opt.value} value={opt.value}>
                          {opt.label}
                        </SelectItem>
                      ))}
                    </SelectPopup>
                  </Select>
                </FormField>
                <FormField label="Metric">
                  <Select value={goalMetric} onValueChange={(v) => setGoalMetric((v as GoalMetric) ?? "profit")}>
                    <SelectTrigger size="sm" aria-label="Goal metric">
                      <SelectValue>{metricOption.label}</SelectValue>
                    </SelectTrigger>
                    <SelectPopup>
                      {METRIC_OPTIONS.map((opt) => (
                        <SelectItem key={opt.value} value={opt.value}>
                          {opt.label}
                        </SelectItem>
                      ))}
                    </SelectPopup>
                  </Select>
                </FormField>
                <FormField label="Target">
                  <Input
                    type="number"
                    step="any"
                    min="0.01"
                    max={goalMetric === "sell_through_rate" ? "100" : undefined}
                    value={goalTarget}
                    onChange={(event) => setGoalTarget(event.target.value)}
                    placeholder={metricOption.placeholder}
                    aria-label="Goal target"
                    required
                  />
                </FormField>
                <FormField label="Label (optional)">
                  <Input
                    type="text"
                    value={goalLabel}
                    onChange={(event) => setGoalLabel(event.target.value)}
                    placeholder={`${periodOption.label} target`}
                    aria-label="Goal label"
                    maxLength={80}
                  />
                </FormField>
              </div>
              {goalFormError ? <FormError>{goalFormError}</FormError> : null}
              <div className="flex items-center justify-end gap-2">
                <Button type="button" variant="ghost" size="sm" onClick={cancelGoalForm}>
                  Cancel
                </Button>
                <Button type="submit" size="sm" disabled={saveGoal.isPending || !goalTarget.trim()}>
                  {saveGoal.isPending ? <Spinner /> : <TargetIcon />}
                  {editingGoalId ? "Save changes" : "Save goal"}
                </Button>
              </div>
            </form>
          </Panel>
        ) : null}

        {goals.length === 0 ? (
          <EmptyRow>
            No goals set yet. Add a weekly, monthly, quarterly, or yearly target to track pacing here.
          </EmptyRow>
        ) : (
          <div className="grid grid-cols-1 gap-3 @2xl/page:grid-cols-2">
            {goals.map((goal) => {
              const clampedWidth = Math.max(0, Math.min(100, goal.progress_percent));
              const remainingText =
                goal.remaining <= 0
                  ? "Target reached"
                  : `${formatGoalValue(goal.remaining, goal.unit)} remaining`;
              return (
                <Panel key={goal.id} divided={false} className="flex flex-col gap-3 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex min-w-0 flex-col gap-0.5">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="truncate text-sm font-medium text-foreground">{goal.label}</span>
                        <Badge variant={paceBadgeVariant(goal.pace)} size="sm">
                          {PACE_LABELS[goal.pace]}
                        </Badge>
                      </div>
                      <span className="text-xs text-muted-foreground tabular-nums">
                        {formatWindow(goal.window_start, goal.window_end)} · {goal.days_remaining}{" "}
                        {goal.days_remaining === 1 ? "day" : "days"} left
                      </span>
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-xs"
                        onClick={() => openEditGoalForm(goal)}
                        aria-label={`Edit ${goal.label}`}
                      >
                        <PencilIcon />
                      </Button>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon-xs"
                        disabled={removeGoal.isPending}
                        onClick={async () => {
                          if (await confirmDialog(`Delete “${goal.label}”?`, { variant: "destructive", confirmLabel: "Delete goal" })) {
                            removeGoal.mutate(goal.id);
                          }
                        }}
                        aria-label={`Delete ${goal.label}`}
                      >
                        <Trash2Icon />
                      </Button>
                    </div>
                  </div>

                  <div className="flex items-baseline justify-between gap-2 tabular-nums">
                    <div className="flex items-baseline gap-1.5">
                      <span className="text-xl font-semibold tracking-tight text-foreground">
                        {formatGoalValue(goal.current, goal.unit)}
                      </span>
                      <span className="text-xs text-muted-foreground">
                        / {formatGoalValue(goal.target, goal.unit)}
                      </span>
                    </div>
                    <span className="text-xs text-muted-foreground">{remainingText}</span>
                  </div>

                  <div className="flex flex-col gap-1.5">
                    <div
                      className="h-1.5 w-full overflow-hidden rounded-full bg-muted"
                      role="progressbar"
                      aria-label={goal.label}
                      aria-valuenow={Math.round(clampedWidth)}
                      aria-valuemin={0}
                      aria-valuemax={100}
                    >
                      <div
                        className={cn(
                          "h-full rounded-full transition-[width]",
                          goal.pace === "achieved" ? "bg-success" : "bg-foreground",
                        )}
                        style={{ width: `${clampedWidth}%` }}
                      />
                    </div>
                    <div className="flex flex-wrap items-center justify-between gap-x-2 text-xs text-muted-foreground tabular-nums">
                      <span>{Math.round(goal.progress_percent)}% of target</span>
                      <span>
                        {goal.metric === "sell_through_rate"
                          ? `Day ${goal.days_elapsed} of ${goal.days_total}.`
                          : `Should be at ${Math.round(goal.expected_percent)}% by today.`}
                      </span>
                    </div>
                  </div>
                </Panel>
              );
            })}
          </div>
        )}
      </PageSection>
    </WorkspacePage>
  );
}
