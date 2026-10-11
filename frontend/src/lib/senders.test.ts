import { describe, expect, it } from "vitest";
import {
  normalizeEntry,
  normalizeKeyword,
  validateKeywordEntry,
  validateSenderEntry,
  validateSourceName,
} from "@/lib/senders";

describe("sender entries", () => {
  it("accepts domains and full addresses", () => {
    for (const ok of [
      "nptel.ac.in",
      "onlinecourses.nptel.ac.in",
      "example.com",
      "news@example.com",
      "first.last+tag@mail.example.co.uk",
      "a-b.example.org",
    ]) {
      expect(validateSenderEntry(ok), ok).toBeNull();
    }
  });

  it("rejects everything else, including anything that could act as a search operator", () => {
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
      "x OR from:y",
      "(a.com)",
      `${"a".repeat(64)}.com`,
    ]) {
      expect(validateSenderEntry(bad), bad).not.toBeNull();
    }
  });

  it("normalises before validating: case, spaces and a leading @", () => {
    expect(normalizeEntry("  @NPTEL.ac.in ")).toBe("nptel.ac.in");
    expect(validateSenderEntry(normalizeEntry("  News@Example.COM "))).toBeNull();
  });
});

describe("watch keywords", () => {
  it("accepts words and short phrases", () => {
    for (const ok of ["hackathon", "ideathon", "smart india", "web3", "e-summit"]) {
      expect(validateKeywordEntry(ok), ok).toBeNull();
    }
  });

  it("rejects operators, punctuation and over-long text", () => {
    for (const bad of ["", "in:inbox", "a) OR (from:boss@work.test", "-spam", "x".repeat(61)]) {
      expect(validateKeywordEntry(bad), bad).not.toBeNull();
    }
  });

  it("normalises case and spacing", () => {
    expect(normalizeKeyword("  Smart   INDIA ")).toBe("smart india");
  });
});

describe("source names", () => {
  it("must be present, short and unique ignoring case", () => {
    expect(validateSourceName("Digii", ["NPTEL"])).toBeNull();
    expect(validateSourceName("   ", [])).not.toBeNull();
    expect(validateSourceName("x".repeat(41), [])).not.toBeNull();
    expect(validateSourceName("nptel", ["NPTEL"])).toBe('You already have a source called "nptel".');
  });
});
