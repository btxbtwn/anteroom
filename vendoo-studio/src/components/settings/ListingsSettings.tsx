import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { DuplicateVendooLinksButton } from "../DuplicateVendooLinksButton";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { SettingsRow, SettingsSection } from "./SettingsLayout";

function orderedSelection(
  available: { id: string }[],
  selected: Iterable<string>,
): string[] {
  const chosen = new Set(selected);
  return available.map((item) => item.id).filter((id) => chosen.has(id));
}

function MarketplacesSection() {
  const queryClient = useQueryClient();
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["settings-marketplaces"],
    queryFn: api.settings.marketplaces,
  });
  const mutation = useMutation({
    mutationFn: (selected: string[]) => api.settings.setMarketplaces(selected),
    onSuccess: (payload) => {
      queryClient.setQueryData(["settings-marketplaces"], payload);
    },
  });
  const available = data?.available || [];
  const selected = data?.selected || [];
  const selectedSet = new Set(selected);
  const save = (next: Iterable<string>) => mutation.mutate(orderedSelection(available, next));

  return (
    <SettingsSection id="marketplaces" title="Marketplaces">
      <SettingsRow
        title="List to"
        description="Every marketplace Vendoo lists to. The thread status popup and sidebar logos show only the ones you check; empty-field prompts and Send to Vendoo fill the checked ones Studio supports."
        control={
          <>
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              disabled={mutation.isPending || available.every((item) => selectedSet.has(item.id))}
              onClick={() => save(available.map((item) => item.id))}
            >
              All
            </button>
            <button
              type="button"
              className="btn btn-sm btn-ghost"
              disabled={mutation.isPending || selected.length === 0}
              onClick={() => save([])}
            >
              None
            </button>
          </>
        }
      >
        {isPending ? (
          <p className="settings-row-desc">Loading marketplaces…</p>
        ) : isError ? (
          <p className="settings-row-desc text-error">{(error as Error).message || "Could not load marketplaces"}</p>
        ) : (
          <div className="settings-marketplace-grid" role="group" aria-label="Marketplaces to list to">
            {available.map((item) => (
              <label key={item.id} className="settings-marketplace-option">
                <input
                  type="checkbox"
                  checked={selectedSet.has(item.id)}
                  disabled={mutation.isPending}
                  onChange={(event) => {
                    const next = new Set(selected);
                    if (event.target.checked) next.add(item.id);
                    else next.delete(item.id);
                    save(next);
                  }}
                />
                <MarketplaceLogo id={item.id} label={item.label} size={16} />
                <span>
                  {item.label}
                  {!item.fillable ? <span className="settings-marketplace-note"> · Vendoo crosslist only</span> : null}
                </span>
              </label>
            ))}
            {available.some((item) => !item.fillable) ? (
              <p className="settings-row-desc">
                Studio doesn’t fill forms for “Vendoo crosslist only” marketplaces yet — Send skips them and you
                crosslist to them from Vendoo. Their live status still shows on the thread.
              </p>
            ) : null}
          </div>
        )}
        {mutation.isError ? (
          <p className="settings-row-desc text-error">{(mutation.error as Error).message}</p>
        ) : null}
      </SettingsRow>
    </SettingsSection>
  );
}

