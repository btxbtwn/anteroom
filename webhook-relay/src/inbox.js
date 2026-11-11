import { DurableObject } from "cloudflare:workers";

// Events wait here until Studio acknowledges them, or for RETENTION_MS if
// Studio never comes back. One instance holds the whole queue.
const RETENTION_MS = 30 * 24 * 60 * 60 * 1000;
const PAGE_LIMIT = 200;

function rowToEvent(row) {
  return {
    seq: row.seq,
    source: row.source,
    eventType: row.event_type,
    ids: JSON.parse(row.ids),
    occurredAt: row.occurred_at,
    receivedAt: row.received_at,
  };
}

export class Inbox extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS events (
        seq INTEGER PRIMARY KEY AUTOINCREMENT,
        dedupe_key TEXT NOT NULL UNIQUE,
        source TEXT NOT NULL,
        event_type TEXT NOT NULL,
        ids TEXT NOT NULL,
        occurred_at TEXT,
        received_at INTEGER NOT NULL
      )
    `);
    // Every event ID accepted in the last RETENTION_MS, acknowledged or not,
    // so a captured webhook cannot be delivered (or act) a second time.
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS seen (
        dedupe_key TEXT PRIMARY KEY,
        received_at INTEGER NOT NULL
      )
    `);
    // Events stored before this table existed.
    this.sql.exec("INSERT OR IGNORE INTO seen (dedupe_key, received_at) SELECT dedupe_key, received_at FROM events");
  }

  seen(dedupeKey) {
    return this.sql.exec("SELECT 1 FROM seen WHERE dedupe_key = ?", dedupeKey).toArray().length > 0;
  }

  async add(event) {
    const receivedAt = Date.now();
    const fresh = this.sql
      .exec(
        "INSERT INTO seen (dedupe_key, received_at) VALUES (?, ?) ON CONFLICT(dedupe_key) DO NOTHING RETURNING dedupe_key",
        event.dedupeKey,
        receivedAt,
      )
      .toArray();
    if (fresh.length === 0) return { duplicate: true };
    const cursor = this.sql.exec(
      `INSERT INTO events (dedupe_key, source, event_type, ids, occurred_at, received_at)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT(dedupe_key) DO NOTHING
       RETURNING *`,
      event.dedupeKey,
      event.source,
      event.eventType,
      JSON.stringify(event.ids),
      event.occurredAt,
      receivedAt,
    );
    const rows = cursor.toArray();
    if (rows.length === 0) return { duplicate: true };
    const stored = rowToEvent(rows[0]);
    const message = JSON.stringify({ type: "events", events: [stored] });
    for (const socket of this.ctx.getWebSockets()) {
      try {
        socket.send(message);
      } catch {
        // A dead socket is dropped by the runtime; the event waits for the next pull.
      }
    }
    if ((await this.ctx.storage.getAlarm()) === null) {
      await this.ctx.storage.setAlarm(receivedAt + 24 * 60 * 60 * 1000);
    }
    return { duplicate: false, seq: stored.seq };
  }

  list(after = 0, limit = PAGE_LIMIT) {
    const rows = this.sql
      .exec(
        "SELECT * FROM events WHERE seq > ? ORDER BY seq LIMIT ?",
        after,
        Math.min(Math.max(limit, 1), PAGE_LIMIT),
      )
      .toArray();
    return rows.map(rowToEvent);
  }

  ack(through) {
    this.sql.exec("DELETE FROM events WHERE seq <= ?", through);
    return this.pending();
  }

  async clear() {
    for (const socket of this.ctx.getWebSockets()) {
      try {
        socket.close(1000, "device removed");
      } catch {
        // Already closed.
      }
    }
    await this.ctx.storage.deleteAlarm();
    await this.ctx.storage.deleteAll();
  }

  pending() {
    return this.sql.exec("SELECT COUNT(*) AS n FROM events").one().n;
  }

  async alarm() {
    const cutoff = Date.now() - RETENTION_MS;
    this.sql.exec("DELETE FROM events WHERE received_at < ?", cutoff);
    this.sql.exec("DELETE FROM seen WHERE received_at < ?", cutoff);
    const remembered = this.sql.exec("SELECT COUNT(*) AS n FROM seen").one().n;
    if (this.pending() > 0 || remembered > 0) await this.ctx.storage.setAlarm(Date.now() + 24 * 60 * 60 * 1000);
  }

  // Studio's live connection. On connect it gets everything after `after`,
  // then each new event as it lands. It acks with {"type":"ack","through":seq}.
  async fetch(request) {
    const after = Number(new URL(request.url).searchParams.get("after") ?? 0) || 0;
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair);
    this.ctx.acceptWebSocket(server);
    server.send(JSON.stringify({ type: "events", events: this.list(after) }));
    return new Response(null, { status: 101, webSocket: client });
  }

  async webSocketMessage(socket, data) {
    let message;
    try {
      message = JSON.parse(typeof data === "string" ? data : new TextDecoder().decode(data));
    } catch {
      return;
    }
    if (message?.type === "ack" && Number.isInteger(message.through)) {
      socket.send(JSON.stringify({ type: "acked", through: message.through, pending: this.ack(message.through) }));
      // A backlog larger than one page arrives a page per ack.
      const next = this.list(message.through);
      if (next.length > 0) socket.send(JSON.stringify({ type: "events", events: next }));
    } else if (message?.type === "ping") {
      socket.send(JSON.stringify({ type: "pong" }));
    }
  }

  async webSocketClose(socket, code) {
    try {
      socket.close(code === 1005 ? 1000 : code, "closing");
    } catch {
      // Already closed.
    }
  }
}
