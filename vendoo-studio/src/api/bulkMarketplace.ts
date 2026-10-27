import type { DelistMarketplace } from "./marketplaceListings";

export type BulkMarketplaceAction = "list" | "delist" | "relist" | "update" | "not_listed";
export interface BulkMarketplaceRow {
  conversation_id: string;
  title: string | null;
  action: BulkMarketplaceAction;
  revision_id: string | null;
  marketplaces: DelistMarketplace[];
  skipped: { marketplace: DelistMarketplace; reason: string }[];
  skip_reason: string | null;
  review_token: string | null;
}
export interface BulkMarketplacePreview { action: BulkMarketplaceAction; marketplaces: DelistMarketplace[]; rows: BulkMarketplaceRow[] }
export interface BulkMarketplacePerform {
  conversation_id: string;
  action: BulkMarketplaceAction;
  marketplaces: DelistMarketplace[];
  revision_id: string | null;
  review_token: string;
  confirm: true;
}
export interface BulkMarketplaceResult {
  conversation_id: string;
  action: BulkMarketplaceAction;
  marketplaces: DelistMarketplace[];
  ok: boolean;
  state: string | null;
  error: string | null;
  errors: Partial<Record<DelistMarketplace, string>>;
  down: DelistMarketplace[];
}
