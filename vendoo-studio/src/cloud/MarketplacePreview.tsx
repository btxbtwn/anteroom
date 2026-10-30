import { useRef, useState } from "react";
import type { SupabaseClient } from "@supabase/supabase-js";
import type { MarketplaceForm, MarketplaceFormField } from "../api/types";
import { cloudExtension, type BrowserAccount } from "../api/cloudExtension";
import { nativeFieldVisible } from "../components/nativeFormStability";
import { Photos } from "./Photos";

function shownValue(field: MarketplaceFormField): string {
  const one = (value: unknown): string => {
    const label = Object.entries(field.option_values || {}).find(([, id]) => String(id) === String(value))?.[0];
    if (label) return label;
    if (value && typeof value === "object") {
      const row = value as { sizeId?: string; sizeSetId?: string; quantity?: number; sku?: string };
      const group = field.inventory_groups?.find(group => group.id === row.sizeSetId);
      const size = group?.sizes.find(size => size.id === row.sizeId)?.label || row.sizeId || "";
      return [size, row.quantity != null ? `Quantity: ${row.quantity}` : "", row.sku].filter(Boolean).join(" · ");
    }
    if (typeof value === "boolean") return value ? "On" : "Off";
    return value == null ? "" : String(value);
  };
  return Array.isArray(field.value) ? field.value.map(one).join("\n") : one(field.value);
}

export function NativeFormPreview({ form, renderPhotos }: { form: MarketplaceForm; renderPhotos?: () => React.ReactNode }) {
  // Use the desktop visibility rules for the native builder's conditional rows.
  const rows = form.fields.map(field => ({ key: field.key, defaultValue: field.value, optionValues: field.option_values,
    options: field.options, categorySpecific: field.category_specific === true }));
  const visible = form.fields.filter((_field, i) => nativeFieldVisible(rows[i], { tab: "poshmark", listing: {}, local: {}, fields: rows, categoryStale: false }));
  const sections: { title: string; fields: MarketplaceFormField[] }[] = [];
  for (const field of visible) {
    const title = field.section || "";
    if (!sections.length || sections[sections.length - 1].title !== title) sections.push({ title, fields: [] });
    sections[sections.length - 1].fields.push(field);
  }
  return <div className="native-preview">{sections.map((section, i) => <section className="native-section" key={i}>
    {section.title && <h3>{section.title}</h3>}
    {section.fields[0]?.section_help && <p className="field-help">{section.fields[0].section_help}</p>}
    <div className="native-grid">{section.fields.map(field => <div key={field.key} className={`native-field width-${field.width || "full"} control-${field.control_width || "full"}`}>
      {field.label && <span className={field.label_hidden ? "visually-hidden" : "preview-label"}>{field.label}</span>}
      {field.input_type === "photos" ? renderPhotos?.() : field.input_type !== "note" && <output>{field.prefix}{shownValue(field) || "—"}{field.suffix}</output>}
      {field.help && <p className="field-help">{field.help}</p>}
      {field.options.length > 0 && <details><summary>View choices ({field.options.length})</summary><ul>{field.options.map((option, index) => <li key={index}>{option}</li>)}</ul></details>}
    </div>)}</div>
  </section>)}</div>;
}

export function MarketplacePreview({ account, listing, client, draftId }: {
  account: BrowserAccount; listing: Record<string, string | number | null>; client: SupabaseClient; draftId: string;
}) {
  const [form, setForm] = useState<MarketplaceForm | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const active = useRef(0);
  async function load() {
    const request = ++active.current;
    setBusy(true); setError(""); setForm(null);
    try { const next = await cloudExtension(account).readPoshmarkForm(listing); if (active.current === request) setForm(next); }
    catch (error) { if (active.current === request) setError((error as Error).message); }
    finally { if (active.current === request) setBusy(false); }
  }
  return <div className="marketplace-preview"><div className="list-heading"><h2>Poshmark preview</h2><button type="button" disabled={busy} onClick={() => void load()}>{busy ? "Loading Poshmark…" : "Load Poshmark form"}</button></div>
    <p className="field-help">Review this draft using Poshmark’s current fields and choices. Edit General fields and load again to refresh the preview.</p>
    {error && <p className="message" role="alert">{error} <a href="https://poshmark.com/feed" target="_blank" rel="noopener noreferrer">Open Poshmark</a></p>}
    {busy && <p role="status">Reading Poshmark’s form through Chrome…</p>}
    {form && <NativeFormPreview form={form} renderPhotos={() => <Photos client={client} draftId={draftId} disabled preview onBusy={() => {}}/>}/>}
  </div>;
}
