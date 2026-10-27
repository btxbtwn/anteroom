import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { LABEL_COLORS, labelColor, labelDotClass, type LabelColor } from "../../labelColors";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { SettingsNote, SettingsRow, SettingsSection, SettingsSelect } from "./SettingsLayout";

const NONE = "none";

/** Stale-listing threshold and label colors. Both change only how Anteroom shows inventory. */
export function InventorySettings() {
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({ queryKey: ["settings-ui"], queryFn: api.settings.ui });
  const { data: conversations } = useQuery({ queryKey: ["conversations"], queryFn: api.conversations.list });
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: api.settings.setUi,
    onSuccess: (payload) => {
      setError(null);
      setDraft(null);
      queryClient.setQueryData(["settings-ui"], payload);
      for (const key of ["suggestions", "analytics"]) void queryClient.invalidateQueries({ queryKey: [key] });
    },
    onError: (failure) => setError(failure instanceof Error ? failure.message : "Could not save."),
  });
  const colors = useMemo(() => data?.label_colors || {}, [data?.label_colors]);
  const labels = useMemo(() => {
    const seen = new Map<string, string>();
    for (const label of [...Object.keys(colors), ...(data?.recent_vendoo_labels || []), ...(conversations || []).flatMap((item) => item.vendoo_labels || [])]) {
      if (!seen.has(label.toLowerCase())) seen.set(label.toLowerCase(), label);
    }
    return [...seen.values()].sort((a, b) => a.localeCompare(b));
  }, [colors, data?.recent_vendoo_labels, conversations]);
  const days = draft ?? String(data?.stale_listing_days ?? 60);
  const commitDays = () => {
    if (draft === null) return;
    const value = Number(draft);
    if (!Number.isInteger(value) || value < 15 || value > 120) { setError("Enter a whole number of days from 15 to 120."); return; }
    save.mutate({ stale_listing_days: value });
  };
  const setColor = (label: string, color: LabelColor | typeof NONE) => {
    const next = Object.fromEntries(Object.entries(colors).filter(([name]) => name.toLowerCase() !== label.toLowerCase()));
    if (color !== NONE) next[label] = color;
    save.mutate({ label_colors: next });
  };
  return (
    <SettingsSection id="inventory" title="Inventory">
      <SettingsRow
        id="stale-listing-days"
        title="Stale after"
        description="Days a listing can stay up unsold before Suggestions and Analytics call it stale, from 15 to 120."
        control={<Input aria-label="Days before a listing is stale" className="w-24" inputMode="numeric" value={days}
          disabled={isPending || save.isPending} onChange={(event) => setDraft(event.target.value)} onBlur={commitDays}
          onKeyDown={(event) => { if (event.key === "Enter") commitDays(); }} />}
      />
      {error ? <SettingsNote className="text-destructive-foreground">{error}</SettingsNote> : null}
      <SettingsRow id="label-colors" title="Label colors" description="A color shown beside each label in Item Details and the sidebar filters.">
        {labels.length ? <div className="grid gap-2 sm:grid-cols-2">{labels.map((label) => {
          const color = labelColor(colors, label);
          return <div key={label} className="flex min-w-0 items-center gap-2">
            <span aria-hidden className={`size-2.5 shrink-0 rounded-full ${color ? labelDotClass(color) : "border border-muted-foreground/40"}`} />
            <span className="min-w-0 flex-1 truncate text-sm">{label}</span>
            <SettingsSelect<LabelColor | typeof NONE> aria-label={`Color for ${label}`} value={color ?? NONE} disabled={isPending || save.isPending}
              onChange={(value) => setColor(label, value)}
              options={[{ value: NONE, label: "No color" }, ...LABEL_COLORS.map((value) => ({ value, label: value[0].toUpperCase() + value.slice(1) }))]} />
          </div>;
        })}</div> : <SettingsNote>Labels you add to items appear here.</SettingsNote>}
      </SettingsRow>
      <VendooLabelIdsRow />
    </SettingsSection>
  );
}

/** Items imported from Vendoo can carry its label IDs; converting stores each as its name. Shown only while any remain. */
function VendooLabelIdsRow() {
  const queryClient = useQueryClient();
  const { data: pending } = useQuery({ queryKey: ["vendoo-label-names"], queryFn: api.imports.labelNames });
  const convert = useMutation({
    mutationFn: api.imports.convertLabelNames,
    onSuccess: (result) => {
      queryClient.setQueryData(["vendoo-label-names"], { items: 0, converted: 0, unknown: result.unknown });
      for (const key of ["conversations", "conversation", "listing", "settings-ui", "analytics"]) void queryClient.invalidateQueries({ queryKey: [key] });
    },
  });
  const result = convert.data;
  if (!pending || (!pending.converted && !pending.unknown.length && !result)) return null;
  const waiting = pending.converted + pending.unknown.length;
  return (
    <SettingsRow
      id="vendoo-label-ids"
      title="Vendoo label IDs"
      description="Items imported from Vendoo can carry its label IDs instead of names. Convert stores each label as its name, on every item, so labels no longer depend on Vendoo. Running it again is safe."
      control={<Button size="xs" variant="outline" disabled={convert.isPending || !waiting} onClick={() => convert.mutate()}>
        {convert.isPending ? "Converting…" : "Convert to names"}
      </Button>}
    >
      {result ? (
        <SettingsNote>
          {result.converted ? `Converted ${result.converted} ${result.converted === 1 ? "label" : "labels"} on ${result.items} ${result.items === 1 ? "item" : "items"}.` : "No labels needed converting."}
        </SettingsNote>
      ) : pending.converted ? (
        <SettingsNote>{pending.converted} {pending.converted === 1 ? "label" : "labels"} on {pending.items} {pending.items === 1 ? "item is" : "items are"} still Vendoo IDs.</SettingsNote>
      ) : null}
      {(result ?? pending).unknown.length ? (
        <SettingsNote>
          Vendoo has no name for {(result ?? pending).unknown.join(", ")}. {(result ?? pending).unknown.length === 1 ? "It stays" : "They stay"} on the items as they are; with Chrome connected, Convert asks Vendoo again.
        </SettingsNote>
      ) : null}
      {convert.isError ? <SettingsNote tone="error">{(convert.error as Error).message}</SettingsNote> : null}
    </SettingsRow>
  );
}
