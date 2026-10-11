"use client";

import { ExternalIcon, MailIcon, PencilIcon, TrashIcon, UndoIcon, CheckIcon } from "@/components/icons";
import { SubtaskChecklist } from "@/components/SubtaskChecklist";
import { Button } from "@/components/ui";
import { domainOf, formatCountdown } from "@/lib/time";
import type { Priority, Source, SubtaskChange, Task } from "@/lib/types";

export const SOURCE_LABEL: Record<Source, string> = {
  nptel: "NPTEL",
  digii: "Digii",
  custom: "Source",
  keyword: "Keyword",
  internship: "Internship",
  gform: "Google Form",
  college: "College",
  manual: "Manual",
  other: "Email",
  tracked: "Tracked",
};

const SOURCE_STYLE: Record<Source, string> = {
  nptel: "bg-orange-500/15 text-orange-700 dark:text-orange-300",
  digii: "bg-teal-500/15 text-teal-700 dark:text-teal-300",
  custom: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  keyword: "bg-pink-500/15 text-pink-700 dark:text-pink-300",
  internship: "bg-violet-500/15 text-violet-700 dark:text-violet-300",
  gform: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  college: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  manual: "bg-slate-500/15 text-slate-700 dark:text-slate-300",
  other: "bg-slate-500/15 text-slate-700 dark:text-slate-300",
  tracked: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-300",
};

const PRIORITY_BORDER: Record<Priority, string> = {
  urgent: "border-l-danger",
  important: "border-l-warn",
  regular: "border-l-line",
};

/** The badge shows the user's own name for the source (e.g. "Digii"), falling back to its kind. */
export function SourceBadge({ source, name }: { source: Source; name?: string | null }) {
  return (
    <span
      data-testid="source-badge"
      className={`max-w-[10rem] shrink-0 truncate rounded-full px-2.5 py-0.5 text-xs font-bold ${SOURCE_STYLE[source]}`}
    >
      {name || SOURCE_LABEL[source]}
    </span>
  );
}

interface Props {
  task: Task;
  now: number;
  busy?: boolean;
  completed?: boolean;
  onDone?: (task: Task) => void;
  onReopen?: (task: Task) => void;
  onEdit: (task: Task) => void;
  /** Manual tasks are deleted, email tasks are dismissed. */
  onDelete?: (task: Task) => void;
  onDismiss?: (task: Task) => void;
  onSubtaskChange?: (change: SubtaskChange, ticked: boolean) => void;
  onError?: (message: string) => void;
}

const PRIORITY_LABEL: Record<Priority, string> = { urgent: "Urgent", important: "Important", regular: "Regular" };

export function TaskCard({
  task,
  now,
  busy,
  completed,
  onDone,
  onReopen,
  onEdit,
  onDelete,
  onDismiss,
  onSubtaskChange,
  onError,
}: Props) {
  const manual = task.origin === "manual";
  const countdown = task.deadline ? formatCountdown(task.deadline, now) : null;
  const isEmailLink = task.link_type === "gmail";
  const domain = task.link_domain ?? domainOf(task.link);

  return (
    <article
      id={`task-${task.id}`}
      data-testid="task-card"
      className={`rounded-xl border border-l-4 border-line bg-surface p-4 shadow-sm ${PRIORITY_BORDER[task.priority]} ${completed ? "opacity-70" : ""}`}
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className={`min-w-0 break-words text-base font-semibold leading-snug ${completed ? "line-through" : ""}`}>
          {task.title}
        </h3>
        {manual ? (
          <span
            data-testid="priority-badge"
            className="shrink-0 rounded-full bg-surface-2 px-2.5 py-0.5 text-xs font-bold text-muted"
          >
            {PRIORITY_LABEL[task.priority]}
          </span>
        ) : (
          <SourceBadge source={task.source} name={task.source_name} />
        )}
      </div>
      {task.tag ? (
        <p className="mt-1">
          <span data-testid="task-tag" className="rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-semibold text-primary">
            #{task.tag}
          </span>
        </p>
      ) : null}

      <p className="mt-1.5 text-sm">
        {countdown ? (
          <>
            <span
              className={`font-semibold ${countdown.overdue ? "text-danger" : countdown.soon ? "text-warn" : "text-fg"}`}
            >
              {countdown.text}
            </span>
            <span className="text-muted"> · {task.deadline_display}</span>
          </>
        ) : (
          <span
            data-testid="no-deadline"
            className="rounded-full bg-surface-2 px-2.5 py-0.5 text-xs font-semibold text-muted"
          >
            No deadline
          </span>
        )}
      </p>
      {task.notes ? <p className="mt-1 whitespace-pre-line break-words text-sm text-muted">{task.notes}</p> : null}

      {onSubtaskChange ? (
        <SubtaskChecklist
          task={task}
          disabled={busy}
          onChange={onSubtaskChange}
          onError={(m) => onError?.(m)}
        />
      ) : null}

      {task.link && !completed ? (
        <div className="mt-3">
          <a
            href={task.link}
            target="_blank"
            rel="noopener noreferrer"
            className={`flex w-full items-center justify-center gap-2 rounded-xl px-5 py-3.5 text-base font-bold sm:inline-flex sm:w-auto ${
              isEmailLink
                ? "border border-line bg-surface-2 text-fg hover:bg-line/50"
                : "bg-primary text-primary-fg hover:opacity-90"
            }`}
          >
            {isEmailLink ? <MailIcon /> : <ExternalIcon />}
            {isEmailLink ? "Open email" : "Open & Complete"}
          </a>
          {domain ? <p className="mt-1 text-xs text-muted">{domain}</p> : null}
        </div>
      ) : !completed ? (
        <p className="mt-3 text-xs text-muted">No link. Edit the task to add one.</p>
      ) : null}

      <div className="mt-3 flex flex-wrap gap-2">
        {completed ? (
          <Button size="sm" onClick={() => onReopen?.(task)} disabled={busy}>
            <UndoIcon width={16} height={16} /> Reopen
          </Button>
        ) : (
          <Button size="sm" variant="secondary" onClick={() => onDone?.(task)} disabled={busy}>
            <CheckIcon width={16} height={16} /> Done
          </Button>
        )}
        <Button size="sm" variant="ghost" onClick={() => onEdit(task)} disabled={busy} aria-label={`Edit ${task.title}`}>
          <PencilIcon width={16} height={16} /> Edit
        </Button>
        {manual || completed ? (
          <Button size="sm" variant="ghost" onClick={() => onDelete?.(task)} disabled={busy} aria-label={`Delete ${task.title}`}>
            <TrashIcon width={16} height={16} /> Delete
          </Button>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => onDismiss?.(task)} disabled={busy} aria-label={`Dismiss ${task.title}`}>
            <TrashIcon width={16} height={16} /> Dismiss
          </Button>
        )}
      </div>
    </article>
  );
}
