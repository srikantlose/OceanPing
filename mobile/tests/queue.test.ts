import { describe, expect, it } from "vitest";
import { MAX_ATTEMPTS, QueueItem, SubmissionQueue, backoffMs } from "../src/lib/queue";
import { MemoryQueueStorage } from "../src/lib/storage";

const T0 = 1_700_000_000_000;

function makeQueue(now = () => T0) {
  let n = 0;
  const storage = new MemoryQueueStorage();
  const queue = new SubmissionQueue({ storage, now, newId: () => `id-${++n}` });
  return { queue, storage };
}

const REPORT = { kind: "report" as const, payload: { lat: "13.05", lon: "80.28", hazard_type: "high_waves" } };

describe("enqueue", () => {
  it("stores a submission as pending and immediately due", async () => {
    const { queue } = makeQueue();
    const item = await queue.enqueue(REPORT);
    expect(item.status).toBe("pending");
    expect(item.attempts).toBe(0);
    expect(await queue.due()).toHaveLength(1);
  });

  it("stamps the observation time at enqueue, not at send", async () => {
    let clock = T0;
    const { queue } = makeQueue(() => clock);
    const item = await queue.enqueue(REPORT);
    clock = T0 + 3 * 60 * 60 * 1000; // three hours offline
    const [due] = await queue.due();
    expect(due.observedAt).toBe(T0);
    expect(item.observedAt).toBe(T0);
  });

  it("honours an explicitly supplied observation time", async () => {
    const { queue } = makeQueue();
    const item = await queue.enqueue({ ...REPORT, observedAt: T0 - 60_000 });
    expect(item.observedAt).toBe(T0 - 60_000);
  });

  it("gives every item its own idempotency key", async () => {
    const { queue } = makeQueue();
    const a = await queue.enqueue(REPORT);
    const b = await queue.enqueue(REPORT);
    expect(a.clientKey).not.toBe(b.clientKey);
  });
});

describe("retry and backoff", () => {
  it("keeps the same idempotency key across retries", async () => {
    const { queue, storage } = makeQueue();
    const item = await queue.enqueue(REPORT);
    await queue.markFailed(item, "network down");
    const [stored] = await storage.all();
    expect(stored.clientKey).toBe(item.clientKey);
  });

  it("backs a failed item off instead of retrying instantly", async () => {
    let clock = T0;
    const { queue } = makeQueue(() => clock);
    const item = await queue.enqueue(REPORT);
    await queue.markFailed(item, "network down");
    expect(await queue.due()).toHaveLength(0);
    clock = T0 + backoffMs(1);
    expect(await queue.due()).toHaveLength(1);
  });

  it("grows the backoff with each attempt and caps it", () => {
    expect(backoffMs(1)).toBeLessThan(backoffMs(2));
    expect(backoffMs(2)).toBeLessThan(backoffMs(3));
    expect(backoffMs(50)).toBe(backoffMs(60));
    expect(backoffMs(50)).toBeLessThanOrEqual(15 * 60 * 1000);
  });

  it("stops retrying a non-retryable failure immediately", async () => {
    const { queue } = makeQueue();
    const item = await queue.enqueue(REPORT);
    const failed = await queue.markFailed(item, "HTTP 422", false);
    expect(failed.status).toBe("failed");
    expect(await queue.due()).toHaveLength(0);
  });

  it("gives up after MAX_ATTEMPTS on a retryable failure", async () => {
    let clock = T0;
    const { queue } = makeQueue(() => clock);
    let item: QueueItem = await queue.enqueue(REPORT);
    for (let i = 0; i < MAX_ATTEMPTS; i++) {
      item = await queue.markFailed(item, "network down");
      clock += backoffMs(item.attempts) + 1;
    }
    expect(item.status).toBe("failed");
    expect(item.attempts).toBe(MAX_ATTEMPTS);
    expect(await queue.due()).toHaveLength(0);
  });

  it("can put failed items back in line by hand", async () => {
    const { queue } = makeQueue();
    const item = await queue.enqueue(REPORT);
    await queue.markFailed(item, "HTTP 422", false);
    expect(await queue.retryFailed()).toBe(1);
    expect(await queue.due()).toHaveLength(1);
  });
});

