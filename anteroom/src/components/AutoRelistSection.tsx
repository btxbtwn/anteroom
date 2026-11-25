/**
 * Per-marketplace unattended Auto-Relist and eBay Auto-Recreate settings.
 *
 * Every marketplace rule defaults to OFF and requires explicit seller confirmation
 * to turn on. Respects the 30-day minimum on Poshmark/Mercari/Depop, eBay GTC
 * recreate window (~30m before renewal), daily cap (<= 100), optional price drop
 * with floor, filters, and atomic listing ID swapping.
 */
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDownIcon, RefreshCwIcon } from "lucide-react";

import { api } from "../api/client";
import { autoRelistRulesQuery } from "../api/pageQueries";
import type { AutoRelistMarketplace, AutoRelistRule, AutoRelistRuleBody } from "../api/automations";
import { confirmDialog } from "../ui/confirmDialog";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";
import { Collapsible, CollapsiblePanel, CollapsibleTrigger } from "./ui/collapsible";
import { Input } from "./ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";
import { Spinner } from "./ui/spinner";
import { Switch } from "./ui/switch";
import { PageError, PageSection, Panel } from "./WorkspacePage";

const KEY = autoRelistRulesQuery.queryKey;
const LOGS_KEY = ["auto-relist-logs"] as const;

interface RuleFormState {
  minAgeDays: string;
  ebayGtcMinutes: string;
  dailyCap: string;
  priceMode: "none" | "percent" | "amount";
  priceValue: string;
  priceFloor: string;
  minLikes: string;
  maxLikes: string;
  minPrice: string;
  maxPrice: string;
  conditions: string;
  keywords: string;
  labels: string;
}

function formFromRule(rule: AutoRelistRule): RuleFormState {
  return {
    minAgeDays: String(rule.min_age_days ?? 30),
    ebayGtcMinutes: String(rule.ebay_recreate_before_gtc_minutes ?? 30),
    dailyCap: String(rule.daily_cap ?? 25),
    priceMode: rule.price_adjust_mode ?? "none",
    priceValue: rule.price_adjust_value != null ? String(rule.price_adjust_value) : "",
    priceFloor: rule.price_floor != null ? String(rule.price_floor) : "",
    minLikes: rule.filters.min_likes != null ? String(rule.filters.min_likes) : "",
    maxLikes: rule.filters.max_likes != null ? String(rule.filters.max_likes) : "",
    minPrice: rule.filters.min_price != null ? String(rule.filters.min_price) : "",
    maxPrice: rule.filters.max_price != null ? String(rule.filters.max_price) : "",
    conditions: (rule.filters.conditions ?? []).join(", "),
    keywords: (rule.filters.keywords ?? []).join(", "),
    labels: (rule.filters.labels ?? []).join(", "),
  };
}

function splitCsv(text: string): string[] | undefined {
  const items = text.split(",").map((part) => part.trim()).filter(Boolean);
  return items.length ? items : undefined;
}

function bodyFromRuleForm(
  marketplace: AutoRelistMarketplace,
  form: RuleFormState,
  enabled: boolean,
  confirm: boolean,
): AutoRelistRuleBody {
  const filters: AutoRelistRuleBody["filters"] = {};
  if (form.minLikes.trim()) filters.min_likes = Number(form.minLikes);
  if (form.maxLikes.trim()) filters.max_likes = Number(form.maxLikes);
  if (form.minPrice.trim()) filters.min_price = Number(form.minPrice);
  if (form.maxPrice.trim()) filters.max_price = Number(form.maxPrice);
  const conditions = splitCsv(form.conditions);
  if (conditions) filters.conditions = conditions;
  const keywords = splitCsv(form.keywords);
  if (keywords) filters.keywords = keywords;
  const labels = splitCsv(form.labels);
  if (labels) filters.labels = labels;

  return {
    enabled,
    confirm,
    min_age_days: Number(form.minAgeDays) || 30,
    ebay_recreate_before_gtc_minutes: marketplace === "ebay" ? Number(form.ebayGtcMinutes) || 30 : null,
    daily_cap: Number(form.dailyCap) || 25,
    price_adjust_mode: form.priceMode === "none" ? null : form.priceMode,
    price_adjust_value: form.priceMode === "none" ? null : Number(form.priceValue) || null,
    price_floor: form.priceFloor.trim() ? Number(form.priceFloor) : null,
    filters,
  };
}

