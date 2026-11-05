import type {
  AutomationAction,
  AutomationBody,
  AutomationDraft,
  AutomationFilters,
  AutomationSchedule,
} from "../api/automations";
import { marketplaceName } from "./marketplaceNames";

export const AUTOMATION_MARKETPLACES = [
  "ebay", "poshmark", "mercari", "depop", "etsy",
] as const;

export const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

export interface AutomationForm {
  name: string;
  prompt: string;
  timezone: string;
  kind: "once" | "daily" | "weekly";
  at: string;
  time: string;
  weekdays: number[];
  category: string;
  department: string;
  brand: string;
  minAge: string;
  status: string;
  itemIds: string;
  regenerate: boolean;
  delist: boolean;
  relist: boolean;
  priceOn: boolean;
  priceMode: "amount" | "percent";
  priceRepeat: "once" | "step";
  priceValue: string;
  floor: string;
  marketplaces: string[];
}

export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function emptyForm(timezone = browserTimezone()): AutomationForm {
  return {
    name: "",
    prompt: "",
    timezone,
    kind: "daily",
    at: "",
    time: "09:00",
    weekdays: [0],
    category: "",
    department: "",
    brand: "",
    minAge: "",
    status: "",
    itemIds: "",
    regenerate: false,
    delist: false,
    relist: false,
    priceOn: false,
    priceMode: "percent",
    priceRepeat: "step",
    priceValue: "",
    floor: "",
    marketplaces: [],
  };
}

export function formFromDraft(draft: AutomationDraft): AutomationForm {
  const form = emptyForm(draft.timezone || "UTC");
  form.name = draft.name || "";
  form.prompt = draft.prompt || "";
  const schedule = draft.schedule;
  if (schedule?.kind === "once" || schedule?.kind === "daily" || schedule?.kind === "weekly") {
    form.kind = schedule.kind;
  }
  form.at = (schedule?.at || "").slice(0, 16);
  form.time = schedule?.time || "09:00";
  form.weekdays = schedule?.weekdays?.length ? [...schedule.weekdays] : [0];
  form.category = draft.filters?.category || "";
  form.department = draft.filters?.department || "";
  form.brand = draft.filters?.brand || "";
  form.minAge = draft.filters?.min_age_days ? String(draft.filters.min_age_days) : "";
  form.status = draft.filters?.status || "";
  form.itemIds = (draft.filters?.conversation_ids || []).join(", ");
  form.marketplaces = [...(draft.marketplaces || [])];
  for (const action of draft.actions || []) {
    if (action.type === "regenerate") form.regenerate = true;
    if (action.type === "delist") form.delist = true;
    if (action.type === "relist") form.relist = true;
    if (action.type === "price") {
      form.priceOn = true;
      form.priceMode = action.mode === "amount" ? "amount" : "percent";
      form.priceRepeat = action.repeat === "once" ? "once" : "step";
      form.priceValue = action.value != null ? String(action.value) : "";
      form.floor = action.floor != null ? String(action.floor) : "";
    }
  }
  if (draft.price_repeat === "once" || draft.price_repeat === "step") form.priceRepeat = draft.price_repeat;
  return form;
}

function scheduleFromForm(form: AutomationForm): AutomationSchedule {
  if (form.kind === "once") return { kind: "once", at: form.at };
  if (form.kind === "weekly") return { kind: "weekly", time: form.time || "09:00", weekdays: form.weekdays };
  return { kind: "daily", time: form.time || "09:00" };
}

function actionsFromForm(form: AutomationForm): AutomationAction[] {
  const actions: AutomationAction[] = [];
  if (form.regenerate) actions.push({ type: "regenerate" });
  if (form.priceOn) {
    const action: AutomationAction = {
      type: "price",
      mode: form.priceMode,
      value: form.priceValue === "" ? null : Number(form.priceValue),
    };
    if (form.floor !== "") action.floor = Number(form.floor);
    action.repeat = form.priceRepeat;
    actions.push(action);
  }
  if (form.delist) actions.push({ type: "delist" });
  if (form.relist) actions.push({ type: "relist" });
  return actions;
}

function filtersFromForm(form: AutomationForm) {
  const filters: AutomationBody["filters"] = {};
  if (form.category.trim()) filters.category = form.category.trim();
  if (form.department.trim()) filters.department = form.department.trim();
  if (form.brand.trim()) filters.brand = form.brand.trim();
  if (form.minAge.trim()) filters.min_age_days = Number(form.minAge);
  if (form.status.trim()) filters.status = form.status.trim();
  const ids = form.itemIds.split(/[\s,]+/).map((id) => id.trim()).filter(Boolean);
  if (ids.length) filters.conversation_ids = ids;
  return filters;
}

