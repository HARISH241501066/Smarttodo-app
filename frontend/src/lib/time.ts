// All deadlines are stored in UTC and shown in India Standard Time (Asia/Kolkata, UTC+05:30, no DST).

const IST_OFFSET = "+05:30";
const IST_OFFSET_MS = 5.5 * 3600 * 1000;

export interface Countdown {
  text: string;
  overdue: boolean;
  soon: boolean; // within 48 hours (matches the backend's "urgent" window)
}

export function formatCountdown(deadlineIso: string, nowMs: number): Countdown {
  const diff = new Date(deadlineIso).getTime() - nowMs;
  const abs = Math.abs(diff);
  const minutes = Math.floor(abs / 60000);
  const hours = Math.floor(minutes / 60);
  const days = Math.floor(hours / 24);
  let span: string;
  if (minutes < 1) span = "less than a minute";
  else if (hours < 1) span = `${minutes}m`;
  else if (days < 1) span = `${hours}h ${minutes % 60}m`;
  else if (days < 3) span = `${days}d ${hours % 24}h`;
  else span = `${days} days`;
  const overdue = diff < 0;
  return {
    text: overdue ? `Overdue by ${span}` : `Due in ${span}`,
    overdue,
    soon: !overdue && diff <= 48 * 3600 * 1000,
  };
}

/** A datetime-local value ("2026-10-12T15:30") is an IST wall-clock time; make it an ISO instant. */
export function istInputToIso(value: string): string | null {
  const v = value.trim();
  if (!v) return null;
  return `${v.length === 16 ? `${v}:00` : v}${IST_OFFSET}`;
}

/** The inverse, for filling a datetime-local input from a stored UTC instant. */
export function isoToIstInput(iso: string | null): string {
  if (!iso) return "";
  const shifted = new Date(new Date(iso).getTime() + IST_OFFSET_MS);
  return shifted.toISOString().slice(0, 16);
}

export function timeAgo(iso: string, nowMs: number): string {
  const seconds = Math.max(0, Math.floor((nowMs - new Date(iso).getTime()) / 1000));
  if (seconds < 45) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${Math.max(minutes, 1)}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return days < 30
    ? `${days}d ago`
    : new Date(iso).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });
}

export function domainOf(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

/** Mirrors the backend rule: only absolute http(s) URLs with a host. */
export function isHttpUrl(value: string): boolean {
  const v = value.trim();
  try {
    const u = new URL(v);
    return (u.protocol === "http:" || u.protocol === "https:") && !!u.hostname && !/\s/.test(v);
  } catch {
    return false;
  }
}
