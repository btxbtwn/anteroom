import { createContext, useContext, useState, type ReactNode } from "react";

const DraftsContext = createContext<Map<string, unknown> | null>(null);

/** Unsaved fields stay in memory for this account connection, never on disk. */
export function FormDraftProvider({ children }: { children: ReactNode }) {
  const [drafts] = useState(() => new Map<string, unknown>());
  return <DraftsContext value={drafts}>{children}</DraftsContext>;
}

export function useFormDraft<T>(key: string, initial: () => T) {
  const drafts = useContext(DraftsContext);
  const [values, setValues] = useState<T>(() => drafts?.has(key) ? drafts.get(key) as T : initial());
  return [values, (next: T | ((current: T) => T)) => setValues((current) => {
    const value = typeof next === "function" ? (next as (current: T) => T)(current) : next;
    drafts?.set(key, value);
    return value;
  })] as const;
}
