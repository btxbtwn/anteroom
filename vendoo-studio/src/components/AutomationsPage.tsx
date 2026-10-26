/**
 * Scheduled automations: one-off and recurring tasks the seller confirms
 * before they delist, relist, regenerate, or change a price.
 */
import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClockIcon } from "lucide-react";

import { api } from "../api/client";
import type { Automation, AutomationApproval, AutomationDraft, AutomationPreview, AutomationRun, RelistGap } from "../api/automations";
import { confirmDialog } from "../ui/confirmDialog";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Spinner } from "./ui/spinner";
import { Textarea } from "./ui/textarea";
import { PageHeader, WorkspacePage } from "./WorkspacePage";
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

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      {children}
    </label>
  );
}

function Check({
  label, checked, onChange,
}: { label: string; checked: boolean; onChange: (checked: boolean) => void }) {
  return (
    <label className="flex min-h-8 items-center gap-2 text-sm">
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
  );
}

export function AutomationsPage({onOpenListing}:{onOpenListing:(id:string)=>void}) {
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

  const approve = async (row: AutomationApproval) => {
    const where = row.marketplaces.length ? row.marketplaces.join(", ") : "the marketplaces this task names";
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
        description="Schedule local draft preparation and price edits. Listing, delisting and relisting wait for your review of each item and named marketplace."
      />

      <section className="rounded-xl border border-border bg-card p-4">
        <h2 className="font-medium">Waiting for approval</h2>
        <p className="mt-1 text-sm text-muted-foreground">Review each item, action and marketplace before approving one attempt. Scheduling never publishes or delists on its own.</p>
        {(approvals.data ?? []).length === 0 ? (
          <p className="mt-2 text-sm text-muted-foreground">Nothing is waiting for approval.</p>
        ) : (
          <ul className="mt-3 flex flex-col gap-2 text-sm">
            {(approvals.data ?? []).map((row) => (
              <li key={row.id} className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  {row.title || row.conversation_id}
                  {row.marketplaces.length ? ` · ${row.marketplaces.join(", ")}` : ""}
                  {` · ${row.action === "publish" ? "list" : row.action} · ${row.state}`}
                </span>
                <Button size="sm" onClick={() => void approve(row)}>Review and approve</Button>
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
                {gap.title || gap.conversation_id} · {gap.marketplace} · {gap.state}
                {gap.error ? ` · ${gap.error}` : ""}
                <Button size="xs" variant="ghost" onClick={()=>onOpenListing(gap.conversation_id)}>Open item</Button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <section className="flex flex-col gap-4 rounded-xl border border-border bg-card p-4">
        <h2 className="flex items-center gap-2 font-medium">
          <CalendarClockIcon className="size-4" />
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
            <select className="h-9 rounded-md border border-border bg-background px-2 text-sm" value={form.kind} onChange={(event) => {
              const kind = event.target.value as AutomationForm["kind"];
              patch({ kind, priceRepeat: kind === "once" ? "once" : "step" });
            }}>
              <option value="once">One time</option>
              <option value="daily">Every day</option>
              <option value="weekly">Every week</option>
            </select>
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
          <div className="flex flex-wrap gap-3">
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
          </div>
        ) : null}
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Department">
            <Input nativeInput value={form.department} placeholder="men" onChange={(event) => patch({ department: event.target.value })} />
          </Field>
          <Field label="Category">
            <Input nativeInput value={form.category} placeholder="t-shirt" onChange={(event) => patch({ category: event.target.value })} />
          </Field>
          <Field label="Brand">
            <Input nativeInput value={form.brand} onChange={(event) => patch({ brand: event.target.value })} />
          </Field>
          <Field label="Listed at least (days)">
            <Input nativeInput inputMode="numeric" value={form.minAge} onChange={(event) => patch({ minAge: event.target.value })} />
          </Field>
          <Field label="Status">
            <Input nativeInput value={form.status} placeholder="active" onChange={(event) => patch({ status: event.target.value })} />
          </Field>
          <Field label="Item ids">
            <Input nativeInput value={form.itemIds} placeholder="optional" onChange={(event) => patch({ itemIds: event.target.value })} />
          </Field>
        </div>
        <div className="flex flex-wrap gap-4">
          <Check label="Regenerate" checked={form.regenerate} onChange={(regenerate) => patch({ regenerate })} />
          <Check label="Delist" checked={form.delist} onChange={(delist) => patch({ delist })} />
          <Check label="Relist" checked={form.relist} onChange={(relist) => patch({ relist })} />
          <Check label="Drop the price" checked={form.priceOn} onChange={(priceOn) => patch({ priceOn })} />
        </div>
        {form.priceOn ? (
          <div className="grid gap-3 sm:grid-cols-3">
            <Field label="Drop by">
              <select className="h-9 rounded-md border border-border bg-background px-2 text-sm" value={form.priceMode} onChange={(event) => patch({ priceMode: event.target.value as "amount" | "percent" })}>
                <option value="percent">Percent of the original price</option>
                <option value="amount">Dollars off the original price</option>
              </select>
            </Field>
            <Field label={form.priceMode === "amount" ? "Amount ($)" : "Percent"}>
              <Input nativeInput inputMode="decimal" value={form.priceValue} onChange={(event) => patch({ priceValue: event.target.value })} />
            </Field>
            <Field label="Floor ($)">
              <Input nativeInput inputMode="decimal" value={form.floor} onChange={(event) => patch({ floor: event.target.value })} />
            </Field>
            <Field label="How often">
              <select className="h-9 rounded-md border border-border bg-background px-2 text-sm" value={form.priceRepeat} onChange={(event) => patch({ priceRepeat: event.target.value as AutomationForm["priceRepeat"] })}>
                <option value="step">Each cycle, off the original price</option>
                <option value="once">One-time drop</option>
              </select>
            </Field>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-3">
          {AUTOMATION_MARKETPLACES.map((name) => (
            <Check
              key={name}
              label={name}
              checked={form.marketplaces.includes(name)}
              onChange={(checked) => patch({
                marketplaces: checked ? [...form.marketplaces, name] : form.marketplaces.filter((item) => item !== name),
              })}
            />
          ))}
        </div>
        {issues.length ? (
          <ul className="list-disc pl-5 text-sm text-muted-foreground">
            {issues.map((issue) => <li key={issue}>{issue}</li>)}
          </ul>
        ) : null}
        {error ? <p className="text-sm text-destructive">{error}</p> : null}
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
                  {item.marketplaces.length ? ` · ${item.marketplaces.join(", ")}` : ""}
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
            <li key={task.id} className="rounded-xl border border-border bg-card p-4">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="font-medium">{task.name}</h3>
                <span className="text-muted-foreground text-xs uppercase tracking-wide">{task.status}</span>
              </div>
              <p className="mt-1 text-sm text-muted-foreground">
                {task.summary || describeSchedule(task.schedule, task.timezone)}
                {" · "}
                {describeActions(task.actions)}
                {" · marketplace writes require item approval"}
                {task.marketplaces.length ? ` · ${task.marketplaces.join(", ")}` : ""}
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
    </WorkspacePage>
  );
}
