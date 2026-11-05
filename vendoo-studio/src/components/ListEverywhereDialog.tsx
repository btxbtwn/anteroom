import { useState } from "react";
import type { NativeMarketplace } from "../api/nativeMarketplaces";
import { cn } from "../lib/utils";
import { MarketplaceLogo } from "./MarketplaceLogo";
import { marketplaceName } from "./marketplaceNames";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Dialog, DialogDescription, DialogFooter, DialogHeader, DialogPanel, DialogPopup, DialogTitle } from "./ui/dialog";

/** One marketplace card in the picker: its state, and whether the seller can pick it. */
export interface ListChoice {
  marketplace: NativeMarketplace;
  label: string;
  tone: "ready" | "needs" | "listed" | "sold" | "muted";
  selectable: boolean;
}

const BADGE: Record<ListChoice["tone"], "success" | "warning" | "secondary" | "info" | "outline"> = {
  ready: "success", needs: "warning", listed: "secondary", sold: "info", muted: "outline",
};

/** The marketplaces "Select all ready" picks: selectable ones with every required field filled. */
export function readyMarketplaces(choices: readonly ListChoice[]): NativeMarketplace[] {
  return choices.filter((choice) => choice.selectable && choice.tone === "ready").map((choice) => choice.marketplace);
}

/**
 * List everywhere's picker. Nothing starts selected and nothing is sent until the
 * seller presses List, which names the marketplaces chosen.
 */
export function ListEverywhereDialog({ title, choices, onConfirm, onClose }: {
  title: string;
  choices: readonly ListChoice[];
  onConfirm: (marketplaces: NativeMarketplace[]) => void;
  onClose: () => void;
}) {
  const [picked, setPicked] = useState<ReadonlySet<NativeMarketplace>>(new Set());
  const selectable = choices.filter((choice) => choice.selectable);
  // A marketplace that stops being selectable while the dialog is open drops out.
  const chosen = selectable.filter((choice) => picked.has(choice.marketplace)).map((choice) => choice.marketplace);
  const ready = readyMarketplaces(choices);
  const toggle = (marketplace: NativeMarketplace, on: boolean) => setPicked((current) => {
    const next = new Set(current);
    if (on) next.add(marketplace);
    else next.delete(marketplace);
    return next;
  });
  return (
    <Dialog open onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogPopup className="max-w-xl">
        <DialogHeader>
          <DialogTitle>List “{title}”</DialogTitle>
          <DialogDescription>Each marketplace you pick gets a live listing from what's saved in Anteroom.</DialogDescription>
        </DialogHeader>
        <DialogPanel className="space-y-3 text-sm">
          <div className="flex items-center gap-2">
            <Button size="xs" variant="outline" disabled={!ready.length}
              onClick={() => setPicked(new Set(ready))}>Select all ready</Button>
            {chosen.length ? <Button size="xs" variant="ghost-muted" onClick={() => setPicked(new Set())}>Clear</Button> : null}
            <span role="status" className="ms-auto text-muted-foreground text-xs tabular-nums">
              {chosen.length} of {selectable.length} selected
            </span>
          </div>
          <ul aria-label="Marketplaces" className="grid gap-2 sm:grid-cols-2">
            {choices.map((choice) => {
              const name = marketplaceName(choice.marketplace);
              const checked = chosen.includes(choice.marketplace);
              return (
                <li key={choice.marketplace}>
                  <label className={cn("flex min-h-16 items-center gap-3 rounded-xl border border-border/60 bg-card/40 p-3 transition-colors",
                    choice.selectable ? "cursor-pointer hover:bg-accent" : "opacity-64",
                    checked && "border-foreground/30 bg-accent")}>
                    <span className="flex size-8 shrink-0 items-center justify-center rounded-full border border-border/60 bg-background">
                      <MarketplaceLogo id={choice.marketplace} label={name} size={18} />
                    </span>
                    <span className="flex min-w-0 flex-1 flex-col items-start gap-1">
                      <span className="truncate font-medium text-foreground">{name}</span>
                      <Badge variant={BADGE[choice.tone]} size="sm">{choice.label}</Badge>
                    </span>
                    <Checkbox aria-label={`List on ${name}`} checked={checked} disabled={!choice.selectable}
                      onCheckedChange={(value) => toggle(choice.marketplace, value === true)} />
                  </label>
                </li>
              );
            })}
          </ul>
        </DialogPanel>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button disabled={!chosen.length} title={chosen.length ? `List on ${chosen.map(marketplaceName).join(", ")}` : undefined}
            onClick={() => { onConfirm(chosen); onClose(); }}>
            {chosen.length === 1 ? `List on ${marketplaceName(chosen[0])}` : chosen.length ? `List on ${chosen.length}` : "List"}
          </Button>
        </DialogFooter>
      </DialogPopup>
    </Dialog>
  );
}
