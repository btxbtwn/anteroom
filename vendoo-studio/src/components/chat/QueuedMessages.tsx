import { XIcon } from "lucide-react";
import { type QueuedChatMessage } from "../chatMessageQueue";
import { Button } from "../ui/button";
import { UserMessageRow } from "./TimelineRows";

/** Follow-ups held until the running turn finishes: dashed bubbles, not sent yet. */
export function QueuedMessages({ queue, onRemove }: { queue: QueuedChatMessage[]; onRemove: (id: string) => void }) {
  return (
    <>
      {queue.map((queued, index) => (
        <div key={queued.id} className="pb-4" data-queued-message-id={queued.id}>
          <UserMessageRow
            queued
            text={queued.text || (queued.browserFields?.length
              ? `${queued.browserFields.length} field${queued.browserFields.length === 1 ? "" : "s"} from the browser`
              : "Empty message")}
            footer={
              <div className="mt-1.5 flex items-center gap-2">
                <span
                  className="text-muted-foreground text-xs"
                  title={index === 0 ? "Sends when the current run finishes" : "Sends after the messages above it"}
                >
                  Queued
                </span>
                <Button
                  type="button"
                  size="icon-xs"
                  variant="ghost-muted"
                  className="ms-auto"
                  aria-label="Remove queued message"
                  onClick={() => onRemove(queued.id)}
                >
                  <XIcon />
                </Button>
              </div>
            }
          />
        </div>
      ))}
    </>
  );
}