describe("ordering and lifecycle", () => {
  it("drains oldest observation first so the timeline arrives in order", async () => {
    const { queue } = makeQueue();
    const later = await queue.enqueue({ ...REPORT, observedAt: T0 });
    const earlier = await queue.enqueue({ ...REPORT, observedAt: T0 - 60_000 });
    const due = await queue.due();
    expect(due.map((i) => i.id)).toEqual([earlier.id, later.id]);
  });

  it("does not re-send an item already sent", async () => {
    const { queue } = makeQueue();
    const item = await queue.enqueue(REPORT);
    await queue.markSent(item);
    expect(await queue.due()).toHaveLength(0);
    expect(await queue.pending()).toHaveLength(0);
  });

  it("clears the last error when an item finally succeeds", async () => {
    let clock = T0;
    const { queue, storage } = makeQueue(() => clock);
    const item = await queue.enqueue(REPORT);
    const failed = await queue.markFailed(item, "network down");
    await queue.markSent(failed);
    const [stored] = await storage.all();
    expect(stored.status).toBe("sent");
    expect(stored.lastError).toBeUndefined();
  });

  it("purges only sent items", async () => {
    const { queue } = makeQueue();
    const a = await queue.enqueue(REPORT);
    await queue.enqueue(REPORT);
    await queue.markSent(a);
    expect(await queue.purgeSent()).toBe(1);
    expect(await queue.counts()).toMatchObject({ pending: 1, sent: 0 });
  });

  it("reports counts by status", async () => {
    const { queue } = makeQueue();
    const a = await queue.enqueue(REPORT);
    const b = await queue.enqueue(REPORT);
    await queue.enqueue(REPORT);
    await queue.markSent(a);
    await queue.markFailed(b, "HTTP 422", false);
    expect(await queue.counts()).toEqual({ pending: 1, sent: 1, failed: 1, relayed: 0 });
  });

  it("lists everything still on the device, newest observation first", async () => {
    const { queue } = makeQueue();
    const older = await queue.enqueue({ ...REPORT, observedAt: T0 - 60_000 });
    const newer = await queue.enqueue({ ...REPORT, observedAt: T0 });
    await queue.markSent(newer);
    const all = await queue.all();
    expect(all.map((i) => i.id)).toEqual([newer.id, older.id]);
    expect(all.map((i) => i.status)).toEqual(["sent", "pending"]);
  });

  it("survives a restart by reading state back from storage", async () => {
    const storage = new MemoryQueueStorage();
    const first = new SubmissionQueue({ storage, now: () => T0 });
    await first.enqueue(REPORT);
    // A fresh queue object over the same storage is what a relaunch looks like.
    const second = new SubmissionQueue({ storage, now: () => T0 });
    expect(await second.pending()).toHaveLength(1);
  });
});

describe("mesh relay support", () => {
  it("marks a handed-off item relayed instead of sent, with a future retry deadline", async () => {
    let clock = T0;
    const { queue } = makeQueue(() => clock);
    const item = await queue.enqueue(REPORT);
    const relayed = await queue.markRelayed(item);
    expect(relayed.status).toBe("relayed");
    expect(relayed.nextAttemptAt).toBeGreaterThan(clock);
    expect(await queue.due()).toHaveLength(0);
    expect(await queue.pending()).toHaveLength(0);
  });

  it("falls back to pending once the relay ack deadline passes", async () => {
    let clock = T0;
    const { queue } = makeQueue(() => clock);
    const item = await queue.enqueue(REPORT);
    const relayed = await queue.markRelayed(item);
    expect(await queue.reclaimStaleRelays()).toBe(0);
    clock = relayed.nextAttemptAt + 1;
    expect(await queue.reclaimStaleRelays()).toBe(1);
    expect(await queue.counts()).toMatchObject({ pending: 1, relayed: 0 });
    expect(await queue.due()).toHaveLength(1);
  });

  it("does not reclaim a relay that still has time left", async () => {
    const { queue } = makeQueue();
    const item = await queue.enqueue(REPORT);
    await queue.markRelayed(item);
    expect(await queue.reclaimStaleRelays()).toBe(0);
    expect(await queue.counts()).toMatchObject({ relayed: 1, pending: 0 });
  });

  it("inserts a forwarded item under the origin device's identity, preserving its clientKey and observedAt", async () => {
    const { queue } = makeQueue();
    const merged = await queue.enqueueRelayed({
      kind: "report",
      clientKey: "origin-key-1",
      observedAt: T0 - 3_600_000,
      payload: REPORT.payload,
      relayedFrom: "device-A",
      relayHop: 0,
    });
    expect(merged).not.toBeNull();
    expect(merged!.clientKey).toBe("origin-key-1");
    expect(merged!.observedAt).toBe(T0 - 3_600_000);
    expect(merged!.relayedFrom).toBe("device-A");
    expect(merged!.status).toBe("pending");
    expect(await queue.due()).toHaveLength(1);
  });

  it("refuses to merge the same relayed item twice", async () => {
    const { queue } = makeQueue();
    const input = {
      kind: "report" as const,
      clientKey: "origin-key-2",
      observedAt: T0,
      payload: REPORT.payload,
      relayedFrom: "device-A",
      relayHop: 0,
    };
    await queue.enqueueRelayed(input);
    const second = await queue.enqueueRelayed(input);
    expect(second).toBeNull();
    expect(await queue.all()).toHaveLength(1);
  });
});
