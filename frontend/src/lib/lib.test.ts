import { describe, expect, it } from "vitest";
import { formatDetail } from "@/lib/api";
import { buildConfirm, buildCreate, buildPatch, groupBySender } from "@/lib/tasks";
import {
  domainOf,
  formatCountdown,
  isHttpUrl,
  isoToIstInput,
  istInputToIso,
  timeAgo,
} from "@/lib/time";
import type { Task, TaskInput } from "@/lib/types";

const HOUR = 3600 * 1000;
const NOW = Date.parse("2030-01-01T06:00:00Z");

describe("formatCountdown", () => {
  it("counts down in minutes, hours, days", () => {
    expect(formatCountdown("2030-01-01T06:45:00Z", NOW).text).toBe("Due in 45m");
    expect(formatCountdown("2030-01-01T09:20:00Z", NOW).text).toBe("Due in 3h 20m");
    expect(formatCountdown("2030-01-03T10:00:00Z", NOW).text).toBe("Due in 2d 4h");
    expect(formatCountdown("2030-01-13T06:00:00Z", NOW).text).toBe("Due in 12 days");
  });
  it("flags overdue and the 48 hour window", () => {
    const overdue = formatCountdown("2030-01-01T03:30:00Z", NOW);
    expect(overdue).toMatchObject({ text: "Overdue by 2h 30m", overdue: true, soon: false });
    expect(formatCountdown(new Date(NOW + 47 * HOUR).toISOString(), NOW).soon).toBe(true);
    expect(formatCountdown(new Date(NOW + 49 * HOUR).toISOString(), NOW).soon).toBe(false);
  });
  it("handles right now", () => {
    expect(formatCountdown(new Date(NOW + 20_000).toISOString(), NOW).text).toBe("Due in less than a minute");
  });
});

describe("India time conversion", () => {
  it("reads datetime-local input as IST", () => {
    expect(istInputToIso("2030-01-22T23:59")).toBe("2030-01-22T23:59:00+05:30");
    expect(new Date(istInputToIso("2030-01-22T23:59")!).toISOString()).toBe("2030-01-22T18:29:00.000Z");
    expect(istInputToIso("  ")).toBeNull();
  });
  it("round-trips a stored UTC instant back into the input", () => {
    expect(isoToIstInput("2030-01-22T18:29:00Z")).toBe("2030-01-22T23:59");
    expect(isoToIstInput("2030-01-22T20:00:00+00:00")).toBe("2030-01-23T01:30"); // crosses midnight
    expect(isoToIstInput(null)).toBe("");
    const input = "2030-06-05T09:05";
    expect(isoToIstInput(new Date(istInputToIso(input)!).toISOString())).toBe(input);
  });
});

describe("helpers", () => {
  it("accepts only absolute http(s) links", () => {
    for (const ok of ["https://example.com", "http://example.com/a?b=1", " https://forms.gle/x "]) {
      expect(isHttpUrl(ok)).toBe(true);
    }
    for (const bad of ["javascript:alert(1)", "ftp://x.test", "example.com", "//evil.test", "", "https://", "data:text/html,x", "https://a b.test"]) {
      expect(isHttpUrl(bad)).toBe(false);
    }
  });
  it("extracts a link's domain", () => {
    expect(domainOf("https://www.example.com/a")).toBe("example.com");
    expect(domainOf("https://onlinecourses.nptel.ac.in/x")).toBe("onlinecourses.nptel.ac.in");
    expect(domainOf("nonsense")).toBeNull();
    expect(domainOf(null)).toBeNull();
  });
  it("describes how long ago", () => {
    expect(timeAgo("2030-01-01T05:59:50Z", NOW)).toBe("just now");
    expect(timeAgo("2030-01-01T05:15:00Z", NOW)).toBe("45m ago");
    expect(timeAgo("2030-01-01T01:00:00Z", NOW)).toBe("5h ago");
    expect(timeAgo("2029-12-30T06:00:00Z", NOW)).toBe("2d ago");
  });
  it("turns FastAPI error bodies into text", () => {
    expect(formatDetail("Task not found")).toBe("Task not found");
    expect(
      formatDetail([{ loc: ["body", "link"], msg: "Value error, link must be an http or https URL" }]),
    ).toBe("link: link must be an http or https URL");
    expect(formatDetail([{ loc: ["body"], msg: "Field required" }])).toBe("Field required");
    expect(formatDetail(null)).toBeNull();
  });
});

