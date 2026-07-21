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

export type QueueStatus = "pending" | "sent" | "failed";

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
    };
  }
}
