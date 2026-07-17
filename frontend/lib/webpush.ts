import { API_BASE } from "@/lib/api";

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

export function browserAlertsSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

/** Registers the SW, subscribes to push, and posts the subscription to the
 * backend geofenced around (lat, lon). Throws with a user-facing message on
 * any failure — callers should catch and display it. */
export async function subscribeToBrowserAlerts(lat: number, lon: number): Promise<void> {
  const { key } = await (await fetch(`${API_BASE}/subscribe/vapid-public-key`)).json();
  if (!key) throw new Error("Browser alerts aren't configured on this server yet.");

  const permission = await Notification.requestPermission();
  if (permission !== "granted") throw new Error("Notification permission was denied.");

  const registration = await navigator.serviceWorker.register("/sw.js");
  await navigator.serviceWorker.ready;
  const pushSub =
    (await registration.pushManager.getSubscription()) ||
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key) as BufferSource,
    }));

  const json = pushSub.toJSON();
  const res = await fetch(`${API_BASE}/subscribe/web-push`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      lat,
      lon,
      endpoint: json.endpoint,
      keys: json.keys,
      lang: navigator.language.split("-")[0],
    }),
  });
  if (!res.ok) throw new Error(`Subscribe request failed (${res.status}).`);
}
