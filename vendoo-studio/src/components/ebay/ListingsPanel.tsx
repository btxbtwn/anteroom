import { useId, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { PageError, PageNote, PageSection } from "../WorkspacePage";
import { ImageUploader, RemoteList, RowCards, fileBase64 } from "./shared";
import { ActionForm } from "./ActionForm";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { useFormDraft } from "./FormDrafts";

export function ListingsPanel() {
  const aspectId = useId();
  const [task, setTask] = useFormDraft("listings:task", () => "Create draft");
  const [search, setSearch] = useFormDraft("listings:search", () => "");
  const [keywords, setKeywords] = useFormDraft("listings:keywords", () => "");
  const [category, setCategory] = useFormDraft("listings:category", () => "");
  const [aspects, setAspects] = useFormDraft<Record<string, string>>("listings:aspects", () => ({}));
  const [images, setImages] = useFormDraft<string[]>("listings:images", () => []);
  const [id, setId] = useFormDraft("listings:lookup", () => "");
  const [sku, setSku] = useFormDraft("listings:sku", () => "");
  const [offerId, setOfferId] = useFormDraft("listings:offer", () => "");
  const specifics = useQuery({ queryKey: ["ebay", "aspects", category], queryFn: () => api.ebay.read("aspects", { target: category }), enabled: /^\d+$/.test(category), retry: false });
  const lookup = useMutation({ mutationFn: api.ebay.listing, retry: false });
  const policies = useQuery({ queryKey: ["ebay", "policies"], queryFn: () => api.ebay.read("policies"), retry: false });
  const locations = useQuery({ queryKey: ["ebay", "locations"], queryFn: () => api.ebay.read("locations"), retry: false });
  const policyOptions = (kind: string) => policies.data?.rows.filter((row) => row.status === kind).map((row) => ({ value: row.id, label: row.title })) ?? [];
  return <WorkspaceTabs label="Listing tools" items={["Create draft", "Inventory", "Edit live listing"]} value={task} onChange={setTask}>
    {task === "Create draft" ? <>
    <PageSection title="Category suggestions" description="Use eBay's own category and item-specific requirements.">
      <form className="ebay-search-form" onSubmit={(event) => { event.preventDefault(); setKeywords(search); }}>
        <label className="min-w-48 flex-1 space-y-1 text-xs">Describe the item<Input value={search} required onChange={(event) => setSearch(event.target.value)} /></label>
        <Button size="sm" variant="outline" type="submit">Find categories</Button>
      </form>
      {keywords ? <RemoteList key={keywords} section="categories" params={{ q: keywords }} onSelect={(row) => { setCategory(row.id); setAspects({}); }} selected={category} /> : null}
    </PageSection>
    <PageSection title="Create an unpublished offer" description="Choose a category above, upload images, and enter the item details. Saving creates an unpublished eBay offer; publishing requires another explicit review.">
      <label className="max-w-xs space-y-1 text-xs">Category ID<Input value={category} onChange={(event) => { setCategory(event.target.value); setAspects({}); }} /></label>
      <ImageUploader onResult={(result) => { if (result.imageUrl) setImages((old) => [...old, result.imageUrl!]); }} />
      <details className="space-y-3 text-xs"><summary className="cursor-pointer">Business policies and inventory locations</summary>
        <RemoteList section="policies" /><RemoteList section="locations" />
      </details>
      {images.length ? <p className="text-xs text-muted-foreground">{images.length} uploaded images ready for this draft.</p> : null}
      {specifics.error ? <PageError message={specifics.error.message} /> : null}
      <div className="ebay-form-fields">{specifics.data?.rows.filter((row) => row.status === "Required").map((row, index) => <div key={row.id} className="ebay-field">
        <label htmlFor={`${aspectId}-${index}`} className="ebay-field-label">{row.title} (required)</label>
        <Input id={`${aspectId}-${index}`} nativeInput aria-describedby={row.subtitle ? `${aspectId}-${index}-help` : undefined} value={aspects[row.id] || ""} onChange={(event) => setAspects({ ...aspects, [row.id]: event.target.value })} />
        {row.subtitle ? <p id={`${aspectId}-${index}-help`} className="ebay-field-help">Examples: {row.subtitle}</p> : null}
      </div>)}</div>
      <ActionForm title="Save unpublished offer" kind="draft"
        disabled={!category || !specifics.data || specifics.isError} context={{ category_id: category, image_urls: images, aspects: Object.fromEntries(Object.entries(aspects).map(([k, v]) => [k, v.trim() ? [v.trim()] : []])) }}
        fields={[{ name: "sku", label: "New SKU", help: "A unique label for this item in eBay inventory.", group: "Item details" }, { name: "title", label: "Listing title", group: "Item details", maxLength: 80 },
                 { name: "description", label: "Description", type: "textarea", group: "Item details" }, { name: "price", label: "Price (USD)", type: "number", min: "0.01", group: "Price and inventory" },
                 { name: "quantity", label: "Quantity", type: "number", initial: "1", min: "1", group: "Price and inventory" },
                 { name: "weight_oz", label: "Packaged weight (ounces)", type: "number", initial: "16", min: "0.1", group: "Shipping and returns" },
                 { name: "condition", label: "Condition", options: ["NEW", "NEW_OTHER", "NEW_WITH_DEFECTS", "PRE_OWNED_EXCELLENT", "USED_EXCELLENT", "PRE_OWNED_FAIR", "USED_VERY_GOOD", "USED_GOOD", "USED_ACCEPTABLE"], group: "Item details" },
                 { name: "fulfillment_policy_id", label: "Shipping policy", options: policyOptions("fulfillment"), group: "Shipping and returns" }, { name: "payment_policy_id", label: "Payment policy", options: policyOptions("payment"), group: "Shipping and returns" },
                 { name: "return_policy_id", label: "Return policy", options: policyOptions("return"), group: "Shipping and returns" }, { name: "merchant_location_key", label: "Inventory location", group: "Shipping and returns", options: locations.data?.rows.filter((row) => row.status === "ENABLED").map((row) => ({ value: row.id, label: row.title || row.id })) ?? [] },
                 { name: "image_urls", label: "Additional image URLs", help: "One HTTPS URL per line. Photos uploaded above are included automatically.", type: "textarea", optional: true, group: "Photos" }]}
        build={(values) => ({ ...values, quantity: Number(values.quantity), image_urls: [...new Set([...images, ...values.image_urls.split(/\s+/).filter(Boolean)])] })}
        onResult={(result) => { if (result.receiptId) setOfferId(result.receiptId); }} />
    </PageSection>
    </> : null}
    {task === "Inventory" ?
    <PageSection title="eBay inventory and offers">
      <RemoteList section="inventory" onSelect={(row) => setSku(row.id)} selected={sku} />
      {sku ? <RemoteList key={sku} section="offers" params={{ target: sku }} onSelect={(row) => setOfferId(row.id)} selected={offerId} /> : null}
      <label className="max-w-xs space-y-1 text-xs">Unpublished offer ID<Input value={offerId} onChange={(event) => setOfferId(event.target.value)} /></label>
      {offerId ? <ActionForm key={offerId} title="Publish on eBay" kind="publish_offer" context={{ offer_id: offerId, marketplace: "ebay" }} /> : null}
      {offerId ? <ActionForm key={`inventory-${offerId}`} title="Update eBay inventory price and quantity" kind="inventory_revision" context={{ offer_id: offerId }}
        fields={[{ name: "price", label: "Price (USD)", type: "number", min: "0.01" }, { name: "quantity", label: "Available quantity", type: "number", initial: "1", min: "1" }]}
        build={(values) => ({ ...values, quantity: Number(values.quantity) })} /> : null}
      <PageNote>Publishing also requires complete eBay business policies, shipping details, and a valid inventory location. eBay rejects incomplete offers.</PageNote>
    </PageSection>
    : null}
    {task === "Edit live listing" ?
    <PageSection title="Edit an existing eBay listing" description="For fixed-price listings created through Vendoo or eBay's Trading listing system. Changes affect the live eBay listing; sync Vendoo afterward to reconcile its copy.">
      <form className="ebay-search-form" onSubmit={(event) => { event.preventDefault(); lookup.mutate(id); }}>
        <label className="min-w-48 flex-1 space-y-1 text-xs">eBay item ID<Input required value={id} onChange={(event) => { setId(event.target.value); lookup.reset(); }} /></label>
        <Button size="sm" variant="outline" disabled={lookup.isPending} type="submit">Read live listing</Button>
      </form>
      {lookup.error ? <PageError message={lookup.error.message} /> : null}
      {lookup.data ? <ActionForm key={`${lookup.data.id}-${lookup.submittedAt}`} draftId={`${lookup.data.id}-${lookup.submittedAt}`} title="Update live listing" kind="revise" context={{ listing_id: lookup.data.id }}
        fields={[{ name: "title", label: "Title", initial: lookup.data.title }, { name: "description", label: "Description", type: "textarea", initial: lookup.data.description },
                 { name: "price", label: "Price (USD)", type: "number", initial: lookup.data.price || "", min: "0.01" },
                 { name: "quantity", label: "Total quantity including units already sold", type: "number", initial: lookup.data.quantity, min: "1" }]}
        build={(values) => ({ ...values, quantity: Number(values.quantity) })} /> : null}
    </PageSection>
    : null}
  </WorkspaceTabs>;
}

export function ResearchPanel() {
  const [text, setText] = useState("");
  const [query, setQuery] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const image = useMutation({ mutationFn: async () => {
    if (!file) throw new Error("Choose an image.");
    if (file.size > 8000000) throw new Error("Choose an image smaller than 8 MB.");
    return api.ebay.imageSearch(await fileBase64(file), file.type);
  }, retry: false });
  return <>
    <PageSection title="Active comparable listings" description="These are current asking prices, not sold prices. Sold-comps access is restricted by eBay and is not enabled here.">
      <form className="ebay-search-form" onSubmit={(event) => { event.preventDefault(); setQuery(text); }}>
        <label className="min-w-48 flex-1 space-y-1 text-xs">Search eBay<Input value={text} required onChange={(event) => setText(event.target.value)} /></label>
        <Button size="sm" type="submit" variant="outline">Search active listings</Button>
      </form>
      {query ? <RemoteList key={query} section="research" params={{ q: query }} /> : null}
    </PageSection>
    <PageSection title="Search by image" description="Send a JPEG or PNG to eBay to find visually similar active listings.">
      <div className="ebay-upload"><label>Item image<input type="file" accept="image/jpeg,image/png" onChange={(event) => { setFile(event.target.files?.[0] || null); image.reset(); }} /></label></div>
      <Button size="sm" variant="outline" disabled={!file || image.isPending} onClick={() => image.mutate()}>Search eBay by image</Button>
      {image.error ? <PageError message={image.error.message} /> : null}
      {image.data ? <RowCards rows={image.data.rows} /> : null}
    </PageSection>
  </>;
}
