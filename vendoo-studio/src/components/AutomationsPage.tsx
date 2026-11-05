/**
 * Scheduled automations: one-off and recurring tasks the seller confirms
 * before they delist, relist, regenerate, or change a price. The page is a
 * list of saved automations; creating and editing happen in a side panel.
 */
import { useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDownIcon, EllipsisIcon, PlusIcon } from "lucide-react";

import { api } from "../api/client";
import type { Automation, AutomationApproval, AutomationDraft, AutomationPreview, AutomationRun, RelistGap } from "../api/automations";
import { confirmDialog } from "../ui/confirmDialog";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Collapsible, CollapsiblePanel, CollapsibleTrigger } from "./ui/collapsible";
import { Input } from "./ui/input";
import { Menu, MenuItem, MenuPopup, MenuSeparator, MenuTrigger } from "./ui/menu";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { Sheet, SheetPopup, SheetTitle } from "./ui/sheet";
import { Skeleton } from "./ui/skeleton";
import { Switch } from "./ui/switch";
import { Textarea } from "./ui/textarea";
import { MarketplaceAutomations } from "./MarketplaceAutomations";
import { EbayOffersSettings } from "./settings/EbayOffersSettings";
import { marketplaceName } from "./marketplaceNames";
import {
  EmptyRow, FormError, FormField, ListRow, PageError, PageHeader, PageNote, PageSection, Panel, RowList, WorkspacePage,
} from "./WorkspacePage";
import { useWorkspaceRuntime } from "../app/runtime";
import {
  AUTOMATION_MARKETPLACES,
  WEEKDAY_LABELS,
  bodyFromForm,
  describeActions,
  describeTrigger,
  emptyForm,
  formFromDraft,
  formatRunTime,
  listedSourceLabel,
  money,
  type AutomationForm,
} from "./automationsFormat";

const LIST_KEY = ["automations"] as const;
const APPROVAL_KEY = ["automation-approvals"] as const;
const GAP_KEY = ["automation-gaps"] as const;

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

function formFromTask(task: Automation): AutomationForm {
  return formFromDraft({
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
  });
}

/** What the side panel shows: the new/edit form, or one automation's runs. */
type PanelContent =
  | { kind: "edit"; task: Automation | null }
  | { kind: "history"; task: Automation; runs: AutomationRun[] };
type PanelState = PanelContent & { key: number };

