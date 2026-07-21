# OceanPing mobile

Offline-first React Native (Expo) client — phase 3, milestone 5.

The app exists because the moment a coastal hazard is worth reporting is often
the moment the network stops working. Everything a user submits is written to
a durable local queue first and uploaded whenever a network next appears; no
screen ever blocks on connectivity.

## Layout

| Path | What it is |
|---|---|
| `src/lib/queue.ts` | The queue itself — pure logic, no Expo/RN imports, runs in plain Node |
| `src/lib/sync.ts` | Drains the queue against the API over an injected `fetch` |
| `src/lib/storage.ts` | SQLite persistence (device) and an in-memory fallback |
| `src/lib/client.ts` | App singletons: queue, device identity, background drain loop |
| `src/screens/` | Report, Mark Safe, and Outbox screens |

`queue.ts` and `sync.ts` deliberately import nothing from React Native, which
is what lets the same code that runs on device also run under `vitest` and
against a real backend from Node.

## The two properties this client must not get wrong

**A queued observation keeps its own time.** `observedAt` is stamped when the
user submits, not when the upload succeeds. The backend keys its coherence
window and incident merge off that timestamp, so a report held for three hours
and then stamped "now" would silently corroborate whatever is unfolding at
sync time. The server clamps the value it's given
(`ingest/service.py::clamp_observed_at`) because it arrives on a public
endpoint.

**A retry must not multiply a sighting.** `clientKey` is generated once at
enqueue and reused for every attempt, so a reply lost on a flaky link — which
the phone cannot distinguish from a request that never arrived — resolves to
the same server-side record. Report volume feeds the confidence signal, so
duplicates here would be actively harmful rather than untidy.

## Running

```bash
npm install
npm test          # queue + sync unit tests, no device needed
npm run typecheck
npx expo start    # requires the Expo toolchain and a device/emulator
```

`EXPO_PUBLIC_API_BASE` points the app at a backend. It defaults to
`http://10.0.2.2:8000`, which is how an Android emulator reaches a server on
the host machine.

### Live check against a real backend

With the stack up (`docker compose up -d` in the repo root):

```bash
npx tsx tests/live.integration.ts
```

This drives the real queue through a simulated offline period, a recovery, a
replayed submission, and a Mark Safe check-in, asserting against what the
server actually stored. It is not part of `npm test` because it needs the
stack running.

## Not built

- **Mesh relay (BLE/Wi-Fi Direct).** The phase-3 plan calls for it as a
  timeboxed research spike explicitly after the offline queue, and marks it
  additive. The queue's client timestamps and idempotency keys are the pieces
  a hop-and-forward layer would need, so the seam is there, unused.
- **Offline map packs.** The report and check-in flows don't need a map, so
  the tile-caching work sits behind the parts that do.
- **CRDT sync.** Nothing in the current model has concurrent writers on the
  same record — submissions are append-only from one device — so there is no
  conflict to resolve yet.
- **Photo attachment from the queue.** `POST /reports` accepts a photo, but
  queuing binary payloads durably is a different storage problem than queuing
  form fields, and the text path is what the offline case needs first.
- **Verification on a device or emulator.** The queue logic is covered by
  unit tests, the client↔server contract by the live check above, and the
  screens by a clean `tsc` pass — but the UI itself has not been rendered on
  a real device in this environment.