/** The request body. A sentence with no boxes filled is parsed on the server. */
export function bodyFromForm(form: AutomationForm, activate: boolean): AutomationBody {
  const actions = actionsFromForm(form);
  const filters = filtersFromForm(form);
  const structured = actions.length > 0 || form.marketplaces.length > 0 || Object.keys(filters).length > 0 || form.kind === "once";
  const body: AutomationBody = {
    name: form.name.trim() || undefined,
    prompt: form.prompt.trim() || undefined,
    timezone: form.timezone.trim() || "UTC",
    activate,
  };
  if (!structured && form.prompt.trim()) return body;
  body.schedule = scheduleFromForm(form);
  body.filters = filters;
  body.actions = actions;
  body.marketplaces = form.marketplaces;
  body.price_repeat = form.priceRepeat;
  return body;
}

export function describeSchedule(schedule: AutomationSchedule | null | undefined, timezone: string): string {
  if (!schedule?.kind) return "No schedule yet";
  if (schedule.kind === "once") return `Once at ${schedule.at || "a time"} (${timezone})`;
  const clock = schedule.time || "09:00";
  if (schedule.kind === "daily") return `Daily at ${clock} (${timezone})`;
  const days = (schedule.weekdays || []).map((day) => WEEKDAY_LABELS[day] || String(day));
  return `Weekly on ${days.join(", ") || "no days"} at ${clock} (${timezone})`;
}

/** "18:30" as "6:30 PM". */
function clockLabel(time: string | undefined): string {
  const [hours, minutes] = (time || "09:00").split(":").map(Number);
  const hour = Number.isFinite(hours) ? hours : 9;
  const minute = Number.isFinite(minutes) ? minutes : 0;
  return `${hour % 12 || 12}:${String(minute).padStart(2, "0")} ${hour < 12 ? "AM" : "PM"}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;

function whenLabel(schedule: AutomationSchedule | null | undefined): string {
  if (!schedule?.kind) return "No schedule yet";
  if (schedule.kind === "once") {
    const match = /^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}:\d{2}))?/.exec(schedule.at || "");
    if (!match) return "Once";
    return `Once ${MONTHS[Number(match[2]) - 1]} ${Number(match[3])}${match[4] ? `, ${clockLabel(match[4])}` : ""}`;
  }
  if (schedule.kind === "daily") return `Every day ${clockLabel(schedule.time)}`;
  const days = (schedule.weekdays || []).map((day) => WEEKDAY_LABELS[day] || String(day));
  return `Every ${days.join(", ") || "week"} ${clockLabel(schedule.time)}`;
}

/** One line for a saved automation: when it runs, which items and where. */
export function describeTrigger(task: {
  schedule: AutomationSchedule | null | undefined;
  filters: AutomationFilters | null | undefined;
  marketplaces: readonly string[];
}): string {
  const filters = task.filters ?? {};
  const picks = [filters.department, filters.category, filters.brand, filters.status].filter(Boolean) as string[];
  if (filters.min_age_days) picks.push(`listed ${filters.min_age_days}+ days`);
  if (filters.conversation_ids?.length) {
    picks.push(`${filters.conversation_ids.length} item${filters.conversation_ids.length === 1 ? "" : "s"}`);
  }
  const where = task.marketplaces.length ? `on ${task.marketplaces.map(marketplaceName).join(", ")}` : "";
  const which = [picks.join(", "), where].filter(Boolean).join(" ");
  return [whenLabel(task.schedule), which].filter(Boolean).join(" · ");
}

/** A server time as "Nov 5, 9:00 AM" in the automation's timezone. */
export function formatRunTime(iso: string | null | undefined, timezone: string): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const options: Intl.DateTimeFormatOptions = { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" };
  let text: string;
  try {
    text = date.toLocaleString("en-US", { ...options, timeZone: timezone });
  } catch {
    text = date.toLocaleString("en-US", options);
  }
  // Newer ICU puts a narrow no-break space before AM/PM.
  return text.replace(/\u202f/g, " ");
}

export function describeActions(actions: AutomationAction[]): string {
  if (!actions.length) return "No actions";
  return actions.map((action) => {
    if (action.type !== "price") return action.type.replace("_", " ");
    const amount = action.mode === "amount" ? `$${action.value}` : `${action.value}%`;
    const cadence = action.repeat === "step" ? " each cycle" : action.repeat === "once" ? " once" : "";
    const floor = action.floor != null ? `, floor $${action.floor}` : "";
    return `drop ${amount}${cadence}${floor}`;
  }).join(", ");
}

export function listedSourceLabel(source: string | null | undefined): string {
  if (source === "publish_job") return "publish job";
  if (source === "vendoo_job") return "Vendoo";
  if (source === "item_listed") return "item date";
  if (source === "marketplace_listing") return "marketplace record";
  return "listed date";
}

export function money(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "—";
  return `$${Number.isInteger(value) ? value : value.toFixed(2)}`;
}
