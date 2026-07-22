/**
 * QueueStorage implementations.
 *
 * `MemoryQueueStorage` is not a test-only toy: it's the fallback when SQLite
 * can't be opened, so a storage failure degrades the app to "works until you
 * close it" instead of refusing to accept a report during an emergency.
 */
import type { QueueItem, QueueStorage } from "./queue";

export class MemoryQueueStorage implements QueueStorage {
  private items = new Map<string, QueueItem>();

  async insert(item: QueueItem): Promise<void> {
    this.items.set(item.id, { ...item });
  }

  async update(item: QueueItem): Promise<void> {
    this.items.set(item.id, { ...item });
  }

  async all(): Promise<QueueItem[]> {
    return [...this.items.values()].map((i) => ({ ...i }));
  }

  async remove(id: string): Promise<void> {
    this.items.delete(id);
  }
}

/** Minimal shape of the expo-sqlite database this adapter needs — declared
 *  structurally rather than imported so this module stays loadable (and the
 *  queue tests stay runnable) outside a React Native runtime. */
export interface SqliteDb {
  execAsync(sql: string): Promise<void>;
  runAsync(sql: string, params: any[]): Promise<unknown>;
  getAllAsync<T>(sql: string, params?: any[]): Promise<T[]>;
}

interface Row {
  id: string;
  kind: string;
  client_key: string;
  observed_at: number;
  payload: string;
  status: string;
  attempts: number;
  next_attempt_at: number;
  last_error: string | null;
  relayed_from: string | null;
  relay_hop: number | null;
}

const CREATE_TABLE = `
CREATE TABLE IF NOT EXISTS queue_items (
  id TEXT PRIMARY KEY NOT NULL,
  kind TEXT NOT NULL,
  client_key TEXT NOT NULL UNIQUE,
  observed_at INTEGER NOT NULL,
  payload TEXT NOT NULL,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  relayed_from TEXT,
  relay_hop INTEGER
);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue_items (status, next_attempt_at);
`;

function toItem(row: Row): QueueItem {
  return {
    id: row.id,
    kind: row.kind as QueueItem["kind"],
    clientKey: row.client_key,
    observedAt: row.observed_at,
    payload: JSON.parse(row.payload),
    status: row.status as QueueItem["status"],
    attempts: row.attempts,
    nextAttemptAt: row.next_attempt_at,
    lastError: row.last_error ?? undefined,
    relayedFrom: row.relayed_from ?? undefined,
    relayHop: row.relay_hop ?? undefined,
  };
}

export class SqliteQueueStorage implements QueueStorage {
  constructor(private db: SqliteDb) {}

  static async open(db: SqliteDb): Promise<SqliteQueueStorage> {
    await db.execAsync(CREATE_TABLE);
    return new SqliteQueueStorage(db);
  }

  async insert(item: QueueItem): Promise<void> {
    await this.db.runAsync(
      `INSERT INTO queue_items
         (id, kind, client_key, observed_at, payload, status, attempts, next_attempt_at, last_error,
          relayed_from, relay_hop)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        item.id, item.kind, item.clientKey, item.observedAt,
        JSON.stringify(item.payload), item.status, item.attempts,
        item.nextAttemptAt, item.lastError ?? null,
        item.relayedFrom ?? null, item.relayHop ?? null,
      ],
    );
  }

  async update(item: QueueItem): Promise<void> {
    await this.db.runAsync(
      `UPDATE queue_items
          SET status = ?, attempts = ?, next_attempt_at = ?, last_error = ?
        WHERE id = ?`,
      [item.status, item.attempts, item.nextAttemptAt, item.lastError ?? null, item.id],
    );
  }

  async all(): Promise<QueueItem[]> {
    const rows = await this.db.getAllAsync<Row>("SELECT * FROM queue_items");
    return rows.map(toItem);
  }

  async remove(id: string): Promise<void> {
    await this.db.runAsync("DELETE FROM queue_items WHERE id = ?", [id]);
  }
}
