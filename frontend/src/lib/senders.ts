// Validation for the sender and keyword lists on the settings page. These mirror the backend's
// rules, so a mistake is caught before saving (the backend still checks everything).

const LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
const DOMAIN = `(?:${LABEL}\\.)+[a-z]{2,63}`;
const DOMAIN_RE = new RegExp(`^${DOMAIN}$`);
const EMAIL_RE = new RegExp(`^[a-z0-9][a-z0-9._%+-]{0,63}@${DOMAIN}$`);
const KEYWORD_RE = /^[a-z0-9](?:[a-z0-9 -]{0,58}[a-z0-9])?$/;

export const SOURCE_TYPE_LABEL = {
  nptel: "NPTEL",
  digii: "Digii",
  internship: "Internship",
  custom: "Custom",
} as const;

export function normalizeEntry(raw: string): string {
  return raw.trim().toLowerCase().replace(/^@/, "");
}

/** Lower-case and collapse spaces: "Smart  India" -> "smart india". */
export function normalizeKeyword(raw: string): string {
  return raw.trim().toLowerCase().split(/\s+/).join(" ");
}

/** A real domain (nptel.ac.in) or a full address (news@example.com). A domain also matches its
 *  subdomains (onlinecourses.nptel.ac.in) but never look-alikes such as fakenptel.ac.in. */
export function validateSenderEntry(entry: string): string | null {
  return DOMAIN_RE.test(entry) || EMAIL_RE.test(entry)
    ? null
    : "Enter a domain such as nptel.ac.in, or a full address such as news@example.com.";
}

export function validateKeywordEntry(entry: string): string | null {
  return KEYWORD_RE.test(entry) ? null : "Use letters, numbers, spaces or hyphens only, e.g. hackathon.";
}

/** Error text for a source name that is empty or already used (case-insensitive), or null. */
export function validateSourceName(name: string, others: string[]): string | null {
  const clean = name.trim().replace(/\s+/g, " ");
  if (!clean) return "Give the source a name.";
  if (clean.length > 40) return "Keep the name under 40 characters.";
  if (others.some((o) => o.trim().toLowerCase() === clean.toLowerCase())) {
    return `You already have a source called "${clean}".`;
  }
  return null;
}
