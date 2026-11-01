import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getAccountMove, startAccountMove, type AccountMoveResult } from "../api/accountMove";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogPanel,
  DialogPopup,
  DialogTitle,
} from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { useMacApp } from "../lib/macApp";

const plural = (count: number, word: string) => `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;

/** In the Mac app, opens the seller's account, moving this Mac's listings into it first. */
export function MacAccountButton() {
  const mac = useMacApp();
  const [open, setOpen] = useState(false);
  if (!mac) return null;
  return (
    <>
      <Button type="button" size="xs" variant="outline" onClick={() => setOpen(true)}
        title="See your listings in your account, the same as on the web and iPhone">
        Sign in
      </Button>
      {open && <MoveToAccountDialog onClose={() => setOpen(false)} />}
    </>
  );
}

function MoveToAccountDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const status = useQuery({
    queryKey: ["account-move"],
    queryFn: getAccountMove,
    refetchInterval: (query) => (query.state.data?.progress.state === "running" ? 1000 : false),
  });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const data = status.data;
  const openAccount = () => { if (data) window.location.assign(data.hosted_url); };

  async function move(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStarting(true); setError("");
    try {
      queryClient.setQueryData(["account-move"], await startAccountMove(email.trim(), password));
      setPassword("");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "Could not start the move.");
    } finally {
      setStarting(false);
    }
  }

  const progress = data?.progress;
  const running = progress?.state === "running";
  const result = progress?.state === "done" ? progress.result : undefined;
  return (
    <Dialog open onOpenChange={(next) => { if (!next && !running) onClose(); }}>
      <DialogPopup className="max-w-md">
        <DialogHeader>
          <DialogTitle>{result || data?.moved ? "This Mac is in your account" : "Move this Mac to your account"}</DialogTitle>
          <DialogDescription>
            Your account shows the same listings on the web, iPhone and this Mac.
          </DialogDescription>
        </DialogHeader>
        <DialogPanel className="space-y-4 text-sm">
          {!data ? (
            <p role={status.error ? "alert" : "status"}>{status.error ? (status.error as Error).message : "Checking this Mac’s listings…"}</p>
          ) : running ? (
            <MoveProgress step={progress?.step} done={progress?.done ?? 0} total={progress?.total ?? 0} />
          ) : result ? (
            <MoveSummary result={result} />
          ) : data.moved ? (
            <p>This Mac’s listings moved to {data.moved.email ?? "your account"}. Its own copy stays here as a backup.</p>
          ) : data.listings === 0 ? (
            <p>This Mac has no listings to move. Open your account to sign in.</p>
          ) : (
            <form id="move-to-account" className="space-y-3" onSubmit={(event) => void move(event)}>
              <p>
                Sign in to copy {plural(data.listings, "listing")} and {plural(data.photos, "photo")}, with their history,
                settings and provider keys, into your account. This Mac keeps its own copy as a backup.
              </p>
              {progress?.state === "failed" && <p role="alert" className="text-destructive-foreground">{progress.error}</p>}
              {error && <p role="alert" className="text-destructive-foreground">{error}</p>}
              <div className="space-y-1.5">
                <Label htmlFor="move-email">Email address</Label>
                <Input id="move-email" type="email" autoComplete="email" required value={email}
                  onChange={(event) => setEmail(event.target.value)} disabled={starting} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="move-password">Password</Label>
                <Input id="move-password" type="password" autoComplete="current-password" required value={password}
                  onChange={(event) => setPassword(event.target.value)} disabled={starting} />
              </div>
              <p className="text-muted-foreground">No password yet? Create an account or reset your password on the web first.</p>
            </form>
          )}
        </DialogPanel>
        <DialogFooter>
          {data && !running && !result && !data.moved && data.listings > 0 ? (
            <>
              <Button type="button" variant="ghost" onClick={openAccount}>Skip for now</Button>
              <Button type="submit" form="move-to-account" disabled={starting}>
                {starting ? "Starting…" : progress?.state === "failed" ? "Try again" : "Move to my account"}
              </Button>
            </>
          ) : (
            <>
              <Button type="button" variant="ghost" onClick={onClose} disabled={running}>Close</Button>
              <Button type="button" onClick={openAccount} disabled={!data || running}>Open my account</Button>
            </>
          )}
        </DialogFooter>
      </DialogPopup>
    </Dialog>
  );
}

function MoveProgress({ step, done, total }: { step?: string; done: number; total: number }) {
  return (
    <div className="space-y-2" role="status">
      <p>{step ?? "Moving…"}</p>
      {total > 0 && (
        <>
          <div role="progressbar" aria-label="Move progress" aria-valuemin={0} aria-valuemax={total} aria-valuenow={done}
            className="h-1.5 overflow-hidden rounded-full bg-muted">
            <div className="h-full bg-primary transition-[width]" style={{ width: `${Math.round((done / total) * 100)}%` }} />
          </div>
          <p className="text-muted-foreground">{done.toLocaleString()} of {total.toLocaleString()}</p>
        </>
      )}
      <p className="text-muted-foreground">Keep Anteroom open until this finishes. If it stops, run it again to pick up where it left off.</p>
    </div>
  );
}

function MoveSummary({ result }: { result: AccountMoveResult }) {
  return (
    <ul className="list-disc space-y-1.5 pl-5" aria-label="What moved">
      <li>Copied {plural(result.records, "record")} and {plural(result.photos, "photo")}.</li>
      {result.missing_photos > 0 && <li>{plural(result.missing_photos, "photo")} could not be found on this Mac and were left out.</li>}
      {result.keys.length > 0 && <li>Your provider keys are saved, encrypted, in your account.</li>}
      {result.paused_automations > 0 && <li>{plural(result.paused_automations, "automation")} now run from your account and are paused on this Mac.</li>}
      <li>Reconnect eBay and Etsy in your account under Settings → Connections, so sales arrive there.</li>
    </ul>
  );
}
