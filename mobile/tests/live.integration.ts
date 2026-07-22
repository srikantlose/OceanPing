/**
 * Drives the real offline queue against a running OceanPing backend.
 *
 * Not part of `npm test` — it needs the stack up (docker compose up -d), so
 * it's run explicitly:
 *     npx tsx tests/live.integration.ts [apiBase]
 *
 * This is the check that matters for the offline queue, because the two
 * properties it exists to guarantee are both properties of the *client and
 * server together*, and neither can be observed from a unit test with a fake
 * fetch: that a report queued while offline reaches the server carrying the
 * time it was observed, and that retrying a submission whose reply was lost
 * produces one report rather than two.
 */
import { MeshEnvelope, generateKeyPair, handOff, openBundle, receiveBundle } from "../src/lib/mesh";
import { SubmissionQueue } from "../src/lib/queue";
import { MemoryQueueStorage } from "../src/lib/storage";
import { syncOnce } from "../src/lib/sync";

const API = process.argv[2] ?? "http://localhost:8000";
const MARINA = { lat: 13.0512, lon: 80.2831 };

let failures = 0;

function check(label: string, ok: boolean, detail = "") {
  console.log(`${ok ? "  PASS" : "  FAIL"}  ${label}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures += 1;
}

async function analystToken(): Promise<string> {
  const resp = await fetch(`${API}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "analyst", password: "oceanping-dev" }),
  });
  return (await resp.json()).token;
}

