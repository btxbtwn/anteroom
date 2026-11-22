// @vitest-environment happy-dom
import { act, type ComponentProps } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceRuntimeProvider } from "../app/runtime";
import { ListingSidebar } from "./ListingSidebar";
import type { ListingFilters } from "./ListingFilters";
import type { VirtualListingList } from "./sidebar/VirtualListingList";
vi.mock("@tanstack/react-query", () => ({ useQuery: () => ({ data: undefined }) }));
vi.mock("./sidebar/SidebarFooter", () => ({ SidebarFooter: () => null }));
vi.mock("./sidebar/BulkSelection", () => ({ BulkActionBar: () => null, BulkSelectionDialogs: () => null }));
vi.mock("./sidebar/VirtualListingList", () => ({ VirtualListingList: ({ rows, children }: ComponentProps<typeof VirtualListingList>) => <div>{rows.map(row => <span key={row.id}>{row.id}</span>)}{children}</div> }));
vi.mock("./ListingFilters", () => ({ ListingFilters: (p: ComponentProps<typeof ListingFilters>) => <div>
  {(["draft", "active", "sold"] as const).map(status => <button key={status} onClick={() => p.onChange({ ...p.filters, status })}>{status}:{p.counts?.[status] || 0}</button>)}
  <output>{p.filters.status} labels:{p.labelCounts?.test || 0}</output>
</div> }));
const noop = () => {};
const container = document.createElement("div");
document.body.append(container);
let root = createRoot(container);
afterEach(async () => { await act(() => root.unmount()); root = createRoot(container); });
const base: ComponentProps<typeof ListingSidebar> = {
  conversations: [], selectedConvId: "one", activeView: "listings", settingsSection: "general", creating: false,
  onSelect: noop, onDelete: noop, onCreate: noop, onOpenHome: noop, onOpenSettings: noop, onOpenAnalytics: noop,
  onOpenMarketplaces: noop, onOpenAutomations: noop, onOpenAssistant: noop, onOpenSourcing: noop,
  onOpenSuggestions: noop, onToggleInventoryTable: noop, onCloseSettings: noop,
  onSettingsSectionChange: noop, onSettingsSearchResult: noop,
  bulk: { running: false } as ComponentProps<typeof ListingSidebar>["bulk"],
};
async function render(overrides: Partial<typeof base>) {
  await act(() => root.render(<WorkspaceRuntimeProvider value={{ hosted: false, features: new Set(), views: new Set(["listings"]), marketplaceForms: new Set(), actionMarketplaces: new Set() }}><ListingSidebar {...base} {...overrides} /></WorkspaceRuntimeProvider>));
}
async function pick(status: string) {
  const button = [...container.querySelectorAll("button")].find(b => b.textContent?.startsWith(`${status}:`))!;
  await act(() => button.click());
}
const draft = { id: "one", title: "Test listing", status: "draft", vendoo_labels: ["test"] };
it("follows the same listing again after it closes", async () => {
  await render({ conversations: [draft] });
  await render({ conversations: [draft], selectedConvId: null });
  await pick("active");
  await render({ conversations: [draft] });
  expect(container.querySelector("output")?.textContent).toBe("draft labels:1");
});
it("keeps counts, facets and nonempty state with a retained row", async () => {
  await render({ conversations: [draft] });
  await render({ conversations: [{ ...draft, status: "active" }] });
  expect(container.textContent).toContain("draft:1active:0sold:0");
  expect(container.querySelector("output")?.textContent).toBe("draft labels:1");
  expect(container.textContent).toContain("one");
  expect(container.textContent).not.toContain("No draft listings");
  await pick("active");
  expect(container.textContent).toContain("draft:0active:1sold:0");
});
