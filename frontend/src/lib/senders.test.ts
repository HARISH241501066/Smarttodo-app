import { describe, expect, it } from "vitest";
import { conflictMessage, normalizeEntry, validateLooseEntry, validateTrackedEntry } from "@/lib/senders";

describe("always-track entries", () => {
  it("accepts domains and full addresses", () => {
    for (const ok of [
      "nptel.ac.in",
      "onlinecourses.nptel.ac.in",
      "example.com",
      "news@example.com",
      "first.last+tag@mail.example.co.uk",
      "a-b.example.org",
    ]) {
      expect(validateTrackedEntry(ok), ok).toBeNull();
    }
  });

  it("rejects everything else", () => {
    for (const bad of [
      "",
      "nodot",
      "localhost",
      "https://nptel.ac.in",
      "javascript:alert(1)",
      "a b.com",
      "-bad.com",
      "bad-.com",
      "a..b.com",
      ".com",
      "x@",
      "@",
      "x@@b.com",
      "x@b",
      "x@b.c",
      "a.com/path",
      "from:boss@work.test",
      "(a.com)",
      `${"a".repeat(64)}.com`,
    ]) {
      expect(validateTrackedEntry(bad), bad).not.toBeNull();
    }
  });

  it("normalises before validating: case, spaces and a leading @", () => {
    expect(normalizeEntry("  @NPTEL.ac.in ")).toBe("nptel.ac.in");
    expect(validateTrackedEntry(normalizeEntry("  News@Example.COM "))).toBeNull();
  });
});

describe("other lists keep their looser rule", () => {
  it("allows plain domains and rejects anything that looks like a search operator", () => {
    expect(validateLooseEntry("linkedin.com")).toBeNull();
    expect(validateLooseEntry("localhost")).toBeNull();
    expect(validateLooseEntry("a b")).not.toBeNull();
    expect(validateLooseEntry("javascript:alert(1)")).not.toBeNull();
    expect(validateLooseEntry("x OR from:y")).not.toBeNull();
  });
});

describe("conflicts between the two lists", () => {
  it("names both lists in the message", () => {
    expect(conflictMessage("nptel.ac.in", true, "Never scan mail from")).toBe(
      'nptel.ac.in is already in "Never scan mail from". Remove it there first.',
    );
    expect(conflictMessage("nptel.ac.in", false, "Never scan mail from")).toBeNull();
  });
});
