import { useMutation } from "@tanstack/react-query";

import { api } from "../api/client";
import type { AssistantAutomation, AssistantMessage } from "../api/assistant";
import { describeActions, describeSchedule } from "./automationsFormat";
import { Button } from "./ui/button";

/** What the card says before and after the seller confirms the draft. */
export function automationHeading(automation: AssistantAutomation): string {
  const name = automation.proposal?.name || "Automation";
  if (automation.status === "dismissed") return `Dismissed “${name}”. Nothing was saved.`;
  if (automation.status === "confirmed") return `Confirmed “${name}”. It can run on the schedule you set.`;
  return `Review “${name}” before it is saved`;
}

interface Props {
  message: AssistantMessage;
  automation: AssistantAutomation;
  onSettled: (message: AssistantMessage) => void;
}

/** A task the assistant drafted. Marketplace actions wait until Confirm. */
export function AssistantAutomationCard({ message, automation, onSettled }: Props) {
  const proposal = automation.proposal;
  const confirm = useMutation({
    mutationFn: () => api.assistant.confirmAutomation(message.id),
    onSuccess: (saved) => onSettled(saved),
  });
  const dismiss = useMutation({
    mutationFn: () => api.assistant.dismissAutomation(message.id),
    onSuccess: (saved) => onSettled(saved),
  });
  const busy = confirm.isPending || dismiss.isPending;
  const failure = (confirm.isError ? confirm.error : dismiss.isError ? dismiss.error : null) as Error | null;

  return (
    <section className="mt-3 rounded-xl border border-border bg-card p-3" aria-label="Automation the assistant drafted">
      <h2 className="font-medium text-sm">{automationHeading(automation)}</h2>
      {proposal ? (
        <p className="mt-1 text-muted-foreground text-sm">
          {describeSchedule(proposal.schedule, proposal.timezone || "UTC")}
          {" · "}
          {describeActions(proposal.actions || [])}
          {proposal.marketplaces?.length ? ` · ${proposal.marketplaces.join(", ")}` : " · no marketplaces named"}
        </p>
      ) : null}
      {proposal?.issues?.length ? (
        <ul className="mt-2 list-disc pl-5 text-muted-foreground text-sm">
          {proposal.issues.map((issue: string) => <li key={issue}>{issue}</li>)}
        </ul>
      ) : null}
      {automation.status === "proposed" ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" disabled={busy || proposal?.ready === false} onClick={() => confirm.mutate()}>Confirm</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => dismiss.mutate()}>Dismiss</Button>
        </div>
      ) : null}
      {failure ? <p className="mt-2 text-destructive-foreground text-sm">{failure.message}</p> : null}
    </section>
  );
}
