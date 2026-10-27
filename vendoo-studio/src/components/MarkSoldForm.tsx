import { useState } from "react";
import type { SaleMarketplace, SellerSale } from "../api/marketplaceListings";
import { marketplaceName } from "./marketplaceNames";
import { Button } from "./ui/button";
import { Input } from "./ui/input";
import { Select, SelectItem, SelectPopup, SelectTrigger, SelectValue } from "./ui/select";

export const SALE_MARKETPLACES: SaleMarketplace[] = ["ebay", "etsy", "poshmark", "mercari", "depop", "sellwild", "vinted", "facebook", "other"];
const MONEY = /^\d{1,12}(\.\d{1,4})?$/;

function today(): string {
  return new Date().toISOString().slice(0, 10);
}

function newRequestId(): string {
  return crypto.randomUUID().replace(/-/g, "");
}

/** The amounts as the API takes them: blank stays unknown, anything else must be a plain amount. */
export function saleBody(fields: { marketplace: SaleMarketplace; soldOn: string; quantity: string; price: string; shippingCharged: string;
  fees: string; shippingCost: string }, requestId: string): SellerSale | string {
  const money: Record<string, string> = { item_revenue: fields.price, shipping_credit: fields.shippingCharged, fees: fields.fees, shipping_cost: fields.shippingCost };
  const amounts: Record<string, string | null> = {};
  for (const [key, raw] of Object.entries(money)) {
    const value = raw.trim().replace(/^\$/, "");
    if (value && !MONEY.test(value)) return "Enter amounts like 24.50, or leave them blank.";
    amounts[key] = value || null;
  }
  const quantity = Number(fields.quantity);
  if (!Number.isInteger(quantity) || quantity < 1) return "Enter how many sold, 1 or more.";
  if (!fields.soldOn || fields.soldOn > today()) return "Choose the day it sold, today or earlier.";
  return { marketplace: fields.marketplace, sold_on: fields.soldOn, quantity, currency: "USD", ...amounts, request_id: requestId };
}

/**
 * Mark as sold: where it sold, when, and what it brought in. Saving records
 * the sale only; any delist is offered afterwards and needs its own confirm.
 */
export function MarkSoldForm({ initial, pending, error, onSubmit, onCancel }: {
  initial: SaleMarketplace;
  pending: boolean;
  error?: string | null;
  onSubmit: (sale: SellerSale) => void;
  onCancel: () => void;
}) {
  const [requestId] = useState(newRequestId);
  const [fields, setFields] = useState({ marketplace: initial, soldOn: today(), quantity: "1", price: "", shippingCharged: "", fees: "", shippingCost: "" });
  const [problem, setProblem] = useState<string | null>(null);
  const set = (key: keyof typeof fields) => (event: { target: { value: string } }) => setFields({ ...fields, [key]: event.target.value });
  return (
    <form aria-label="Mark as sold" className="flex flex-col gap-2 rounded-lg bg-muted/40 px-2.5 py-2 text-xs" onSubmit={(event) => {
      event.preventDefault();
      const body = saleBody(fields, requestId);
      if (typeof body === "string") { setProblem(body); return; }
      setProblem(null);
      onSubmit(body);
    }}>
      <p className="font-medium text-foreground">Mark as sold</p>
      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1">Sold on
          <Select value={fields.marketplace} onValueChange={(value) => { if (value) setFields({ ...fields, marketplace: value as SaleMarketplace }); }}>
            <SelectTrigger aria-label="Where it sold" size="compact"><SelectValue>{marketplaceName(fields.marketplace)}</SelectValue></SelectTrigger>
            <SelectPopup>{SALE_MARKETPLACES.map((market) => <SelectItem key={market} value={market}>{marketplaceName(market)}</SelectItem>)}</SelectPopup>
          </Select>
        </label>
        <label className="flex flex-col gap-1">Date
          <Input nativeInput size="compact" type="date" required max={today()} value={fields.soldOn} onChange={set("soldOn")} />
        </label>
        <label className="flex flex-col gap-1">Sale price
          <Input size="compact" inputMode="decimal" placeholder="0.00" value={fields.price} onChange={set("price")} />
        </label>
        <label className="flex flex-col gap-1">Quantity
          <Input size="compact" inputMode="numeric" value={fields.quantity} onChange={set("quantity")} />
        </label>
        <label className="flex flex-col gap-1">Shipping the buyer paid
          <Input size="compact" inputMode="decimal" placeholder="0.00" value={fields.shippingCharged} onChange={set("shippingCharged")} />
        </label>
        <label className="flex flex-col gap-1">Fees
          <Input size="compact" inputMode="decimal" placeholder="0.00" value={fields.fees} onChange={set("fees")} />
        </label>
        <label className="flex flex-col gap-1">Shipping you paid
          <Input size="compact" inputMode="decimal" placeholder="0.00" value={fields.shippingCost} onChange={set("shippingCost")} />
        </label>
      </div>
      <p className="text-muted-foreground">Leave an amount blank if you don't know it. Nothing changes on any marketplace.</p>
      {(problem || error) && <p role="alert" className="text-destructive-foreground">{problem || error}</p>}
      <div className="flex items-center gap-2">
        <Button type="submit" size="xs" variant="outline" disabled={pending}>{pending ? "Saving…" : "Save sale"}</Button>
        <Button type="button" size="xs" variant="ghost" disabled={pending} onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}
