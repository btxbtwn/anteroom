import { renderToStaticMarkup } from "react-dom/server";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { CloudBridge, CloudConnection } from "../api/cloudExtension";
import type { NativeDrafts } from "../api/nativeMarketplaces";
import { WorkspaceRuntimeProvider, type WorkspaceRuntime } from "../app/runtime";
import { MarketplacesSection } from "./MarketplacesSection";

const { useQuery, useMutation } = vi.hoisted(() => ({ useQuery: vi.fn(), useMutation: vi.fn() }));
const setQueryData = vi.fn();
vi.mock("@tanstack/react-query", async original => ({
  ...await original<typeof import("@tanstack/react-query")>(),
  useQuery, useMutation, useQueryClient: () => ({ invalidateQueries: vi.fn(), setQueryData }),
}));

const CONNECTED: CloudConnection = { ok: true, state: "connected", extensionVersion: "0.2.40", supportedActions: ["marketplace.save_draft"] };

function bridge(): CloudBridge {
  return { saveDraft: vi.fn(async () => ({ ok: true, recorded: true })), status: vi.fn() } as unknown as CloudBridge;
}

function render(selected: string, { drafts = {} as NativeDrafts, connection = CONNECTED as CloudConnection | null,
  chrome = bridge() as CloudBridge | null } = {}) {
  useQuery.mockImplementation(({ queryKey }: { queryKey: string[] }) =>
    queryKey[0] === "native-marketplaces" ? { data: drafts, error: null, isError: false, isPending: false }
    : queryKey[0] === "marketplace-listings" ? { data: {}, error: null, isPending: false }
    : queryKey[0] === "native-preflight" ? { data: { marketplaces: {} }, isPending: false, isPlaceholderData: false, isError: false }
    : queryKey[0] === "chrome-bridge-status" ? { data: connection ?? undefined }
    : { data: undefined, isPending: false, error: null });
  const mutation = { mutate: vi.fn(), isPending: false, error: null };
  useMutation.mockReturnValue(mutation);
  const runtime: WorkspaceRuntime = { hosted: true, features: new Set(), views: new Set(["listings"]),
    marketplaceForms: new Set(["poshmark", "mercari", "depop", "ebay"]), actionMarketplaces: new Set(["ebay"]),
    draftMarketplaces: new Set(["poshmark", "mercari", "depop"]), chrome: chrome ?? undefined };
  const html = renderToStaticMarkup(<WorkspaceRuntimeProvider value={runtime}>
    <MarketplacesSection convId="item" revisionId="current" title="Coat" disabled={false} etsyEligible selected={selected} />
  </WorkspaceRuntimeProvider>);
  return { html, chrome, mutation };
}

const pane = (html: string) => html.slice(html.indexOf("data-marketplace-pane"));
const enabledButton = (html: string, label: string) => new RegExp(`<button(?![^>]*disabled="")[^>]*>${label}</button>`).test(html);

describe("Save draft from the web app", () => {
  beforeEach(() => vi.clearAllMocks());

  it.each(["poshmark", "mercari", "depop"])("offers only Save draft on %s once Chrome can take it", (marketplace) => {
    const html = pane(render(marketplace).html);
    expect(enabledButton(html, "Save draft")).toBe(true);
    expect(html).not.toContain("List…");
    expect(html).not.toContain("Preview");
  });

  it("explains what Chrome needs instead of offering the button", () => {
    expect(pane(render("poshmark", { connection: { ok: true, state: "disconnected", extensionVersion: "0.2.40" } }).html))
      .toContain("Connect Chrome to save drafts.");
    expect(pane(render("poshmark", { connection: null }).html)).toContain("Connect Chrome to save drafts.");
    expect(pane(render("poshmark", { connection: { ...CONNECTED, supportedActions: ["poshmark.form.read"] } }).html))
      .toContain("Update Anteroom Bridge to save drafts.");
    expect(pane(render("poshmark", { chrome: null }).html)).toContain("Open your account in desktop Chrome to save drafts.");
    for (const html of [render("poshmark", { connection: null }).html, render("poshmark", { chrome: null }).html]) {
      expect(pane(html)).not.toContain(">Save draft<");
    }
  });

  it("keeps eBay's own actions and other marketplaces a preview", () => {
    expect(pane(render("ebay").html)).toContain("List…");
    expect(pane(render("sellwild").html)).toContain("Preview");
  });

  it("leaves an unconfirmed web save for the seller, with no Chrome check", () => {
    const html = pane(render("depop", { drafts: { depop: { state: "unknown", action: "save_draft", running: false,
      error: "Anteroom could not confirm what happened." } } }).html);
    expect(html).toContain("I checked it myself…");
    expect(html).not.toContain("Check result");
    expect(html).not.toContain("Check link");
    expect(html).not.toContain(">Save draft<");
  });

  it("shows a running save without actions", () => {
    const html = render("mercari", { drafts: { mercari: { state: "saving", action: "save_draft", running: true } } }).html;
    expect(html).toContain("Saving draft…");
    expect(pane(html)).not.toContain(">Save draft<");
    expect(pane(html)).not.toContain("Cancel");
  });

  it("records the save, hands the signed command to Chrome and never lists", async () => {
    const { chrome } = render("poshmark");
    const options = useMutation.mock.calls.find(([arg]) => String(arg.mutationFn).includes("chromeDraft"))![0];
    const command = { body: "signed", signature: "s".repeat(86) };
    const record = { state: "saving" as const, action: "save_draft" as const, running: true };
    const chromeDraft = vi.spyOn(api.nativeMarketplaces, "chromeDraft").mockResolvedValue({ record, command });
    const saveDraft = vi.spyOn(api.nativeMarketplaces, "saveDraft");
    const list = vi.spyOn(api.nativeMarketplaces, "list");
    await expect(options.mutationFn({ marketplace: "poshmark", kind: "save_draft" })).resolves.toEqual(record);
    expect(chromeDraft).toHaveBeenCalledWith("item", "poshmark", "current");
    expect(chrome!.saveDraft).toHaveBeenCalledWith(command);
    expect(setQueryData).toHaveBeenCalled();
    expect(saveDraft).not.toHaveBeenCalled();
    expect(list).not.toHaveBeenCalled();
  });
});
