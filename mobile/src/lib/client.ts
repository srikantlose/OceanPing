/**
 * App-wide singletons: the durable queue, a stable device identity, and the
 * background drain loop.
 *
 * Device identity is a random id generated once and kept in SQLite, not a
 * hardware identifier: the backend only needs a stable handle to attach
 * reporter trust to (it hashes whatever it's given — see
 * ingest/service.py::_hash_identity), and a hardware id would be a
 * durable cross-app tracker with no upside here.
 */
import { SubmissionQueue } from "./queue";
import { MemoryQueueStorage, SqliteDb, SqliteQueueStorage } from "./storage";
import { syncOnce } from "./sync";

export const API_BASE = process.env.EXPO_PUBLIC_API_BASE ?? "http://10.0.2.2:8000";

const DEVICE_ID_TABLE = `
CREATE TABLE IF NOT EXISTS device (k TEXT PRIMARY KEY NOT NULL, v TEXT NOT NULL);
`;

function randomId(): string {
  return "xxxxxxxxxxxxxxxx".replace(/x/g, () => ((Math.random() * 16) | 0).toString(16));
}

export async function loadDeviceId(db: SqliteDb): Promise<string> {
  await db.execAsync(DEVICE_ID_TABLE);
  const rows = await db.getAllAsync<{ v: string }>("SELECT v FROM device WHERE k = 'device_id'");
  if (rows.length > 0) return rows[0].v;
  const id = `mobile-${randomId()}`;
  await db.runAsync("INSERT INTO device (k, v) VALUES ('device_id', ?)", [id]);
  return id;
}

export interface AppContext {
  queue: SubmissionQueue;
  deviceId: string;
  /** True when SQLite failed and the queue is memory-only for this session. */
  degraded: boolean;
}

export async function initApp(openDb: () => Promise<SqliteDb>): Promise<AppContext> {
  try {
    const db = await openDb();
    const storage = await SqliteQueueStorage.open(db);
    return { queue: new SubmissionQueue({ storage }), deviceId: await loadDeviceId(db), degraded: false };
  } catch (err) {
    // Refusing to accept a report because local storage is broken would be
    // the worst possible failure mode for this app, so fall back to a
    // memory queue and tell the user it won't survive a restart.
    console.warn("SQLite unavailable, falling back to in-memory queue", err);
    return {
      queue: new SubmissionQueue({ storage: new MemoryQueueStorage() }),
      deviceId: `mobile-${randomId()}`,
      degraded: true,
    };
  }
}

/** Drain loop. Runs unconditionally on an interval rather than subscribing to
 *  connectivity events: "the OS says we have a network" and "requests
 *  actually succeed" differ often enough (captive portals, dead zones with
 *  bars) that attempting and failing is the more reliable signal. */
export function startSyncLoop(ctx: AppContext, intervalMs = 30_000): () => void {
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    try {
      await syncOnce({ apiBase: API_BASE, queue: ctx.queue, clientId: ctx.deviceId });
    } catch (err) {
      console.warn("sync tick failed", err);
    }
  };
  void tick();
  const handle = setInterval(tick, intervalMs);
  return () => {
    stopped = true;
    clearInterval(handle);
  };
}
