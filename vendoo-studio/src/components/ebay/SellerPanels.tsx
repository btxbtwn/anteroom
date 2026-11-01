import { useState } from "react";
import type { EbayRow } from "../../api/ebay";
import { api } from "../../api/client";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PageNote, PageSection } from "../WorkspacePage";
import { Input } from "../ui/input";
import { Button } from "../ui/button";
import { EvidenceUploader, MetricValue, RemoteList } from "./shared";
import { ActionForm } from "./ActionForm";
import { displayMoney, summarizeDisplayedEarnings } from "./dashboardSummary";
import { WorkspaceTabs } from "./WorkspaceTabs";

export function DashboardPanel() {
  const [today] = useState(() => new Date().toISOString().slice(0, 10));
  const [start, setStart] = useState(() => new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10));
  const [end, setEnd] = useState(() => new Date().toISOString().slice(0, 10));
  const valid = Boolean(start && end && start <= end);
  const finance = useQuery({ queryKey: ["ebay", "finances", { start, end }, 0], queryFn: () => api.ebay.read("finances", { start, end, offset: 0 }), enabled: valid, retry: false, staleTime: 30000 });
  const summary = summarizeDisplayedEarnings(valid ? finance.data?.rows || [] : []);
  const coverage = finance.data ? `First ${finance.data.rows.length}${finance.data.total != null ? ` of ${finance.data.total}` : ""} ${finance.data.total === 1 ? "order" : "orders"} · totals cover these orders only` : "Totals appear when order transactions are available.";
  return <>
    <div className="ebay-dashboard-toolbar">
      <div className="ebay-tab-intro"><h2>Sales overview</h2><p>Follow your eBay earnings and listing activity.</p></div>
      <div className="ebay-date-range">
        <label>From<Input nativeInput type="date" value={start} max={end} onChange={(event) => setStart(event.target.value)} /></label>
        <label>To<Input nativeInput type="date" value={end} min={start} max={today} onChange={(event) => setEnd(event.target.value)} /></label>
      </div>
    </div>
    {!valid ? <PageNote>Choose a valid start and end date to view this period.</PageNote> : null}
    <div>
      <dl className="ebay-metrics">
        <div className="ebay-metric"><dt>Net order proceeds</dt><MetricValue value={displayMoney(summary.earnings)} /><p>Recorded credits minus order charges</p></div>
        <div className="ebay-metric"><dt>Item costs</dt><MetricValue value={displayMoney(summary.costs)} /><p>Local costs for these orders</p></div>
        <div className="ebay-metric"><dt>Profit after item costs</dt><MetricValue value={displayMoney(summary.profit)} /><p>Other business expenses excluded</p></div>
      </dl>
      <p className="text-xs text-muted-foreground mt-3">{coverage}</p>
    </div>
    <div className="ebay-dashboard-grid">
      <PageSection className="ebay-data-panel" title="Order proceeds and item costs" description="Orders created in the selected dates (UTC), with their latest recorded sales, fees, refunds, and eBay label charges. Missing costs stay unavailable; transfers and other business expenses are excluded.">
        {valid ? <RemoteList key={`finance-${start}-${end}`} section="finances" params={{ start, end }} /> : null}
      </PageSection>
      <PageSection className="ebay-data-panel" title="Payouts" description="Recent payouts reported by eBay.">
        <RemoteList section="payouts" />
      </PageSection>
    </div>
    <PageSection className="ebay-data-panel" title="Listing performance" description="Performance for your linked eBay listings. Rates are reported by eBay.">
      {valid ? <RemoteList key={`traffic-${start}-${end}`} section="traffic" params={{ start, end }} /> : null}
    </PageSection>
  </>;
}

export function InboxPanel() {
  const [selected, setSelected] = useState<EbayRow | null>(null);
  return <>
    <div className="ebay-tab-intro"><h2>Buyer inbox</h2><p>Read a conversation and review your reply before sending.</p></div>
    <div className="ebay-inbox-grid">
    <PageSection className="ebay-data-panel" title="Buyer conversations" description="Fetched directly from eBay.">
      <RemoteList section="inbox" selected={selected?.id} onSelect={setSelected} />
    </PageSection>
    {selected ? <PageSection className="ebay-data-panel" title={selected.title || "Conversation"}>
      <RemoteList key={selected.id} section="messages" params={{ target: selected.id }} />
      <ActionForm defaultOpen={false} key={`read-${selected.id}`} title="Mark conversation read" kind="mark_read" context={{ conversation_id: selected.id }} />
      <ActionForm key={`reply-${selected.id}`} title="Send reply" kind="message" context={{ conversation_id: selected.id }}
        fields={[{ name: "text", label: "Your reply", type: "textarea" }]} />
    </PageSection> : <div className="ebay-empty"><p>Select a conversation to read and reply.</p></div>}
    </div>
    <FeedbackPanel />
  </>;
}

function FeedbackPanel() {
  const [selected, setSelected] = useState<EbayRow | null>(null);
  return <PageSection title="Feedback to leave" description="Select a completed sale to review positive buyer feedback.">
    <RemoteList section="feedback" selected={selected?.id} onSelect={setSelected} />
    {selected ? <ActionForm key={selected.id} title="Leave positive feedback" kind="feedback"
      context={{ listing_id: selected.fields.listingId, transaction_id: selected.fields.transactionId, line_item_id: selected.id }}
      fields={[{ name: "text", label: "Feedback comment", initial: "Thank you for your purchase!" }]} /> : null}
  </PageSection>;
}

