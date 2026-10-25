import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { NativeDraft, NativeMarketplace } from "../api/nativeMarketplaces";
import { confirmDialog } from "../ui/confirmDialog";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Select, SelectTrigger, SelectValue, SelectPopup, SelectItem } from "./ui/select";

export function NativeMarketplaceActions({ convId, revisionId, title, disabled }: {
  convId: string; revisionId: string | null; title: string; disabled: boolean;
}) {
  const queryClient = useQueryClient();
  const [draftLinks, setDraftLinks] = useState<Partial<Record<NativeMarketplace, string>>>({});
  const [expanded, setExpanded] = useState(false);
  const [shippingProfile, setShippingProfile] = useState("");
  const [processingProfile, setProcessingProfile] = useState("");
  const [packed, setPacked] = useState({ weight: "", length: "", width: "", height: "" });
  const [sellwildPick, setSellwildPick] = useState<{ categoryId?: number; conditionId?: number }>({});
  const queryKey = ["native-marketplaces", convId];
  const status = useQuery({ queryKey, queryFn: () => api.nativeMarketplaces.status(convId) });
  const profiles = useQuery({ queryKey: ["etsy-listing-profiles"], queryFn: () => api.etsy.listingProfiles(),
    enabled: expanded && !status.isPending && !status.data?.etsy?.draft_url, staleTime: 60_000, retry: false });
  const sellwildOptions = useQuery({ queryKey: ["sellwild-options", convId, revisionId], queryFn: () => api.nativeMarketplaces.sellwildOptions(convId),
    enabled: expanded, staleTime: 60_000, retry: false });
  const savedSellwild = status.data?.sellwild;
  const sellwildCategory = sellwildPick.categoryId ?? savedSellwild?.sellwild_category_id ?? sellwildOptions.data?.suggested.category_id ?? undefined;
  const sellwildCondition = sellwildPick.conditionId ?? savedSellwild?.sellwild_condition_id ?? sellwildOptions.data?.suggested.condition_id ?? undefined;
  const sellwildChanged = savedSellwild?.state === "draft" && (savedSellwild.sellwild_category_id !== sellwildCategory || savedSellwild.sellwild_condition_id !== sellwildCondition);
  const calculated = profiles.data?.shipping.find((profile) => profile.id === shippingProfile)?.is_calculated;
  const packageReady = Object.values(packed).every((value) => Number(value) > 0 && Number.isFinite(Number(value)));
  const action = useMutation({
    mutationFn: async ({ marketplace, kind, draftUrl }: { marketplace: NativeMarketplace; kind: "draft" | "publish" | "check"; draftUrl?: string }) => {
      if (kind === "check") return api.nativeMarketplaces.check(convId, marketplace, draftUrl);
      if (!revisionId) throw new Error("Generate a listing first.");
      if (kind === "draft") return api.nativeMarketplaces.saveDraft(convId, marketplace, revisionId,
        marketplace === "etsy" ? { shippingProfileId: shippingProfile, readinessStateId: processingProfile,
          ...(calculated ? { package: { item_weight: Number(packed.weight), item_length: Number(packed.length),
            item_width: Number(packed.width), item_height: Number(packed.height), item_weight_unit: "oz", item_dimensions_unit: "in" } } : {}) } : undefined,
        marketplace === "sellwild" && sellwildCategory && sellwildCondition ? { categoryId: sellwildCategory, conditionId: sellwildCondition } : undefined);
      const draft = status.data?.[marketplace];
      if (!draft || (marketplace !== "sellwild" && !draft.draft_url)) throw new Error("Save a draft first.");
      return api.nativeMarketplaces.publish(convId, marketplace, revisionId, draft.draft_url);
    },
    retry: false,
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey });
      void queryClient.invalidateQueries({ queryKey: ["jobs", convId] });
    },
  });

  async function publish(marketplace: NativeMarketplace, draft: NativeDraft) {
    const name = marketplaceName(marketplace);
    const approved = await confirmDialog(
      `Publish “${title}” on ${name}?\n${marketplace === "sellwild"
        ? "This creates a live Sellwild listing from the saved title, description, price, photos, category, condition, package size and your Sellwild ship-from address. Sellwild may hold it for review."
        : `This makes the saved ${name} draft public. Open the draft first to review its category, condition, photos, price, quantity, and shipping.`}`,
      { confirmLabel: `Publish on ${name}` },
    );
    if (!approved || (marketplace !== "sellwild" && !draft.draft_url)) return;
    action.mutate({ marketplace, kind: "publish" });
  }

  return (
    <details className="mx-3 mt-2 rounded-md border border-border px-3 py-2 text-xs" onToggle={(event) => setExpanded(event.currentTarget.open)}>
      <summary className="cursor-pointer font-medium">Marketplace drafts</summary>
      <p className="mt-2 text-muted-foreground">Save a draft, open it in Chrome to finish its category, size, condition, and shipping, then choose Publish for that marketplace. Copies title, description, price, and photos; existing drafts keep their photos. Depop combines the title and description. Etsy uses its official API after you connect it in Settings → Connections and needs its category and item details first.</p>
      {(["poshmark", "mercari", "depop", "etsy", "sellwild"] as const).map((marketplace) => {
        const draft = status.data?.[marketplace];
        const uncertain = draft && ["saving", "publishing", "unknown"].includes(draft.state);
        const published = draft?.state === "published" || draft?.state === "pending_review";
        const local = marketplace === "sellwild";
        const changed = draft?.state === "draft" && (draft.revision_id !== revisionId || (local && sellwildChanged));
        const busy = disabled || action.isPending || !revisionId || status.isPending || status.isError;
        return (
          <div key={marketplace} role="group" aria-label={`${marketplaceName(marketplace)} listing actions`} className="mt-2 flex flex-wrap items-center gap-2">
            <span className="w-20 font-medium">{marketplaceName(marketplace)}</span>
            {marketplace === "etsy" && !draft?.draft_url && <Select value={shippingProfile || null}
              onValueChange={(value) => setShippingProfile(value || "")} disabled={busy || uncertain || !profiles.data?.shipping.length}
              items={profiles.data?.shipping.map((profile) => ({ value: profile.id, label: profile.title }))}>
              <SelectTrigger size="compact" className="w-52" aria-label="Etsy shipping profile"><SelectValue placeholder="Choose shipping profile" /></SelectTrigger>
              <SelectPopup>{profiles.data?.shipping.map((profile) => <SelectItem key={profile.id} value={profile.id}>{profile.title}</SelectItem>)}</SelectPopup>
            </Select>}
            {marketplace === "etsy" && !draft?.draft_url && <Select value={processingProfile || null}
              onValueChange={(value) => setProcessingProfile(value || "")} disabled={busy || uncertain || !profiles.data?.processing.length}
              items={profiles.data?.processing.map((profile) => ({ value: profile.id, label: profile.title }))}>
              <SelectTrigger size="compact" className="w-52" aria-label="Etsy processing profile"><SelectValue placeholder="Choose processing profile" /></SelectTrigger>
              <SelectPopup>{profiles.data?.processing.map((profile) => <SelectItem key={profile.id} value={profile.id}>{profile.title}</SelectItem>)}</SelectPopup>
            </Select>}
            {local && !published && <Select value={sellwildCategory ? String(sellwildCategory) : null}
              onValueChange={(value) => setSellwildPick({ ...sellwildPick, categoryId: value ? Number(value) : undefined })} disabled={busy || uncertain || !sellwildOptions.data}
              items={sellwildOptions.data?.categories.map((option) => ({ value: String(option.id), label: option.label }))}>
              <SelectTrigger size="compact" className="w-56" aria-label="Sellwild category"><SelectValue placeholder="Choose Sellwild category" /></SelectTrigger>
              <SelectPopup>{sellwildOptions.data?.categories.map((option) => <SelectItem key={option.id} value={String(option.id)}>{option.label}</SelectItem>)}</SelectPopup>
            </Select>}
            {local && !published && <Select value={sellwildCondition ? String(sellwildCondition) : null}
              onValueChange={(value) => setSellwildPick({ ...sellwildPick, conditionId: value ? Number(value) : undefined })} disabled={busy || uncertain || !sellwildOptions.data}
              items={sellwildOptions.data?.conditions.map((option) => ({ value: String(option.id), label: option.label }))}>
              <SelectTrigger size="compact" className="w-36" aria-label="Sellwild condition"><SelectValue placeholder="Condition" /></SelectTrigger>
              <SelectPopup>{sellwildOptions.data?.conditions.map((option) => <SelectItem key={option.id} value={String(option.id)}>{option.label}</SelectItem>)}</SelectPopup>
            </Select>}
            <Button size="xs" variant="outline" disabled={busy || uncertain || published || (local && (!sellwildCategory || !sellwildCondition)) || (marketplace === "etsy" && !draft?.draft_url && (!shippingProfile || !processingProfile || (calculated && !packageReady)))}
              onClick={() => action.mutate({ marketplace, kind: "draft" })}>
              {action.isPending && action.variables.marketplace === marketplace && action.variables.kind === "draft" ? "Saving…" : local ? "Save in Anteroom" : changed ? "Save changes" : "Save draft"}
            </Button>
            {(published ? draft.listing_url : draft?.draft_url) && <a className="underline" href={published ? draft.listing_url : draft?.draft_url} target="_blank" rel="noopener noreferrer">{published ? "View listing" : "Open draft"}</a>}
            <Button size="xs" variant="outline"
              disabled={busy || draft?.state !== "draft" || changed}
              onClick={() => draft && void publish(marketplace, draft)}>
              {draft?.state === "pending_review" ? "In review" : published ? "Published" : "Publish…"}
            </Button>
            {uncertain && <Button size="xs" variant="ghost" disabled={action.isPending}
              onClick={() => action.mutate({ marketplace, kind: "check" })}>Check result</Button>}
            {uncertain && !draft.draft_url && !local && <div className="flex basis-full items-center gap-2">
              <Input className="h-7 text-xs" aria-label={`Saved ${marketplaceName(marketplace)} draft link`}
                placeholder="Or paste the saved draft link from Chrome" value={draftLinks[marketplace] || ""}
                onChange={(event) => setDraftLinks({ ...draftLinks, [marketplace]: event.target.value })} />
              <Button size="xs" variant="outline" disabled={action.isPending || !draftLinks[marketplace]?.trim()}
                onClick={() => action.mutate({ marketplace, kind: "check", draftUrl: draftLinks[marketplace]?.trim() })}>Check link</Button>
            </div>}
            {draft?.error && <p className="basis-full text-destructive">{draft.error}</p>}
            {draft && !draft.error && (uncertain || changed || published || (!local && draft.state === "draft")) && <p role="status" className="basis-full text-muted-foreground">
              {uncertain ? "Waiting for a confirmed result. Check before trying again."
                : changed ? "The listing changed. Save it again before publishing."
                : draft.state === "pending_review" ? "Submitted to Sellwild for review."
                : published ? "Published."
                : draft.state === "draft" && !local ? "Draft saved. Open it to finish the marketplace fields and review before publishing."
                : null}
            </p>}
            {marketplace === "etsy" && !draft?.draft_url && calculated && <div className="flex basis-full flex-wrap items-end gap-2">
              {([['weight', 'Packed weight (oz)'], ['length', 'Length (in)'], ['width', 'Width (in)'], ['height', 'Height (in)']] as const).map(([key, label]) =>
                <label key={key} className="grid gap-1">{label}<Input className="h-7 w-28 text-xs" type="number" min="0.01" step="any"
                  disabled={busy || uncertain} value={packed[key]} onChange={(event) => setPacked({ ...packed, [key]: event.target.value })} /></label>)}
              <p className="basis-full text-muted-foreground">Etsy calculated postage needs the packed item's weight and dimensions.</p>
            </div>}
            {marketplace === "etsy" && !draft?.draft_url && profiles.error && <p className="basis-full text-destructive">{profiles.error.message}</p>}
            {local && !published && <p className="basis-full text-muted-foreground">Sellwild has no drafts, so this one is saved only in Anteroom. Publish creates the Sellwild listing after confirmation, without opening a tab. It needs a package weight and size, and your ship-from address on Sellwild.</p>}
          </div>
        );
      })}
      {(status.error || action.error) && <p role="alert" className="mt-2 text-destructive">{(action.error || status.error)?.message}</p>}
    </details>
  );
}
