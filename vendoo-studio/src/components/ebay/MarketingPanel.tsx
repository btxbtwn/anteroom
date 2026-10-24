import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { EbayRow } from "../../api/ebay";
import { PageError, PageSection } from "../WorkspacePage";
import { Button } from "../ui/button";
import { ListingPicker, RemoteList } from "./shared";
import { ActionForm } from "./ActionForm";
import { WorkspaceTabs } from "./WorkspaceTabs";

const future = (days: number) => {
  const date = new Date(Date.now() + days * 86400000);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
};
const iso = (value: string) => new Date(value).toISOString();

export function MarketingPanel() {
  const [task, setTask] = useState("Discounts");
  const [audienceCodes, setAudienceCodes] = useState<string[]>([]);
  const [defaults] = useState(() => ({ start: future(1), end: future(8),
    reportStart: new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10),
    reportEnd: new Date(Date.now() - 86400000).toISOString().slice(0, 10) }));
  const [ids, setIds] = useState<string[]>([]);
  const [campaign, setCampaign] = useState<EbayRow | null>(null);
  const [promotion, setPromotion] = useState<EbayRow | null>(null);
  const [report, setReport] = useState<EbayRow | null>(null);
  const dateFields = [
    { name: "name", label: "Promotion name", group: "Discount" }, { name: "percent", label: "Discount (%)", type: "number", initial: "10", min: "5", max: "80", step: "1", help: "Choose a whole percentage from 5% to 80%.", group: "Discount" },
    { name: "start", label: "Starts", type: "datetime-local", initial: defaults.start, help: "Your local time.", group: "Schedule" },
    { name: "end", label: "Ends", type: "datetime-local", initial: defaults.end, help: "Your local time.", group: "Schedule" },
    { name: "image_url", label: "Promotion image URL", type: "url", help: "JPEG or PNG, at least 500 × 500 pixels, up to 12 MB.", group: "Promotion image" },
  ];
  const promotionPayload = (values: Record<string, string>) => ({ name: values.name, percent: Number(values.percent),
    start: iso(values.start), end: iso(values.end), quantity: Number(values.quantity || 2), image_url: values.image_url });
  return <WorkspaceTabs label="Marketing tools" items={["Discounts", "Advertising", "Store emails"]} value={task} onChange={setTask}>
    <PageSection title="Choose listings" description="New discounts and ads apply only to the listings you select. eBay checks seller eligibility and promotion rules.">
      <ListingPicker selected={ids} onChange={setIds} />
    </PageSection>
    {task === "Discounts" ? <>
    <div className="space-y-3">
      <ActionForm defaultOpen={false} title="Schedule markdown sale" kind="markdown" context={{ listing_ids: ids }}
        disabled={!ids.length || ids.length > 50} fields={dateFields} build={promotionPayload}>
        <p className="text-xs text-muted-foreground">US markdown sales must last between 24 hours and 45 days.</p>
      </ActionForm>
      <ActionForm defaultOpen={false} title="Schedule quantity discount" kind="quantity_discount" context={{ listing_ids: ids }}
        disabled={!ids.length || ids.length > 50} fields={[...dateFields, { name: "quantity", label: "Minimum items per order", type: "number", initial: "2", min: "2", max: "10" }]} build={promotionPayload} />
    </div>
    <PageSection title="Discount promotions">
      <RemoteList section="promotions" selected={promotion?.id} onSelect={setPromotion} />
      {promotion ? <ActionForm defaultOpen={false} key={promotion.id} title="Change promotion status" kind="promotion_control" context={{ resource_id: promotion.id }}
        fields={[{ name: "action", label: "Action", options: ["pause", "resume"] }]} /> : null}
    </PageSection>
    <PageSection title="Promotion results"><RemoteList section="promotion-reports" /></PageSection>
    </> : null}
    {task === "Advertising" ? <>
    <PageSection title="Promoted Listings campaigns" description="General campaigns charge an ad percentage on attributed sales. Creating a campaign does not add listings automatically.">
      <RemoteList section="campaigns" selected={campaign?.id} onSelect={setCampaign} />
      <ActionForm defaultOpen={false} title="Create ad campaign" kind="campaign"
        fields={[{ name: "name", label: "Campaign name" }, { name: "bid_percent", label: "Default ad rate (%)", type: "number", initial: "2", min: "2", max: "100", step: "0.1" },
                 { name: "start", label: "Starts (your local time)", type: "datetime-local", initial: defaults.start }]}
        build={(values) => ({ ...values, start: iso(values.start) })} />
      {campaign ? <div className="space-y-3">
        <ActionForm defaultOpen={false} key={`ad-${campaign.id}`} title="Advertise selected listing" kind="ad" disabled={ids.length !== 1}
          context={{ campaign_id: campaign.id, listing_id: ids[0] }}
          fields={[{ name: "bid_percent", label: "Ad rate (%)", type: "number", initial: "2", min: "2", max: "100", step: "0.1" }]}>
          <p className="text-xs text-muted-foreground">Select exactly one listing above for this ad.</p>
        </ActionForm>
        <ActionForm defaultOpen={false} key={`campaign-${campaign.id}`} title="Change campaign status" kind="campaign_control" context={{ resource_id: campaign.id }}
          fields={[{ name: "action", label: "Action", options: ["pause", "resume", "end"] }]} />
        <ActionForm defaultOpen={false} key={`report-${campaign.id}`} title="Generate campaign report" kind="ad_report" context={{ campaign_id: campaign.id }}
          fields={[{ name: "start", label: "From", type: "date", initial: defaults.reportStart },
                   { name: "end", label: "To", type: "date", initial: defaults.reportEnd }]} />
      </div> : null}
    </PageSection>
    <PageSection title="Advertising results" description="Select a completed report to see eBay's campaign metrics. Refresh while eBay generates a new report.">
      <RemoteList section="ad-reports" selected={report?.id} onSelect={setReport} />
      {report ? <RemoteList key={report.id} section="ad-report" params={{ target: report.id }} /> : null}
    </PageSection>
    </> : null}
    {task === "Store emails" ? <>
    <PageSection title="Store email campaigns" description="Store eligibility is required. Emails go to eBay's eligible opted-in audiences at the time you approve.">
      <AudiencePicker selected={audienceCodes} onChange={setAudienceCodes} />
      <RemoteList section="emails" />
      <ActionForm title="Schedule store email" kind="email" context={{ listing_ids: ids, audience_codes: audienceCodes }}
        disabled={!ids.length || ids.length > 10 || !audienceCodes.length}
        fields={[{ name: "subject", label: "Email subject", maxLength: 70, help: "Up to 70 characters." },
                 { name: "message", label: "Your message", type: "textarea" },
                 { name: "schedule", label: "Send at (your local time)", type: "datetime-local", initial: defaults.start }]}
        build={(values) => ({ ...values, schedule: iso(values.schedule) })} />
    </PageSection>
    </> : null}
  </WorkspaceTabs>;
}

