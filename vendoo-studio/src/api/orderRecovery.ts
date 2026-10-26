import type { DirectMarketplace } from "./directConnections";

export interface OrderRecoveryRequest {
  marketplace: DirectMarketplace;
  start: string;
  end: string;
  page: number;
  connected_at: string;
  connection?: string;
  through?: string;
}
export interface OrderRecoveryResult {
  marketplace: DirectMarketplace;
  connection: string;
  through: string;
  page: number;
  nextPage: number | null;
  ordersScanned: number;
  skippedOrders: number;
  matched: number;
  recorded: number;
  updated: number;
  unchanged: number;
  conflicts: number;
  truncated: boolean;
}