function PackageDimensionsSection() {
  const queryClient = useQueryClient();
  const { data, error, isPending } = useQuery({
    queryKey: ["settings-package-dimensions"],
    queryFn: api.settings.packageDimensions,
  });
  const [draft, setDraft] = useState<{ length: string; width: string; height: string } | null>(null);
  const values = draft || {
    length: String(data?.length ?? 13),
    width: String(data?.width ?? 10),
    height: String(data?.height ?? 3),
  };
  const saveMutation = useMutation({
    mutationFn: (dimensions: { length: number; width: number; height: number }) =>
      api.settings.setPackageDimensions(dimensions),
    onSuccess: (payload) => {
      queryClient.setQueryData(["settings-package-dimensions"], payload);
      setDraft(null);
    },
  });
  const parsed = {
    length: Number(values.length),
    width: Number(values.width),
    height: Number(values.height),
  };
  const valid = Object.values(parsed).every((value) => Number.isInteger(value) && value >= 1 && value <= 120);
  const dirty = Boolean(
    data && valid && (
      parsed.length !== data.length || parsed.width !== data.width || parsed.height !== data.height
    ),
  );

  return (
    <SettingsSection id="package-dimensions" title="Shipping defaults">
      <SettingsRow
        title="Package dimensions"
        description="Used as the L × W × H default on every generated listing. Packaged weight is inferred separately for each item from its evidence."
      >
        {isPending ? (
          <p className="settings-row-desc">Loading package dimensions…</p>
        ) : error ? (
          <p className="settings-row-desc text-error">{(error as Error).message || "Could not load package dimensions"}</p>
        ) : (
          <div className="settings-dimensions-editor">
            {(["length", "width", "height"] as const).map((name) => (
              <label className="settings-dimension-field" key={name}>
                <span className="settings-formula-label">{name[0].toUpperCase() + name.slice(1)} (in)</span>
                <input
                  className="input"
                  type="number"
                  min="1"
                  max="120"
                  step="1"
                  value={values[name]}
                  onChange={(event) => setDraft({ ...values, [name]: event.target.value })}
                />
              </label>
            ))}
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={!dirty || saveMutation.isPending}
              onClick={() => saveMutation.mutate(parsed)}
            >
              {saveMutation.isPending ? "Saving…" : "Save dimensions"}
            </button>
          </div>
        )}
        {draft && !valid ? (
          <p className="settings-row-desc text-error">Use whole inches from 1 to 120.</p>
        ) : null}
        {saveMutation.isError ? (
          <p className="settings-row-desc text-error">{(saveMutation.error as Error).message}</p>
        ) : null}
      </SettingsRow>
    </SettingsSection>
  );
}

function HiddenFieldsSection() {
  const queryClient = useQueryClient();
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["settings-hidden-fields"],
    queryFn: () => api.settings.hiddenFields(),
  });
  const restoreMutation = useMutation({
    mutationFn: (item: { marketplace: string; field: string }) =>
      api.settings.showField({ ...item, scope: "always" }),
    onSuccess: (payload) => {
      queryClient.setQueryData(["settings-hidden-fields"], payload);
      queryClient.invalidateQueries({ queryKey: ["settings-hidden-fields"] });
    },
  });
  const always = data?.always || [];

  return (
    <SettingsSection id="hidden-fields" title="Fields">
      <SettingsRow
        title="Always hidden"
        description="These marketplace fields stay off the Fields tab on every listing. Hide more from a field’s × menu, or restore them here."
      >
        {isPending ? (
          <p className="settings-row-desc">Loading hidden fields…</p>
        ) : isError ? (
          <p className="settings-row-desc text-error">{(error as Error).message || "Could not load hidden fields"}</p>
        ) : always.length === 0 ? (
          <p className="settings-row-desc">None yet. On Fields, open a field’s × menu and choose Always hide.</p>
        ) : (
          <div className="settings-hidden-list">
            {always.map((item) => (
              <div key={`${item.marketplace}:${item.field}`} className="settings-hidden-row">
                <div className="settings-hidden-copy">
                  <span className="settings-hidden-name">{item.label || item.field}</span>
                  <span className="settings-hidden-meta">
                    {item.marketplace === "general" ? "Vendoo" : item.marketplace}
                  </span>
                </div>
                <button
                  type="button"
                  className="btn btn-sm btn-ghost"
                  disabled={restoreMutation.isPending}
                  onClick={() => restoreMutation.mutate({ marketplace: item.marketplace, field: item.field })}
                >
                  Show
                </button>
              </div>
            ))}
          </div>
        )}
        {restoreMutation.isError ? (
          <p className="settings-row-desc text-error">{(restoreMutation.error as Error).message}</p>
        ) : null}
      </SettingsRow>
    </SettingsSection>
  );
}