export function OrdersPanel() {
  const [task, setTask] = useState("Shipment");
  const [selected, setSelected] = useState<EbayRow | null>(null);
  const [dispute, setDispute] = useState<EbayRow | null>(null);
  const [fileId, setFileId] = useState("");
  return <>
    <PageSection title="Orders" description="Select an order to review tracking or a refund. Shipping applies to all remaining unshipped items in that order.">
      <RemoteList section="orders" selected={selected?.id} onSelect={setSelected} />
    </PageSection>
    {selected ? <PageSection title={`Manage order ${selected.id}`}>
      <RemoteList key={selected.id} section="order" params={{ target: selected.id }} />
      <WorkspaceTabs label="Order action" items={["Shipment", "Refund"]} value={task} onChange={setTask}>
        {task === "Shipment" ?
        <ActionForm key={`ship-${selected.id}`} title="Record shipment" kind="shipment" context={{ order_id: selected.id }}
          fields={[{ name: "carrier", label: "Carrier code", options: ["USPS", "UPS", "FedEx", "DHL", "Other"] },
                   { name: "tracking", label: "Tracking number", help: "Recorded for all remaining unshipped items in this order." }]} /> :
        <ActionForm key={`refund-${selected.id}`} title="Issue refund" kind="refund" context={{ order_id: selected.id }}
          fields={[{ name: "amount", label: "Refund amount (USD)", type: "number", min: "0.01" },
                   { name: "reason", label: "Reason", options: ["BUYER_CANCEL", "SELLER_CANCEL", "ITEM_NOT_RECEIVED", "BUYER_RETURN", "ITEM_NOT_AS_DESCRIBED", "OTHER_ADJUSTMENT", "SHIPPING_DISCOUNT"] }]} />
        }
      </WorkspaceTabs>
    </PageSection> : null}
    <PageSection title="Payment disputes" description="View deadlines, add evidence, and review a response before submitting it to eBay.">
      <RemoteList section="disputes" selected={dispute?.id} onSelect={(row) => { setDispute(row); setFileId(""); }} />
      {dispute ? <div className="space-y-4">
        <RemoteList key={dispute.id} section="dispute" params={{ target: dispute.id }} />
        <EvidenceUploader key={`upload-${dispute.id}`} disputeId={dispute.id} onResult={(result) => setFileId(result.receiptId || "")} />
        <ActionForm defaultOpen={false} key={`evidence-${dispute.id}-${fileId}`} draftId={`${dispute.id}-${fileId}`} title="Attach dispute evidence" kind="evidence" context={{ dispute_id: dispute.id }}
          fields={[{ name: "file_id", label: "Uploaded evidence file ID", initial: fileId },
                   { name: "evidence_type", label: "Evidence type", options: ["PROOF_OF_DELIVERY", "PROOF_OF_AUTHENTICITY", "PROOF_OF_ITEM_AS_DESCRIBED", "PROOF_OF_CREDIT_NOT_DUE", "PROOF_OF_RETURN_NOT_RECEIVED", "OPTIONAL_SUPPORTING_DOCUMENTS"] }]} />
        <ActionForm defaultOpen={false} key={`contest-${dispute.id}`} title="Contest payment dispute" kind="contest" context={{ dispute_id: dispute.id }}
          fields={[{ name: "note", label: "Your explanation", type: "textarea" },
                   { name: "fullName", label: "Return address: full name", optional: true, group: "US return address" }, { name: "addressLine1", label: "Return street address", optional: true, group: "US return address" },
                   { name: "addressLine2", label: "Apartment or suite", optional: true, group: "US return address" }, { name: "city", label: "Return city", optional: true, group: "US return address" },
                   { name: "stateOrProvince", label: "Return state", help: "Two-letter US state code.", optional: true, group: "US return address" }, { name: "postalCode", label: "Return ZIP code", optional: true, group: "US return address" },
                   { name: "phone", label: "Return contact phone", optional: true, group: "US return address" }]}
          build={({ note, ...address }) => ({ note, return_address: Object.values(address).some((v) => v.trim()) ? { ...address, country: "US" } : null })}>
          <p className="text-xs text-muted-foreground">A return address is required for significantly not as described disputes. US return addresses are supported here.</p>
        </ActionForm>
      </div> : null}
    </PageSection>
  </>;
}

export function ActivityPanel() {
  const [selected, setSelected] = useState<EbayRow | null>(null);
  const queryClient = useQueryClient();
  const checked = useMutation({ mutationFn: (id: string) => api.ebay.checked(id), retry: false,
    onSuccess: () => { setSelected(null); void queryClient.invalidateQueries({ queryKey: ["ebay", "activity"] }); } });
  return <PageSection title="Reviewed actions" description="Only action IDs, fingerprints, and receipts are saved. Message text, buyer details, and image files are not kept in this journal.">
    <RemoteList section="activity" selected={selected?.id} onSelect={setSelected} />
    {selected?.status === "unknown" ? <div className="space-y-2">
      <PageNote>This action may have completed. Check the result in eBay before marking it checked. Checking does not repeat it.</PageNote>
      <a href="https://www.ebay.com/sh/ovw" target="_blank" rel="noreferrer" className="text-sm underline">Open Seller Hub</a>
      <Button size="sm" variant="outline" disabled={checked.isPending} onClick={() => checked.mutate(selected.id)}>I've checked the result in eBay</Button>
      {checked.error ? <p role="alert">{checked.error.message}</p> : null}
    </div> : null}
  </PageSection>;
}
