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
import { SettingsNote, SettingsRow, SettingsSection } from "./SettingsLayout";

export function ListingTemplatesSettings({onOpenListing}:{onOpenListing:(id:string)=>void}) {
  const client=useQueryClient();
  const query=useQuery({queryKey:["listing-templates"],queryFn:api.templates.list,retry:false});
  const [draft,setDraft]=useState<ListingTemplate|null>(null);
  const [renaming,setRenaming]=useState<ListingTemplate|null>(null);
  const remove=useMutation({mutationFn:(row:ListingTemplate)=>api.templates.remove(row.id,row.revision),retry:false,
    onSuccess:()=>void client.invalidateQueries({queryKey:["listing-templates"]})});
  return <SettingsSection id="listing-templates" title="Saved templates">
    <SettingsRow title="Reusable listing fields" description="Save selected fields from an inventory item’s Templates action. To change a template’s fields, save from an item again and choose that template. Templates never include SKU, private notes, photos or marketplace links."
      control={<Button size="sm" variant="outline" disabled={query.isFetching} onClick={()=>void query.refetch()}>Reload templates</Button>} />
    {query.isPending?<SettingsNote>Loading templates…</SettingsNote>:null}
    {query.data&&!query.data.templates.length?<SettingsNote>No saved templates yet.</SettingsNote>:null}
    {query.data?.templates.map(row=><SettingsRow key={row.id} title={row.name} description={templateFieldGroups(row.fields).map(group=>group.name).join(", ")}
      control={<div className="flex flex-wrap gap-2"><Button size="sm" onClick={()=>setDraft(row)}>Create draft</Button><Button size="sm" variant="outline" onClick={()=>setRenaming(row)}>Rename</Button><Button size="sm" variant="ghost" disabled={remove.isPending} onClick={async()=>{if(await confirmDialog(`Delete template “${row.name}”? Existing items stay as they are.`,{variant:"destructive",confirmLabel:"Delete template"}))remove.mutate(row);}}>Delete</Button></div>} />)}
    {query.error||remove.error?<SettingsNote tone="error">{remove.error?.message||query.error?.message}</SettingsNote>:null}
    {draft?<TemplateDraftDialog template={draft} onClose={()=>setDraft(null)} onCreated={id=>{setDraft(null);onOpenListing(id);}}/>:null}
    {renaming?<RenameTemplateDialog template={renaming} onClose={()=>setRenaming(null)}/>:null}
  </SettingsSection>;
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
    footer={<>{save.error?<Button variant="ghost" disabled={busy} onClick={()=>reload.mutate()}>Reload template</Button>:null}{save.error?<Button variant="ghost" disabled={busy} onClick={()=>check.mutate()}>Check draft result</Button>:null}<Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button><Button disabled={busy||!valid} onClick={()=>save.mutate()}>{save.isPending?"Creating…":"Create local draft"}</Button></>}>
    <label className="block space-y-1">Inventory name<Input aria-label="Inventory name" value={title} maxLength={200} disabled={busy} onChange={event=>setTitle(event.target.value)}/></label>
    <label className="block space-y-1">New SKU (optional)<Input aria-label="New SKU" value={sku} maxLength={120} disabled={busy} onChange={event=>setSku(event.target.value)}/></label>
    <label className="block space-y-1">Quantity<Input aria-label="Quantity" inputMode="numeric" value={quantity} disabled={busy} onChange={event=>setQuantity(event.target.value)}/></label>
    <PageNote>The draft has no photos, marketplace links, sales history or sourcing-box assignment. Saved cost and labels are used only when they are part of this template.</PageNote>
    <TemplateFields fields={template.fields}/>
    {save.error||check.error||reload.error?<PageError message={reload.error?.message||check.error?.message||save.error?.message||"Could not create draft"}/>:null}
  </PriceDialog>;
}
