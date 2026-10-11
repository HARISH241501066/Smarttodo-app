"use client";

import { useState } from "react";
import { useApp } from "@/components/AppProvider";
import { SourceBadge } from "@/components/TaskCard";
import { TaskModal } from "@/components/TaskModal";
import { useToast } from "@/components/toast";
import { Button, EmptyState, ErrorNote, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useLoader } from "@/lib/hooks";
import { buildConfirm } from "@/lib/tasks";
import { domainOf } from "@/lib/time";
import type { Task, TaskInput } from "@/lib/types";

export default function ReviewPage() {
  const { refreshCounts, refreshNotifications } = useApp();
  const toast = useToast();
  const { data: tasks, setData: setTasks, error } = useLoader<Task[]>("/review");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [editing, setEditing] = useState<Task | null>(null);

  function removeLocally(id: number) {
    setTasks((list) => list?.filter((t) => t.id !== id) ?? list);
    void refreshCounts();
    void refreshNotifications();
  }

  async function confirm(task: Task, body?: Record<string, unknown>) {
    setBusyId(task.id);
    try {
      await api(`/review/${task.id}/confirm`, { method: "POST", json: body });
      removeLocally(task.id);
      toast.show("Confirmed. It is now in your tasks.");
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setBusyId(null);
    }
  }

  async function dismiss(task: Task) {
    setBusyId(task.id);
    try {
      await api(`/tasks/${task.id}/dismiss`, { method: "POST" });
      removeLocally(task.id);
      toast.show("Dismissed");
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setBusyId(null);
    }
  }

  async function submitEdit(input: TaskInput) {
    if (!editing) return;
    await api(`/review/${editing.id}/confirm`, { method: "POST", json: buildConfirm(editing, input) });
    removeLocally(editing.id);
    setEditing(null);
    toast.show("Confirmed. It is now in your tasks.");
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Needs review</h1>
        <p className="mt-1 text-sm text-muted">
          We weren&apos;t sure about these emails, usually because no clear deadline was found. Confirm the
          ones that are real tasks.
        </p>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {tasks === null ? (
        error ? null : <Spinner />
      ) : tasks.length === 0 ? (
        <EmptyState title="Nothing to review">Emails we are unsure about will appear here.</EmptyState>
      ) : (
        tasks.map((task) => {
          const domain = task.link_domain ?? domainOf(task.link);
          return (
            <article
              key={task.id}
              data-testid="review-card"
              className="rounded-xl border border-line bg-surface p-4 shadow-sm"
            >
              <div className="flex items-start justify-between gap-3">
                <h2 className="min-w-0 break-words font-semibold leading-snug">{task.title}</h2>
                <SourceBadge source={task.source} />
              </div>
              <dl className="mt-2 space-y-0.5 text-sm">
                <div className="flex gap-2">
                  <dt className="text-muted">Deadline:</dt>
                  <dd>{task.deadline_display ?? "none found"}</dd>
                </div>
                {task.confidence !== null ? (
                  <div className="flex gap-2">
                    <dt className="text-muted">Confidence:</dt>
                    <dd>{Math.round(task.confidence * 100)}%</dd>
                  </div>
                ) : null}
              </dl>
              {task.link ? (
                <p className="mt-2 text-sm">
                  <a
                    href={task.link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="font-semibold text-primary underline"
                  >
                    {task.link_type === "gmail" ? "View the email" : "Open the link"}
                  </a>
                  {domain ? <span className="text-muted"> · {domain}</span> : null}
                </p>
              ) : null}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="primary" size="sm" disabled={busyId === task.id} onClick={() => void confirm(task)}>
                  Confirm
                </Button>
                <Button size="sm" disabled={busyId === task.id} onClick={() => setEditing(task)}>
                  Edit &amp; confirm
                </Button>
                <Button variant="ghost" size="sm" disabled={busyId === task.id} onClick={() => void dismiss(task)}>
                  Dismiss
                </Button>
              </div>
            </article>
          );
        })
      )}

      {editing ? (
        <TaskModal mode="review" task={editing} onClose={() => setEditing(null)} onSubmit={submitEdit} />
      ) : null}
    </div>
  );
}
