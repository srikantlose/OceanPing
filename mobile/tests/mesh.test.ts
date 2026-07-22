import { describe, expect, it } from "vitest";
import {
  MAX_HOPS,
  MeshEnvelope,
  MeshTransport,
  generateKeyPair,
  handOff,
  openBundle,
  packBundle,
  receiveBundle,
  sealBundle,
} from "../src/lib/mesh";
import { SubmissionQueue } from "../src/lib/queue";
import { MemoryQueueStorage } from "../src/lib/storage";
import { formBody } from "../src/lib/sync";

const T0 = 1_700_000_000_000;
const REPORT = { kind: "report" as const, payload: { lat: "13.05", lon: "80.28", hazard_type: "high_waves" } };

function makeQueue(now = () => T0) {
  let n = 0;
  const storage = new MemoryQueueStorage();
  return new SubmissionQueue({ storage, now, newId: () => `id-${++n}` });
}

/** Stands in for the BLE/Wi-Fi Direct radio: hands the envelope straight to
 *  the recipient's inbox, in-process. The real transport is out of scope
 *  for this spike (see mesh.ts's module docstring). */
function directTransport(deliver: (envelope: MeshEnvelope) => Promise<void>): MeshTransport {
  return { send: deliver };
}

describe("packBundle / sealBundle / openBundle", () => {
  it("round-trips a bundle through encryption", async () => {
    const queue = makeQueue();
    const item = await queue.enqueue(REPORT);
    const alice = generateKeyPair();
    const bob = generateKeyPair();

    const bundle = packBundle([item], "device-A", 0, () => T0);
    const envelope = sealBundle(bundle, alice, bob.publicKey);
    const opened = openBundle(envelope, bob);

    expect(opened.fromDeviceId).toBe("device-A");
    expect(opened.hop).toBe(0);
    expect(opened.items).toHaveLength(1);
    expect(opened.items[0].clientKey).toBe(item.clientKey);
    expect(opened.items[0].payload).toEqual(item.payload);
  });

  it("strips queue bookkeeping fields the receiving device shouldn't inherit", async () => {
    const queue = makeQueue();
    const item = await queue.enqueue(REPORT);
    const failed = await queue.markFailed(item, "network down");

    const bundle = packBundle([failed], "device-A", 0, () => T0);

    expect((bundle.items[0] as any).id).toBeUndefined();
    expect((bundle.items[0] as any).attempts).toBeUndefined();
    expect((bundle.items[0] as any).status).toBeUndefined();
    expect((bundle.items[0] as any).lastError).toBeUndefined();
  });

  it("cannot be opened by the wrong recipient", () => {
    const alice = generateKeyPair();
    const bob = generateKeyPair();
    const mallory = generateKeyPair();
    const bundle = packBundle([], "device-A", 0, () => T0);
    const envelope = sealBundle(bundle, alice, bob.publicKey);
    expect(() => openBundle(envelope, mallory)).toThrow(/decrypt/);
  });

  it("rejects a tampered ciphertext instead of returning garbage", () => {
    const alice = generateKeyPair();
    const bob = generateKeyPair();
    const bundle = packBundle([], "device-A", 0, () => T0);
    const envelope = sealBundle(bundle, alice, bob.publicKey);
    const tampered: MeshEnvelope = { ...envelope, ciphertext: envelope.ciphertext.slice(0, -4) + "abcd" };
    expect(() => openBundle(tampered, bob)).toThrow();
  });
});

describe("receiveBundle", () => {
  it("merges a fresh bundle's items into the local queue, tagged with their origin", async () => {
    const sender = makeQueue();
    const item = await sender.enqueue(REPORT);
    const bundle = packBundle([item], "device-A", 0, () => T0);

    const receiver = makeQueue();
    const merged = await receiveBundle(bundle, receiver);

    expect(merged).toBe(1);
    const [local] = await receiver.due();
    expect(local.relayedFrom).toBe("device-A");
    expect(local.clientKey).toBe(item.clientKey);
    expect(local.observedAt).toBe(item.observedAt);
  });

  it("drops a bundle that already exceeded the hop cap", async () => {
    const sender = makeQueue();
    const item = await sender.enqueue(REPORT);
    const bundle = packBundle([item], "device-A", MAX_HOPS + 1, () => T0);

    const receiver = makeQueue();
    const merged = await receiveBundle(bundle, receiver);

    expect(merged).toBe(0);
    expect(await receiver.due()).toHaveLength(0);
  });

  it("does not duplicate items when the same bundle is delivered twice", async () => {
    const sender = makeQueue();
    const item = await sender.enqueue(REPORT);
    const bundle = packBundle([item], "device-A", 0, () => T0);

    const receiver = makeQueue();
    await receiveBundle(bundle, receiver);
    const second = await receiveBundle(bundle, receiver);

    expect(second).toBe(0);
    expect(await receiver.all()).toHaveLength(1);
  });
});

describe("handOff — end to end between two devices", () => {
  it("moves items to relayed on the sender and pending-with-origin on the receiver", async () => {
    const alice = generateKeyPair();
    const bob = generateKeyPair();

    const senderQueue = makeQueue();
    const item = await senderQueue.enqueue(REPORT);

    const receiverQueue = makeQueue();
    const transport = directTransport(async (envelope) => {
      const bundle = openBundle(envelope, bob);
      await receiveBundle(bundle, receiverQueue);
    });

    const count = await handOff(senderQueue, "device-A", alice, bob.publicKey, transport, 0, () => T0);

    expect(count).toBe(1);
    expect(await senderQueue.due()).toHaveLength(0);
    expect(await senderQueue.counts()).toMatchObject({ relayed: 1, pending: 0 });

    const [received] = await receiverQueue.due();
    expect(received.relayedFrom).toBe("device-A");
    expect(received.clientKey).toBe(item.clientKey);
  });

  it("does nothing when there is nothing due to hand off", async () => {
    const alice = generateKeyPair();
    const bob = generateKeyPair();
    const senderQueue = makeQueue();
    let sent = 0;
    const transport = directTransport(async () => {
      sent += 1;
    });
    const count = await handOff(senderQueue, "device-A", alice, bob.publicKey, transport);
    expect(count).toBe(0);
    expect(sent).toBe(0);
  });

  it("a relayed item syncs under the origin device's identity, not the relay's", async () => {
    const alice = generateKeyPair();
    const bob = generateKeyPair();

    const senderQueue = makeQueue();
    await senderQueue.enqueue(REPORT);

    const receiverQueue = makeQueue();
    const transport = directTransport(async (envelope) => {
      await receiveBundle(openBundle(envelope, bob), receiverQueue);
    });
    await handOff(senderQueue, "device-A", alice, bob.publicKey, transport, 0, () => T0);

    const [received] = await receiverQueue.due();
    const body = formBody(received, "device-B");
    expect(body.get("client_id")).toBe("device-A");
  });
});
