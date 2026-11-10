import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../../api/client";
import type { ListingTemplate } from "../../api/listingTemplates";
import { ListingTemplatesSettings, TemplateDraftDialog } from "./ListingTemplatesSettings";
const {useQuery,useMutation}=vi.hoisted(()=>({useQuery:vi.fn(),useMutation:vi.fn()}));
vi.mock("@tanstack/react-query",()=>({useQuery,useMutation,useQueryClient:()=>({invalidateQueries:vi.fn()})}));
const template:ListingTemplate={id:"coats",name:"Coats",revision:3,fields:{price:40,quantity:2,labels:["Winter"]},updated_at:"2026-10-26T00:00:00Z"};
beforeEach(()=>{useQuery.mockReset();useMutation.mockReset();useQuery.mockReturnValue({data:{templates:[template]},isPending:false,isFetching:false});useMutation.mockReturnValue({mutate:vi.fn(),isPending:false});});
describe("saved templates",()=>{
  it("offers create, rename and delete without changing items on load",()=>{
    const html=renderToStaticMarkup(<ListingTemplatesSettings onOpenListing={vi.fn()}/>);
    expect(html).toContain("Saved templates");expect(html).toContain("Coats");expect(html).toContain("Create draft");expect(html).toContain("Rename");expect(html).toContain("Delete");
    expect(useMutation.mock.calls[0][0].retry).toBe(false);
  });
  it("loads with a skeleton row, shows an empty row, and needs no reload button",()=>{
    useQuery.mockReturnValue({data:undefined,isPending:true,isFetching:true});
    const loading=renderToStaticMarkup(<ListingTemplatesSettings onOpenListing={vi.fn()}/>);
    expect(loading).toContain('aria-label="Loading templates"');
    expect(loading).not.toContain("Loading templates…");
    expect(loading).not.toContain("Reload templates");
    useQuery.mockReturnValue({data:{templates:[]},isPending:false,isFetching:false});
    const empty=renderToStaticMarkup(<ListingTemplatesSettings onOpenListing={vi.fn()}/>);
    expect(empty).toContain("No saved templates yet.");
    expect(empty).not.toContain("Reload templates");
  });
  it("offers Try again only when the templates could not load",()=>{
    useQuery.mockReturnValue({data:undefined,isPending:false,isFetching:false,error:new Error("Offline")});
    const html=renderToStaticMarkup(<ListingTemplatesSettings onOpenListing={vi.fn()}/>);
    expect(html).toContain("Offline");expect(html).toContain("Try again");
  });
  it("freezes the template version and starts a draft with its own SKU and request ID",async()=>{
    const save=vi.spyOn(api.templates,"draft").mockRejectedValue(new Error("No network"));
    renderToStaticMarkup(<TemplateDraftDialog template={template} onClose={vi.fn()} onCreated={vi.fn()}/>);
    expect(save).not.toHaveBeenCalled();
    await expect(useMutation.mock.calls[0][0].mutationFn()).rejects.toThrow("No network");
    expect(save).toHaveBeenCalledWith("coats",{request_id:expect.stringMatching(/^[a-f0-9]{32}$/),expected_revision:3,title:"Coats draft",sku:"",quantity:2});
    expect(useMutation.mock.calls[0][0].retry).toBe(false);save.mockRestore();
  });
});
