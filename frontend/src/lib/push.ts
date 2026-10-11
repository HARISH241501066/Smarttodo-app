// Web Push opt-in. The service worker (public/sw.js) shows the notification and, on click, opens
// the task's action link.

import { api, ApiError } from "@/lib/api";

// "unavailable": supported, but the service worker could not start (so there is nothing to subscribe with).
export type PushState = "unsupported" | "unavailable" | "unconfigured" | "denied" | "off" | "on";

const READY_TIMEOUT_MS = 8000;

function urlBase64ToUint8Array(base64: string): Uint8Array<ArrayBuffer> {
  const padding = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + padding).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

export function pushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export async function registerServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) return null;
  try {
    return await navigator.serviceWorker.register("/sw.js", { scope: "/", updateViaCache: "none" });
  } catch {
    return null;
  }
}

/** The active registration, or null if the service worker did not start in time. */
async function readyRegistration(): Promise<ServiceWorkerRegistration | null> {
  if (!(await registerServiceWorker())) return null;
  return Promise.race([
    navigator.serviceWorker.ready,
    new Promise<null>((resolve) => window.setTimeout(() => resolve(null), READY_TIMEOUT_MS)),
  ]);
}

async function publicKey(): Promise<string | null> {
  try {
    return (await api<{ public_key: string }>("/push/public-key")).public_key;
  } catch (err) {
    if (err instanceof ApiError && err.status === 503) return null;
    throw err;
  }
}

export async function getPushState(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  if (!(await publicKey())) return "unconfigured";
  if (Notification.permission === "denied") return "denied";
  const registration = await readyRegistration();
  if (!registration) return "unavailable";
  return (await registration.pushManager.getSubscription()) ? "on" : "off";
}

export async function enablePush(): Promise<PushState> {
  if (!pushSupported()) return "unsupported";
  const key = await publicKey();
  if (!key) return "unconfigured";
  const permission = await Notification.requestPermission();
  if (permission !== "granted") return permission === "denied" ? "denied" : "off";
  const registration = await readyRegistration();
  if (!registration) return "unavailable";
  const subscription =
    (await registration.pushManager.getSubscription()) ??
    (await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(key),
    }));
  await api("/push/subscribe", { method: "POST", json: subscription.toJSON() });
  return "on";
}

export async function disablePush(): Promise<PushState> {
  const registration = await readyRegistration();
  const subscription = registration ? await registration.pushManager.getSubscription() : null;
  if (subscription) {
    try {
      await api("/push/subscribe", { method: "DELETE", json: { endpoint: subscription.endpoint } });
    } catch (err) {
      if (!(err instanceof ApiError && err.status === 404)) throw err;
    }
    await subscription.unsubscribe();
  }
  return "off";
}
