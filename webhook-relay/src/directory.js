import { DurableObject } from "cloudflare:workers";

// Who is who: each Mac running Anteroom is a device with its own token (only
// a hash is kept), each marketplace account a seller signs in with is linked
// to one device, and sign-ins in progress wait here for a few minutes.
const SESSION_TTL_MS = 15 * 60 * 1000;

export class Directory extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.sql = ctx.storage.sql;
    this.sql.exec(`
      CREATE TABLE IF NOT EXISTS devices (
        id TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,
        created_at INTEGER NOT NULL
      );
      CREATE TABLE IF NOT EXISTS accounts (
        marketplace TEXT NOT NULL,
        account_id TEXT NOT NULL,
        device_id TEXT NOT NULL,
        label TEXT,
        linked_at INTEGER NOT NULL,
        PRIMARY KEY (marketplace, account_id)
      );
      CREATE INDEX IF NOT EXISTS accounts_by_device ON accounts (device_id);
      CREATE TABLE IF NOT EXISTS oauth_sessions (
        state TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        marketplace TEXT NOT NULL,
        verifier TEXT,
        result TEXT,
        created_at INTEGER NOT NULL
      );
    `);
  }

  createDevice(id, tokenHash) {
    this.sql.exec(
      "INSERT INTO devices (id, token_hash, created_at) VALUES (?, ?, ?)",
      id,
      tokenHash,
      Date.now(),
    );
  }

  deviceForToken(tokenHash) {
    const rows = this.sql.exec("SELECT id FROM devices WHERE token_hash = ?", tokenHash).toArray();
    return rows[0]?.id ?? null;
  }

  deleteDevice(id) {
    this.sql.exec("DELETE FROM accounts WHERE device_id = ?", id);
    this.sql.exec("DELETE FROM oauth_sessions WHERE device_id = ?", id);
    this.sql.exec("DELETE FROM devices WHERE id = ?", id);
  }

  // A sign-in proves the account is the seller's, so linking moves the account
  // to the device that completed it (a reinstall, or a new Mac).
  link(marketplace, accountId, deviceId, label = null) {
    this.sql.exec(
      `INSERT INTO accounts (marketplace, account_id, device_id, label, linked_at)
       VALUES (?, ?, ?, ?, ?)
       ON CONFLICT (marketplace, account_id)
       DO UPDATE SET device_id = excluded.device_id, label = excluded.label, linked_at = excluded.linked_at`,
      marketplace,
      String(accountId),
      deviceId,
      label,
      Date.now(),
    );
  }

  unlink(marketplace, accountId, deviceId = null) {
    if (deviceId) {
      this.sql.exec(
        "DELETE FROM accounts WHERE marketplace = ? AND account_id = ? AND device_id = ?",
        marketplace,
        String(accountId),
        deviceId,
      );
    } else {
      this.sql.exec("DELETE FROM accounts WHERE marketplace = ? AND account_id = ?", marketplace, String(accountId));
    }
  }

  deviceForAccount(marketplace, accountId) {
    const rows = this.sql
      .exec("SELECT device_id FROM accounts WHERE marketplace = ? AND account_id = ?", marketplace, String(accountId))
      .toArray();
    return rows[0]?.device_id ?? null;
  }

  accountsForDevice(deviceId) {
    return this.sql
      .exec(
        "SELECT marketplace, account_id, label, linked_at FROM accounts WHERE device_id = ? ORDER BY marketplace, linked_at",
        deviceId,
      )
      .toArray()
      .map((row) => ({
        marketplace: row.marketplace,
        accountId: row.account_id,
        label: row.label,
        linkedAt: row.linked_at,
      }));
  }

  #expireSessions() {
    this.sql.exec("DELETE FROM oauth_sessions WHERE created_at < ?", Date.now() - SESSION_TTL_MS);
  }

  startSession(state, deviceId, marketplace, verifier = null) {
    this.#expireSessions();
    this.sql.exec(
      "INSERT INTO oauth_sessions (state, device_id, marketplace, verifier, created_at) VALUES (?, ?, ?, ?, ?)",
      state,
      deviceId,
      marketplace,
      verifier,
      Date.now(),
    );
  }

  session(state) {
    this.#expireSessions();
    const row = this.sql.exec("SELECT * FROM oauth_sessions WHERE state = ?", state).toArray()[0];
    if (!row) return null;
    return { deviceId: row.device_id, marketplace: row.marketplace, verifier: row.verifier, done: row.result !== null };
  }

  finishSession(state, result) {
    this.sql.exec(
      "UPDATE oauth_sessions SET result = ?, verifier = NULL WHERE state = ? AND result IS NULL",
      JSON.stringify(result),
      state,
    );
  }

  // Hands the sign-in result to the device that started it, once.
  takeResult(state, deviceId) {
    this.#expireSessions();
    const row = this.sql
      .exec("SELECT result FROM oauth_sessions WHERE state = ? AND device_id = ?", state, deviceId)
      .toArray()[0];
    if (!row) return { status: "unknown" };
    if (row.result === null) return { status: "pending" };
    this.sql.exec("DELETE FROM oauth_sessions WHERE state = ?", state);
    return { status: "done", result: JSON.parse(row.result) };
  }
}
