import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { DuplicateVendooLinksButton } from "../DuplicateVendooLinksButton";
import { MarketplaceLogo } from "../MarketplaceLogo";
import { EyeIcon, RotateCcwIcon } from "lucide-react";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Label } from "../ui/label";
import { Spinner } from "../ui/spinner";
import { Switch } from "../ui/switch";
import { Textarea } from "../ui/textarea";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

function LoadingNote({ children }: { children: string }) {
  return (
    <SettingsNote className="flex items-center gap-1.5">
      <Spinner size="xs" tone="muted" />
      {children}
    </SettingsNote>
  );
}

function orderedSelection(
  available: { id: string }[],
  selected: Iterable<string>,
): string[] {
  const chosen = new Set(selected);
  return available.map((item) => item.id).filter((id) => chosen.has(id));
}

function VendooSection() {
  const queryClient = useQueryClient();
  const { data, isPending } = useQuery({
    queryKey: ["settings-ui"],
    queryFn: api.settings.ui,
  });
  const mutation = useMutation({
    mutationFn: (enabled: boolean) => api.settings.setUi({ vendoo_enabled: enabled }),
    onSuccess: (payload) => queryClient.setQueryData(["settings-ui"], payload),
  });
  return (
    <SettingsSection id="vendoo" title="Vendoo">
      <SettingsRow
        title="Send to Vendoo"
        description="Shows the Send to Vendoo button under each listing. Turn off to list without Vendoo."
        control={
          <Switch
            checked={data?.vendoo_enabled ?? true}
            disabled={isPending || mutation.isPending}
            aria-label="Send to Vendoo"
            onCheckedChange={(checked) => mutation.mutate(checked)}
          />
        }
      />
    </SettingsSection>
  );
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
        description="Every marketplace Vendoo lists to. The thread status popup and sidebar logos show only the ones you check; empty-field prompts and Send to Vendoo fill the checked ones Anteroom supports."
        control={
          <>
            <Button
              size="xs"
              variant="ghost-muted"
              disabled={mutation.isPending || available.every((item) => selectedSet.has(item.id))}
              onClick={() => save(available.map((item) => item.id))}
            >
              All
            </Button>
            <Button
              size="xs"
              variant="ghost-muted"
              disabled={mutation.isPending || selected.length === 0}
              onClick={() => save([])}
            >
              None
            </Button>
          </>
        }
      >
        {isPending ? (
          <LoadingNote>Loading marketplaces</LoadingNote>
        ) : isError ? (
          <SettingsNote tone="error">{(error as Error).message || "Could not load marketplaces"}</SettingsNote>
        ) : (
          <>
            <div
              className="grid gap-px overflow-hidden rounded-lg border border-border/60 bg-border/50 sm:grid-cols-2"
              role="group"
              aria-label="Marketplaces to list to"
            >
              {available.map((item) => (
                <Label
                  key={item.id}
                  className="flex min-h-11 cursor-pointer items-center gap-2.5 bg-background px-3 py-2 font-normal transition-colors hover:bg-muted/40 dark:bg-card"
                >
                  <MarketplaceLogo id={item.id} label={item.label} size={16} />
                  <span className="min-w-0 flex-1 truncate text-sm text-foreground">{item.label}</span>
                  {!item.fillable ? (
                    <Badge variant="outline" size="sm" className="shrink-0 text-muted-foreground">
                      Crosslist only
                    </Badge>
                  ) : null}
                  <Switch
                    checked={selectedSet.has(item.id)}
                    disabled={mutation.isPending}
                    aria-label={`List to ${item.label}`}
                    onCheckedChange={(checked) => {
                      const next = new Set(selected);
                      if (checked) next.add(item.id);
                      else next.delete(item.id);
                      save(next);
                    }}
                  />
                </Label>
              ))}
              {available.length % 2 === 1 ? <span aria-hidden className="hidden bg-background sm:block dark:bg-card" /> : null}
            </div>
            {available.some((item) => !item.fillable) ? (
              <SettingsNote>
                Anteroom doesn’t fill forms for “Crosslist only” marketplaces yet — Send skips them and you
                crosslist to them from Vendoo. Their live status still shows on the thread.
              </SettingsNote>
            ) : null}
          </>
        )}
        {mutation.isError ? <SettingsNote tone="error">{(mutation.error as Error).message}</SettingsNote> : null}
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
          <LoadingNote>Loading package dimensions</LoadingNote>
        ) : error ? (
          <SettingsNote tone="error">{(error as Error).message || "Could not load package dimensions"}</SettingsNote>
        ) : (
          <div className="flex flex-wrap items-end gap-2">
            {(["length", "width", "height"] as const).map((name) => (
              <Label className="grid w-24 gap-1 text-xs font-normal text-muted-foreground" key={name}>
                {name[0].toUpperCase() + name.slice(1)} (in)
                <Input
                  size="sm"
                  type="number"
                  min="1"
                  max="120"
                  step="1"
                  value={values[name]}
                  aria-invalid={draft && !valid ? true : undefined}
                  onChange={(event) => setDraft({ ...values, [name]: event.target.value })}
                />
              </Label>
            ))}
            <Button size="sm" disabled={!dirty || saveMutation.isPending} onClick={() => saveMutation.mutate(parsed)}>
              {saveMutation.isPending ? <Spinner /> : null}
              {saveMutation.isPending ? "Saving" : "Save dimensions"}
            </Button>
          </div>
        )}
        {draft && !valid ? <SettingsNote tone="error">Use whole inches from 1 to 120.</SettingsNote> : null}
        {saveMutation.isError ? <SettingsNote tone="error">{(saveMutation.error as Error).message}</SettingsNote> : null}
      </SettingsRow>
      <ShipsFromZipRow />
    </SettingsSection>
  );
}