function ListingFormulasSection() {
  const queryClient = useQueryClient();
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["settings-formulas"],
    queryFn: () => api.settings.formulas(),
  });
  const [draft, setDraft] = useState<{ title: string; description: string } | null>(null);

  const defaultTitle = data?.default_title || "";
  const defaultDescription = data?.default_description || "";
  const serverTitle = data ? data.title || defaultTitle : "";
  const serverDescription = data ? data.description || defaultDescription : "";
  const title = draft?.title ?? serverTitle;
  const description = draft?.description ?? serverDescription;

  const saveMutation = useMutation({
    mutationFn: (body: { title: string; description: string }) => api.settings.setFormulas(body),
    onSuccess: (payload) => {
      queryClient.setQueryData(["settings-formulas"], payload);
      setDraft(null);
    },
  });

  const dirty =
    draft !== null &&
    (draft.title.trim() !== serverTitle.trim() || draft.description.trim() !== serverDescription.trim());
  const usingCustom = Boolean(data?.title || data?.description);

  const persist = (nextTitle: string, nextDescription: string) => {
    const titleToSave = nextTitle.trim() === defaultTitle.trim() ? "" : nextTitle;
    const descriptionToSave = nextDescription.trim() === defaultDescription.trim() ? "" : nextDescription;
    saveMutation.mutate({ title: titleToSave, description: descriptionToSave });
  };

  return (
    <SettingsSection id="listing-formulas" title="Listing formulas">
      <SettingsRow
        title="Title and description"
        description="Studio pins these formulas into every listing generation. Leave them as the defaults, or edit the placeholders for your shop. Clearing a field and saving restores the built-in formula."
      >
        {isPending ? (
          <p className="settings-row-desc">Loading formulas…</p>
        ) : isError ? (
          <p className="settings-row-desc text-error">{(error as Error).message || "Could not load formulas"}</p>
        ) : (
          <div className="settings-formula-editor">
            <label className="settings-formula-field">
              <span className="settings-formula-label">Title</span>
              <input
                className="input"
                value={title}
                onChange={(e) => setDraft({ title: e.target.value, description })}
                spellCheck={false}
                aria-label="Title formula"
              />
            </label>
            <label className="settings-formula-field">
              <span className="settings-formula-label">Description</span>
              <textarea
                className="input settings-formula-textarea"
                value={description}
                onChange={(e) => setDraft({ title, description: e.target.value })}
                spellCheck={false}
                rows={6}
                aria-label="Description formula"
              />
            </label>
            <div className="settings-formula-actions">
              <button
                type="button"
                className="btn btn-sm btn-primary"
                disabled={saveMutation.isPending || !dirty}
                onClick={() => persist(title, description)}
              >
                {saveMutation.isPending ? "Saving…" : "Save formulas"}
              </button>
              <button
                type="button"
                className="btn btn-sm btn-outline"
                disabled={saveMutation.isPending || (!usingCustom && !dirty)}
                onClick={() => {
                  setDraft({ title: defaultTitle, description: defaultDescription });
                  persist(defaultTitle, defaultDescription);
                }}
              >
                Reset to defaults
              </button>
            </div>
            {usingCustom ? (
              <p className="settings-row-desc">Custom formulas are active for new generations.</p>
            ) : (
              <p className="settings-row-desc">Using the built-in list-this formulas.</p>
            )}
          </div>
        )}
        {saveMutation.isError ? (
          <p className="settings-row-desc text-error">{(saveMutation.error as Error).message}</p>
        ) : null}
      </SettingsRow>
    </SettingsSection>
  );
}

export function ListingsSettings({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  return (
    <>
      <MarketplacesSection />
      <PackageDimensionsSection />
      <HiddenFieldsSection />
      <ListingFormulasSection />
      <SettingsSection id="duplicate-vendoo-links" title="Duplicate check">
        <SettingsRow
          title="Duplicate Vendoo links"
          description="Find Studio listings that share the same Vendoo item and open each listing to review its link."
          control={<DuplicateVendooLinksButton onSelect={onOpenListing} />}
        />
      </SettingsSection>
    </>
  );
}
