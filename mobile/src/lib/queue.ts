/**
 * Offline-first submission queue (phase 3, milestone 5) — pure logic, no
 * React Native or Expo imports, so it runs and is testable in plain Node.
 *
 * The queue is the whole point of the mobile app: the moment a coastal
 * hazard is worth reporting is often the moment the network stops working.
 * So a submission is written to durable local storage *first* and sent
 * later, rather than being attempted live and lost on failure.
 *
 * Two properties this file exists to guarantee:
 *
 *  - **The observation keeps its own time.** `observedAt` is stamped when the
 *    user hits submit, not when the item finally uploads. A report held for
 *    three hours must not arrive claiming to be about right now — the backend
 *    keys its coherence window and incident merge off that timestamp, so a
 *    late-syncing report stamped "now" would silently corroborate whatever
 *    happens to be unfolding at sync time.
 *
 *  - **Retries can't multiply a sighting.** `clientKey` is generated once at
 *    enqueue and reused for every attempt, so a reply lost on a flaky link —
 *    indistinguishable, from the client, from a request that never arrived —
 *    resolves to the same server-side record instead of a second report.
 *    Report volume feeds the confidence signal, so duplicates here would be
 *    actively harmful, not just untidy.
 */

export type QueueKind = "report" | "checkin";

export type QueueStatus = "pending" | "sent" | "failed" | "relayed";

export interface QueueItem {
  id: string;
  kind: QueueKind;
  /** Stable idempotency key, generated at enqueue and never regenerated. */
  clientKey: string;
  /** When the user acted, in epoch milliseconds. */
  observedAt: number;
  /** Arbitrary submission fields (lat/lon/hazard_type/status/note/…). */
  payload: Record<string, string>;
  status: QueueStatus;
  attempts: number;
  /** Epoch ms before which this item should not be retried (backoff). */
  nextAttemptAt: number;
  lastError?: string;
  /** Mesh relay only (phase 3, milestone 5 follow-on — see lib/mesh.ts): the
   *  device id of whoever this item's report actually belongs to, if it
   *  arrived here by hand-off rather than being enqueued locally. When set,
   *  sync.ts sends the report under *this* identity, not the relaying
   *  device's own — a relay is a network pipe, not a co-author. */
  relayedFrom?: string;
  /** How many hand-offs this item has already been through. Purely a
   *  chain-length bound (see MAX_HOPS in mesh.ts) — never an input to trust
   *  or scoring. */
  relayHop?: number;
}

export interface NewQueueItem {
  kind: QueueKind;
  payload: Record<string, string>;
  observedAt?: number;
}

/** Storage the queue persists through. Implemented by SQLite on device and
 *  by an in-memory map in tests — the queue logic itself never knows which. */
export interface QueueStorage {
  insert(item: QueueItem): Promise<void>;
  update(item: QueueItem): Promise<void>;
  all(): Promise<QueueItem[]>;
  remove(id: string): Promise<void>;
}

export interface QueueDeps {
  storage: QueueStorage;
  /** Injected so tests can drive time without sleeping. */
  now?: () => number;
  /** Injected so tests get deterministic ids. */
  newId?: () => string;
}

/** Attempts after which an item stops being retried automatically. Chosen so
 *  a phone offline overnight still drains on reconnect (backoff caps at 15
 *  min, so 8 attempts spans hours) without retrying forever on something
 *  that will never succeed. */
export const MAX_ATTEMPTS = 8;

const BACKOFF_BASE_MS = 5_000;
const BACKOFF_CAP_MS = 15 * 60 * 1000;

/** How long a handed-off item waits for its relay to actually deliver it
 *  before the origin device gives up trusting the hand-off and falls back
 *  to trying directly again. Long enough that a relay still without signal
 *  gets a real chance; short enough that a report doesn't sit silently
 *  stuck for a day when trying again directly might have found a tower. */
export const RELAY_ACK_TIMEOUT_MS = 2 * 60 * 60 * 1000;

/** Exponential backoff, capped. Deterministic (no jitter): a single phone
 *  isn't a thundering herd, and predictable timing is worth more here for
 *  being able to reason about — and test — drain behaviour. */
export function backoffMs(attempts: number): number {
  const raw = BACKOFF_BASE_MS * Math.pow(2, Math.max(0, attempts - 1));
  return Math.min(BACKOFF_CAP_MS, raw);
}

