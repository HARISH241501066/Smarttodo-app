import type { Task } from "@/lib/types";

export type DeadlineGroupKey = "overdue" | "today" | "tomorrow" | "week" | "later" | "none" | "completed";

export const DEADLINE_GROUPS: { key: DeadlineGroupKey; label: string }[] = [
  { key: "overdue", label: "Overdue" },
  { key: "today", label: "Today" },
  { key: "tomorrow", label: "Tomorrow" },
  { key: "week", label: "This week" },
  { key: "later", label: "Later" },
  { key: "none", label: "No deadline" },
  { key: "completed", label: "Completed" },
];

const DAY = 86_400_000;
const IST_OFFSET = 5.5 * 3_600_000;

/** Whole days since the epoch, counted on the India clock (days change at midnight IST). */
export const istDay = (ms: number): number => Math.floor((ms + IST_OFFSET) / DAY);

/** Which group a task belongs to, judged on India days. "This week" is the 5 days after tomorrow. */
export function groupKey(task: Task, nowMs: number): DeadlineGroupKey {
  if (task.status === "done") return "completed";
  if (!task.deadline) return "none";
  const at = new Date(task.deadline).getTime();
  if (at < nowMs) return "overdue";
  const days = istDay(at) - istDay(nowMs);
  if (days <= 0) return "today";
  if (days === 1) return "tomorrow";
  if (days <= 6) return "week";
  return "later";
}

export type OriginFilter = "all" | "manual" | "email";

/** Tasks grouped for the Deadlines page, empty groups left out, each sorted by deadline
 *  (soonest first; ties keep their order; completed ones newest first). */
export function groupDeadlines(
  tasks: Task[],
  nowMs: number,
  filter: OriginFilter = "all",
): { key: DeadlineGroupKey; label: string; tasks: Task[] }[] {
  const wanted = tasks.filter((t) => filter === "all" || t.origin === filter);
  return DEADLINE_GROUPS.map(({ key, label }) => {
    const items = wanted.filter((t) => groupKey(t, nowMs) === key);
    items.sort((a, b) =>
      key === "completed"
        ? b.updated_at.localeCompare(a.updated_at)
        : (a.deadline ? new Date(a.deadline).getTime() : Infinity) -
          (b.deadline ? new Date(b.deadline).getTime() : Infinity),
    );
    return { key, label, tasks: items };
  }).filter((g) => g.tasks.length > 0);
}
