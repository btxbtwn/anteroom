import type { FillLogEntry } from "../../api/types";
import { marketplaceLabel } from "./marketplaces";

export interface DraftField {
  key: string;
  label: string;
  value: string;
  missing: boolean;
  /** Listing marked Does Not Apply / N/A — show yellow and never try to fill. */
  notApplicable?: boolean;
  leftover?: FillLogEntry;
  section?: string;
  /** Listing/chat overlay only — not confirmed empty on the live Vendoo draft. */
  listingOnly?: boolean;
}

export interface DraftForm {
  id: string;
  label: string;
  fields: DraftField[];
  filled: number;
  missing: number;
  notApplicable: number;
  liveStatus?: string;
}

export function toForm(id: string, fields: DraftField[], liveStatus?: string): DraftForm {
  return {
    id,
    label: marketplaceLabel(id),
    fields,
    filled: fields.filter((field) => !field.missing && !field.notApplicable).length,
    missing: fields.filter((field) => field.missing && !field.notApplicable).length,
    notApplicable: fields.filter((field) => field.notApplicable).length,
    liveStatus,
  };
}
