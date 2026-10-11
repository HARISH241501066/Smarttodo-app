import { describe, expect, it } from "vitest";
import { groupDeadlines, groupKey, istDay } from "@/lib/deadlines";
import type { Task } from "@/lib/types";

// 2030-01-10 10:00 IST (04:30Z), a Thursday
const NOW = Date.parse("2030-01-10T04:30:00Z");
const HOUR = 3_600_000;

const task = (id: number, deadline: string | null, over: Partial<Task> = {}): Task =>
  ({
    id,
    title: `t${id}`,
    deadline,
    status: "open",
    origin: "manual",
    updated_at: "2030-01-01T00:00:00Z",
    ...over,
  }) as Task;

const at = (ms: number) => new Date(ms).toISOString();

describe("India day boundaries", () => {
  it("rolls over at 18:30 UTC", () => {
    expect(istDay(Date.parse("2030-01-10T18:29:00Z"))).toBe(istDay(Date.parse("2030-01-10T00:00:00Z")));
    expect(istDay(Date.parse("2030-01-10T18:30:00Z"))).toBe(istDay(Date.parse("2030-01-10T00:00:00Z")) + 1);
  });
});

describe("deadline groups", () => {
  it("puts a task in the right group", () => {
    const key = (iso: string | null, over: Partial<Task> = {}) => groupKey(task(1, iso, over), NOW);
    expect(key(at(NOW - HOUR))).toBe("overdue"); // earlier today but already past
    expect(key(at(NOW - 40 * 24 * HOUR))).toBe("overdue");
    expect(key(at(NOW + HOUR))).toBe("today");
    expect(key("2030-01-10T18:29:00Z")).toBe("today"); // 23:59 IST tonight
    expect(key("2030-01-10T18:30:00Z")).toBe("tomorrow"); // 00:00 IST tomorrow
    expect(key("2030-01-11T18:29:00Z")).toBe("tomorrow");
    expect(key("2030-01-12T06:00:00Z")).toBe("week");
    expect(key("2030-01-16T12:00:00Z")).toBe("week"); // 6 days ahead
    expect(key("2030-01-17T06:00:00Z")).toBe("later");
    expect(key(null)).toBe("none");
    expect(key(at(NOW - HOUR), { status: "done" })).toBe("completed");
  });

  it("sorts inside each group by deadline and mixes both origins", () => {
    const tasks = [
      task(1, at(NOW + 3 * HOUR), { origin: "email" }),
      task(2, at(NOW + 1 * HOUR)),
      task(3, at(NOW - 5 * HOUR), { origin: "email" }),
      task(4, at(NOW - 50 * HOUR)),
      task(5, null),
      task(6, "2030-01-20T00:00:00Z"),
    ];
    const groups = groupDeadlines(tasks, NOW);
    expect(groups.map((g) => g.key)).toEqual(["overdue", "today", "later", "none"]);
    expect(groups[0].tasks.map((t) => t.id)).toEqual([4, 3]);
    expect(groups[1].tasks.map((t) => t.id)).toEqual([2, 1]);
    expect(groups[1].tasks.map((t) => t.origin)).toEqual(["manual", "email"]);
  });

  it("filters by origin and drops empty groups", () => {
    const tasks = [task(1, at(NOW + HOUR), { origin: "email" }), task(2, at(NOW + 2 * HOUR))];
    expect(groupDeadlines(tasks, NOW, "email")[0].tasks.map((t) => t.id)).toEqual([1]);
    expect(groupDeadlines(tasks, NOW, "manual")[0].tasks.map((t) => t.id)).toEqual([2]);
    expect(groupDeadlines([], NOW)).toEqual([]);
  });

  it("lists completed tasks last, newest first", () => {
    const done = (id: number, updated: string) => task(id, null, { status: "done", updated_at: updated });
    const groups = groupDeadlines([done(1, "2030-01-02T00:00:00Z"), task(2, null), done(3, "2030-01-05T00:00:00Z")], NOW);
    expect(groups.map((g) => g.key)).toEqual(["none", "completed"]);
    expect(groups[1].tasks.map((t) => t.id)).toEqual([3, 1]);
  });
});