function ruleSummary(rule: AutoRelistRule): string {
  const cadence = rule.marketplace === "ebay"
    ? `Recreates ~${rule.ebay_recreate_before_gtc_minutes ?? 30} min before 30-day GTC renewal`
    : `Minimum ${rule.min_age_days}-day age cycle`;
  const price = rule.price_adjust_mode && rule.price_adjust_value
    ? ` · Drop ${rule.price_adjust_mode === "percent" ? `${rule.price_adjust_value}%` : `$${rule.price_adjust_value}`}${rule.price_floor != null ? ` (floor $${rule.price_floor})` : ""}`
    : "";
  return `${cadence} · Cap ${rule.relisted_today}/${rule.daily_cap} today${price}`;
}

export function AutoRelistSection() {
  const queryClient = useQueryClient();
  const rulesQuery = useQuery(autoRelistRulesQuery);
  const logsQuery = useQuery({
    queryKey: LOGS_KEY,
    queryFn: () => api.automations.autoRelistLogs(undefined, 30),
  });
  const [editing, setEditing] = useState<AutoRelistMarketplace | null>(null);
  const [form, setForm] = useState<RuleFormState | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: KEY });
    void queryClient.invalidateQueries({ queryKey: LOGS_KEY });
  };

  const saveRule = useMutation({
    mutationFn: ({ marketplace, body }: { marketplace: AutoRelistMarketplace; body: AutoRelistRuleBody }) =>
      api.automations.saveAutoRelistRule(marketplace, body),
    onSuccess: (saved) => {
      setError("");
      setNotice(
        `${marketplaceName(saved.marketplace)} auto-relist ${saved.enabled ? "enabled" : "saved (off)"}.`,
      );
      setEditing(null);
      setForm(null);
      refresh();
    },
    onError: (err: Error) => setError(err.message),
  });

  const runNow = useMutation({
    mutationFn: () => api.automations.runAutoRelist(),
    onSuccess: (res) => {
      setError("");
      setNotice(`Auto-relist pass finished: ${res.outcomes.length} listing ${res.outcomes.length === 1 ? "action" : "actions"} logged.`);
      refresh();
    },
    onError: (err: Error) => {
      setError(err.message);
      // Mobile Safari often drops long POSTs; the server may still have finished.
      refresh();
    },
  });

  const rules = rulesQuery.data ?? [];
  const logs = logsQuery.data ?? [];
  const anyEnabled = rules.some((r) => r.enabled);
  const busy = saveRule.isPending || runNow.isPending;

  const toggleRule = async (rule: AutoRelistRule, nextEnabled: boolean) => {
    setError("");
    if (nextEnabled) {
      const modeText = rule.marketplace === "ebay"
        ? `recreate eligible eBay listings ~${rule.ebay_recreate_before_gtc_minutes ?? 30} minutes before their 30-day GTC renewal`
        : `delist and relist stale ${marketplaceName(rule.marketplace)} listings at least ${rule.min_age_days} days old`;
      const ok = await confirmDialog(
        `Enable unattended auto-relist on ${marketplaceName(rule.marketplace)}? Anteroom will ${modeText}, up to ${rule.daily_cap}/day, and atomically update tracked listing IDs so post-sale auto-delisting stays pinned. Any open buyer offer on a relisted listing ends with the old listing.`,
        { confirmLabel: "Enable auto-relist" },
      );
      if (!ok) return;
    }
    const body = bodyFromRuleForm(rule.marketplace, formFromRule(rule), nextEnabled, nextEnabled);
    saveRule.mutate({ marketplace: rule.marketplace, body });
  };

  const openEditor = (rule: AutoRelistRule) => {
    setError("");
    if (editing === rule.marketplace) {
      setEditing(null);
      setForm(null);
    } else {
      setEditing(rule.marketplace);
      setForm(formFromRule(rule));
    }
  };

  const saveEdited = async (rule: AutoRelistRule) => {
    if (!form) return;
    setError("");
    const body = bodyFromRuleForm(rule.marketplace, form, rule.enabled, rule.enabled);
    saveRule.mutate({ marketplace: rule.marketplace, body });
  };

  return (
    <PageSection
      title="Auto-relist and eBay auto-recreate"
      description="Opt-in per-marketplace relisting (off by default). Atomically swaps tracked listing IDs so sale auto-delisting never loses track."
      actions={anyEnabled ? (
        <Button size="xs" variant="outline" disabled={busy} onClick={() => runNow.mutate()}>
          {runNow.isPending ? <Spinner size="sm" /> : <RefreshCwIcon className="size-3.5" />}
          Run due relists
        </Button>
      ) : null}
    >
      {error ? <PageError message={error} /> : null}
      {notice ? <p role="status" className="text-xs text-muted-foreground">{notice}</p> : null}

      <Panel>
        {rules.map((rule) => {
          const isEditing = editing === rule.marketplace && form !== null;
          return (
            <div key={rule.marketplace} className="px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-foreground">
                    {marketplaceName(rule.marketplace)}
                    {rule.marketplace === "ebay" ? " · Auto-recreate" : " · Auto-relist"}
                  </p>
                  <p className="text-xs text-muted-foreground">{ruleSummary(rule)}</p>
                  {rule.paused_reason ? <p className="text-xs text-warning-foreground">{rule.paused_reason}</p> : null}
                </div>
                <div className="flex items-center gap-3">
                  <Button size="xs" variant="ghost" disabled={busy} onClick={() => openEditor(rule)}>
                    {isEditing ? "Close" : "Configure"}
                  </Button>
                  <Switch
                    size="sm"
                    checked={rule.enabled}
                    disabled={busy}
                    aria-label={`Enable auto-relist for ${marketplaceName(rule.marketplace)}`}
                    onCheckedChange={(checked) => void toggleRule(rule, checked)}
                  />
                </div>
              </div>

              {isEditing ? (
                <div className="mt-3 space-y-3 rounded-lg border border-border/60 p-3 text-xs">
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    {rule.marketplace === "ebay" ? (
                      <label className="flex flex-col gap-1">
                        Minutes before 30-day GTC renewal
                        <Input
                          type="number"
                          min={5}
                          max={1440}
                          value={form.ebayGtcMinutes}
                          onChange={(e) => setForm({ ...form, ebayGtcMinutes: e.target.value })}
                        />
                      </label>
                    ) : (
                      <label className="flex flex-col gap-1">
                        Minimum age (days, min 30)
                        <Input
                          type="number"
                          min={30}
                          max={365}
                          value={form.minAgeDays}
                          onChange={(e) => setForm({ ...form, minAgeDays: e.target.value })}
                        />
                      </label>
                    )}
                    <label className="flex flex-col gap-1">
                      Daily cap (1–100)
                      <Input
                        type="number"
                        min={1}
                        max={100}
                        value={form.dailyCap}
                        onChange={(e) => setForm({ ...form, dailyCap: e.target.value })}
                      />
                    </label>
                    <label className="flex flex-col gap-1">
                      Price adjustment
                      <Select
                        value={form.priceMode}
                        onValueChange={(next) => setForm({ ...form, priceMode: (next || "none") as RuleFormState["priceMode"] })}
                      >
                        <SelectTrigger size="sm" aria-label="Price adjustment mode">
                          <SelectValue>
                            {form.priceMode === "percent" ? "Drop by %" : form.priceMode === "amount" ? "Drop by $" : "Keep same price"}
                          </SelectValue>
                        </SelectTrigger>
                        <SelectPopup>
                          <SelectItem value="none">Keep same price</SelectItem>
                          <SelectItem value="percent">Drop by %</SelectItem>
                          <SelectItem value="amount">Drop by $</SelectItem>
                        </SelectPopup>
                      </Select>
                    </label>
                  </div>

                  {form.priceMode !== "none" ? (
                    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                      <label className="flex flex-col gap-1">
                        {form.priceMode === "percent" ? "Drop percent (%)" : "Drop amount ($)"}
                        <Input
                          type="number"
                          min="0.01"
                          step="0.01"
                          value={form.priceValue}
                          onChange={(e) => setForm({ ...form, priceValue: e.target.value })}
                        />
                      </label>
                      <label className="flex flex-col gap-1">
                        Price floor ($, optional)
                        <Input
                          type="number"
                          min="0"
                          step="0.01"
                          placeholder="No floor"
                          value={form.priceFloor}
                          onChange={(e) => setForm({ ...form, priceFloor: e.target.value })}
                        />
                      </label>
                    </div>
                  ) : null}

                  <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                    <label className="flex flex-col gap-1">
                      Min likes / watchers
                      <Input type="number" min={0} placeholder="Any" value={form.minLikes}
                        onChange={(e) => setForm({ ...form, minLikes: e.target.value })} />
                    </label>
                    <label className="flex flex-col gap-1">
                      Max likes / watchers
                      <Input type="number" min={0} placeholder="Any" value={form.maxLikes}
                        onChange={(e) => setForm({ ...form, maxLikes: e.target.value })} />
                    </label>
                    <label className="flex flex-col gap-1">
                      Min price ($)
                      <Input type="number" min={0} step="0.01" placeholder="Any" value={form.minPrice}
                        onChange={(e) => setForm({ ...form, minPrice: e.target.value })} />
                    </label>
                    <label className="flex flex-col gap-1">
                      Max price ($)
                      <Input type="number" min={0} step="0.01" placeholder="Any" value={form.maxPrice}
                        onChange={(e) => setForm({ ...form, maxPrice: e.target.value })} />
                    </label>
                  </div>

                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                    <label className="flex flex-col gap-1">
                      Conditions (comma-separated)
                      <Input placeholder="New with tags, Good" value={form.conditions}
                        onChange={(e) => setForm({ ...form, conditions: e.target.value })} />
                    </label>
                    <label className="flex flex-col gap-1">
                      Keywords (comma-separated)
                      <Input placeholder="vintage, denim" value={form.keywords}
                        onChange={(e) => setForm({ ...form, keywords: e.target.value })} />
                    </label>
                    <label className="flex flex-col gap-1">
                      Labels (comma-separated)
                      <Input placeholder="winter, clearance" value={form.labels}
                        onChange={(e) => setForm({ ...form, labels: e.target.value })} />
                    </label>
                  </div>

                  <p>Relisting ends any open buyer offer on the old listing.</p>

                  <div className="flex gap-2">
                    <Button size="xs" disabled={busy} onClick={() => void saveEdited(rule)}>
                      {saveRule.isPending ? <Spinner size="sm" /> : null}
                      Save settings
                    </Button>
                    <Button size="xs" variant="ghost" disabled={busy} onClick={() => { setEditing(null); setForm(null); }}>
                      Cancel
                    </Button>
                  </div>
                </div>
              ) : null}
            </div>
          );
        })}
      </Panel>

      {logs.length ? (
        <Collapsible>
          <CollapsibleTrigger className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground">
            <ChevronDownIcon className="size-3.5" />
            Auto-relist log ({logs.length})
          </CollapsibleTrigger>
          <CollapsiblePanel className="mt-2">
            <Panel>
              {logs.map((entry) => (
                <div key={entry.id} className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-xs">
                  <div className="min-w-0">
                    <p className="truncate font-medium text-foreground">
                      {entry.title || entry.conversation_id} · {marketplaceName(entry.marketplace)}
                    </p>
                    <p className={entry.status === "failed" || entry.status === "down_needs_relist" ? "text-destructive-foreground" : "text-muted-foreground"}>
                      {entry.status}
                      {entry.old_listing_id && entry.new_listing_id ? ` · ${entry.old_listing_id} → ${entry.new_listing_id}` : ""}
                      {entry.old_price != null && entry.new_price != null && entry.old_price !== entry.new_price
                        ? ` · $${entry.old_price} → $${entry.new_price}`
                        : ""}
                      {entry.reason ? ` · ${entry.reason}` : ""}
                    </p>
                  </div>
                  <span className="tabular-nums text-muted-foreground">
                    {entry.created_at ? new Date(entry.created_at).toLocaleString() : ""}
                  </span>
                </div>
              ))}
            </Panel>
          </CollapsiblePanel>
        </Collapsible>
      ) : null}
    </PageSection>
  );
}
