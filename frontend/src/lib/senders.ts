// Validation for the sender lists on the settings page. These mirror the backend's rules, so a
// mistake is caught before saving (the backend still checks everything).

const LABEL = "[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?";
const DOMAIN = `(?:${LABEL}\\.)+[a-z]{2,63}`;
const DOMAIN_RE = new RegExp(`^${DOMAIN}$`);
const EMAIL_RE = new RegExp(`^[a-z0-9][a-z0-9._%+-]{0,63}@${DOMAIN}$`);
// "Never scan" and "college" lists are looser: a plain domain or address, nothing that could act
// as a Gmail search operator.
const LOOSE_RE = /^[a-z0-9][a-z0-9._+@-]*$/;

export function normalizeEntry(raw: string): string {
  return raw.trim().toLowerCase().replace(/^@/, "");
}

/** For "Always turn into tasks": a real domain (nptel.ac.in) or a full address (news@example.com). */
export function validateTrackedEntry(entry: string): string | null {
  return DOMAIN_RE.test(entry) || EMAIL_RE.test(entry)
    ? null
    : "Enter a domain such as nptel.ac.in, or a full address such as news@example.com.";
}

export function validateLooseEntry(entry: string): string | null {
  return LOOSE_RE.test(entry)
    ? null
    : "Enter a plain domain such as example.com, or an address such as news@example.com.";
}

export const TRACKED_LABEL = "Always turn into tasks";
export const DENY_LABEL = "Never scan mail from";

/** Error text if `entry` is already in the other list (an entry can only be in one of the two). */
export function conflictMessage(entry: string, inOtherList: boolean, otherLabel: string): string | null {
  return inOtherList ? `${entry} is already in "${otherLabel}". Remove it there first.` : null;
}
