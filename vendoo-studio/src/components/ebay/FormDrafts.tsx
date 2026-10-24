import { createContext, useContext, useState, type ReactNode } from "react";

const DraftsContext = createContext<Map<string, Record<string, string>> | null>(null);

/** Unsaved fields stay in memory for this account connection, never on disk. */
export function FormDraftProvider({ children }: { children: ReactNode }) {
  const [drafts] = useState(() => new Map<string, Record<string, string>>());
  return <DraftsContext value={drafts}>{children}</DraftsContext>;
}

export function useFormDraft(key: string, initial: () => Record<string, string>) {
  const drafts = useContext(DraftsContext);
  const [values, setValues] = useState(() => drafts?.get(key) || initial());
  return [values, (next: Record<string, string>) => { drafts?.set(key, next); setValues(next); }] as const;
}
