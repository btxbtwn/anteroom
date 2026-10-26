import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { PageError, PageNote, PageSection } from "../WorkspacePage";
import { RemoteList, RowCards, fileBase64 } from "./shared";
import { ActionForm } from "./ActionForm";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { useFormDraft } from "./FormDrafts";
import { InventoryImport } from "../InventoryImport";

export function ListingsPanel({ accountKey, onOpenListing }: { accountKey: string; onOpenListing: (id: string) => void }) {
  const [task, setTask] = useFormDraft("listings:task", () => "Import from eBay");
  const [id, setId] = useFormDraft("listings:lookup", () => "");
  const [sku, setSku] = useFormDraft("listings:sku", () => "");
  const [offerId, setOfferId] = useFormDraft("listings:offer", () => "");
  const lookup = useMutation({ mutationFn: api.ebay.listing, retry: false });
  return <WorkspaceTabs label="Listing tools" items={["Import from eBay", "Inventory", "Edit live listing"]} value={task} onChange={setTask}>
    {task === "Import from eBay" ? <InventoryImport key={accountKey} marketplace="ebay" accountKey={accountKey} onOpenListing={onOpenListing} /> : null}
    {task === "Inventory" ?
    <PageSection title="eBay inventory and offers">
      <RemoteList section="inventory" onSelect={(row) => setSku(row.id)} selected={sku} />
      {sku ? <RemoteList key={sku} section="offers" params={{ target: sku }} onSelect={(row) => setOfferId(row.id)} selected={offerId} /> : null}
      <label className="max-w-xs space-y-1 text-xs">Offer ID<Input value={offerId} onChange={(event) => setOfferId(event.target.value)} /></label>
      {offerId ? <ActionForm key={`inventory-${offerId}`} title="Update eBay inventory price and quantity" kind="inventory_revision" context={{ offer_id: offerId }}
        fields={[{ name: "price", label: "Price (USD)", type: "number", min: "0.01" }, { name: "quantity", label: "Available quantity", type: "number", initial: "1", min: "1" }]}
        build={(values) => ({ ...values, quantity: Number(values.quantity) })} /> : null}
      <PageNote>To list an item on eBay, open it from Inventory and use List… under Marketplaces, the same as every other marketplace.</PageNote>
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
