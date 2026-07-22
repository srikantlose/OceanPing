/**
 * Drains the offline queue against the OceanPing API — pure logic over an
 * injected `fetch`, so it runs in plain Node against the real backend as
 * well as on device.
 *
 * The one judgement call this file makes is which failures are worth
 * retrying (see `isRetryable`). Getting that wrong in either direction is
 * costly: treating everything as retryable keeps a malformed item at the head
 * of the queue forever, while treating everything as permanent throws away
 * real reports the moment a tunnel drops the connection.
 */
import { QueueItem, SubmissionQueue } from "./queue";

export interface SyncResult {
  attempted: number;
  sent: number;
  failed: number;
  errors: string[];
}

export interface SyncDeps {
  apiBase: string;
  queue: SubmissionQueue;
  fetchImpl?: typeof fetch;
  clientId: string;
}

const PATHS: Record<QueueItem["kind"], string> = {
  report: "/reports",
  checkin: "/safety/checkin",
};

/**
 * 4xx means the server understood and refused — sending the identical bytes
 * again will be refused identically. The deliberate exceptions are 408
 * (request timeout) and 429 (rate limited), which are both explicitly
 * "try again later", not "this is wrong".
 */
export function isRetryable(status: number | null): boolean {
  if (status === null) return true; // transport failure: no response at all
  if (status === 408 || status === 429) return true;
  return status >= 500;
}

/** Build the multipart/form-encoded body the ingest endpoints expect. The
 *  queue stores payload fields as strings precisely so this stays a
 *  mechanical translation with nothing to get wrong at send time.
 *
 *  An item that arrived via mesh relay (lib/mesh.ts) carries `relayedFrom`
 *  — the *origin* device's id — and that, not the relaying device's own
 *  clientId, is what goes out as client_id. The backend hashes source+
 *  external_id into a reporter identity (ingest/service.py::_hash_identity)
 *  with no idea a report took a hop to arrive, so this one line is the
 *  entire reason relayed reports still attribute, rate-limit, and dedupe
 *  against the right person instead of the phone that happened to have
 *  signal. */
export function formBody(item: QueueItem, clientId: string): URLSearchParams {
  const body = new URLSearchParams();
  for (const [k, v] of Object.entries(item.payload)) {
    body.set(k, v);
  }
  body.set("client_id", item.relayedFrom ?? clientId);
  body.set("client_key", item.clientKey);
  body.set("observed_at", new Date(item.observedAt).toISOString());
  return body;
}

export async function syncOnce({ apiBase, queue, fetchImpl, clientId }: SyncDeps): Promise<SyncResult> {
  const doFetch = fetchImpl ?? fetch;
  const due = await queue.due();
  const result: SyncResult = { attempted: 0, sent: 0, failed: 0, errors: [] };

  for (const item of due) {
    result.attempted += 1;
    let status: number | null = null;
    let detail = "";
    try {
      const resp = await doFetch(apiBase + PATHS[item.kind], {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: formBody(item, clientId).toString(),
      });
      status = resp.status;
      if (resp.ok) {
        await queue.markSent(item);
        result.sent += 1;
        continue;
      }
      detail = `HTTP ${status}`;
      try {
        detail += `: ${(await resp.text()).slice(0, 200)}`;
      } catch {
        /* body already consumed or unreadable — the status is enough */
      }
    } catch (err: any) {
      detail = `network: ${err?.message ?? String(err)}`;
    }

    await queue.markFailed(item, detail, isRetryable(status));
    result.failed += 1;
    result.errors.push(detail);
  }

  return result;
}
