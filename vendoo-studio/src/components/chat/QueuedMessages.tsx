import { ChatMarkdown } from "../ChatMarkdown";
import { type QueuedChatMessage } from "../chatMessageQueue";

export function QueuedMessages({ queue, onRemove }: { queue: QueuedChatMessage[]; onRemove: (id: string) => void }) {
  return (
    <>
      {queue.map((queued, index) => (
        <div
          key={queued.id}
          className="msg msg-user msg-queued"
          data-queued-message-id={queued.id}
        >
          {queued.text ? (
            <ChatMarkdown text={queued.text} lineBreaks />
          ) : (
            <span className="msg-queued-empty">
              {queued.browserFields?.length
                ? `${queued.browserFields.length} field${queued.browserFields.length === 1 ? "" : "s"} from the browser`
                : "Empty message"}
            </span>
          )}
          <div className="msg-queued-meta">
            <span className="msg-queued-label" title={index === 0
              ? "Sends when the current run finishes"
              : "Sends after the messages above it"}
            >
              Queued
            </span>
            <button
              type="button"
              className="msg-queued-remove"
              aria-label="Remove queued message"
              onClick={() => onRemove(queued.id)}
            >
              ×
            </button>
          </div>
        </div>
      ))}
    </>
  );
}
