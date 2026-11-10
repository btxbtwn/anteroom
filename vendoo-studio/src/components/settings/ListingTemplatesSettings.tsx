import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { templateFieldGroups, type ListingTemplate } from "../../api/listingTemplates";
import { confirmDialog } from "../../ui/confirmDialog";
import { TemplateFields } from "../ListingTemplatesDialog";
import { PriceDialog } from "../PriceDropParts";
import { PageError, PageNote } from "../WorkspacePage";
import { Button } from "../ui/button";
import { Input } from "../ui/input";
import { Skeleton } from "../ui/skeleton";
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

export function ListingTemplatesSettings({onOpenListing}:{onOpenListing:(id:string)=>void}) {
  const client=useQueryClient();
  const query=useQuery({queryKey:["listing-templates"],queryFn:api.templates.list,retry:false});
  const [draft,setDraft]=useState<ListingTemplate|null>(null);
  const [renaming,setRenaming]=useState<ListingTemplate|null>(null);
  const remove=useMutation({mutationFn:(row:ListingTemplate)=>api.templates.remove(row.id,row.revision),retry:false,
    onSuccess:()=>void client.invalidateQueries({queryKey:["listing-templates"]})});
  return <SettingsSection id="listing-templates" title="Saved templates">
    <SettingsRow title="Reusable listing fields" description="Save fields from an item’s Templates action, never including SKU, private notes, photos or marketplace links." />
    {query.isPending?<TemplateRowSkeleton/>:null}
    {query.data&&!query.data.templates.length?<div className="px-3 py-3 sm:px-4"><SettingsNote>No saved templates yet.</SettingsNote></div>:null}
    {query.data?.templates.map(row=><SettingsRow key={row.id} title={row.name} description={templateFieldGroups(row.fields).map(group=>group.name).join(", ")}
      control={<div className="flex flex-wrap gap-2"><Button size="sm" onClick={()=>setDraft(row)}>Create draft</Button><Button size="sm" variant="outline" onClick={()=>setRenaming(row)}>Rename</Button><Button size="sm" variant="ghost" disabled={remove.isPending} onClick={async()=>{if(await confirmDialog(`Delete template “${row.name}”? Existing items stay as they are.`,{variant:"destructive",confirmLabel:"Delete template"}))remove.mutate(row);}}>Delete</Button></div>} />)}
    {query.error&&!query.data?<div className="flex flex-wrap items-center gap-3 px-3 py-3 sm:px-4"><SettingsNote tone="error">{query.error.message||"Could not load your templates."}</SettingsNote>
      <Button size="xs" variant="outline" disabled={query.isFetching} onClick={()=>void query.refetch()}>Try again</Button></div>:null}
    {remove.error?<div className="px-3 py-3 sm:px-4"><SettingsNote tone="error">{remove.error.message}</SettingsNote></div>:null}
    {draft?<TemplateDraftDialog template={draft} onClose={()=>setDraft(null)} onCreated={id=>{setDraft(null);onOpenListing(id);}}/>:null}
    {renaming?<RenameTemplateDialog template={renaming} onClose={()=>setRenaming(null)}/>:null}
  </SettingsSection>;
}
/** Stands in for the template rows on the first load, in the same shape. */
function TemplateRowSkeleton() {
  return <div role="status" aria-label="Loading templates" aria-busy="true" className="flex items-center gap-4 px-3 py-3 sm:px-4">
    <div className="min-w-0 flex-1 space-y-2"><Skeleton className="h-4 w-36"/><Skeleton className="h-3 w-56 max-w-full"/></div>
    <Skeleton shape="card" className="h-8 w-24"/>
  </div>;
}
function RenameTemplateDialog({template,onClose}:{template:ListingTemplate;onClose:()=>void}) {
  const client=useQueryClient();
  const [name,setName]=useState(template.name);
  const save=useMutation({mutationFn:()=>api.templates.save({name:name.trim(),expected_revision:template.revision},template.id),retry:false,
    onSuccess:()=>{void client.invalidateQueries({queryKey:["listing-templates"]});onClose();}});
  return <PriceDialog title="Rename template" description="Existing drafts keep their saved values." busy={save.isPending} onClose={onClose}
    footer={<><Button variant="outline" disabled={save.isPending} onClick={onClose}>Cancel</Button><Button disabled={save.isPending||!name.trim()} onClick={()=>save.mutate()}>Save name</Button></>}>
    <label className="block space-y-1">Template name<Input aria-label="Template name" maxLength={120} value={name} disabled={save.isPending} onChange={event=>setName(event.target.value)}/></label>
    {save.error?<PageError message={save.error.message}/>:null}
  </PriceDialog>;
}
export function TemplateDraftDialog({template:initialTemplate,onClose,onCreated}:{template:ListingTemplate;onClose:()=>void;onCreated:(id:string)=>void}) {
  const client=useQueryClient();
  const [template,setTemplate]=useState(initialTemplate);
  const [requestId]=useState(()=>crypto.randomUUID().replace(/-/g,""));
  const [title,setTitle]=useState(`${template.name} draft`.slice(0,200));
  const [sku,setSku]=useState("");
  const [quantity,setQuantity]=useState(()=>typeof template.fields.quantity==="number"&&Number.isInteger(template.fields.quantity)&&template.fields.quantity>0?String(template.fields.quantity):"1");
  const created=(result:{conversation_id:string})=>{void client.invalidateQueries({queryKey:["conversations"]});void client.invalidateQueries({queryKey:["analytics"]});onCreated(result.conversation_id);};
  const save=useMutation({mutationFn:()=>api.templates.draft(template.id,{request_id:requestId,expected_revision:template.revision,title:title.trim(),sku:sku.trim(),quantity:Number(quantity)}),retry:false,onSuccess:created});
  const check=useMutation({mutationFn:()=>api.templates.draftResult(template.id,requestId),retry:false,onSuccess:created});
  const reload=useMutation({mutationFn:async()=>{const result=await api.templates.list();const latest=result.templates.find(row=>row.id===template.id);if(!latest)throw new Error("Template no longer exists.");return latest;},retry:false,onSuccess:setTemplate});
  const busy=save.isPending||check.isPending||reload.isPending;
  const valid=title.trim()&&/^\d+$/.test(quantity)&&Number(quantity)>=1&&Number(quantity)<=1000000;
  return <PriceDialog title={`Draft from ${template.name}`} description="Review the saved fields, set the new item’s SKU and quantity, then add its photos and finish its details. Creating a draft sends nothing to a marketplace." className="max-w-3xl" busy={busy} onClose={onClose}
    footer={<>{save.error?<Button variant="ghost" disabled={busy} onClick={()=>reload.mutate()}>Reload template</Button>:null}{save.error?<Button variant="ghost" disabled={busy} onClick={()=>check.mutate()}>Check draft result</Button>:null}<Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button><Button disabled={busy||!valid} onClick={()=>save.mutate()}>{save.isPending?"Creating…":"Create draft"}</Button></>}>
    <label className="block space-y-1">Inventory name<Input aria-label="Inventory name" value={title} maxLength={200} disabled={busy} onChange={event=>setTitle(event.target.value)}/></label>
    <label className="block space-y-1">New SKU (optional)<Input aria-label="New SKU" value={sku} maxLength={120} disabled={busy} onChange={event=>setSku(event.target.value)}/></label>
    <label className="block space-y-1">Quantity<Input aria-label="Quantity" inputMode="numeric" value={quantity} disabled={busy} onChange={event=>setQuantity(event.target.value)}/></label>
    <PageNote>The draft has no photos, marketplace links, sales history or sourcing-box assignment. Saved cost and labels are used only when they are part of this template.</PageNote>
    <TemplateFields fields={template.fields}/>
    {save.error||check.error||reload.error?<PageError message={reload.error?.message||check.error?.message||save.error?.message||"Could not create draft"}/>:null}
  </PriceDialog>;
}
