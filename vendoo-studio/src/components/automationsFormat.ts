import type {
  AutomationAction,
  AutomationBody,
  AutomationDraft,
  AutomationSchedule,
} from "../api/automations";

export const AUTOMATION_MARKETPLACES = [
  "ebay", "poshmark", "mercari", "depop", "etsy", "sellwild", "vinted",
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
  requireApproval: boolean;
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
    requireApproval: true,
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
  form.requireApproval = draft.require_approval !== false;
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
  body.require_approval = form.requireApproval;
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
