import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2Icon, Settings2Icon, StoreIcon } from "lucide-react";
import { api } from "../../api/client";
import { PageError, PageNote } from "../WorkspacePage";
import { SaleCalendar } from "../SaleCalendar";
import { Button } from "../ui/button";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogPanel, DialogPopup, DialogTitle } from "../ui/dialog";
import { EbayOffersSettings } from "../settings/EbayOffersSettings";
import { ActivityPanel, DashboardPanel, InboxPanel, OrdersPanel } from "./SellerPanels";
import { MarketingPanel } from "./MarketingPanel";
import { ListingsPanel, ResearchPanel } from "./ListingsPanel";
import { WorkspaceTabs } from "./WorkspaceTabs";
import { useFormDraft } from "./FormDrafts";
import "./ebay.css";

const TABS = ["Dashboard", "Inbox", "Orders", "Marketing", "Listings", "Research", "Offers", "Activity"] as const;
type Tab = (typeof TABS)[number];
const FEATURES = [
  { id: "analytics", name: "Listing performance", detail: "Impressions, views, and conversion." },
  { id: "finances", name: "Earnings and payouts", detail: "Fees, refunds, and order earnings." },
  { id: "orders", name: "Order management", detail: "Tracking updates and reviewed refunds." },
  { id: "disputes", name: "Payment disputes", detail: "Evidence and dispute responses." },
  { id: "messages", name: "Buyer messages", detail: "Read conversations and send reviewed replies." },
  { id: "feedback", name: "Buyer feedback", detail: "Leave positive feedback for completed sales." },
  { id: "marketing", name: "Discounts and advertising", detail: "Promotions, campaigns, reports, and store emails." },
  { id: "listings", name: "Listing tools", detail: "Business policies and unpublished offers." },
];

export function EbayWorkspace({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  const [tab, setTab] = useFormDraft<Tab>("workspace:tab", () => "Dashboard");
  const [permissionsOpen, setPermissionsOpen] = useState(false);
  const [features, setFeatures] = useState(FEATURES.map((feature) => feature.id));
  const queryClient = useQueryClient();
  const connection = useQuery({ queryKey: ["direct-connections"], queryFn: api.directConnections.status,
    refetchInterval: (query) => query.state.data?.accounts.ebay.pending ? 2000 : 15000 });
  const grant = useMutation({ mutationFn: () => api.ebay.grant(features), retry: false,
    onSuccess: () => { void queryClient.invalidateQueries({ queryKey: ["direct-connections"] }); } });
  const account = connection.data?.accounts.ebay;
  return <div className="ebay-workspace">
    <header className="ebay-page-heading">
      <div><p className="ebay-eyebrow mb-3">Sales channel</p><div className="flex items-center gap-3"><StoreIcon className="size-6 text-muted-foreground" aria-hidden="true" /><h2>eBay</h2></div>
        <p>Orders, buyers, listings, and performance.</p></div>
      <div className="ebay-connection">
        {account?.connected ? <span className="ebay-status"><CheckCircle2Icon aria-hidden="true" />{account.label || "Connected"}</span> : null}
        <Button variant="outline" onClick={() => setPermissionsOpen(true)}><Settings2Icon />{account?.connected ? "Connection" : "Connect eBay"}</Button>
      </div>
    </header>
    {connection.error ? <PageError message={connection.error.message} /> : null}
    {account?.connected ? <WorkspaceTabs label="eBay workspace" main items={TABS} value={tab} onChange={(value) => setTab(value as Tab)}>
      <div className="ebay-tab-content" key={`${account.connectedAt}-${tab}`}>
        {tab === "Dashboard" ? <DashboardPanel /> : tab === "Inbox" ? <InboxPanel /> : tab === "Orders" ? <OrdersPanel />
          : tab === "Marketing" ? <><MarketingPanel /><SaleCalendar marketplace="ebay" onOpenListing={onOpenListing} /></> : tab === "Listings" ? <ListingsPanel /> : tab === "Research" ? <ResearchPanel />
            : tab === "Offers" ? <><div className="ebay-tab-intro"><h2>Offers to interested buyers</h2><p>Set each listing’s minimum price before sending or enabling automatic offers.</p></div><EbayOffersSettings /></> : <ActivityPanel />}
      </div>
    </WorkspaceTabs> : <div className="ebay-data-panel space-y-4">
      <h2>{connection.isPending ? "Checking your connection…" : "Connect your eBay account"}</h2>
      <p className="text-muted-foreground">Choose the tools you need and authorize them through eBay. You review account changes before confirming them.</p>
      {!connection.isPending ? <Button onClick={() => setPermissionsOpen(true)}>Connect eBay</Button> : null}
    </div>}
    {!account?.connected ? <SaleCalendar marketplace="ebay" onOpenListing={onOpenListing} /> : null}
    <Dialog open={permissionsOpen} onOpenChange={setPermissionsOpen}>
      <DialogPopup className="ebay-action-dialog sm:max-w-xl">
        <DialogHeader><DialogTitle>eBay connection</DialogTitle><DialogDescription>Choose the tools you want to authorize. You can grant groups separately if eBay restricts a feature.</DialogDescription></DialogHeader>
        <DialogPanel>
          <div className="grid gap-2">{FEATURES.map((feature) => <label key={feature.id} className="ebay-permission">
            <input type="checkbox" checked={features.includes(feature.id)} onChange={(event) => setFeatures(event.target.checked ? [...features, feature.id] : features.filter((id) => id !== feature.id))} />
            <span>{feature.name}<small>{feature.detail}</small></span>
          </label>)}</div>
          <PageNote>eBay may require seller eligibility or app approval. After reconnecting, refresh the view you want to use.</PageNote>
          {account?.pending ? <PageNote>Finish sign-in in your browser. {account.authorizeUrl ? <a className="underline" href={account.authorizeUrl} target="_blank" rel="noreferrer">Open eBay sign-in</a> : null}</PageNote> : null}
          {grant.error || account?.error ? <PageError message={grant.error?.message || account?.error || ""} /> : null}
        </DialogPanel>
        <DialogFooter><Button variant="outline" onClick={() => setPermissionsOpen(false)}>Close</Button>
          <Button disabled={grant.isPending || account?.pending} onClick={() => grant.mutate()}>Grant selected permissions</Button></DialogFooter>
      </DialogPopup>
    </Dialog>
  </div>;
}
