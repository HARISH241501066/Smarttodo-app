"use client";

import { useMemo, useState } from "react";
import { useApp } from "@/components/AppProvider";
import { MailIcon, UserIcon } from "@/components/icons";
import { SubtaskProgress } from "@/components/SubtaskChecklist";
import { useToast } from "@/components/toast";
import { EmptyState, ErrorNote, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { groupDeadlines, type DeadlineGroupKey, type OriginFilter } from "@/lib/deadlines";
import { useLoader, useNow } from "@/lib/hooks";
import { domainOf, formatCountdown } from "@/lib/time";
import type { Task } from "@/lib/types";

const FILTERS: { key: OriginFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "manual", label: "My Tasks" },
  { key: "email", label: "From Email" },
];

const GROUP_STYLE: Partial<Record<DeadlineGroupKey, string>> = {
  overdue: "text-danger",
  today: "text-warn",
};

export default function DeadlinesPage() {
  const now = useNow();
  const toast = useToast();
  const { refreshCounts, refreshNotifications } = useApp();
  const [filter, setFilter] = useState<OriginFilter>("all");
  const [showDone, setShowDone] = useState(false);
  const { data, setData, error, reload } = useLoader<Task[]>(`/deadlines?include_done=${showDone}`, {
    intervalMs: 60_000,
  });
  const [busyId, setBusyId] = useState<number | null>(null);

  const groups = useMemo(() => groupDeadlines(data ?? [], now, filter), [data, now, filter]);

  async function setDone(task: Task, done: boolean) {
    setBusyId(task.id);
    try {
      if (done) await api(`/tasks/${task.id}/done`, { method: "POST" });
      else await api(`/tasks/${task.id}`, { method: "PATCH", json: { status: "open" } });
      if (done && !showDone) setData((list) => list?.filter((t) => t.id !== task.id) ?? list);
      else reload();
      if (done) {
        toast.show("Marked as done", { action: { label: "Undo", onClick: () => void setDone(task, false) } });
      }
      void refreshCounts();
      void refreshNotifications();
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-xl font-bold">Deadlines</h1>
        <p className="mt-1 text-sm text-muted">Every open task from both places, soonest first.</p>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <div role="group" aria-label="Show" className="flex flex-wrap gap-2">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              aria-pressed={filter === f.key}
              onClick={() => setFilter(f.key)}
              className={`rounded-full border px-3.5 py-1.5 text-sm font-semibold ${
                filter === f.key ? "border-primary bg-primary text-primary-fg" : "border-line bg-surface text-fg"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <label className="ml-auto flex items-center gap-2 text-sm font-semibold">
          <input
            type="checkbox"
            className="h-5 w-5"
            checked={showDone}
            onChange={(e) => setShowDone(e.target.checked)}
          />
          Show completed
        </label>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {data === null ? (
        error ? null : <Spinner />
      ) : groups.length === 0 ? (
        <EmptyState title="No deadlines">Nothing is waiting on you in this view.</EmptyState>
      ) : (
        groups.map((group) => (
          <section
            key={group.key}
            aria-label={group.label}
            data-testid={`group-${group.key}`}
            className="space-y-2"
          >
            <h2
              className={`flex items-center gap-2 text-sm font-bold uppercase tracking-wide ${GROUP_STYLE[group.key] ?? "text-muted"}`}
            >
              {group.label}
              <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted">{group.tasks.length}</span>
            </h2>
            <ul className="space-y-2">
              {group.tasks.map((task) => (
                <Row key={task.id} task={task} now={now} group={group.key} busy={busyId === task.id} onDone={setDone} />
              ))}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}

function Row({
  task,
  now,
  group,
  busy,
  onDone,
}: {
  task: Task;
  now: number;
  group: DeadlineGroupKey;
  busy: boolean;
  onDone: (task: Task, done: boolean) => void;
}) {
  const completed = task.status === "done";
  const countdown = task.deadline ? formatCountdown(task.deadline, now) : null;
  const manual = task.origin === "manual";
  const label = manual ? task.tag : task.source_name;
  const domain = task.link_domain ?? domainOf(task.link);
  return (
    <li
      data-testid="deadline-row"
      data-origin={task.origin}
      className={`flex items-start gap-3 rounded-xl border border-l-4 bg-surface p-3 shadow-sm ${
        group === "overdue" ? "border-line border-l-danger" : "border-line"
      } ${completed ? "opacity-70" : ""}`}
    >
      <input
        type="checkbox"
        className="mt-1 h-5 w-5 shrink-0"
        checked={completed}
        disabled={busy}
        aria-label={completed ? `Reopen ${task.title}` : `Mark ${task.title} done`}
        onChange={(e) => onDone(task, e.target.checked)}
      />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-start gap-2">
          <span
            title={manual ? "My task" : "From email"}
            data-testid="origin-icon"
            className="mt-0.5 shrink-0 text-muted"
          >
            {manual ? <UserIcon width={16} height={16} /> : <MailIcon width={16} height={16} />}
            <span className="sr-only">{manual ? "My task" : "From email"}</span>
          </span>
          <p className={`min-w-0 break-words font-semibold leading-snug ${completed ? "line-through" : ""}`}>
            {task.title}
          </p>
        </div>
        <p className="text-sm text-muted">
          {label ? <span className="font-semibold text-fg">{manual ? `#${label}` : label}</span> : null}
          {label ? " · " : null}
          {task.deadline ? (
            <>
              <span>{task.deadline_display}</span>
              {countdown && !completed ? (
                <span className={`font-semibold ${countdown.overdue ? "text-danger" : countdown.soon ? "text-warn" : ""}`}>
                  {" "}
                  · {countdown.text}
                </span>
              ) : null}
            </>
          ) : (
            <span>No deadline</span>
          )}
        </p>
        <SubtaskProgress done={task.subtasks_done} total={task.subtasks_total} />
      </div>
      {task.link && !completed ? (
        <a
          href={task.link}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open ${task.title}`}
          className="shrink-0 rounded-lg border border-line px-3 py-2 text-sm font-semibold text-primary hover:bg-surface-2"
        >
          {task.link_type === "gmail" ? "Open email" : "Open"}
          {domain && task.link_type !== "gmail" ? <span className="sr-only"> ({domain})</span> : null}
        </a>
      ) : null}
    </li>
  );
}