function AudiencePicker({ selected, onChange }: { selected: string[]; onChange: (codes: string[]) => void }) {
  const [offset, setOffset] = useState(0);
  const audiences = useQuery({ queryKey: ["ebay", "audiences", offset], queryFn: () => api.ebay.read("audiences", { offset }), retry: false });
  return <fieldset className="ebay-selection"><legend>Email audiences</legend>
    <p className="ebay-selection-meta">{selected.length} selected. Choose who should receive this email.</p>
    {audiences.error ? <PageError message={audiences.error.message} /> : null}
    {audiences.isPending ? <p className="text-sm">Loading audiences…</p> : null}
    <div className="ebay-selection-list">{audiences.data?.rows.map((row) => <label key={row.id}>
      <input type="checkbox" checked={selected.includes(row.id)} onChange={(event) => onChange(event.target.checked ? [...selected, row.id] : selected.filter((id) => id !== row.id))} />{row.title}
    </label>)}</div>
    {offset > 0 || audiences.data?.nextOffset != null ? <div className="ebay-pagination"><Button variant="outline" disabled={!offset} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous</Button>
      <Button variant="outline" disabled={audiences.data?.nextOffset == null} onClick={() => setOffset(audiences.data!.nextOffset!)}>Next</Button></div> : null}
  </fieldset>;
}
