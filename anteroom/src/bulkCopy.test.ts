import { describe, expect, it, vi } from "vitest";
import { approvedCopies, copyDraft, copyDraftError, runCopies } from "./bulkCopy";
import type { BatchCopyPreviewRow } from "./api/listingCopy";
const row: BatchCopyPreviewRow = {source_id:"coat",source_title:"Coat",title:"Copy of Coat",expected_basis:"a".repeat(64),photo_count:2,has_listing:true,skip_reason:null};

describe("reviewed inventory copies", () => {
  it("freezes separate defaults, edited values and request IDs at approval", () => {
    let index=0;
    const rows=[row,{...row,source_id:"shirt",source_title:"Shirt"},{...row,source_id:"missing",skip_reason:"Gone"}];
    const edits=new Map([["shirt",{...copyDraft(row),title:"New shirt",sku:"SHIRT-2",quantity:"3",includeCost:true,includePhotos:false}]]);
    const tasks=approvedCopies(rows,edits,()=>String(++index).repeat(32));
    expect(tasks).toHaveLength(2);
    expect(tasks[0].request).toMatchObject({request_id:"1".repeat(32),sku:"",quantity:1,include_photos:true,include_cost:false});
    expect(tasks[1].request).toMatchObject({request_id:"2".repeat(32),title:"New shirt",sku:"SHIRT-2",quantity:3,include_photos:false,include_cost:true});
    edits.get("shirt")!.sku="CHANGED";
    expect(tasks[1].request.sku).toBe("SHIRT-2");
  });
  it("requires valid rows and leaves explicitly excluded items alone", () => {
    expect(copyDraftError(row,{...copyDraft(row),quantity:"1.5"})).toContain("whole quantity");
    expect(()=>approvedCopies([row],new Map([["coat",{...copyDraft(row),title:" "}]]))).toThrow();
    expect(approvedCopies([row],new Map([["coat",{...copyDraft(row),skip:true}]]))).toEqual([]);
  });
  it("recovers a lost reply by reading its saved result without a second write", async () => {
    const tasks=approvedCopies([row],new Map(),()=>"1".repeat(32));
    const api={copy:vi.fn().mockRejectedValue(new Error("Reply lost")),copyResult:vi.fn().mockResolvedValue({id:"new-coat"})};
    const result=await runCopies(tasks,api,()=>false,vi.fn());
    expect(result[0]).toMatchObject({state:"saved",targetId:"new-coat"});
    expect(api.copy).toHaveBeenCalledTimes(1);
    expect(api.copyResult).toHaveBeenCalledWith("coat","1".repeat(32));
  });
  it("continues past failures and retries only unresolved requests with the same approval", async () => {
    const tasks=approvedCopies([row,{...row,source_id:"shirt"}],new Map(),()=>"1".repeat(32));
    const api={copy:vi.fn().mockRejectedValueOnce(new Error("Unavailable")).mockResolvedValueOnce({id:"new-shirt"}).mockResolvedValueOnce({id:"new-coat"}),copyResult:vi.fn().mockRejectedValue(new Error("Not saved"))};
    const first=await runCopies(tasks,api,()=>false,vi.fn());
    expect(first.map(task=>task.state)).toEqual(["failed","saved"]);
    const second=await runCopies(first,api,()=>false,vi.fn());
    expect(second.map(task=>task.state)).toEqual(["saved","saved"]);
    expect(api.copy.mock.calls.map(([id])=>id)).toEqual(["coat","shirt","coat"]);
    expect(api.copy.mock.calls[2][1]).toEqual(tasks[0].request);
  });
  it("stops after the current item without starting remaining copies", async () => {
    const tasks=approvedCopies([row,{...row,source_id:"shirt"}],new Map(),()=>"1".repeat(32));
    let stopped=false;
    const api={copy:vi.fn().mockImplementation(async()=>{stopped=true;return{id:"new-coat"};}),copyResult:vi.fn()};
    const result=await runCopies(tasks,api,()=>stopped,vi.fn());
    expect(result.map(task=>task.state)).toEqual(["saved","pending"]);
    expect(api.copy).toHaveBeenCalledTimes(1);
  });
});
