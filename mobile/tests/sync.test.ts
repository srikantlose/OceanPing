import { describe, expect, it } from "vitest";
import { SubmissionQueue } from "../src/lib/queue";
import { MemoryQueueStorage } from "../src/lib/storage";
import { formBody, isRetryable, syncOnce } from "../src/lib/sync";

const T0 = 1_700_000_000_000;
const REPORT = { kind: "report" as const, payload: { lat: "13.05", lon: "80.28", hazard_type: "high_waves" } };

function makeQueue(now = () => T0) {
  let n = 0;
  const storage = new MemoryQueueStorage();
  return new SubmissionQueue({ storage, now, newId: () => `id-${++n}` });
}

function respond(status: number) {
  return async () => ({ ok: status >= 200 && status < 300, status, text: async () => "" }) as any;
}

describe("isRetryable", () => {
  it("retries when there was no response at all", () => {
    expect(isRetryable(null)).toBe(true);
  });

  it("retries server errors", () => {
    expect(isRetryable(500)).toBe(true);
    expect(isRetryable(503)).toBe(true);
  });

  it("retries rate limiting and request timeout, which are explicitly 'later'", () => {
    expect(isRetryable(429)).toBe(true);
    expect(isRetryable(408)).toBe(true);
  });

  it("does not retry a payload the server refused as invalid", () => {
    expect(isRetryable(422)).toBe(false);
    expect(isRetryable(400)).toBe(false);
    expect(isRetryable(404)).toBe(false);
  });
});

describe("formBody", () => {
  it("carries the idempotency key and observation time on every attempt", async () => {
    const queue = makeQueue();
    const item = await queue.enqueue(REPORT);
    const body = formBody(item, "device-1");
    expect(body.get("client_key")).toBe(item.clientKey);
    expect(body.get("observed_at")).toBe(new Date(T0).toISOString());
    expect(body.get("client_id")).toBe("device-1");
    expect(body.get("hazard_type")).toBe("high_waves");
  });
});

describe("syncOnce", () => {
  it("marks an accepted submission as sent", async () => {
    const queue = makeQueue();
    await queue.enqueue(REPORT);
    const result = await syncOnce({ apiBase: "http://x", queue, clientId: "d1", fetchImpl: respond(200) });
    expect(result).toMatchObject({ attempted: 1, sent: 1, failed: 0 });
    expect(await queue.pending()).toHaveLength(0);
  });

  it("keeps a submission queued when the network is down", async () => {
    const queue = makeQueue();
    await queue.enqueue(REPORT);
    const fetchImpl = async () => {
      throw new Error("Network request failed");
    };
    const result = await syncOnce({ apiBase: "http://x", queue, clientId: "d1", fetchImpl: fetchImpl as any });
    expect(result.failed).toBe(1);
    expect(await queue.counts()).toMatchObject({ pending: 1, failed: 0 });
  });

  it("gives up on a rejected payload rather than retrying it forever", async () => {
    const queue = makeQueue();
    await queue.enqueue(REPORT);
    await syncOnce({ apiBase: "http://x", queue, clientId: "d1", fetchImpl: respond(422) });
    expect(await queue.counts()).toMatchObject({ pending: 0, failed: 1 });
  });

  it("keeps retrying a rate-limited submission", async () => {
    const queue = makeQueue();
    await queue.enqueue(REPORT);
    await syncOnce({ apiBase: "http://x", queue, clientId: "d1", fetchImpl: respond(429) });
    expect(await queue.counts()).toMatchObject({ pending: 1, failed: 0 });
  });

  it("reuses the same idempotency key after a failed attempt", async () => {
    let clock = T0;
    const queue = makeQueue(() => clock);
    const item = await queue.enqueue(REPORT);
    const keys: string[] = [];
    const capture = async (_url: string, init: any) => {
      keys.push(new URLSearchParams(init.body).get("client_key")!);
      return { ok: false, status: 500, text: async () => "" } as any;
    };
    await syncOnce({ apiBase: "http://x", queue, clientId: "d1", fetchImpl: capture as any });
    clock += 60_000;
    await syncOnce({ apiBase: "http://x", queue, clientId: "d1", fetchImpl: capture as any });
    expect(keys).toHaveLength(2);
    expect(new Set(keys).size).toBe(1);
    expect(keys[0]).toBe(item.clientKey);
  });

  it("routes each kind to its own endpoint", async () => {
    const queue = makeQueue();
    await queue.enqueue(REPORT);
    await queue.enqueue({ kind: "checkin", payload: { lat: "13.0", lon: "80.2", status: "safe" } });
    const urls: string[] = [];
    const capture = async (url: string) => {
      urls.push(url);
      return { ok: true, status: 200, text: async () => "" } as any;
    };
    await syncOnce({ apiBase: "http://x", queue, clientId: "d1", fetchImpl: capture as any });
    expect(urls.sort()).toEqual(["http://x/reports", "http://x/safety/checkin"]);
  });

  it("does nothing when the queue is empty", async () => {
    const queue = makeQueue();
    const result = await syncOnce({ apiBase: "http://x", queue, clientId: "d1", fetchImpl: respond(200) });
    expect(result).toMatchObject({ attempted: 0, sent: 0, failed: 0 });
  });
});
