/**
 * BLE/Wi-Fi Direct mesh relay — the timeboxed research spike the depth plan
 * scheduled *after* the offline queue and marked additive
 * (docs/plans/phase-3-depth.md, milestone 5's "physical edge" section).
 *
 * This file is pure protocol logic only: packing a device's due items into
 * a bundle, encrypting it, and merging a received bundle into another
 * device's queue. The radio transport itself — actually discovering and
 * talking to a nearby phone over BLE or Wi-Fi Direct — is a native module
 * this environment can't build or verify (the mobile app's screens already
 * carry the same limitation; see mobile/README.md), so it's represented
 * here only as an injected `MeshTransport` interface, exactly the way
 * `QueueStorage` and `fetch` are already injected seams with one real
 * implementation each and a fake for tests.
 *
 * The backend needs no changes at all for this. A relayed report reaches
 * the server through the exact same POST /reports every direct submission
 * uses, carrying the *origin* device's client_id/client_key/observed_at
 * untouched (see sync.ts::formBody) — reporter identity, idempotency, and
 * the observation-time clamp already do the right thing without knowing a
 * report took a hop to get there.
 */
import nacl from "tweetnacl";
import { decodeBase64, decodeUTF8, encodeBase64, encodeUTF8 } from "tweetnacl-util";
import { QueueItem, SubmissionQueue } from "./queue";

export interface MeshKeyPair {
  publicKey: Uint8Array;
  secretKey: Uint8Array;
}

export function generateKeyPair(): MeshKeyPair {
  return nacl.box.keyPair();
}

/** What travels over the radio hop, before encryption. */
export interface MeshBundle {
  fromDeviceId: string;
  /** Bundles handed straight from the origin device start at 0; a device
   *  that relays an already-relayed bundle onward (multi-hop store and
   *  forward) increments it. */
  hop: number;
  packedAt: number;
  items: Pick<QueueItem, "kind" | "clientKey" | "observedAt" | "payload">[];
}

/** Chain-length bound only — never a trust or confidence input. A bundle
 *  that hopped through the cap is dropped outright, not scored any
 *  differently once it does arrive: the same "report volume never
 *  escalates anything on its own" rule the scoring engine already enforces
 *  applies here too — a long relay chain proves nothing about a report's
 *  truth, it just needs a ceiling so two devices can't loop a bundle back
 *  and forth forever. */
export const MAX_HOPS = 4;

export function packBundle(
  items: QueueItem[],
  fromDeviceId: string,
  hop: number,
  now: () => number = Date.now,
): MeshBundle {
  return {
    fromDeviceId,
    hop,
    packedAt: now(),
    items: items.map(({ kind, clientKey, observedAt, payload }) => ({ kind, clientKey, observedAt, payload })),
  };
}

export interface MeshEnvelope {
  senderPublicKey: string;
  nonce: string;
  ciphertext: string;
}

/**
 * Encrypts a bundle to the recipient's public key (nacl box: X25519 +
 * XSalsa20-Poly1305). This is transport security for the radio hop, not
 * access control against the relay itself — a relay device is a fellow
 * human who has agreed to carry someone else's reports and will decrypt
 * them to do it, the same trust as handing someone a sealed note to run to
 * the next village. What it stops is a *different* nearby device passively
 * harvesting report contents off the air without ever being asked to relay
 * anything.
 */
export function sealBundle(bundle: MeshBundle, sender: MeshKeyPair, recipientPublicKey: Uint8Array): MeshEnvelope {
  const nonce = nacl.randomBytes(nacl.box.nonceLength);
  const plaintext = decodeUTF8(JSON.stringify(bundle));
  const ciphertext = nacl.box(plaintext, nonce, recipientPublicKey, sender.secretKey);
  return {
    senderPublicKey: encodeBase64(sender.publicKey),
    nonce: encodeBase64(nonce),
    ciphertext: encodeBase64(ciphertext),
  };
}

/** Throws if the envelope was tampered with, corrupted, or sealed for a
 *  different device — nacl.box.open failing authentication is exactly that
 *  signal, surfaced as an error rather than silently returning junk. */
export function openBundle(envelope: MeshEnvelope, recipient: MeshKeyPair): MeshBundle {
  const plaintext = nacl.box.open(
    decodeBase64(envelope.ciphertext),
    decodeBase64(envelope.nonce),
    decodeBase64(envelope.senderPublicKey),
    recipient.secretKey,
  );
  if (plaintext === null) {
    throw new Error("mesh bundle failed to decrypt — tampered, corrupted, or sealed for a different device");
  }
  return JSON.parse(encodeUTF8(plaintext));
}

/** The real BLE/Wi-Fi Direct link. Not implemented here (see module
 *  docstring) — every device that speaks this protocol needs exactly this
 *  much of a shape, so the spike's tests exercise it with a plain
 *  in-process stand-in for the radio. */
export interface MeshTransport {
  send(envelope: MeshEnvelope): Promise<void>;
}

/** Merge a received bundle into the local queue. Bundles past the hop cap
 *  are dropped whole (return 0) rather than partially trusted. Each item
 *  keeps its original clientKey/observedAt and is tagged with where it
 *  really came from, so sync.ts sends it under the origin device's
 *  identity — see enqueueRelayed's own duplicate guard for why re-merging
 *  the same bundle twice is harmless. */
export async function receiveBundle(bundle: MeshBundle, queue: SubmissionQueue): Promise<number> {
  if (bundle.hop > MAX_HOPS) return 0;
  let merged = 0;
  for (const item of bundle.items) {
    const added = await queue.enqueueRelayed({
      ...item,
      relayedFrom: bundle.fromDeviceId,
      relayHop: bundle.hop,
    });
    if (added) merged += 1;
  }
  return merged;
}

/**
 * Pack, seal, and hand off a device's due items to a nearby relay in one
 * step. On a successful send the items move to "relayed" (queue.ts::
 * markRelayed) rather than "sent" — this device knows the bytes left the
 * phone, not that they reached the server, and will fall back to sending
 * them itself once reclaimStaleRelays() decides the relay never delivered.
 *
 * Returns 0 (and sends nothing) when there's nothing due, so a caller can
 * poll this on a timer without it becoming a busy loop over an empty queue.
 */
export async function handOff(
  queue: SubmissionQueue,
  deviceId: string,
  sender: MeshKeyPair,
  recipientPublicKey: Uint8Array,
  transport: MeshTransport,
  hop = 0,
  now: () => number = Date.now,
): Promise<number> {
  const due = await queue.due();
  if (due.length === 0) return 0;
  const bundle = packBundle(due, deviceId, hop, now);
  const envelope = sealBundle(bundle, sender, recipientPublicKey);
  await transport.send(envelope);
  for (const item of due) {
    await queue.markRelayed(item);
  }
  return due.length;
}
