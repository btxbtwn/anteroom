import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { templateApproval, templateFieldGroups, templateFieldName, templateValue, type ListingTemplate, type TemplateApproval, type TemplateReview, type TemplateSave, type TemplateSource } from "../api/listingTemplates";
import { PriceDialog } from "./PriceDropParts";
import { PageError, PageNote } from "./WorkspacePage";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Input } from "./ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";

export function ListingTemplatesDialog({ conversationId, onClose }: { conversationId:string; onClose:()=>void }) {
  const [action,setAction]=useState<"save"|"apply"|null>(null);
  if(action==="save")return <SaveTemplateDialog conversationId={conversationId} onClose={onClose} />;
  if(action==="apply")return <ApplyTemplateDialog conversationId={conversationId} onClose={onClose} />;
  return <PriceDialog title="Listing templates" description="Save selected reusable fields or review a saved template against this item. All changes stay in Anteroom." onClose={onClose} footer={<Button variant="outline" onClick={onClose}>Cancel</Button>}>
    <div className="flex flex-wrap gap-2"><Button onClick={()=>setAction("save")}>Save as template</Button><Button variant="outline" onClick={()=>setAction("apply")}>Apply template</Button></div>
    <PageNote>Manage saved templates and create new drafts from Settings → Listings → Saved templates.</PageNote>
  </PriceDialog>;
}

function SaveTemplateDialog({conversationId,onClose}:{conversationId:string;onClose:()=>void}) {
  const source=useQuery({queryKey:["template-source",conversationId],queryFn:()=>api.templates.source(conversationId),retry:false,staleTime:Infinity,gcTime:0,refetchOnWindowFocus:false});
  const templates=useQuery({queryKey:["listing-templates"],queryFn:api.templates.list,retry:false});
  return source.data ? <SaveTemplateForm source={source.data} templates={templates.data?.templates || []} onReload={async()=>{const [,result]=await Promise.all([source.refetch(),templates.refetch()]);return result.data?.templates;}} reloading={source.isFetching||templates.isFetching} reloadError={source.error?.message} onClose={onClose} />
    : <PriceDialog title="Save listing template" description="Choose the reusable fields to keep." onClose={onClose} footer={<><Button variant="outline" onClick={onClose}>Cancel</Button><Button disabled={source.isFetching} onClick={()=>void source.refetch()}>Reload item</Button></>}>
      {source.error ? <PageError message={source.error.message} /> : <PageNote>Loading saved fields…</PageNote>}
    </PriceDialog>;
}
function SaveTemplateForm({source,templates,onReload,reloading,reloadError,onClose}:{source:TemplateSource;templates:ListingTemplate[];onReload:()=>Promise<ListingTemplate[]|undefined>;reloading:boolean;reloadError?:string;onClose:()=>void}) {
  const client=useQueryClient();
  const [selected,setSelected]=useState<ListingTemplate|null>(null);
  const [name,setName]=useState("");
  const [checked,setChecked]=useState<Set<string>>(new Set());
  const groups=templateFieldGroups(source.fields);
  const fields=groups.filter(group=>checked.has(group.key)).flatMap(group=>group.fields);
  const save=useMutation({mutationFn:()=> {
    const body:TemplateSave={name:name.trim(),selection:{source_id:source.source_id,expected_basis:source.expected_basis,fields},...(selected?{expected_revision:selected.revision}:{})};
    return api.templates.save(body,selected?.id);
  },retry:false,onSuccess:()=>{void client.invalidateQueries({queryKey:["listing-templates"]});onClose();}});
  const busy=reloading||save.isPending;
  return <PriceDialog title="Save listing template" description="Check only reusable fields. A selected marketplace form saves all of its shown answers. SKU, private notes, photos and marketplace links are excluded." busy={busy} onClose={onClose} className="max-w-3xl"
    footer={<><Button variant="ghost" disabled={busy} onClick={async()=>{const latest=await onReload();if(selected&&latest)setSelected(latest.find(row=>row.id===selected.id)||null);}}>Reload item and templates</Button><Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button><Button disabled={busy||!name.trim()||!fields.length} onClick={()=>save.mutate()}>{save.isPending?"Saving…":selected?"Replace saved fields":"Save template"}</Button></>}>
    <Select value={selected?.id || "new"} disabled={busy} onValueChange={value=>{const template=templates.find(row=>row.id===value)||null;setSelected(template);setName(template?.name||"");setChecked(new Set(template?templateFieldGroups(template.fields).map(group=>group.key):[]));}}>
      <SelectTrigger aria-label="Save template as"><SelectValue>{selected?.name||"New template"}</SelectValue></SelectTrigger><SelectPopup><SelectItem value="new">New template</SelectItem>{templates.map(row=><SelectItem key={row.id} value={row.id}>{row.name}</SelectItem>)}</SelectPopup>
    </Select>
    <label className="block space-y-1">Template name<Input aria-label="Template name" maxLength={120} value={name} disabled={busy} onChange={event=>setName(event.target.value)} /></label>
    {selected?<PageNote>Replacing saves only the checked fields below; other fields in this template are removed.</PageNote>:null}
    {groups.length?<ul className="space-y-3">{groups.map(group=><li key={group.key} className="min-w-0 rounded-lg border p-3"><label className="flex gap-2 font-medium"><Checkbox disabled={busy} checked={checked.has(group.key)} onCheckedChange={value=>setChecked(previous=>{const next=new Set(previous);if(value)next.add(group.key);else next.delete(group.key);return next;})} />{group.name}</label><pre className="mt-2 whitespace-pre-wrap break-words font-sans text-xs">{templateValue(group.value)}</pre></li>)}</ul>:<PageNote>This item has no reusable saved fields yet. Save listing fields first.</PageNote>}
    {save.error||reloadError?<PageError message={save.error?.message||reloadError||"Could not save template"}/>:null}
  </PriceDialog>;
}

