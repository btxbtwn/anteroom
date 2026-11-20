import { DurableObject } from "cloudflare:workers";

function timingSafeEqual(a, b) {
  const left = new TextEncoder().encode(a);
  const right = new TextEncoder().encode(b);
  return left.length === right.length && crypto.subtle.timingSafeEqual(left, right);
}

// Who is who: each Mac running Anteroom is a device with its own token (only
// a hash is kept), each marketplace account a seller signs in with is linked
// to one device, and sign-ins in progress wait here for a few minutes.
//
// A sign-in is handed over only with the claim the relay gave the browser that
// approved it (only its hash is kept). The device that started it cannot
// collect a seller's tokens by sending someone else the sign-in link.
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
        etsy_user_id TEXT,
        PRIMARY KEY (marketplace, account_id)
      );
      CREATE INDEX IF NOT EXISTS accounts_by_device ON accounts (device_id);
      DROP TABLE IF EXISTS oauth_sessions;
      CREATE TABLE IF NOT EXISTS sign_ins (
        state TEXT PRIMARY KEY,
        device_id TEXT NOT NULL,
        marketplace TEXT NOT NULL,
        return_to TEXT NOT NULL,
        verifier TEXT,
        result TEXT,
        claim_hash TEXT,
        created_at INTEGER NOT NULL
      );
    `);
    const accountColumns = this.sql.exec("PRAGMA table_info(accounts)").toArray();
    if (!accountColumns.some((col) => col.name === "etsy_user_id")) {
      this.sql.exec("ALTER TABLE accounts ADD COLUMN etsy_user_id TEXT");
    }
    ctx.blockConcurrencyWhile(async () => {
      this.#expireSessions();
      await this.#scheduleExpiry();
    });
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

  async deleteDevice(id) {
    this.sql.exec("DELETE FROM accounts WHERE device_id = ?", id);
    this.sql.exec("DELETE FROM sign_ins WHERE device_id = ?", id);
    this.sql.exec("DELETE FROM devices WHERE id = ?", id);
    await this.#scheduleExpiry();
  }

  // A sign-in proves the account is the seller's, so linking moves the account
  // to the device that completed it (a reinstall, or a new Mac).
  link(marketplace, accountId, deviceId, label = null, etsyUserId = null) {
    this.sql.exec(
      `INSERT INTO accounts (marketplace, account_id, device_id, label, linked_at, etsy_user_id)
       VALUES (?, ?, ?, ?, ?, ?)
       ON CONFLICT (marketplace, account_id)
       DO UPDATE SET
         device_id = excluded.device_id,
         label = excluded.label,
         linked_at = excluded.linked_at,
         etsy_user_id = excluded.etsy_user_id`,
      marketplace,
      String(accountId),
      deviceId,
      label,
      Date.now(),
      etsyUserId === null || etsyUserId === undefined ? null : String(etsyUserId),
    );
  }

  unlink(marketplace, accountId, deviceId = null) {
    const id = String(accountId);
    this.ctx.storage.transactionSync(() => {
      const rows = deviceId
        ? this.sql
            .exec(
              "SELECT device_id, etsy_user_id FROM accounts WHERE marketplace = ? AND account_id = ? AND device_id = ?",
              marketplace,
              id,
              deviceId,
            )
            .toArray()
        : this.sql
            .exec("SELECT device_id, etsy_user_id FROM accounts WHERE marketplace = ? AND account_id = ?", marketplace, id)
            .toArray();
      if (rows.length === 0) return;

      const ownerDeviceId = rows[0].device_id;
      const etsyUserId = rows[0].etsy_user_id;

      if (deviceId) {
        this.sql.exec(
          "DELETE FROM accounts WHERE marketplace = ? AND account_id = ? AND device_id = ?",
          marketplace,
          id,
          deviceId,
        );
      } else {
        this.sql.exec("DELETE FROM accounts WHERE marketplace = ? AND account_id = ?", marketplace, id);
      }

      if (marketplace !== "etsy") return;

      if (etsyUserId !== null) {
        const remaining = this.sql
          .exec(
            "SELECT 1 FROM accounts WHERE marketplace = 'etsy' AND device_id = ? AND (etsy_user_id = ? OR etsy_user_id IS NULL) LIMIT 1",
            ownerDeviceId,
            etsyUserId,
          )
          .toArray();
        if (remaining.length === 0) {
          this.sql.exec(
            "DELETE FROM accounts WHERE marketplace = 'etsy-user' AND account_id = ? AND device_id = ?",
            etsyUserId,
            ownerDeviceId,
          );
        }
      } else {
        // Legacy pre-migration row with no recorded etsy_user_id: once no
        // unassociated Etsy shops remain on this device, remove any etsy-user
        // links not backed by another linked Etsy shop on this device.
        const remainingLegacy = this.sql
          .exec(
            "SELECT 1 FROM accounts WHERE marketplace = 'etsy' AND device_id = ? AND etsy_user_id IS NULL LIMIT 1",
            ownerDeviceId,
          )
          .toArray();
        if (remainingLegacy.length === 0) {
          this.sql.exec(
            `DELETE FROM accounts
             WHERE marketplace = 'etsy-user'
               AND device_id = ?
               AND account_id NOT IN (
                 SELECT etsy_user_id FROM accounts
                 WHERE marketplace = 'etsy' AND device_id = ? AND etsy_user_id IS NOT NULL
               )`,
            ownerDeviceId,
            ownerDeviceId,
          );
        }
      }
    });
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
    this.sql.exec("DELETE FROM sign_ins WHERE created_at <= ?", Date.now() - SESSION_TTL_MS);
  }

  async #scheduleExpiry() {
    const { oldest } = this.sql.exec("SELECT MIN(created_at) AS oldest FROM sign_ins").one();
    const alarm = await this.ctx.storage.getAlarm();
    if (oldest === null) {
      if (alarm !== null) await this.ctx.storage.deleteAlarm();
    } else {
      const next = Math.max(Date.now() + 1, oldest + SESSION_TTL_MS);
      if (alarm !== next) await this.ctx.storage.setAlarm(next);
    }
  }

  async alarm() {
    this.#expireSessions();
    await this.#scheduleExpiry();
  }

  async startSession(state, deviceId, marketplace, returnTo, verifier = null) {
    this.#expireSessions();
    this.sql.exec(
      "INSERT INTO sign_ins (state, device_id, marketplace, return_to, verifier, created_at) VALUES (?, ?, ?, ?, ?, ?)",
      state,
      deviceId,
      marketplace,
      returnTo,
      verifier,
      Date.now(),
    );
    await this.#scheduleExpiry();
  }

  async session(state) {
    this.#expireSessions();
    await this.#scheduleExpiry();
    const row = this.sql.exec("SELECT * FROM sign_ins WHERE state = ?", state).toArray()[0];
    if (!row) return null;
    return {
      deviceId: row.device_id,
      marketplace: row.marketplace,
      returnTo: row.return_to,
      verifier: row.verifier,
      done: row.result !== null,
    };
  }

  // ``outcome`` is { error } for a sign-in that failed, or { link, result } for
  // one that succeeded, which is only handed over with the claim.
  async finishSession(state, outcome, claimHash = null) {
    this.#expireSessions();
    this.sql.exec(
      "UPDATE sign_ins SET result = ?, claim_hash = ?, verifier = NULL WHERE state = ? AND result IS NULL",
      JSON.stringify(outcome),
      claimHash,
      state,
    );
    await this.#scheduleExpiry();
  }

  // Hands the sign-in result to the device that started it, once. A success
  // also needs the claim, and links the account to that device only then.
  async takeResult(state, deviceId, claimHash = null) {
    this.#expireSessions();
    await this.#scheduleExpiry();
    const row = this.sql
      .exec("SELECT result, claim_hash FROM sign_ins WHERE state = ? AND device_id = ?", state, deviceId)
      .toArray()[0];
    if (!row) return { status: "unknown" };
    if (row.result === null) return { status: "pending" };
    const outcome = JSON.parse(row.result);
    if (!outcome.error) {
      if (claimHash === null) return { status: "pending" };
      if (!timingSafeEqual(claimHash, row.claim_hash ?? "")) return { status: "refused" };
    }
    this.ctx.storage.transactionSync(() => {
      if (!outcome.error) {
        const etsyUserId = outcome.link.find((a) => a.marketplace === "etsy-user")?.accountId ?? null;
        for (const account of outcome.link) {
          this.link(
            account.marketplace,
            account.accountId,
            deviceId,
            account.label,
            account.marketplace === "etsy" ? (account.etsyUserId ?? etsyUserId) : null,
          );
        }
      }
      this.sql.exec("DELETE FROM sign_ins WHERE state = ?", state);
    });
    await this.#scheduleExpiry();
    return { status: "done", result: outcome.error ? { marketplace: outcome.marketplace, error: outcome.error } : outcome.result };
  }
}
