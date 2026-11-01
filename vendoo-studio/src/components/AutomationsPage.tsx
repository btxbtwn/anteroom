/**
 * Scheduled automations: one-off and recurring tasks the seller confirms
 * before they delist, relist, regenerate, or change a price.
 */
import { useState, type ReactNode } from "react";
import { cn } from "../lib/utils";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClockIcon } from "lucide-react";

import { api } from "../api/client";
import type { Automation, AutomationApproval, AutomationDraft, AutomationPreview, AutomationRun, RelistGap } from "../api/automations";
import { confirmDialog } from "../ui/confirmDialog";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { Spinner } from "./ui/spinner";
import { Textarea } from "./ui/textarea";
import { MarketplaceAutomations } from "./MarketplaceAutomations";
import { EbayOffersSettings } from "./settings/EbayOffersSettings";
import { marketplaceName } from "./marketplaceNames";
import { FormField, PageHeader, WorkspacePage } from "./WorkspacePage";
import { useWorkspaceRuntime } from "../app/runtime";
import {
  AUTOMATION_MARKETPLACES,
  WEEKDAY_LABELS,
  bodyFromForm,
  describeActions,
  listedSourceLabel,
  describeSchedule,
  emptyForm,
  formFromDraft,
  money,
  type AutomationForm,
} from "./automationsFormat";

const LIST_KEY = ["automations"] as const;
const APPROVAL_KEY = ["automation-approvals"] as const;
const GAP_KEY = ["automation-gaps"] as const;

const SURFACE = "rounded-xl border border-border/60 bg-card/40 p-4";

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <FormField label={label}>{children}</FormField>;
}

function Check({
  label, checked, onChange,
}: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="inline-flex min-h-7 cursor-pointer items-center gap-2 text-sm text-foreground">
      <Checkbox checked={checked} onCheckedChange={(next) => onChange(next === true)} />
      {label}
    </label>
  );
}

/** A labelled row of checkboxes. */
function CheckGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <fieldset className="flex min-w-0 flex-col gap-1.5">
      <legend className="mb-1.5 text-xs font-medium text-muted-foreground">{label}</legend>
      <div className="flex flex-wrap gap-x-5 gap-y-1">{children}</div>
    </fieldset>
  );
}

function Choice<T extends string>({ label, value, options, onChange }: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <Select value={value} onValueChange={(next) => onChange(next as T)}>
      <SelectTrigger aria-label={label}>
        <SelectValue>{options.find((option) => option.value === value)?.label}</SelectValue>
      </SelectTrigger>
      <SelectPopup>
        {options.map((option) => <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>)}
      </SelectPopup>
    </Select>
  );
}

const STATUS_BADGE: Record<string, "success" | "warning" | "outline"> = { active: "success", paused: "warning" };