export function AutomationsPage({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  const queryClient = useQueryClient();
  const list = useQuery({ queryKey: LIST_KEY, queryFn: () => api.automations.list() });
  const approvals = useQuery({ queryKey: APPROVAL_KEY, queryFn: () => api.automations.approvals() });
  const gaps = useQuery({ queryKey: GAP_KEY, queryFn: () => api.errors.list() });
  const [panel, setPanel] = useState<PanelState | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");

  const openPanel = (next: PanelContent) => {
    setPanel((current) => ({ ...next, key: (current?.key ?? 0) + 1 }));
    setPanelOpen(true);
  };

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: LIST_KEY });
    void queryClient.invalidateQueries({ queryKey: APPROVAL_KEY });
    void queryClient.invalidateQueries({ queryKey: GAP_KEY });
  };

  /** The row switch: on resumes a paused automation, off pauses it. Neither runs anything now. */
  const setActive = (task: Automation, on: boolean) => {
    setError("");
    setBusy(task.id);
    void (on ? api.automations.resume(task.id) : api.automations.pause(task.id))
      .then(refresh)
      .catch((err: Error) => setError(err.message))
      .finally(() => setBusy(null));
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
      openPanel({ kind: "history", task, runs: [result] });
      refresh();
    }).catch((err: Error) => setError(err.message));
  };
  const showHistory = (task: Automation) => {
    void api.automations.history(task.id).then((result) => {
      openPanel({ kind: "history", task, runs: result.runs });
    }).catch((err: Error) => setError(err.message));
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
    } catch (err) {
      setError((err as Error).message);
    } finally {
      refresh();
    }
  };

  const waiting = approvals.data ?? [];
  const down = gaps.data ?? [];
  const tasks = list.data ?? [];

  return (
    <WorkspacePage>
      <PageHeader
        title="Automations"
        description="Schedule price drops, relists and draft prep; nothing lists or delists until you approve each item."
        actions={(
          <Button size="sm" onClick={() => openPanel({ kind: "edit", task: null })}>
            <PlusIcon />
            New automation
          </Button>
        )}
      />

      {error ? <PageError message={error} /> : null}

      {waiting.length ? (
        <PageSection
          title="Waiting for approval"
          description="Review each item, action and marketplace before approving one attempt."
        >
          <RowList>
            {waiting.map((row) => (
              <li key={row.id} className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5 text-sm">
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <span className="truncate text-foreground">{row.title || row.conversation_id}</span>
                  <span className="truncate text-xs text-muted-foreground">
                    {[row.action === "publish" ? "List" : row.action === "delist" ? "Delist" : "Relist",
                      row.marketplaces.map(marketplaceName).join(", "), row.state].filter(Boolean).join(" · ")}
                  </span>
                </span>
                {Object.entries(row.targets || {}).map(([name, target]) => target.url ? (
                  <a key={name} href={target.url} target="_blank" rel="noreferrer" className="text-xs text-muted-foreground underline">
                    {marketplaceName(name)} listing
                  </a>
                ) : null)}
                <Button size="sm" variant="ghost" onClick={() => onOpenListing(row.conversation_id)}>Open item</Button>
                <Button
                  size="sm"
                  disabled={!canApprove}
                  title={canApprove ? undefined : "Marketplace actions are not available on the web yet."}
                  onClick={() => void approve(row)}
                >
                  Review and approve
                </Button>
              </li>
            ))}
          </RowList>
        </PageSection>
      ) : null}

      {down.length ? (
        <PageSection title="Down, needs relist">
          <RowList>
            {down.map((gap: RelistGap) => (
              <ListRow
                key={gap.id}
                title={gap.title || gap.conversation_id}
                meta={[marketplaceName(gap.marketplace), gap.state, gap.error].filter(Boolean).join(" · ")}
                valueHint="Open item"
                onClick={() => onOpenListing(gap.conversation_id)}
              />
            ))}
          </RowList>
        </PageSection>
      ) : null}

      <PageSection title="Scheduled" aria-label="Scheduled automations">
        {list.isLoading ? (
          <Panel aria-busy="true" aria-label="Reading automations">
            {[0, 1].map((index) => (
              <div key={index} className="flex items-center gap-4 px-4 py-3">
                <span className="flex flex-1 flex-col gap-1.5">
                  <Skeleton className="h-3.5 w-40" />
                  <Skeleton className="h-3 w-64 max-w-full" />
                </span>
                <Skeleton className="h-4 w-8" />
              </div>
            ))}
          </Panel>
        ) : list.error ? (
          <PageError message={`Could not read automations: ${(list.error as Error).message}`} />
        ) : tasks.length === 0 ? (
          <EmptyRow>No automations yet. Use New automation to schedule a price drop or relist.</EmptyRow>
        ) : (
          <Panel divided={false}>
            <div
              aria-hidden="true"
              className="hidden items-center gap-4 border-b border-border/50 px-4 py-2 text-xs font-medium text-muted-foreground @2xl/page:flex"
            >
              <span className="flex-1">Automation</span>
              <span className="w-32">Last run</span>
              <span className="w-32">Next run</span>
              <span className="w-9 text-center">On</span>
              <span className="w-7" />
            </div>
            <ul className="divide-y divide-border/50">
              {tasks.map((task) => (
                <AutomationRow
                  key={task.id}
                  task={task}
                  busy={busy === task.id}
                  onEdit={() => openPanel({ kind: "edit", task })}
                  onToggle={(on) => setActive(task, on)}
                  onRun={(dryRun) => run(task, dryRun)}
                  onHistory={() => showHistory(task)}
                  onDelete={() => void remove(task)}
                />
              ))}
            </ul>
          </Panel>
        )}
      </PageSection>

      {features.has("ebay_offers") ? (
        <PageSection title="eBay offers" aria-label="eBay offers">
          <EbayOffersSettings />
        </PageSection>
      ) : null}

      {features.has("marketplace_sessions") ? <MarketplaceAutomations /> : null}

      <Sheet open={panelOpen} onOpenChange={setPanelOpen}>
        <SheetPopup side="right" className="max-w-xl">
          {panel?.kind === "edit" ? (
            <AutomationEditor
              key={panel.key}
              task={panel.task}
              onSaved={() => {
                setPanelOpen(false);
                refresh();
              }}
            />
          ) : panel?.kind === "history" ? (
            <AutomationHistory key={panel.key} task={panel.task} runs={panel.runs} />
          ) : null}
        </SheetPopup>
      </Sheet>
    </WorkspacePage>
  );
}

