import type { Priority, Task, TaskInput } from "@/lib/types";

export const GROUPS: { key: Priority; label: string; dot: string }[] = [
  { key: "urgent", label: "Urgent", dot: "bg-danger" },
  { key: "important", label: "Important", dot: "bg-warn" },
  { key: "regular", label: "Regular", dot: "bg-muted" },
];

/** Body for POST /tasks: only the fields that have a value. */
export function buildCreate(input: TaskInput): Record<string, unknown> {
  const body: Record<string, unknown> = { title: input.title };
  if (input.deadline) body.deadline = input.deadline;
  if (input.link) body.link = input.link;
  if (input.notes) body.notes = input.notes;
  if (input.priority) body.priority = input.priority;
  return body;
}

const minute = (iso: string | null) => (iso ? Math.floor(new Date(iso).getTime() / 60000) : null);

/** Body for PATCH /tasks/{id}: only what changed (an empty object means nothing to save).
 *  A Gmail-fallback link is shown as an empty field, so leaving it empty must not clear it. */
export function buildPatch(task: Task, input: TaskInput): Record<string, unknown> {
  const patch: Record<string, unknown> = {};
  if (input.title !== task.title) patch.title = input.title;
  if ((input.notes ?? null) !== (task.notes ?? null)) patch.notes = input.notes;
  if (minute(input.deadline) !== minute(task.deadline)) patch.deadline = input.deadline;
  const originalLink = task.link_type === "gmail" ? null : task.link;
  if (input.link !== originalLink) patch.link = input.link;
  if (input.priority && input.priority !== task.priority) patch.priority = input.priority;
  return patch;
}

/** Body for POST /review/{id}/confirm: only fields the user changed. */
export function buildConfirm(task: Task, input: TaskInput): Record<string, unknown> {
  const { title, deadline, link } = buildPatch(task, input);
  const body: Record<string, unknown> = {};
  if (title !== undefined) body.title = title;
  if (deadline) body.deadline = deadline; // the API cannot clear a deadline on confirm
  if (link) body.link = link;
  return body;
}

export interface SenderGroup {
  sender: string | null; // null: older items that never recorded a sender
  tasks: Task[];
}

/** Review items grouped by sender address, biggest group first. Items without a sender keep their
 *  own group (last) because bulk actions need an address to act on. */
export function groupBySender(tasks: Task[]): SenderGroup[] {
  const groups = new Map<string, Task[]>();
  const unknown: Task[] = [];
  for (const task of tasks) {
    if (!task.sender_email) {
      unknown.push(task);
      continue;
    }
    groups.set(task.sender_email, [...(groups.get(task.sender_email) ?? []), task]);
  }
  const named = [...groups.entries()]
    .map(([sender, items]) => ({ sender, tasks: items }))
    .sort((a, b) => b.tasks.length - a.tasks.length || a.sender.localeCompare(b.sender));
  return unknown.length ? [...named, { sender: null, tasks: unknown }] : named;
}