function ShipsFromZipRow() {
  const queryClient = useQueryClient();
  const { data, error, isPending } = useQuery({
    queryKey: ["settings-ships-from-zip"],
    queryFn: api.settings.shipsFromZip,
  });
  const [draft, setDraft] = useState<string | null>(null);
  const value = draft ?? data?.zip ?? "";
  const valid = value === "" || /^\d{5}$/.test(value);
  const saveMutation = useMutation({
    mutationFn: (zip: string) => api.settings.setShipsFromZip(zip),
    onSuccess: (payload) => {
      queryClient.setQueryData(["settings-ships-from-zip"], { zip: payload.zip });
      setDraft(null);
    },
  });

  return (
    <SettingsRow
      title="Ships from ZIP"
      description="Mercari and Depop use it as the ships-from address when Anteroom lists there directly."
    >
      {isPending ? (
        <LoadingNote>Loading ZIP</LoadingNote>
      ) : error ? (
        <SettingsNote tone="error">{(error as Error).message || "Could not load the ZIP"}</SettingsNote>
      ) : (
        <div className="flex flex-wrap items-end gap-2">
          <Input
            size="sm"
            className="w-24"
            inputMode="numeric"
            maxLength={5}
            aria-label="Ships from ZIP"
            value={value}
            aria-invalid={!valid ? true : undefined}
            onChange={(event) => setDraft(event.target.value.trim())}
          />
          <Button
            size="sm"
            disabled={!valid || value === (data?.zip ?? "") || saveMutation.isPending}
            onClick={() => saveMutation.mutate(value)}
          >
            {saveMutation.isPending ? <Spinner /> : null}
            {saveMutation.isPending ? "Saving" : "Save ZIP"}
          </Button>
        </div>
      )}
      {!valid ? <SettingsNote tone="error">A ZIP code is five digits.</SettingsNote> : null}
      {saveMutation.isError ? <SettingsNote tone="error">{(saveMutation.error as Error).message}</SettingsNote> : null}
    </SettingsRow>
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
        description="These marketplace fields stay hidden on every listing. Restore any you want back."
      >
        {isPending ? (
          <LoadingNote>Loading hidden fields</LoadingNote>
        ) : isError ? (
          <SettingsNote tone="error">{(error as Error).message || "Could not load hidden fields"}</SettingsNote>
        ) : always.length === 0 ? (
          <SettingsNote>None yet.</SettingsNote>
        ) : (
          <ul className="divide-y divide-border/50 overflow-hidden rounded-lg border border-border/60">
            {always.map((item) => (
              <li key={`${item.marketplace}:${item.field}`} className="flex min-w-0 items-center gap-3 py-1.5 pr-1.5 pl-3">
                <span className="min-w-0 flex-1 truncate text-sm text-foreground">{item.label || item.field}</span>
                <span className="shrink-0 text-xs capitalize text-muted-foreground">
                  {item.marketplace === "general" ? "Vendoo" : item.marketplace}
                </span>
                <Button
                  size="xs"
                  variant="ghost-muted"
                  disabled={restoreMutation.isPending}
                  onClick={() => restoreMutation.mutate({ marketplace: item.marketplace, field: item.field })}
                >
                  <EyeIcon />
                  Show
                </Button>
              </li>
            ))}
          </ul>
        )}
        {restoreMutation.isError ? <SettingsNote tone="error">{(restoreMutation.error as Error).message}</SettingsNote> : null}
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
        description="Anteroom pins these formulas into every listing generation. Leave them as the defaults, or edit the placeholders for your shop. Clearing a field and saving restores the built-in formula."
      >
        {isPending ? (
          <LoadingNote>Loading formulas</LoadingNote>
        ) : isError ? (
          <SettingsNote tone="error">{(error as Error).message || "Could not load formulas"}</SettingsNote>
        ) : (
          <div className="grid gap-3">
            <Label className="grid gap-1 text-xs font-normal text-muted-foreground">
              Title
              <Input
                size="sm"
                font="mono"
                value={title}
                onChange={(e) => setDraft({ title: e.target.value, description })}
                spellCheck={false}
                aria-label="Title formula"
              />
            </Label>
            <Label className="grid gap-1 text-xs font-normal text-muted-foreground">
              Description
              <Textarea
                size="sm"
                className="font-mono text-xs"
                value={description}
                onChange={(e) => setDraft({ title, description: e.target.value })}
                spellCheck={false}
                rows={6}
                aria-label="Description formula"
              />
            </Label>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" disabled={saveMutation.isPending || !dirty} onClick={() => persist(title, description)}>
                {saveMutation.isPending ? <Spinner /> : null}
                {saveMutation.isPending ? "Saving" : "Save formulas"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={saveMutation.isPending || (!usingCustom && !dirty)}
                onClick={() => {
                  setDraft({ title: defaultTitle, description: defaultDescription });
                  persist(defaultTitle, defaultDescription);
                }}
              >
                <RotateCcwIcon />
                Reset to defaults
              </Button>
              <span className="text-xs text-muted-foreground">
                {usingCustom ? "Custom formulas are active for new generations." : "Using the built-in list-this formulas."}
              </span>
            </div>
          </div>
        )}
        {saveMutation.isError ? <SettingsNote tone="error">{(saveMutation.error as Error).message}</SettingsNote> : null}
      </SettingsRow>
    </SettingsSection>
  );
}

export function ListingsSettings({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  return (
    <>
      <VendooSection />
      <MarketplacesSection />
      <PackageDimensionsSection />
      <HiddenFieldsSection />
      <ListingFormulasSection />
      <SettingsSection id="duplicate-vendoo-links" title="Duplicate check">
        <SettingsRow
          title="Duplicate Vendoo links"
          description="Find Anteroom listings that share the same Vendoo item and open each listing to review its link."
          control={<DuplicateVendooLinksButton onSelect={onOpenListing} />}
        />
      </SettingsSection>
    </>
  );
}
