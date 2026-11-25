import { useSyncExternalStore } from "react";
import { CloudOffIcon } from "lucide-react";
import { reachability } from "../api/databaseReachability";
import { Alert, AlertDescription, AlertTitle } from "./ui/alert";

export const UNREACHABLE_TITLE = "Can't reach your data right now — retrying";
export const UNREACHABLE_DETAIL = "Anteroom's database isn't answering. This clears and reloads on its own once it's back.";

/** Shown over the whole hosted app while `/api/ready` fails; only a passing check hides it. */
export function DatabaseUnreachableBanner({ store = reachability }: { store?: Pick<typeof reachability, "get" | "subscribe"> }) {
  const state = useSyncExternalStore(store.subscribe, store.get, store.get);
  return (
    <div role="status" aria-live="polite" aria-atomic="true"
      className="pointer-events-none fixed inset-x-0 top-[calc(env(safe-area-inset-top)+4rem)] z-50 flex justify-center px-4 sm:top-12">
      {state === "unreachable" && (
        // Opaque backing: the warning surface is a tint and the page must not show through.
        <div className="pointer-events-auto w-full max-w-lg rounded-xl bg-background shadow-sm">
          <Alert variant="warning" role="presentation">
            <CloudOffIcon aria-hidden="true" />
            <AlertTitle>{UNREACHABLE_TITLE}</AlertTitle>
            <AlertDescription>{UNREACHABLE_DETAIL}</AlertDescription>
          </Alert>
        </div>
      )}
    </div>
  );
}