export function AutomationsPage({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: LIST_KEY, queryFn: () => api.automations.list() });
  const approvals = useQuery({ queryKey: APPROVAL_KEY, queryFn: () => api.automations.approvals() });
  const gaps = useQuery({ queryKey: GAP_KEY, queryFn: () => api.errors.list() });
  const [form, setForm] = useState<AutomationForm>(() => emptyForm());
  const [editing, setEditing] = useState<string | null>(null);
  const [issues, setIssues] = useState<string[]>([]);
  const [preview, setPreview] = useState<AutomationPreview | null>(null);
  const [error, setError] = useState("");
  const [historyFor, setHistoryFor] = useState<string | null>(null);
  const [history, setHistory] = useState<AutomationRun[]>([]);

  const patch = (next: Partial<AutomationForm>) => setForm((current) => ({ ...current, ...next }));

  const draft = useMutation({
    mutationFn: () => api.automations.draft(form.prompt, form.timezone),
    onSuccess: (result: AutomationDraft) => {
      setForm(formFromDraft(result));
      setIssues(result.issues || []);
      setPreview(null);
      setError("");
    },
    onError: (err: Error) => setError(err.message),
  });

  const showPreview = useMutation({
    mutationFn: () => api.automations.preview(bodyFromForm(form, false)),
    onSuccess: (result) => {
      setPreview(result);
      setIssues(result.issues || []);
      setError("");
    },
    onError: (err: Error) => setError(err.message),
  });

  const save = useMutation({
    mutationFn: (activate: boolean) => {
      const body = bodyFromForm(form, activate);
      return editing ? api.automations.update(editing, body) : api.automations.create(body);
    },
    onSuccess: () => {
      setForm(emptyForm(form.timezone));
      setEditing(null);
      setPreview(null);
      setIssues([]);
      setError("");
      void queryClient.invalidateQueries({ queryKey: LIST_KEY });
    },
    onError: (err: Error) => setError(err.message),
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: LIST_KEY });
    void queryClient.invalidateQueries({ queryKey: APPROVAL_KEY });
    void queryClient.invalidateQueries({ queryKey: GAP_KEY });
  };

  const pause = async (task: Automation) => {
    const ok = await confirmDialog(`Pause “${task.name}”? It will not start a new run.`, { confirmLabel: "Pause" });
    if (!ok) return;
    await api.automations.pause(task.id).then(refresh).catch((err: Error) => setError(err.message));
  };
  const resume = (task: Automation) => {
    void api.automations.resume(task.id).then(refresh).catch((err: Error) => setError(err.message));
  };
  const remove = async (task: Automation) => {
    const ok = await confirmDialog(`Delete “${task.name}”? Its run history goes with it.`, {
      variant: "destructive", confirmLabel: "Delete",
    });
    if (!ok) return;
    await api.automations.remove(task.id).then(refresh).catch((err: Error) => setError(err.message));
  };
  const run = (task: Automation, dryRun: boolean) => {
    setError("");
    void api.automations.run(task.id, dryRun).then((result) => {
      setHistoryFor(task.id);
      setHistory([result]);
      refresh();
    }).catch((err: Error) => setError(err.message));
  };
  const loadHistory = (task: Automation) => {
    void api.automations.history(task.id).then((result) => {
      setHistoryFor(task.id);
      setHistory(result.runs);
    }).catch((err: Error) => setError(err.message));
  };
  const edit = (task: Automation) => {
    setEditing(task.id);
    setForm(formFromDraft({
      name: task.name,
      prompt: task.prompt || "",
      timezone: task.timezone,
      schedule: task.schedule,
      filters: task.filters,
      actions: task.actions,
      marketplaces: task.marketplaces,
      price_repeat: task.price_repeat,
      issues: [],
      ready: task.status !== "draft",
    }));
    setPreview(null);
    setIssues([]);
  };

  const { features } = useWorkspaceRuntime();
  // Approving runs a marketplace action. Offers and sharing need the seller's marketplace sessions.
  const canApprove = features.has("marketplace_actions");
  const approve = async (row: AutomationApproval) => {
    const where = row.marketplaces.length ? row.marketplaces.map(marketplaceName).join(", ") : "the marketplaces this task names";
    const label = row.action === "publish" ? "List" : row.action === "delist" ? "Delist" : "Relist";
    const ok = await confirmDialog(`${label} “${row.title || "this listing"}” on ${where}? This approval makes one attempt per marketplace.`, { confirmLabel: label });
    if (!ok) return;
    setError("");
    try {
      const result = await api.automations.approve(row);
      if (result.result?.state || result.result?.error) setError(`${row.title || "Listing"}: ${result.result.state || result.result.error}`);
      refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      refresh();
    }
  };

  const included = preview?.items.filter((item) => item.include) ?? [];
  const skipped = preview?.items.filter((item) => !item.include) ?? [];

  return (
    <WorkspacePage>
      <PageHeader
        title="Automations"
        description="Manage automatic offers, sharing, draft preparation and price edits. Listing, delisting and relisting wait for your review of each item and named marketplace."
      />

      <section className={SURFACE}>
        <h2 className="text-sm font-medium">Waiting for approval</h2>
        <p className="mt-1 text-xs text-muted-foreground">Review each item, action and marketplace before approving one attempt. Scheduling never publishes or delists on its own.</p>
        {(approvals.data ?? []).length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">Nothing is waiting for approval.</p>
        ) : (
          <ul className="mt-3 flex flex-col gap-2 text-sm">
            {(approvals.data ?? []).map((row) => (
              <li key={row.id} className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  {row.title || row.conversation_id}
                  {row.marketplaces.length ? ` · ${row.marketplaces.map(marketplaceName).join(", ")}` : ""}
                  {` · ${row.action === "publish" ? "list" : row.action} · ${row.state}`}
                </span>
                <Button size="sm" variant="ghost" onClick={() => onOpenListing(row.conversation_id)}>Open item</Button>
                <Button size="sm" disabled={!canApprove} title={canApprove ? undefined : "Marketplace actions are not available on the web yet."} onClick={() => void approve(row)}>Review and approve</Button>
                {Object.entries(row.targets || {}).map(([name,target])=>target.url?<a key={name} href={target.url} target="_blank" rel="noreferrer" className="text-xs underline">{name} listing</a>:null)}
              </li>
            ))}
          </ul>
        )}
      </section>

      {(gaps.data ?? []).length ? (
        <section className="rounded-xl border border-border bg-card p-4">
          <h2 className="font-medium">Down, needs relist</h2>
          <ul className="mt-2 flex flex-col gap-1 text-sm">
            {(gaps.data ?? []).map((gap: RelistGap) => (
              <li key={gap.id}>
                {gap.title || gap.conversation_id} · {marketplaceName(gap.marketplace)} · {gap.state}
                {gap.error ? ` · ${gap.error}` : ""}
                <Button size="xs" variant="ghost" onClick={()=>onOpenListing(gap.conversation_id)}>Open item</Button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className={cn(SURFACE, "flex flex-col gap-4")}>
        <h2 className="flex items-center gap-2 text-sm font-medium">
          <CalendarClockIcon className="size-4 text-muted-foreground" />
          {editing ? "Edit automation" : "New automation"}
        </h2>
        <Field label="Describe it">
          <Textarea
            value={form.prompt}
            placeholder="When an item reaches 60 days listed on eBay and Poshmark, delist and relist it and drop the price by 10%. Don't go below $15."
            onChange={(event) => patch({ prompt: event.target.value })}
          />
        </Field>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={!form.prompt.trim() || draft.isPending} onClick={() => draft.mutate()}>
            {draft.isPending ? "Reading…" : "Draft from prompt"}
          </Button>
        </div>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Name">
            <Input nativeInput value={form.name} onChange={(event) => patch({ name: event.target.value })} />
          </Field>
          <Field label="Timezone">
            <Input nativeInput value={form.timezone} onChange={(event) => patch({ timezone: event.target.value })} />
          </Field>
          <Field label="When">
            <Choice
              label="When"
              value={form.kind}
              options={[{ value: "once", label: "One time" }, { value: "daily", label: "Every day" }, { value: "weekly", label: "Every week" }]}
              onChange={(kind) => patch({ kind, priceRepeat: kind === "once" ? "once" : "step" })}
            />
          </Field>
          {form.kind === "once" ? (
            <Field label="Date and time">
              <Input nativeInput type="datetime-local" value={form.at} onChange={(event) => patch({ at: event.target.value })} />
            </Field>
          ) : (
            <Field label="Time">
              <Input nativeInput type="time" value={form.time} onChange={(event) => patch({ time: event.target.value })} />
            </Field>
          )}
        </div>
        {form.kind === "weekly" ? (
          <CheckGroup label="Days">
            {WEEKDAY_LABELS.map((label, index) => (
              <Check
                key={label}
                label={label}
                checked={form.weekdays.includes(index)}
                onChange={(checked) => patch({
                  weekdays: checked ? [...form.weekdays, index].sort() : form.weekdays.filter((day) => day !== index),
                })}
              />
            ))}
          </CheckGroup>
        ) : null}
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Department">
            <Input nativeInput value={form.department} placeholder="Any" onChange={(event) => patch({ department: event.target.value })} />
          </Field>
          <Field label="Category">
            <Input nativeInput value={form.category} placeholder="Any" onChange={(event) => patch({ category: event.target.value })} />
          </Field>
          <Field label="Brand">
            <Input nativeInput value={form.brand} placeholder="Any" onChange={(event) => patch({ brand: event.target.value })} />
          </Field>
          <Field label="Listed at least (days)">
            <Input nativeInput inputMode="numeric" value={form.minAge} onChange={(event) => patch({ minAge: event.target.value })} />
          </Field>
          <Field label="Status">
            <Input nativeInput value={form.status} placeholder="Any" onChange={(event) => patch({ status: event.target.value })} />
          </Field>
          <Field label="Item ids">
            <Input nativeInput value={form.itemIds} placeholder="All matching items" onChange={(event) => patch({ itemIds: event.target.value })} />
          </Field>
        </div>
        <CheckGroup label="Actions">
          <Check label="Regenerate" checked={form.regenerate} onChange={(regenerate) => patch({ regenerate })} />
          <Check label="Delist" checked={form.delist} onChange={(delist) => patch({ delist })} />
          <Check label="Relist" checked={form.relist} onChange={(relist) => patch({ relist })} />
          <Check label="Drop the price" checked={form.priceOn} onChange={(priceOn) => patch({ priceOn })} />
        </CheckGroup>
        {form.priceOn ? (
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Drop by">
              <Choice
                label="Drop by"
                value={form.priceMode}
                options={[{ value: "percent", label: "Percent of the original price" }, { value: "amount", label: "Dollars off the original price" }]}
                onChange={(priceMode) => patch({ priceMode })}
              />
            </Field>
            <Field label={form.priceMode === "amount" ? "Amount ($)" : "Percent"}>
              <Input nativeInput inputMode="decimal" value={form.priceValue} onChange={(event) => patch({ priceValue: event.target.value })} />
            </Field>
            <Field label="Floor ($)">
              <Input nativeInput inputMode="decimal" value={form.floor} onChange={(event) => patch({ floor: event.target.value })} />
            </Field>
            <Field label="How often">
              <Choice<AutomationForm["priceRepeat"]>
                label="How often"
                value={form.priceRepeat}
                options={[{ value: "step", label: "Each cycle, off the original price" }, { value: "once", label: "One-time drop" }]}
                onChange={(priceRepeat) => patch({ priceRepeat })}
              />
            </Field>
          </div>
        ) : null}
        <CheckGroup label="Marketplaces">
          {AUTOMATION_MARKETPLACES.map((name) => (
            <Check
              key={name}
              label={marketplaceName(name)}
              checked={form.marketplaces.includes(name)}
              onChange={(checked) => patch({
                marketplaces: checked ? [...form.marketplaces, name] : form.marketplaces.filter((item) => item !== name),
              })}
            />
          ))}
        </CheckGroup>
        {issues.length ? (
          <ul className="list-disc pl-5 text-sm text-muted-foreground">
            {issues.map((issue) => <li key={issue}>{issue}</li>)}
          </ul>
        ) : null}
        {error ? <p className="text-sm text-destructive-foreground">{error}</p> : null}
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={showPreview.isPending} onClick={() => showPreview.mutate()}>
            {showPreview.isPending ? "Checking…" : "Preview"}
          </Button>
          <Button size="sm" variant="outline" disabled={save.isPending} onClick={() => save.mutate(false)}>
            Save draft
          </Button>
          <Button size="sm" disabled={save.isPending} onClick={() => save.mutate(true)}>
            {save.isPending ? "Saving…" : "Save and confirm"}
          </Button>
          {editing ? (
            <Button size="sm" variant="ghost" onClick={() => { setEditing(null); setForm(emptyForm(form.timezone)); }}>
              Cancel edit
            </Button>
          ) : null}
        </div>
        {preview ? (
          <div className="text-sm">
            <p className="mb-2">{preview.count} item{preview.count === 1 ? "" : "s"} would be touched. {preview.summary}</p>
            <ul className="flex flex-col gap-1">
              {included.map((item) => (
                <li key={item.conversation_id}>
                  {item.title}
                  {item.next_price != null ? ` · ${money(item.price)} → ${money(item.next_price)} from ${money(item.original_price)}` : ""}
                  {item.age_days != null ? ` · ${item.age_days} days (${listedSourceLabel(item.listed_source)}${item.listed_at ? `, ${item.listed_at.slice(0, 10)}` : ""})` : ""}
                  {item.marketplaces.length ? ` · ${item.marketplaces.map(marketplaceName).join(", ")}` : ""}
                </li>
              ))}
              {skipped.slice(0, 8).map((item) => (
                <li key={item.conversation_id} className="text-muted-foreground">
                  {item.title}: {item.skip_reason}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
      </section>

      {list.isLoading ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground"><Spinner className="size-4" /> Reading automations…</p>
      ) : list.error ? (
        <p className="text-sm text-muted-foreground">Could not read automations: {(list.error as Error).message}</p>
      ) : (list.data ?? []).length === 0 ? (
        <p className="text-sm text-muted-foreground">No automations yet.</p>
      ) : (
        <ul className="flex flex-col gap-3">
          {(list.data ?? []).map((task) => (
            <li key={task.id} className={SURFACE}>
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="text-sm font-medium">{task.name}</h3>
                <Badge variant={STATUS_BADGE[task.status] ?? "outline"} className="capitalize">{task.status}</Badge>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                {task.summary || describeSchedule(task.schedule, task.timezone)}
                {" · "}
                {describeActions(task.actions)}
                {" · marketplace writes require item approval"}
                {task.marketplaces.length ? ` · ${task.marketplaces.map(marketplaceName).join(", ")}` : ""}
              </p>
              {task.next_run_at ? <p className="mt-1 text-xs text-muted-foreground">Next run {task.next_run_at}</p> : null}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button size="sm" variant="outline" onClick={() => edit(task)}>Edit</Button>
                {task.status === "active" ? <Button size="sm" variant="outline" onClick={() => void pause(task)}>Pause</Button> : null}
                {task.status === "paused" ? <Button size="sm" variant="outline" onClick={() => resume(task)}>Resume</Button> : null}
                {task.status !== "draft" ? (
                  <>
                    <Button size="sm" variant="outline" onClick={() => run(task, true)}>Dry run</Button>
                    <Button size="sm" variant="outline" onClick={() => run(task, false)}>Run now</Button>
                  </>
                ) : null}
                <Button size="sm" variant="ghost" onClick={() => loadHistory(task)}>History</Button>
                <Button size="sm" variant="ghost" onClick={() => void remove(task)}>Delete</Button>
              </div>
              {historyFor === task.id ? (
                <ul className="mt-3 flex flex-col gap-2 text-sm">
                  {history.length === 0 ? <li className="text-muted-foreground">No runs yet.</li> : null}
                  {history.map((run) => (
                    <li key={run.id}>
                      <span className="text-muted-foreground">
                        {run.started_at || run.scheduled_for} · {run.origin} · {run.status}{run.dry_run ? " · dry run" : ""}
                        {run.error ? ` · ${run.error}` : ""}
                      </span>
                      <ul className="mt-1 pl-4">
                        {run.items.map((item) => (
                          <li key={item.conversation_id}>
                            {item.title || item.conversation_id}
                            {item.state ? ` — ${item.state}` : item.ok ? "" : ` — ${item.error || "failed"}`}
                            {item.down?.length ? ` · ${item.down.join(", ")}` : ""}
                            {item.next_price != null ? ` · ${money(item.price)} → ${money(item.next_price)}` : ""}
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
        </ul>
      )}

      {features.has("ebay_offers") ? (
        <section aria-label="eBay offers" className={SURFACE}>
          <h2 className="mb-3 text-sm font-medium">eBay offers</h2>
          <EbayOffersSettings />
        </section>
      ) : null}

      {features.has("marketplace_sessions") ? <MarketplaceAutomations /> : null}
    </WorkspacePage>
  );
}
