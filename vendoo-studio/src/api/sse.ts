/**
 * The longest a stream may stay silent. The server sends a keepalive every 3 s
 * while it works (10 s on the sold-comps search), so this much silence means the
 * connection is gone even though the browser has not said so.
 */
export const STREAM_IDLE_MS = 20_000;

/** A stream went silent past its deadline; callers treat it as a dropped connection. */
export class StreamIdleError extends Error {
  constructor() {
    super("Lost the connection to Anteroom: nothing arrived for 20 seconds.");
    this.name = "StreamIdleError";
  }
}

/** The next chunk, or StreamIdleError (with the stream cancelled) when none arrives in time. */
export async function readWithin<T>(reader: ReadableStreamDefaultReader<T>, ms = STREAM_IDLE_MS): Promise<ReadableStreamReadResult<T>> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const idle = new Promise<never>((_resolve, reject) => {
    timer = setTimeout(() => {
      // Reject first: cancelling settles the pending read as finished.
      reject(new StreamIdleError());
      void reader.cancel().catch(() => undefined);
    }, ms);
  });
  try {
    return await Promise.race([reader.read(), idle]);
  } finally {
    clearTimeout(timer);
  }
}

/** Read a `text/event-stream` response, calling `onEvent` once per complete event. */
export async function readSse(
  res: Response,
  onEvent: (event: string, data: string) => void,
): Promise<void> {
  const reader = res.body?.getReader();
  if (!reader) {
    dispatch(await res.text(), onEvent);
    return;
  }
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await readWithin(reader);
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const blocks = buffer.split("\n\n");
    buffer = blocks.pop() || "";
    for (const block of blocks) dispatch(block, onEvent);
  }
  if (buffer.trim()) dispatch(buffer, onEvent);
}

function dispatch(text: string, onEvent: (event: string, data: string) => void): void {
  for (const block of text.split("\n\n")) {
    let event = "message";
    const data: string[] = [];
    for (const raw of block.split("\n")) {
      const line = raw.replace(/\r$/, "");
      if (line.startsWith("event:")) event = line.slice(6).trim() || "message";
      else if (line.startsWith("data:")) data.push(line.startsWith("data: ") ? line.slice(6) : line.slice(5));
    }
    if (!data.length) continue;
    const joined = data.join("\n");
    if (joined === "[DONE]") continue;
    onEvent(event, joined);
  }
}