const STATUS_WORD: Record<string, string> = { draft: "Draft", completed: "Done" };

function AutomationRow({ task, busy, onEdit, onToggle, onRun, onHistory, onDelete }: {
  task: Automation;
  busy: boolean;
  onEdit: () => void;
  onToggle: (on: boolean) => void;
  onRun: (dryRun: boolean) => void;
  onHistory: () => void;
  onDelete: () => void;
}) {
  const togglable = task.status === "active" || task.status === "paused";
  const next = task.status === "active" ? formatRunTime(task.next_run_at, task.timezone) : "—";
  return (
    <li className="flex min-w-0 items-center gap-4 px-4 py-2.5 text-sm transition-colors hover:bg-accent/60">
      <button
        type="button"
        className="flex min-w-0 flex-1 cursor-pointer flex-col gap-0.5 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
        onClick={onEdit}
      >
        <span className="truncate font-medium text-foreground">{task.name}</span>
        <span className="text-xs text-muted-foreground @2xl/page:truncate">{describeTrigger(task)}</span>
        <span className="text-xs text-muted-foreground @2xl/page:truncate">
          {describeActions(task.actions)}
          <span className="@2xl/page:hidden">{task.status === "active" ? ` · next ${next}` : ""}</span>
        </span>
      </button>
      <span className="hidden w-32 text-xs text-muted-foreground tabular-nums @2xl/page:block">
        {formatRunTime(task.last_run_at, task.timezone)}
      </span>
      <span className="hidden w-32 text-xs text-muted-foreground tabular-nums @2xl/page:block">{next}</span>
      <span className="flex w-9 justify-center">
        {togglable ? (
          <Switch
            size="sm"
            checked={task.status === "active"}
            disabled={busy}
            aria-label={task.status === "active" ? `Pause ${task.name}` : `Turn on ${task.name}`}
            onCheckedChange={onToggle}
          />
        ) : (
          <span className="text-xs text-muted-foreground">{STATUS_WORD[task.status] ?? task.status}</span>
        )}
      </span>
      <Menu>
        <MenuTrigger render={<Button variant="ghost" size="icon-xs" aria-label={`More for ${task.name}`} />}>
          <EllipsisIcon />
        </MenuTrigger>
        <MenuPopup align="end" width="content">
          <MenuItem onClick={onEdit}>Edit</MenuItem>
          {task.status !== "draft" ? (
            <>
              <MenuItem onClick={() => onRun(true)}>Dry run</MenuItem>
              <MenuItem onClick={() => onRun(false)}>Run now</MenuItem>
            </>
          ) : null}
          <MenuItem onClick={onHistory}>History</MenuItem>
          <MenuSeparator />
          <MenuItem variant="destructive" onClick={onDelete}>Delete</MenuItem>
        </MenuPopup>
      </Menu>
    </li>
  );
}

function PanelLayout({ title, children, footer }: { title: string; children: ReactNode; footer?: ReactNode }) {
  return (
    <>
      <div className="flex h-14 shrink-0 items-center px-5">
        <SheetTitle className="text-base font-semibold">{title}</SheetTitle>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 pb-5">{children}</div>
      {footer ? (
        <div className="flex shrink-0 flex-wrap gap-2 border-t border-border/50 px-5 py-3 pb-[max(--spacing(3),env(safe-area-inset-bottom))]">
          {footer}
        </div>
      ) : null}
    </>
  );
}

