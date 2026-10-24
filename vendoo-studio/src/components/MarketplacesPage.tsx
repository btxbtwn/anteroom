import { useState } from "react";
import { Tabs } from "@base-ui/react/tabs";
import { AdSpend } from "./AdSpend";
import { MarketplaceLogo } from "./MarketplaceLogo";
import { SaleCalendar } from "./SaleCalendar";
import { marketplaceName } from "./marketplaceNames";
import { EtsyWorkspace } from "./etsy/EtsyWorkspace";
import { EbayWorkspace } from "./ebay/EbayWorkspace";
import { PageHeader, WorkspacePage } from "./WorkspacePage";

const MARKETPLACES = ["ebay", "etsy", "poshmark", "depop", "sellwild"] as const;
type Marketplace = (typeof MARKETPLACES)[number];

export function MarketplacesPage({ onOpenListing }: { onOpenListing: (id: string) => void }) {
  const [marketplace, setMarketplace] = useState<Marketplace>("ebay");
  return (
    <WorkspacePage width="expanded">
      <PageHeader title="Marketplaces" description="Manage each marketplace’s listings, sales, and marketing in one place." />
      <Tabs.Root value={marketplace} onValueChange={(value) => setMarketplace(value as Marketplace)}>
        <Tabs.List aria-label="Marketplaces" className="mb-7 flex flex-wrap gap-1 border-b border-border">
          {MARKETPLACES.map((id) => <Tabs.Tab key={id} value={id}
            className="flex min-h-11 cursor-pointer items-center gap-2 rounded-t-md border-b-2 border-transparent px-3 py-2 text-sm text-muted-foreground outline-none hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring data-[active]:border-primary data-[active]:text-foreground">
            <MarketplaceLogo id={id} label={marketplaceName(id)} size={16} />{marketplaceName(id)}
          </Tabs.Tab>)}
        </Tabs.List>
        <Tabs.Panel value={marketplace}>
          <MarketplacePanel key={marketplace} marketplace={marketplace} onOpenListing={onOpenListing} />
        </Tabs.Panel>
      </Tabs.Root>
    </WorkspacePage>
  );
}

export function MarketplacePanel({ marketplace, onOpenListing }: {
  marketplace: Marketplace;
  onOpenListing: (id: string) => void;
}) {
  if (marketplace === "ebay") return <EbayWorkspace onOpenListing={onOpenListing} />;
  if (marketplace === "etsy") return <EtsyWorkspace onOpenListing={onOpenListing} />;
  return (
    <div className="flex flex-col gap-8">
      <h2 className="text-lg font-semibold">{marketplaceName(marketplace)}</h2>
      {marketplace !== "poshmark" ? <SaleCalendar marketplace={marketplace} onOpenListing={onOpenListing} /> : null}
      {marketplace === "poshmark" ? <AdSpend marketplace={marketplace} /> : null}
    </div>
  );
}