function ApplyTemplateDialog({conversationId,onClose}:{conversationId:string;onClose:()=>void}) {
  const client=useQueryClient();
  const query=useQuery({queryKey:["listing-templates"],queryFn:api.templates.list,retry:false});
  const [selected,setSelected]=useState<ListingTemplate|null>(null);
  const [mode,setMode]=useState<"fill_blank"|"replace">("fill_blank");
  const [review,setReview]=useState<{id:string;body:TemplateApproval;data:TemplateReview}|null>(null);
  const preview=useMutation({mutationFn:async()=>{
    const body={conversation_id:conversationId,expected_template_revision:selected!.revision,mode};
    const data=await api.templates.preview(selected!.id,body);return{id:selected!.id,body:templateApproval(body,data),data};
  },retry:false,onSuccess:setReview});
  const apply=useMutation({mutationFn:()=>api.templates.apply(review!.id,review!.body),retry:false,onSuccess:()=>{
    for(const key of ["conversations","analytics","sales-ledger","suggestions"])void client.invalidateQueries({queryKey:[key]});
    for(const key of ["listing","conversation","revisions"])void client.invalidateQueries({queryKey:[key,conversationId]});onClose();
  }});
  const busy=preview.isPending||apply.isPending||query.isFetching;
  const reload=async()=>{const result=await query.refetch();if(result.data){setSelected(result.data.templates.find(row=>row.id===selected?.id)||null);setReview(null);apply.reset();preview.reset();}};
  return <PriceDialog title={review?"Review template changes":"Apply listing template"} description="Fill blank fields or replace selected fields, then review the listing editor’s full before/after changes. Saving creates a local revision; marketplace listings stay as they are." busy={busy} onClose={onClose} className="max-w-3xl"
    footer={<><Button variant="ghost" disabled={busy} onClick={()=>void reload()}>Reload templates</Button>{review?<Button variant="outline" disabled={busy} onClick={()=>{setReview(null);apply.reset();}}>Back</Button>:<Button variant="outline" disabled={busy} onClick={onClose}>Cancel</Button>}<Button disabled={busy||!selected||Boolean(review&&!review.data.changes.length)} onClick={review?()=>apply.mutate():()=>preview.mutate()}>{busy?"Working…":review?"Save reviewed changes":"Review changes"}</Button></>}>
    {review?<div className="space-y-3">{review.data.changes.length?review.data.changes.map(change=><div key={change.field} className="min-w-0 rounded-lg border p-3"><p className="font-medium">{templateFieldName(change.field)}</p><pre className="whitespace-pre-wrap break-words font-sans text-xs">{templateValue(change.old)} → {templateValue(change.new)}</pre></div>):<PageNote>The item already has these values.</PageNote>}</div>:<>
      <Select value={selected?.id||""} disabled={busy} onValueChange={value=>setSelected(query.data?.templates.find(row=>row.id===value)||null)}><SelectTrigger aria-label="Template to apply"><SelectValue>{selected?.name||"Choose template"}</SelectValue></SelectTrigger><SelectPopup>{query.data?.templates.map(row=><SelectItem key={row.id} value={row.id}>{row.name}</SelectItem>)}</SelectPopup></Select>
      <Select value={mode} disabled={busy} onValueChange={value=>{if(value==="replace"||value==="fill_blank")setMode(value);}}><SelectTrigger aria-label="Template application mode"><SelectValue>{mode==="fill_blank"?"Fill blanks only":"Replace selected fields"}</SelectValue></SelectTrigger><SelectPopup><SelectItem value="fill_blank">Fill blanks only</SelectItem><SelectItem value="replace">Replace selected fields</SelectItem></SelectPopup></Select>
      <PageNote>Fill blanks keeps known values, including zero. Replace overwrites the selected fields; a selected form replaces its saved answer object. Unselected fields stay as they are. The editor may also align related size and title fields, which appear in the review.</PageNote>
      {query.data&&!query.data.templates.length?<PageNote>No saved templates yet. Save selected fields from an inventory item first.</PageNote>:null}
      {selected?<TemplateFields fields={selected.fields}/>:null}
    </>}
    {query.error||preview.error||apply.error?<PageError message={apply.error?.message||preview.error?.message||query.error?.message||"Could not apply template"}/>:null}
  </PriceDialog>;
}
export function TemplateFields({fields}:{fields:Record<string,unknown>}) {
  return <div className="space-y-3">{templateFieldGroups(fields).map(group=><div key={group.key} className="min-w-0 rounded-lg border p-3"><p className="font-medium">{group.name}</p><pre className="whitespace-pre-wrap break-words font-sans text-xs">{templateValue(group.value)}</pre></div>)}</div>;
}
