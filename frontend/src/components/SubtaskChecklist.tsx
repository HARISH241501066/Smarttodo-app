"use client";

import { useState, type FormEvent } from "react";
import { ChevronIcon, TrashIcon } from "@/components/icons";
import { inputClass, Button } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import type { SubtaskChange, Task } from "@/lib/types";

/** "3/5" with a bar. Nothing is drawn for a task without subtasks. */
export function SubtaskProgress({ done, total }: { done: number; total: number }) {
  if (total === 0) return null;
  const pct = Math.round((done / total) * 100);
  return (
    <div className="flex items-center gap-2" data-testid="subtask-progress">
      <div
        role="progressbar"
        aria-label="Subtasks done"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
        className="h-2 min-w-16 flex-1 overflow-hidden rounded-full bg-surface-2"
      >
        <div
          className={`h-full rounded-full ${done === total ? "bg-ok" : "bg-primary"}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-semibold tabular-nums text-muted" data-testid="subtask-count">
        {done}/{total}
      </span>
    </div>
  );
}

interface Props {
  task: Task;
  disabled?: boolean;
  /** Called with the server's answer after every change. */
  onChange: (change: SubtaskChange, ticked: boolean) => void;
  onError: (message: string) => void;
}

/** The progress bar plus an expandable checklist: tick items, add your own, remove them.
 *  Works the same on manual and email tasks. */
export function SubtaskChecklist({ task, disabled, onChange, onError }: Props) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  async function call(path: string, init: { method: string; json?: unknown }, ticked = false) {
    setBusy(true);
    try {
      onChange(await api<SubtaskChange>(`/tasks/${task.id}/subtasks${path}`, init), ticked);
    } catch (err) {
      onError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    const title = draft.trim();
    if (!title) return;
    setDraft("");
    await call("", { method: "POST", json: { title } });
  }

  const total = task.subtasks_total;
  return (
    <div className="mt-3 space-y-2" data-testid="subtasks">
      <SubtaskProgress done={task.subtasks_done} total={total} />
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1 text-xs font-semibold text-primary"
      >
        <ChevronIcon width={14} height={14} className={open ? "rotate-180" : ""} />
        {total > 0 ? (open ? "Hide checklist" : "Show checklist") : open ? "Hide" : "Add a checklist"}
      </button>
      {open ? (
        <div className="space-y-2">
          {total > 0 ? (
            <ul className="space-y-1" aria-label={`Subtasks of ${task.title}`}>
              {task.subtasks.map((sub) => (
                <li key={sub.id} className="flex items-center gap-2 rounded-lg px-1 py-1 hover:bg-surface-2">
                  <input
                    type="checkbox"
                    className="h-5 w-5 shrink-0"
                    checked={sub.done}
                    disabled={disabled || busy}
                    aria-label={sub.title}
                    onChange={(e) =>
                      void call(`/${sub.id}`, { method: "PATCH", json: { done: e.target.checked } }, e.target.checked)
                    }
                  />
                  <span className={`min-w-0 flex-1 break-words text-sm ${sub.done ? "text-muted line-through" : ""}`}>
                    {sub.title}
                  </span>
                  <button
                    type="button"
                    aria-label={`Delete subtask ${sub.title}`}
                    disabled={disabled || busy}
                    onClick={() => void call(`/${sub.id}`, { method: "DELETE" })}
                    className="rounded-lg p-1.5 text-muted hover:bg-line hover:text-fg"
                  >
                    <TrashIcon width={14} height={14} />
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
          <form onSubmit={(e) => void add(e)} className="flex gap-2">
            <input
              className={inputClass}
              value={draft}
              maxLength={300}
              placeholder="Add a subtask"
              aria-label={`Add a subtask to ${task.title}`}
              onChange={(e) => setDraft(e.target.value)}
            />
            <Button type="submit" size="sm" disabled={disabled || busy || !draft.trim()}>
              Add
            </Button>
          </form>
        </div>
      ) : null}
    </div>
  );
}
