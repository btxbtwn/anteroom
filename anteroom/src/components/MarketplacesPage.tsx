import { useQuery } from "@tanstack/react-query";
import { directConnectionsQuery } from "../api/pageQueries";
import { BUSY_POLL_MS, pollMs } from "../api/polling";
import { FormDraftProvider } from "./ebay/FormDrafts";
import { Tabs } from "@base-ui/react/tabs";
import { ChannelWorkspace } from "./channel/ChannelWorkspace";
import { MarketplaceLogo } from "./MarketplaceLogo";
import { marketplaceName } from "./marketplaceNames";
import { EtsyWorkspace } from "./etsy/EtsyWorkspace";
import { EbayWorkspace } from "./ebay/EbayWorkspace";
import { WorkspacePage } from "./WorkspacePage";
import "./ebay/ebay.css";

const MARKETPLACES = ["ebay", "etsy", "poshmark", "mercari", "depop"] as const;
export type Marketplace = (typeof MARKETPLACES)[number];

export function MarketplacesPage({ marketplace, onMarketplaceChange, onOpenListing }: {
  marketplace: Marketplace;
  onMarketplaceChange: (marketplace: Marketplace) => void;
  onOpenListing: (id: string) => void;
}) {
  const connection = useQuery({ ...directConnectionsQuery,
    refetchInterval: (query) => pollMs(Object.values(query.state.data?.accounts || {}).some((account) => account.pending) ? BUSY_POLL_MS : 15000) });
  const ebay = connection.data?.accounts.ebay;
  return (
    <FormDraftProvider key={`${ebay?.label}-${ebay?.connectedAt}`}><WorkspacePage width="expanded">
      <h1 className="sr-only">Marketplaces</h1>
      <Tabs.Root value={marketplace} onValueChange={(value) => onMarketplaceChange(value as Marketplace)}>
        <Tabs.List aria-label="Marketplaces" className="ebay-navigation">
          {MARKETPLACES.map((id) => <Tabs.Tab key={id} value={id}>
            <MarketplaceLogo id={id} label={marketplaceName(id)} size={14} />{marketplaceName(id)}
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
  return <ChannelWorkspace marketplace={marketplace} onOpenListing={onOpenListing} />;
}