/** New or edit: describe it in a sentence, or set it up by hand, preview, then save. */
function AutomationEditor({ task, onSaved }: { task: Automation | null; onSaved: () => void }) {
  const [form, setForm] = useState<AutomationForm>(() => (task ? formFromTask(task) : emptyForm()));
  const [manual, setManual] = useState(task !== null);
  const [issues, setIssues] = useState<string[]>([]);
  const [preview, setPreview] = useState<AutomationPreview | null>(null);
  const [error, setError] = useState("");

  const patch = (next: Partial<AutomationForm>) => setForm((current) => ({ ...current, ...next }));

  const draft = useMutation({
    mutationFn: () => api.automations.draft(form.prompt, form.timezone),
    onSuccess: (result: AutomationDraft) => {
      setForm(formFromDraft(result));
      setIssues(result.issues || []);
      setPreview(null);
      setManual(true);
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
      return task ? api.automations.update(task.id, body) : api.automations.create(body);
    },
    onSuccess: onSaved,
    onError: (err: Error) => setError(err.message),
  });

  const included = preview?.items.filter((item) => item.include) ?? [];
  const skipped = preview?.items.filter((item) => !item.include) ?? [];

  return (
    <PanelLayout
      title={task ? "Edit automation" : "New automation"}
      footer={(
        <>
          <Button size="sm" variant="outline" disabled={showPreview.isPending} onClick={() => showPreview.mutate()}>
            {showPreview.isPending ? "Checking…" : "Preview"}
          </Button>
          <Button size="sm" variant="outline" disabled={save.isPending} onClick={() => save.mutate(false)}>
            Save draft
          </Button>
          <Button size="sm" disabled={save.isPending} onClick={() => save.mutate(true)}>
            {save.isPending ? "Saving…" : "Save and confirm"}
          </Button>
        </>
      )}
    >
      <FormField label="Describe it">
        <Textarea
          value={form.prompt}
          placeholder="When an item reaches 60 days listed on eBay and Poshmark, delist and relist it and drop the price by 10%. Don't go below $15."
          onChange={(event) => patch({ prompt: event.target.value })}
        />
      </FormField>
      <div>
        <Button size="sm" variant="outline" disabled={!form.prompt.trim() || draft.isPending} onClick={() => draft.mutate()}>
          {draft.isPending ? "Reading…" : "Draft from prompt"}
        </Button>
      </div>

      <Collapsible open={manual} onOpenChange={setManual}>
        <CollapsibleTrigger
          render={<Button size="sm" variant="ghost" className="-ms-2 text-muted-foreground" />}
        >
          <ChevronDownIcon className={manual ? "rotate-180 transition-transform" : "transition-transform"} />
          Set it up manually
        </CollapsibleTrigger>
        <CollapsiblePanel>
          <div className="flex flex-col gap-4 pt-3">
            <div className="grid gap-3 sm:grid-cols-2">
              <FormField label="Name">
                <Input nativeInput value={form.name} onChange={(event) => patch({ name: event.target.value })} />
              </FormField>
              <FormField label="Timezone">
                <Input nativeInput value={form.timezone} onChange={(event) => patch({ timezone: event.target.value })} />
              </FormField>
              <FormField label="When">
                <Choice
                  label="When"
                  value={form.kind}
                  options={[{ value: "once", label: "One time" }, { value: "daily", label: "Every day" }, { value: "weekly", label: "Every week" }]}
                  onChange={(kind) => patch({ kind, priceRepeat: kind === "once" ? "once" : "step" })}
                />
              </FormField>
              {form.kind === "once" ? (
                <FormField label="Date and time">
                  <Input nativeInput type="datetime-local" value={form.at} onChange={(event) => patch({ at: event.target.value })} />
                </FormField>
              ) : (
                <FormField label="Time">
                  <Input nativeInput type="time" value={form.time} onChange={(event) => patch({ time: event.target.value })} />
                </FormField>
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
            <div className="grid gap-3 sm:grid-cols-2">
              <FormField label="Department">
                <Input nativeInput value={form.department} placeholder="Any" onChange={(event) => patch({ department: event.target.value })} />
              </FormField>
              <FormField label="Category">
                <Input nativeInput value={form.category} placeholder="Any" onChange={(event) => patch({ category: event.target.value })} />
              </FormField>
              <FormField label="Brand">
                <Input nativeInput value={form.brand} placeholder="Any" onChange={(event) => patch({ brand: event.target.value })} />
              </FormField>
              <FormField label="Listed at least (days)">
                <Input nativeInput inputMode="numeric" value={form.minAge} onChange={(event) => patch({ minAge: event.target.value })} />
              </FormField>
              <FormField label="Status">
                <Input nativeInput value={form.status} placeholder="Any" onChange={(event) => patch({ status: event.target.value })} />
              </FormField>
              <FormField label="Item ids">
                <Input nativeInput value={form.itemIds} placeholder="All matching items" onChange={(event) => patch({ itemIds: event.target.value })} />
              </FormField>
            </div>
            <CheckGroup label="Actions">
              <Check label="Regenerate" checked={form.regenerate} onChange={(regenerate) => patch({ regenerate })} />
              <Check label="Delist" checked={form.delist} onChange={(delist) => patch({ delist })} />
              <Check label="Relist" checked={form.relist} onChange={(relist) => patch({ relist })} />
              <Check label="Drop the price" checked={form.priceOn} onChange={(priceOn) => patch({ priceOn })} />
            </CheckGroup>
            {form.priceOn ? (
              <div className="grid gap-3 sm:grid-cols-2">
                <FormField label="Drop by">
                  <Choice
                    label="Drop by"
                    value={form.priceMode}
                    options={[{ value: "percent", label: "Percent of the original price" }, { value: "amount", label: "Dollars off the original price" }]}
                    onChange={(priceMode) => patch({ priceMode })}
                  />
                </FormField>
                <FormField label={form.priceMode === "amount" ? "Amount ($)" : "Percent"}>
                  <Input nativeInput inputMode="decimal" value={form.priceValue} onChange={(event) => patch({ priceValue: event.target.value })} />
                </FormField>
                <FormField label="Floor ($)">
                  <Input nativeInput inputMode="decimal" value={form.floor} onChange={(event) => patch({ floor: event.target.value })} />
                </FormField>
                <FormField label="How often">
                  <Choice<AutomationForm["priceRepeat"]>
                    label="How often"
                    value={form.priceRepeat}
                    options={[{ value: "step", label: "Each cycle, off the original price" }, { value: "once", label: "One-time drop" }]}
                    onChange={(priceRepeat) => patch({ priceRepeat })}
                  />
                </FormField>
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
          </div>
        </CollapsiblePanel>
      </Collapsible>

      <PageNote>Scheduling never publishes or delists on its own. Each item and marketplace waits for your approval.</PageNote>

      {issues.length ? (
        <ul className="list-disc pl-5 text-sm text-muted-foreground">
          {issues.map((issue) => <li key={issue}>{issue}</li>)}
        </ul>
      ) : null}
      {error ? <FormError>{error}</FormError> : null}
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
    </PanelLayout>
  );
}

/** Runs and what each did to each item. */
function AutomationHistory({ task, runs }: { task: Automation; runs: AutomationRun[] }) {
  return (
    <PanelLayout title={task.name}>
      {runs.length === 0 ? <EmptyRow>No runs yet.</EmptyRow> : null}
      <ul className="flex flex-col gap-3 text-sm">
        {runs.map((run) => (
          <li key={run.id}>
            <span className="text-xs text-muted-foreground">
              {formatRunTime(run.started_at || run.scheduled_for, task.timezone)} · {run.origin} · {run.status}{run.dry_run ? " · dry run" : ""}
              {run.error ? ` · ${run.error}` : ""}
            </span>
            <ul className="mt-1 flex flex-col gap-0.5">
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
    </PanelLayout>
  );
}