function defaultId(): string {
  // RFC4122-ish v4 without pulling a uuid dependency into the queue core.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

export class SubmissionQueue {
  private storage: QueueStorage;
  private now: () => number;
  private newId: () => string;

  constructor({ storage, now, newId }: QueueDeps) {
    this.storage = storage;
    this.now = now ?? (() => Date.now());
    this.newId = newId ?? defaultId;
  }

  /** Durably record a submission. Returns the stored item, including the
   *  clientKey every later attempt will reuse. */
  async enqueue(input: NewQueueItem): Promise<QueueItem> {
    const item: QueueItem = {
      id: this.newId(),
      kind: input.kind,
      clientKey: this.newId(),
      observedAt: input.observedAt ?? this.now(),
      payload: input.payload,
      status: "pending",
      attempts: 0,
      nextAttemptAt: 0,
    };
    await this.storage.insert(item);
    return item;
  }

  /** Items eligible to send right now: still pending, past their backoff,
   *  oldest observation first so the timeline reaches the server in the order
   *  it happened. */
  async due(): Promise<QueueItem[]> {
    const now = this.now();
    const all = await this.storage.all();
    return all
      .filter((i) => i.status === "pending" && i.nextAttemptAt <= now)
      .sort((a, b) => a.observedAt - b.observedAt);
  }

  async pending(): Promise<QueueItem[]> {
    return (await this.storage.all()).filter((i) => i.status === "pending");
  }

  /** Everything still on the device, newest observation first — what the
   *  outbox screen renders. */
  async all(): Promise<QueueItem[]> {
    return (await this.storage.all()).sort((a, b) => b.observedAt - a.observedAt);
  }

  async markSent(item: QueueItem): Promise<void> {
    await this.storage.update({ ...item, status: "sent", lastError: undefined });
  }

  /**
   * Record a failed attempt.
   *
   * `retryable` is the caller's judgement about the *kind* of failure, and it
   * matters: a dropped connection or a 5xx deserves another try, but a 422
   * means the payload itself is malformed and will be just as invalid in an
   * hour. Retrying that forever would keep a permanently-stuck item at the
   * head of the queue and burn battery on a radio that has nothing to do.
   */
  async markFailed(item: QueueItem, error: string, retryable = true): Promise<QueueItem> {
    const attempts = item.attempts + 1;
    const exhausted = !retryable || attempts >= MAX_ATTEMPTS;
    const updated: QueueItem = {
      ...item,
      attempts,
      lastError: error,
      status: exhausted ? "failed" : "pending",
      nextAttemptAt: exhausted ? item.nextAttemptAt : this.now() + backoffMs(attempts),
    };
    await this.storage.update(updated);
    return updated;
  }

  /** Put a permanently-failed item back in line — the "retry" an analyst or
   *  user triggers by hand after fixing whatever was wrong. */
  async retryFailed(): Promise<number> {
    const failed = (await this.storage.all()).filter((i) => i.status === "failed");
    for (const item of failed) {
      await this.storage.update({ ...item, status: "pending", attempts: 0, nextAttemptAt: 0 });
    }
    return failed.length;
  }

  /** Mark an item handed off to a nearby relay (mesh.ts::handOff). Not
   *  "sent" — the device only knows the bytes left the phone, not that they
   *  reached the server, so it's a distinct state that expects to hear
   *  nothing further unless the relay never delivers. */
  async markRelayed(item: QueueItem): Promise<QueueItem> {
    const updated: QueueItem = { ...item, status: "relayed", nextAttemptAt: this.now() + RELAY_ACK_TIMEOUT_MS };
    await this.storage.update(updated);
    return updated;
  }

  /** Items handed to a relay that never confirmed within the timeout fall
   *  back to pending so the device goes back to trying directly — a relay's
   *  silence must never lose a report permanently. Not counted as a failed
   *  attempt (attempts is untouched): the server never saw and rejected
   *  anything, the hand-off itself just didn't pan out. */
  async reclaimStaleRelays(): Promise<number> {
    const now = this.now();
    const stale = (await this.storage.all()).filter((i) => i.status === "relayed" && i.nextAttemptAt <= now);
    for (const item of stale) {
      await this.storage.update({ ...item, status: "pending", nextAttemptAt: 0 });
    }
    return stale.length;
  }

  /** The receiving side of a hand-off (mesh.ts::receiveBundle): insert an
   *  item forwarded by another device, preserving its clientKey and
   *  observedAt so identity, idempotency, and the observation-time clamp
   *  all survive the hop untouched. Returns null without inserting if this
   *  clientKey is already present — guards against being handed the same
   *  bundle twice (a relay that re-broadcasts before hearing back). */
  async enqueueRelayed(input: {
    kind: QueueKind;
    clientKey: string;
    observedAt: number;
    payload: Record<string, string>;
    relayedFrom: string;
    relayHop: number;
  }): Promise<QueueItem | null> {
    const existing = await this.storage.all();
    if (existing.some((i) => i.clientKey === input.clientKey)) return null;
    const item: QueueItem = {
      id: this.newId(),
      kind: input.kind,
      clientKey: input.clientKey,
      observedAt: input.observedAt,
      payload: input.payload,
      status: "pending",
      attempts: 0,
      nextAttemptAt: 0,
      relayedFrom: input.relayedFrom,
      relayHop: input.relayHop,
    };
    await this.storage.insert(item);
    return item;
  }

  /** Drop already-sent items. Kept explicit rather than automatic on send so
   *  the UI can show "3 reports synced" before they disappear. */
  async purgeSent(): Promise<number> {
    const sent = (await this.storage.all()).filter((i) => i.status === "sent");
    for (const item of sent) {
      await this.storage.remove(item.id);
    }
    return sent.length;
  }

  async counts(): Promise<Record<QueueStatus, number>> {
    const all = await this.storage.all();
    return {
      pending: all.filter((i) => i.status === "pending").length,
      sent: all.filter((i) => i.status === "sent").length,
      failed: all.filter((i) => i.status === "failed").length,
      relayed: all.filter((i) => i.status === "relayed").length,
    };
  }
}
