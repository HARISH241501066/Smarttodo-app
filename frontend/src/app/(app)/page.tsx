"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useApp } from "@/components/AppProvider";
import { PlusIcon } from "@/components/icons";
import { TaskCard } from "@/components/TaskCard";
import { TaskModal } from "@/components/TaskModal";
import { useToast } from "@/components/toast";
import { Button, ConfirmDialog, EmptyState, ErrorNote, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useLoader, useNow } from "@/lib/hooks";
import { buildCreate, buildPatch, GROUPS } from "@/lib/tasks";
import type { Task, TaskInput } from "@/lib/types";

export default function TasksPage() {
  // useSearchParams (for ?task=ID from the bell) must sit under Suspense.
  return (
    <Suspense fallback={<Spinner />}>
      <Dashboard />
    </Suspense>
  );
}

type ModalState = { mode: "create" } | { mode: "edit"; task: Task } | null;

function Dashboard() {
  const router = useRouter();
  const params = useSearchParams();
  const { refreshNotifications, refreshCounts } = useApp();
  const toast = useToast();
  const now = useNow();

  const open = useLoader<Task[]>("/tasks?status=open&limit=500", { intervalMs: 60_000 });
  const [showDone, setShowDone] = useState(false);
  const done = useLoader<Task[]>("/tasks?status=done&limit=30", { enabled: showDone });
  const tasks = open.data;
  const doneTasks = done.data;
  const error = open.error ?? done.error;
  const load = open.reload;
  const loadDone = done.reload;
  const [busyId, setBusyId] = useState<number | null>(null);
  const [modal, setModal] = useState<ModalState>(null);
  const [toDelete, setToDelete] = useState<Task | null>(null);
  const [deleting, setDeleting] = useState(false);

  const afterChange = useCallback(() => {
    void refreshNotifications();
    void refreshCounts();
  }, [refreshNotifications, refreshCounts]);

  // A notification click arrives as /?task=ID: scroll to that card, or open the task if it is
  // not in the open list (already done, say).
  const focusId = Number(params.get("task")) || null;
  useEffect(() => {
    if (!focusId || tasks === null) return;
    const el = document.getElementById(`task-${focusId}`);
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.classList.add("flash");
      window.setTimeout(() => el.classList.remove("flash"), 1800);
      router.replace("/");
      return;
    }
    let cancelled = false;
    api<Task>(`/tasks/${focusId}`)
      .then((task) => {
        if (!cancelled) setModal({ mode: "edit", task });
      })
      .catch(() => toast.show("That task no longer exists.", { kind: "error" }))
      .finally(() => router.replace("/"));
    return () => {
      cancelled = true;
    };
  }, [focusId, tasks, router, toast]);

  async function run(task: Task, work: () => Promise<void>) {
    setBusyId(task.id);
    try {
      await work();
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setBusyId(null);
    }
  }

  async function reopen(task: Task) {
    await run(task, async () => {
      await api(`/tasks/${task.id}`, { method: "PATCH", json: { status: "open" } });
      load();
      if (showDone) loadDone();
      afterChange();
    });
  }

  async function markDone(task: Task) {
    await run(task, async () => {
      await api(`/tasks/${task.id}/done`, { method: "POST" });
      open.setData((list) => list?.filter((t) => t.id !== task.id) ?? list);
      if (showDone) loadDone();
      toast.show("Marked as done", { action: { label: "Undo", onClick: () => void reopen(task) } });
      afterChange();
    });
  }

  async function confirmDelete() {
    if (!toDelete) return;
    const task = toDelete;
    setDeleting(true);
    try {
      await api(`/tasks/${task.id}`, { method: "DELETE" });
      open.setData((list) => list?.filter((t) => t.id !== task.id) ?? list);
      done.setData((list) => list?.filter((t) => t.id !== task.id) ?? list);
      setToDelete(null);
      toast.show("Task deleted");
      afterChange();
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setDeleting(false);
    }
  }

  async function save(input: TaskInput) {
    if (modal?.mode === "create") {
      await api("/tasks", { method: "POST", json: buildCreate(input) });
      toast.show("Task added");
    } else if (modal?.mode === "edit") {
      const patch = buildPatch(modal.task, input);
      if (Object.keys(patch).length > 0) {
        await api(`/tasks/${modal.task.id}`, { method: "PATCH", json: patch });
        toast.show("Task updated");
      }
    }
    setModal(null);
    load();
    if (showDone) loadDone();
    afterChange();
  }

  const openTasks = tasks ?? [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-bold">Your tasks</h1>
        <Button variant="primary" onClick={() => setModal({ mode: "create" })}>
          <PlusIcon width={18} height={18} /> Add task
        </Button>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {tasks === null ? (
        error ? null : <Spinner />
      ) : openTasks.length === 0 ? (
        <EmptyState title="Nothing to do right now">
          New actionable emails show up here on their own. You can also add a task yourself.
        </EmptyState>
      ) : (
        GROUPS.map((group) => {
          const items = openTasks.filter((t) => t.priority === group.key);
          if (items.length === 0) return null;
          return (
            <section key={group.key} aria-labelledby={`group-${group.key}`} className="space-y-3">
              <h2 id={`group-${group.key}`} className="flex items-center gap-2 text-sm font-bold uppercase tracking-wide text-muted">
                <span className={`h-2.5 w-2.5 rounded-full ${group.dot}`} aria-hidden="true" />
                {group.label}
                <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs">{items.length}</span>
              </h2>
              {items.map((task) => (
                <TaskCard
                  key={task.id}
                  task={task}
                  now={now}
                  busy={busyId === task.id}
                  onDone={markDone}
                  onEdit={(t) => setModal({ mode: "edit", task: t })}
                  onDelete={setToDelete}
                />
              ))}
            </section>
          );
        })
      )}

      <section className="space-y-3">
        <button
          type="button"
          onClick={() => setShowDone((s) => !s)}
          aria-expanded={showDone}
          className="text-sm font-semibold text-primary"
        >
          {showDone ? "Hide completed tasks" : "Show completed tasks"}
        </button>
        {showDone ? (
          doneTasks === null ? (
            <Spinner />
          ) : doneTasks.length === 0 ? (
            <p className="text-sm text-muted">Nothing completed yet.</p>
          ) : (
            doneTasks.map((task) => (
              <TaskCard
                key={task.id}
                task={task}
                now={now}
                completed
                busy={busyId === task.id}
                onReopen={reopen}
                onEdit={(t) => setModal({ mode: "edit", task: t })}
                onDelete={setToDelete}
              />
            ))
          )
        ) : null}
      </section>

      {modal ? (
        <TaskModal
          mode={modal.mode}
          task={modal.mode === "edit" ? modal.task : undefined}
          onClose={() => setModal(null)}
          onSubmit={save}
        />
      ) : null}

      {toDelete ? (
        <ConfirmDialog
          title="Delete this task?"
          message={
            <>
              <strong className="text-fg">{toDelete.title}</strong> will be removed along with its reminders and
              Calendar event. This can&apos;t be undone.
            </>
          }
          confirmLabel="Delete"
          danger
          busy={deleting}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setToDelete(null)}
        />
      ) : null}
    </div>
  );
}
