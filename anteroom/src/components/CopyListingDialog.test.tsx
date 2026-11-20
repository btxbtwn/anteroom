import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { CopyPreview } from "../api/listingCopy";
import { CopyListingDialog } from "./CopyListingDialog";
const { useQuery, useMutation, invalidateQueries, setQueryData } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn(), invalidateQueries: vi.fn(), setQueryData: vi.fn() }));
vi.mock("@tanstack/react-query", () => ({ useQuery, useMutation, useQueryClient: () => ({ invalidateQueries, setQueryData }) }));
const preview: CopyPreview = { source_id: "original", source_title: "Coat", expected_basis: "a".repeat(64), photo_count: 2, has_listing: true, title: "Copy of Coat" };
beforeEach(() => {
  useQuery.mockReset(); useMutation.mockReset(); invalidateQueries.mockClear(); setQueryData.mockClear();
  useQuery.mockReturnValue({ data: preview, isFetching: false, refetch: vi.fn() });
  useMutation.mockReturnValue({ isPending: false, mutate: vi.fn() });
});
describe("listing copy review", () => {
  it("requires an explicit create action and starts with clean inventory defaults", async () => {
    const copy = vi.spyOn(api.conversations, "copy").mockRejectedValue(new Error("No network"));
    renderToStaticMarkup(<CopyListingDialog conversationId="original" onClose={vi.fn()} onCopied={vi.fn()} />);
    expect(copy).not.toHaveBeenCalled();
    const mutation = useMutation.mock.calls[0][0];
    await expect(mutation.mutationFn()).rejects.toThrow("No network");
    expect(copy).toHaveBeenCalledWith("original", {request_id:expect.stringMatching(/^[a-f0-9]{32}$/),expected_basis:preview.expected_basis,title:"Copy of Coat",sku:"",quantity:1,include_photos:true,include_cost:false});
    expect(mutation.retry).toBe(false);
    copy.mockRestore();
  });
  it("does not rebase the source review on focus or a timed refetch", () => {
    renderToStaticMarkup(<CopyListingDialog conversationId="original" onClose={vi.fn()} onCopied={vi.fn()} />);
    const query = useQuery.mock.calls[0][0];
    expect(query.refetchOnWindowFocus).toBe(false);
    expect(query.staleTime).toBe(Infinity);
    expect(query.retry).toBe(false);
  });
  it("opens the saved copy and refreshes inventory only after success", () => {
    const onCopied = vi.fn();
    renderToStaticMarkup(<CopyListingDialog conversationId="original" onClose={vi.fn()} onCopied={onCopied} />);
    useMutation.mock.calls[0][0].onSuccess({ id: "new-item" });
    expect(onCopied).toHaveBeenCalledWith("new-item");
    expect(invalidateQueries).toHaveBeenCalledWith({queryKey:["conversations"]});
    expect(setQueryData).toHaveBeenCalledWith(["conversation","new-item"],{id:"new-item"});
  });
});
