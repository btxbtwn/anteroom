import { useSyncExternalStore } from "react";
import type { NativeMarketplace } from "../api/nativeMarketplaces";
import { ApiRequestError } from "../api/transport";

/**
 * Seller-confirmed marketplace actions waiting for their marketplace to be free.
 *
 * Each marketplace runs one action at a time, so a List confirmed while Depop is
 * busy with another item waits its turn here instead of failing. Only the action
 * the seller confirmed is sent, once its marketplace is free. A refusal for any
 * other reason stops it; nothing is retried. The list lives in this page only:
 * closing or reloading the page drops everything still waiting.
 */

type WaitingKind = "list" | "update" | "save_draft" | "delist";

export type WaitingAction = {
  id: string;
  convId: string;
  title: string;
  marketplace: NativeMarketplace;
  kind: WaitingKind;
  revisionId: string;
  /** Its turn's request is in flight. */
  starting: boolean;
  /** Why it stopped when its turn came. A stopped action is kept until dismissed. */
  error: string | null;
};

const BUSY_REASON = "marketplace_busy";

/** The server refused only because the marketplace is busy with another action. */
export function isBusyRefusal(error: unknown): boolean {
  return error instanceof ApiRequestError && error.status === 409 && error.reason === BUSY_REASON;
}

type Send = (action: WaitingAction) => Promise<unknown>;
type Listener = { started?: (action: WaitingAction) => void; failed?: (action: WaitingAction) => void };

export class MarketplaceWaitlist {
  private actions: readonly WaitingAction[] = [];
  private subscribers = new Set<() => void>();
  private busy: (marketplace: NativeMarketplace) => boolean = () => false;
  private next = 0;
  /** Refused as busy since the last busy report: wait for the next one before asking again. */
  private deferred = new Set<string>();
  listener: Listener = {};

  constructor(private readonly send: Send) {}

  subscribe = (callback: () => void) => {
    this.subscribers.add(callback);
    return () => { this.subscribers.delete(callback); };
  };

  snapshot = () => this.actions;

  /** Hold a confirmed action until its marketplace is free. One per item and marketplace. */
  add(action: Omit<WaitingAction, "id" | "starting" | "error">): void {
    const rest = this.actions.filter((item) => !(item.convId === action.convId && item.marketplace === action.marketplace));
    this.set([...rest, { ...action, id: `wait-${++this.next}`, starting: false, error: null }]);
  }

  /** Drop a waiting action, or clear a stopped one. A request already sent is not recalled. */
  remove(id: string): void {
    this.set(this.actions.filter((item) => item.id !== id || item.starting));
  }

  /** Clear what a stopped action said, before the seller starts that marketplace again. */
  clearStopped(convId: string, marketplace: NativeMarketplace): void {
    this.set(this.actions.filter((item) => !(item.convId === convId && item.marketplace === marketplace && item.error)));
  }

  /** A fresh read of which marketplaces are busy; the first in line on each free one starts. */
  setBusy(busy: (marketplace: NativeMarketplace) => boolean): void {
    this.busy = busy;
    this.deferred.clear();
    this.tick();
  }

  /** Actions ahead of this one on its marketplace, the one running there included. */
  ahead(action: WaitingAction): number {
    const before = this.actions.filter((item) => item.marketplace === action.marketplace && !item.error);
    return before.indexOf(action) + (this.busy(action.marketplace) ? 1 : 0);
  }

  tick(): void {
    if (this.actions.some((action) => action.starting && action.kind === "delist")) return;
    const taken = new Set<NativeMarketplace>();
    for (const action of this.actions) {
      if (action.kind === "delist" && !action.error) {
        if (taken.size || this.actions.some((item) => item.starting) ||
            (["ebay", "etsy", "poshmark", "mercari", "depop"] as NativeMarketplace[]).some(this.busy)) return;
        if (!this.deferred.has(action.id)) void this.start(action);
        return;
      }
      if (action.error || taken.has(action.marketplace)) continue;
      taken.add(action.marketplace);
      if (!action.starting && !this.deferred.has(action.id) && !this.busy(action.marketplace)) void this.start(action);
    }
  }

  private async start(action: WaitingAction): Promise<void> {
    this.patch(action.id, { starting: true });
    try {
      await this.send(action);
      this.set(this.actions.filter((item) => item.id !== action.id));
      this.listener.started?.(action);
    } catch (error) {
      if (isBusyRefusal(error)) {
        // Another action took the marketplace first: keep the place in line
        // and ask again only after the next read of what is running.
        this.deferred.add(action.id);
        this.patch(action.id, { starting: false });
        return;
      }
      const stopped = { ...action, starting: false, error: error instanceof Error ? error.message : "The action could not start." };
      this.patch(action.id, stopped);
      this.listener.failed?.(stopped);
    }
  }

  private patch(id: string, values: Partial<WaitingAction>): void {
    this.set(this.actions.map((item) => item.id === id ? { ...item, ...values } : item));
  }

  private set(actions: readonly WaitingAction[]): void {
    this.actions = actions;
    for (const callback of this.subscribers) callback();
  }
}

/** Steps a job runs on one marketplace only, beside other marketplaces' (see `blocking_job`). */
const OWN_STEP = /^native_(ebay|etsy|poshmark|mercari|depop)_/;

/**
 * Whether open jobs keep a marketplace busy. A listing step holds its own
 * marketplace; anything else (a delist, an inspection) holds them all. A guess
 * that is wrong only costs a busy refusal, which keeps the action waiting.
 */
export function busyFromJobs(jobs: readonly { current_step?: string | null }[]): (marketplace: NativeMarketplace) => boolean {
  const held = new Set<string>();
  let all = false;
  for (const job of jobs) {
    const step = job.current_step ?? "";
    const own = OWN_STEP.exec(step);
    if (own) held.add(own[1]);
    else all = true;
  }
  return (marketplace) => all || held.has(marketplace);
}

export function useWaitlist(waitlist: MarketplaceWaitlist): readonly WaitingAction[] {
  return useSyncExternalStore(waitlist.subscribe, waitlist.snapshot, waitlist.snapshot);
}