const task = (over: Partial<Task> = {}): Task => ({
  id: 1,
  title: "Quiz",
  notes: null,
  deadline: "2030-01-22T18:29:00+00:00",
  deadline_ist: null,
  deadline_display: null,
  priority: "important",
  source: "nptel",
  source_name: "NPTEL",
  status: "open",
  link: "https://example.com/q",
  link_type: "action",
  link_domain: "example.com",
  link_confidence: 0.8,
  confidence: 0.9,
  sender_email: null,
  created_at: "2030-01-01T00:00:00Z",
  updated_at: "2030-01-01T00:00:00Z",
  ...over,
});

const input = (over: Partial<TaskInput> = {}): TaskInput => ({
  title: "Quiz",
  deadline: "2030-01-22T23:59:00+05:30",
  link: "https://example.com/q",
  notes: null,
  priority: null,
  ...over,
});

describe("task request bodies", () => {
  it("create sends only what has a value", () => {
    expect(buildCreate(input({ deadline: null, link: null }))).toEqual({ title: "Quiz" });
    expect(buildCreate(input({ priority: "urgent", notes: "n" }))).toMatchObject({ priority: "urgent", notes: "n" });
  });
  it("patch sends nothing when nothing changed", () => {
    expect(buildPatch(task(), input())).toEqual({});
  });
  it("patch sends just the changes", () => {
    expect(buildPatch(task(), input({ title: "New" }))).toEqual({ title: "New" });
    expect(buildPatch(task(), input({ deadline: "2030-01-25T10:00:00+05:30" }))).toEqual({
      deadline: "2030-01-25T10:00:00+05:30",
    });
    expect(buildPatch(task(), input({ deadline: null }))).toEqual({ deadline: null });
    expect(buildPatch(task(), input({ link: null }))).toEqual({ link: null });
    expect(buildPatch(task(), input({ priority: "urgent" }))).toEqual({ priority: "urgent" });
  });
  it("an untouched Gmail-fallback link is not sent (it would clear the link)", () => {
    const fallback = task({ link: "https://mail.google.com/mail/u/0/#all/abc", link_type: "gmail" });
    expect(buildPatch(fallback, input({ link: null }))).toEqual({});
    expect(buildPatch(fallback, input({ link: "https://forms.gle/x" }))).toEqual({ link: "https://forms.gle/x" });
  });
  it("confirm sends only title, deadline and link edits", () => {
    expect(buildConfirm(task(), input())).toEqual({});
    expect(buildConfirm(task(), input({ title: "Fixed", deadline: null, notes: "x" }))).toEqual({ title: "Fixed" });
    expect(buildConfirm(task({ deadline: null }), input({ deadline: "2030-02-01T10:00:00+05:30" }))).toEqual({
      deadline: "2030-02-01T10:00:00+05:30",
    });
  });
});

describe("review grouping", () => {
  const item = (id: number, sender: string | null): Task => task({ id, title: `t${id}`, sender_email: sender });

  it("groups by sender address, biggest first, with unknown senders last", () => {
    const groups = groupBySender([
      item(1, "b@x.com"),
      item(2, "a@x.com"),
      item(3, null),
      item(4, "b@x.com"),
      item(5, "b@x.com"),
      item(6, "c@x.com"),
      item(7, "a@x.com"),
    ]);
    expect(groups.map((g) => [g.sender, g.tasks.map((t) => t.id)])).toEqual([
      ["b@x.com", [1, 4, 5]],
      ["a@x.com", [2, 7]],
      ["c@x.com", [6]],
      [null, [3]],
    ]);
  });

  it("handles nothing", () => {
    expect(groupBySender([])).toEqual([]);
  });
});
