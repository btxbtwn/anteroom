import { useId } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftIcon, ArrowRightIcon, ExternalLinkIcon } from "lucide-react";
import { api } from "../api/client";
import type { DirectAccountStatus, DirectMarketplace } from "../api/directConnections";
import { MarketplaceLogo } from "./MarketplaceLogo";
import { PageError, PageHeader, PageNote, PageSection, Panel, ListRow, WorkspacePage } from "./WorkspacePage";
import { Button, buttonVariants } from "./ui/button";
import { EbayPage } from "./ebay/EbayPage";
import { FormDraftProvider } from "./ebay/FormDrafts";

const CHANNELS = [
  { id: "ebay", name: "eBay", description: "Orders, buyer messages, listings, offers, and performance." },
  { id: "etsy", name: "Etsy", description: "Your shop connection, new sale alerts, and Etsy listings." },
] as const;

export function MarketplacePage({ selected, onSelect, onOpenListing, onOpenConnections }: {
  selected: DirectMarketplace | null;
  onSelect: (marketplace: DirectMarketplace | null) => void;
  onOpenListing: (id: string) => void;
  onOpenConnections: () => void;
}) {
  const choiceId = useId();
  const connections = useQuery({ queryKey: ["direct-connections"], queryFn: api.directConnections.status,
    refetchInterval: (query) => Object.values(query.state.data?.accounts || {}).some((account) => account.pending) ? 2000 : 15000 });
  const ebay = connections.data?.accounts.ebay;
  return <FormDraftProvider key={`${ebay?.label}-${ebay?.connectedAt}`}>
    {selected === null ? <WorkspacePage>
      <PageHeader title="Marketplaces" description="Choose a sales channel to manage your shop." />
      {connections.error ? <PageError message={connections.error.message} /> : null}
      <div className="grid gap-4 sm:grid-cols-2">
        {CHANNELS.map((channel) => {
          const account = connections.data?.accounts[channel.id];
          return <button key={channel.id} type="button" aria-label={`Open ${channel.name}`} onClick={() => onSelect(channel.id)}
            className="group flex min-w-0 flex-col gap-5 rounded-xl border border-border/60 bg-card/40 p-6 text-left transition-colors hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <span className="flex w-full items-center justify-between gap-3"><MarketplaceLogo id={channel.id} label={channel.name} size={36} />
              <ArrowRightIcon className="size-4 text-muted-foreground" aria-hidden="true" /></span>
            <span><span className="block text-xl font-semibold tracking-tight">{channel.name}</span>
              <span className="mt-2 block text-sm leading-relaxed text-muted-foreground">{channel.description}</span></span>
            <span className="max-w-full break-words text-xs text-muted-foreground">{connections.error ? "Connection unavailable" : connections.isPending ? "Checking connection…"
              : account?.connected ? `Connected · ${account.label || channel.name}` : account?.pending ? "Sign-in in progress" : "Not connected"}</span>
          </button>;
        })}
      </div>
    </WorkspacePage> : <div className="flex min-h-0 flex-1 flex-col">
      <nav aria-label="Marketplace navigation" className="flex shrink-0 flex-wrap items-center gap-3 border-b border-border/60 px-5 py-2 sm:px-6">
        <Button variant="ghost" onClick={() => onSelect(null)}><ArrowLeftIcon />Marketplaces</Button>
        <label className="sr-only" htmlFor={choiceId}>Marketplace</label>
        <select id={choiceId} value={selected} onChange={(event) => onSelect(event.target.value as DirectMarketplace)}
          className="h-10 min-w-0 rounded-md border border-input bg-background px-3 text-base outline-none focus-visible:ring-2 focus-visible:ring-ring sm:text-sm">
          {CHANNELS.map((channel) => <option key={channel.id} value={channel.id}>{channel.name}</option>)}
        </select>
      </nav>
      {selected === "etsy" && connections.error ? <div className="px-5 pt-4"><PageError message={connections.error.message} /></div> : null}
      {selected === "ebay" ? <EbayPage /> : <EtsyPage account={connections.data?.accounts.etsy} onOpenListing={onOpenListing} onOpenConnections={onOpenConnections} />}
    </div>}
  </FormDraftProvider>;
}

export function EtsyPage({ account, onOpenListing, onOpenConnections }: {
  account?: DirectAccountStatus;
  onOpenListing: (id: string) => void;
  onOpenConnections: () => void;
}) {
  const queryClient = useQueryClient();
  const refreshConnection = () => { void queryClient.invalidateQueries({ queryKey: ["direct-connections"] }); };
  const connect = useMutation({ mutationFn: () => api.directConnections.connect("etsy"), retry: false, onSuccess: refreshConnection });
  const cancel = useMutation({ mutationFn: () => api.directConnections.cancel("etsy"), retry: false, onSuccess: refreshConnection });
  const sales = useQuery({ queryKey: ["marketplace-sales", "new"], queryFn: () => api.marketplaceSales.list(), enabled: Boolean(account?.connected) });
  const etsySales = sales.data?.sales.filter((sale) => sale.marketplace === "etsy") || [];
  return <WorkspacePage>
    <PageHeader title={<span className="flex items-center gap-3"><MarketplaceLogo id="etsy" label="Etsy" size={28} />Etsy</span>}
      description={account?.connected ? `Connected to ${account.label || "your Etsy shop"}.` : !account ? "Checking your shop connection…" : "Connect your Etsy shop to receive sale alerts in Anteroom."}
      actions={account?.connected ? <Button variant="outline" onClick={onOpenConnections}>Connection</Button>
        : <Button disabled={!account || connect.isPending || account.pending} onClick={() => connect.mutate()}>Connect Etsy</Button>} />
    {connect.error || cancel.error || account?.error ? <PageError message={connect.error?.message || cancel.error?.message || account?.error || ""} /> : null}
    {account?.pending ? <div className="space-y-3"><PageNote>Finish Etsy sign-in in your browser. {account.authorizeUrl
      ? <a className="underline" href={account.authorizeUrl} target="_blank" rel="noreferrer">Open Etsy sign-in</a> : null}</PageNote>
      <Button variant="outline" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel sign-in</Button></div> : null}
    <PageSection title="Shop Manager" description="Manage and review your Etsy listings in Etsy.">
      <div><a className={buttonVariants({ variant: "outline" })} href="https://www.etsy.com/your/shops/me/tools/listings" target="_blank" rel="noreferrer">Open Etsy listings<ExternalLinkIcon aria-hidden="true" /></a></div>
    </PageSection>
    {account?.connected ? <PageSection title="New Etsy sales" description="Open a matched item to review its other marketplace listings. You choose any delisting separately."
      actions={<Button variant="outline" disabled={sales.isFetching} onClick={() => { void sales.refetch(); }}>Refresh</Button>}>
      {sales.error ? <PageError message={sales.error.message} /> : sales.isPending ? <PageNote>Loading Etsy sales…</PageNote>
        : etsySales.length ? <Panel><ul>{etsySales.map((sale) => <ListRow key={sale.id} title={sale.title || "Etsy sale"}
          meta={sale.listedElsewhere.length ? "Review other marketplaces" : "Sale received"}
          valueHint={sale.conversationId ? "Open item" : "Unmatched item"}
          onClick={sale.conversationId ? () => onOpenListing(sale.conversationId!) : undefined} />)}</ul></Panel>
          : <PageNote>No new Etsy sale alerts.</PageNote>}
    </PageSection> : null}
  </WorkspacePage>;
}
