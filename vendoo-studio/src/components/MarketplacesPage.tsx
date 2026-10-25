import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import { FormDraftProvider } from "./ebay/FormDrafts";
import { Tabs } from "@base-ui/react/tabs";
import { AdSpend } from "./AdSpend";
import { MarketplaceLogo } from "./MarketplaceLogo";
import { SaleCalendar } from "./SaleCalendar";
import { marketplaceName } from "./marketplaceNames";
import { EtsyWorkspace } from "./etsy/EtsyWorkspace";
import { EbayWorkspace } from "./ebay/EbayWorkspace";
import { PageHeader, WorkspacePage } from "./WorkspacePage";

const MARKETPLACES = ["ebay", "etsy", "poshmark", "depop", "sellwild"] as const;
export type Marketplace = (typeof MARKETPLACES)[number];

export function MarketplacesPage({ marketplace, onMarketplaceChange, onOpenListing }: {
  marketplace: Marketplace;
  onMarketplaceChange: (marketplace: Marketplace) => void;
  onOpenListing: (id: string) => void;
}) {
  const connection = useQuery({ queryKey: ["direct-connections"], queryFn: api.directConnections.status,
    refetchInterval: (query) => Object.values(query.state.data?.accounts || {}).some((account) => account.pending) ? 2000 : 15000 });
  const ebay = connection.data?.accounts.ebay;
  return (
    <FormDraftProvider key={`${ebay?.label}-${ebay?.connectedAt}`}><WorkspacePage width="expanded">
      <PageHeader title="Marketplaces" description="Manage each marketplace’s listings, sales, and marketing in one place." />
      <Tabs.Root value={marketplace} onValueChange={(value) => onMarketplaceChange(value as Marketplace)}>
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
    </WorkspacePage></FormDraftProvider>
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
