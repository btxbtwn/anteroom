import type { AssistantAutomation } from "./automations";
import type { ChatAttachment } from "./types";

export type { AssistantAutomation };

/** Query keys for what the seller told the assistant and what it remembers (Settings → Assistant). */
export const ASSISTANT_NOTE_KEY = ["assistant", "note"] as const;
export const ASSISTANT_MEMORY_KEY = ["assistant", "memory"] as const;

/** One fact the assistant keeps about the shop, across every conversation. */
export interface AssistantMemory {
  id: string;
  text: string;
  updated_at: string | null;
}

/** One conversation with the business assistant, apart from any listing's chat. */
export interface AssistantThread {
  id: string;
  title: string;
  created_at: string | null;
  updated_at: string | null;
}

/** One turn of the seller's conversation with the business assistant. */
export interface AssistantMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  created_at: string | null;
  /** Changes this answer proposed, and what the seller did with them. */
  changes?: AssistantChanges | null;
  /** An automation this answer drafted, and whether the seller confirmed it. */
  automation?: AssistantAutomation | null;
  /** Images attached to a question. */
  attachments?: ChatAttachment[];
}

/**
 * One value that saving a proposal would change on a listing, before and after.
 * A new size reaches every marketplace form, so each form's size is its own change.
 */
export interface AssistantFieldChange {
  field:
    | "asking" | "cost" | "title" | "description" | "brand" | "size" | "sku" | "labels" | "box"
    | "ebay_size" | "poshmark_size" | "mercari_size" | "depop_size" | "etsy_size";
  old: string | number | string[] | null;
  new: string | number | string[] | null;
}

export interface AssistantListingChange {
  listing_id: string;
  title: string;
  fields: AssistantFieldChange[];
  /** Why this listing was left alone when the rest were applied. */
  skipped?: string;
}

/** Nothing in a proposal is written until the seller applies it. */
export interface AssistantChanges {
  status: "proposed" | "applied" | "dismissed";
  items: AssistantListingChange[];
}

export interface AssistantStreamHandlers {
  /** One stream event as it arrives: `message` (the next piece of the answer),
   *  `status`, `thinking`, `step`, `agent`; `error` is returned instead. */
  onEvent: (event: string, data: string) => void;
}

/** Studio answered the request and refused it; a dropped connection is any other error. */
export class AssistantRefused extends Error {}