async function main() {
  console.log(`→ Live offline-queue check against ${API}`);
  const token = await analystToken();
  const device = `mobile-live-${Date.now()}`;
  const queue = new SubmissionQueue({ storage: new MemoryQueueStorage() });

  // --- 1. A report observed three hours ago, queued while offline ---------
  const observedAt = Date.now() - 3 * 60 * 60 * 1000;
  const queued = await queue.enqueue({
    kind: "report",
    observedAt,
    payload: {
      lat: MARINA.lat.toFixed(6),
      lon: MARINA.lon.toFixed(6),
      hazard_type: "high_waves",
      text: "waves breaking over the sea wall, spray across the road",
    },
  });

  // Offline: every attempt fails at the transport layer, exactly as it would
  // with no signal. The item must survive and stay pending.
  const offline = await syncOnce({
    apiBase: API, queue, clientId: device,
    fetchImpl: (async () => {
      throw new Error("Network request failed");
    }) as any,
  });
  const afterOffline = await queue.counts();
  check("offline attempt keeps the report queued", afterOffline.pending === 1 && offline.sent === 0,
        `pending=${afterOffline.pending}`);

  // --- 2. Network returns: the queue drains for real ----------------------
  // Backoff would normally hold it; a fresh queue over the same item is what
  // "the user pressed Sync now" does.
  const drainQueue = new SubmissionQueue({ storage: new MemoryQueueStorage() });
  const readyItem = await drainQueue.enqueue({
    kind: "report", observedAt, payload: queued.payload,
  });

  // Tee the server's answer out of the real sync path. Asserting on what the
  // server said it stored is stronger than scanning /analyst/reports, which
  // is capped and ordered by created_at — a deliberately backdated report
  // isn't reliably in the first page, and a capped list can't prove a
  // duplicate wasn't created either.
  const seen: any[] = [];
  const recording: typeof fetch = async (url: any, init?: any) => {
    const resp = await fetch(url, init);
    try {
      seen.push(await resp.clone().json());
    } catch {
      /* non-JSON error body — the status is what matters then */
    }
    return resp;
  };

  const online = await syncOnce({ apiBase: API, queue: drainQueue, clientId: device, fetchImpl: recording });
  check("queued report uploads once a network is available", online.sent === 1,
        online.errors.join("; "));

  const stored = seen[0];
  const skewMs = stored ? Math.abs(new Date(stored.created_at).getTime() - observedAt) : Infinity;

  // --- 3. The server kept the observation time, not the sync time ---------
  check("report carries the time it was observed, not the time it synced",
        skewMs < 60_000,
        stored ? `created_at=${stored.created_at}, ${(skewMs / 1000).toFixed(1)}s from observed` : "no response");

  // --- 4. A lost reply must not become a second report -------------------
  // Same clientKey, sent again — precisely the case where the phone can't
  // tell "never arrived" from "arrived, reply dropped".
  const replayBody = new URLSearchParams({
    lat: MARINA.lat.toFixed(6),
    lon: MARINA.lon.toFixed(6),
    hazard_type: "high_waves",
    text: "waves breaking over the sea wall, spray across the road",
    client_id: device,
    client_key: readyItem.clientKey,
    observed_at: new Date(observedAt).toISOString(),
  });
  const replay = await fetch(`${API}/reports`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: replayBody.toString(),
  });
  const replayed = await replay.json();
  check("retrying a submission resolves to the original report, not a new one",
        replay.ok && Boolean(stored) && replayed.id === stored.id,
        `${replayed.id} vs ${stored?.id}`);

  // And a *different* key for the same content is a genuinely separate
  // sighting — dedup must key on the client's identity for the submission,
  // not on the content, or two people reporting the same wave collapse.
  const distinct = await fetch(`${API}/reports`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ ...Object.fromEntries(replayBody), client_key: `${readyItem.clientKey}-other` }).toString(),
  });
  const distinctBody = await distinct.json();
  check("a different idempotency key still creates its own report",
        distinct.ok && distinctBody.id !== stored?.id, `${distinctBody.id}`);

  // --- 5. Mark Safe, through the same queue ------------------------------
  const safeQueue = new SubmissionQueue({ storage: new MemoryQueueStorage() });
  await safeQueue.enqueue({
    kind: "checkin",
    payload: { lat: MARINA.lat.toFixed(6), lon: MARINA.lon.toFixed(6), status: "need_help",
               note: "live check: stranded, water rising" },
  });
  const safeSync = await syncOnce({ apiBase: API, queue: safeQueue, clientId: device });
  check("mark-safe check-in uploads through the same queue", safeSync.sent === 1,
        safeSync.errors.join("; "));

  const checkins = await (await fetch(`${API}/analyst/safety/checkins?hours=1&status=need_help`, {
    headers: { Authorization: `Bearer ${token}` },
  })).json();
  check("check-in is visible to responders", checkins.length > 0, `${checkins.length} need_help`);

  // A check-in must never be mistaken for a hazard observation. The check-in
  // was just submitted, so if it had leaked into the report pipeline it would
  // be at the very top of the newest-first report list.
  const newestReports = await (await fetch(`${API}/analyst/reports?limit=25`, {
    headers: { Authorization: `Bearer ${token}` },
  })).json();
  const leaked = newestReports.some((r: any) => (r.text ?? "").includes("stranded, water rising"));
  check("a check-in never becomes a hazard report", !leaked);

  // --- 6. Mesh relay: attribution and rate-limiting survive a hand-off ----
  // The property that only a real server can prove: a report relayed
  // through another device's network connection must land in the
  // *origin* device's reporter bucket, not a fresh one for whoever
  // happened to have signal. A distinct cell isolates this from the
  // per-cell rate limit and from the other checks above.
  const MESH_CELL = { lat: 13.091, lon: 80.301 };
  const deviceA = `mesh-live-A-${Date.now()}`;
  const deviceB = `mesh-live-B-${Date.now()}`;

  async function submitDirect(clientId: string, clientKey: string) {
    const body = new URLSearchParams({
      lat: MESH_CELL.lat.toFixed(6),
      lon: MESH_CELL.lon.toFixed(6),
      hazard_type: "high_waves",
      client_id: clientId,
      client_key: clientKey,
    });
    return fetch(`${API}/reports`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: body.toString(),
    });
  }

  let quota = 0;
  for (let i = 0; i < 5; i++) {
    const resp = await submitDirect(deviceA, `${deviceA}-direct-${i}`);
    if (resp.ok) quota += 1;
  }
  check("direct submissions exhaust device A's own rate-limit quota", quota === 5, `${quota}/5 accepted`);

  const overQuota = await submitDirect(deviceA, `${deviceA}-direct-over`);
  check("a 6th direct submission from device A is rate-limited (sanity check)", overQuota.status === 429);

  const bDirect = await submitDirect(deviceB, `${deviceB}-direct-0`);
  check("device B's own quota is untouched by device A's activity so far", bDirect.ok, `HTTP ${bDirect.status}`);

  // Device A queues one more report while offline and hands it to device B
  // over a simulated radio hop (real BLE/Wi-Fi Direct is out of scope for
  // this spike — see mesh.ts's module docstring — so the transport here is
  // just an in-process function call standing in for the radio).
  const alice = generateKeyPair();
  const bob = generateKeyPair();
  const meshQueueA = new SubmissionQueue({ storage: new MemoryQueueStorage() });
  await meshQueueA.enqueue({
    kind: "report",
    observedAt: Date.now() - 30 * 60 * 1000,
    payload: {
      lat: MESH_CELL.lat.toFixed(6),
      lon: MESH_CELL.lon.toFixed(6),
      hazard_type: "high_waves",
      text: "mesh relay live check: relayed while device A had no signal",
    },
  });

  const meshQueueB = new SubmissionQueue({ storage: new MemoryQueueStorage() });
  const transport = {
    send: async (envelope: MeshEnvelope) => {
      const bundle = openBundle(envelope, bob);
      await receiveBundle(bundle, meshQueueB);
    },
  };
  const handedOff = await handOff(meshQueueA, deviceA, alice, bob.publicKey, transport);
  check("device A hands its queued report to device B over the simulated relay", handedOff === 1);
  check("device A's own queue marks the item relayed, not sent",
        (await meshQueueA.counts()).relayed === 1);

  // Device B drains its own queue — which now includes A's relayed item —
  // against the real backend using device B's own clientId. sync.ts's
  // formBody must still send it under device A's identity.
  const relaySync = await syncOnce({ apiBase: API, queue: meshQueueB, clientId: deviceB });
  check("device B forwards the relayed item to the real backend", relaySync.attempted === 1);
  check("the relayed report is rate-limited under device A's exhausted quota, not device B's fresh one",
        relaySync.failed === 1 && relaySync.errors.some((e) => e.includes("429")),
        relaySync.errors.join("; "));

  const bAfterRelay = await submitDirect(deviceB, `${deviceB}-direct-1`);
  check("device B's own quota is unaffected by relaying someone else's report",
        bAfterRelay.ok, `HTTP ${bAfterRelay.status}`);

  console.log(failures === 0 ? "\n✓ Live offline-queue check passed." : `\n✗ ${failures} check(s) failed.`);
  process.exit(failures === 0 ? 0 : 1);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
