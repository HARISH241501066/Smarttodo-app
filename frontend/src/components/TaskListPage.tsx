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
import { buildCreate, buildPatch, GROUPS, subtasksChanged, syncSubtasks } from "@/lib/tasks";
import type { SubtaskChange, Task, TaskInput } from "@/lib/types";

type Origin = "manual" | "email";
type ModalState = { mode: "create" } | { mode: "edit" | "email"; task: Task } | null;

const PATH: Record<Origin, string> = { manual: "/", email: "/email" };

interface Props {
  origin: Origin;
  title: string;
  empty: string;
}

/** My Tasks (origin manual) and From Email (origin email) share this page. */
export function TaskListPage(props: Props) {
  // useSearchParams (for ?task=ID from the bell) must sit under Suspense.
  return (
    <Suspense fallback={<Spinner />}>
      <List {...props} />
    </Suspense>
  );
}

function List({ origin, title, empty }: Props) {
  const router = useRouter();
  const params = useSearchParams();
  const { refreshNotifications, refreshCounts } = useApp();
  const toast = useToast();
  const now = useNow();
  const manual = origin === "manual";

  const open = useLoader<Task[]>(`/tasks?status=open&origin=${origin}&limit=500`, { intervalMs: 60_000 });
  const [showDone, setShowDone] = useState(false);
  const done = useLoader<Task[]>(`/tasks?status=done&origin=${origin}&limit=30`, { enabled: showDone });
  const tasks = open.data;
  const doneTasks = done.data;
  const error = open.error ?? done.error;
  const load = open.reload;
  const loadDone = done.reload;
  const [busyId, setBusyId] = useState<number | null>(null);
  const [modal, setModal] = useState<ModalState>(null);
  const [toDelete, setToDelete] = useState<Task | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [wholeDone, setWholeDone] = useState<Task | null>(null);
  const [finishing, setFinishing] = useState(false);

  const afterChange = useCallback(() => {
    void refreshNotifications();
    void refreshCounts();
  }, [refreshNotifications, refreshCounts]);

  // A notification click arrives as ?task=ID: scroll to that card; if it is not in this list, open
  // it (or move to the other tab when it belongs there).
  const focusId = Number(params.get("task")) || null;
  useEffect(() => {
    if (!focusId || tasks === null) return;
    const el = document.getElementById(`task-${focusId}`);
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.classList.add("flash");
      window.setTimeout(() => el.classList.remove("flash"), 1800);
      router.replace(PATH[origin]);
      return;
    }
    let cancelled = false;
    api<Task>(`/tasks/${focusId}`)
      .then((task) => {
        if (cancelled) return;
        if (task.origin !== origin && task.status === "open") {
          router.replace(`${PATH[task.origin]}?task=${task.id}`);
          return;
        }
        setModal({ mode: task.origin === "manual" ? "edit" : "email", task });
        router.replace(PATH[origin]);
      })
      .catch(() => {
        toast.show("That task no longer exists.", { kind: "error" });
        router.replace(PATH[origin]);
      });
    return () => {
      cancelled = true;
    };
  }, [focusId, tasks, router, toast, origin]);

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

  async function dismiss(task: Task) {
    await run(task, async () => {
      await api(`/tasks/${task.id}/dismiss`, { method: "POST" });
      open.setData((list) => list?.filter((t) => t.id !== task.id) ?? list);
      toast.show("Dismissed");
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

  function onSubtaskChange(change: SubtaskChange, ticked: boolean) {
    const replace = (list: Task[] | null) => list?.map((t) => (t.id === change.task.id ? change.task : t)) ?? list;
    open.setData(replace);
    done.setData(replace);
    // Ticking the last one never completes the task by itself: ask first.
    if (ticked && change.all_subtasks_done && change.task.status === "open") setWholeDone(change.task);
  }

  async function finishWhole() {
    if (!wholeDone) return;
    setFinishing(true);
    try {
      await markDone(wholeDone);
    } finally {
      setFinishing(false);
      setWholeDone(null);
    }
  }

  async function save(input: TaskInput) {
    if (modal?.mode === "create") {
      await api("/tasks", { method: "POST", json: buildCreate(input) });
      toast.show("Task added");
    } else if (modal) {
      const task = modal.task;
      const patch = buildPatch(task, input);
      if (Object.keys(patch).length > 0) {
        await api(`/tasks/${task.id}`, { method: "PATCH", json: patch });
      }
      if (input.subtasks && subtasksChanged(task, input.subtasks)) await syncSubtasks(task, input.subtasks);
      toast.show("Task updated");
    }
    setModal(null);
    load();
    if (showDone) loadDone();
    afterChange();
  }

  const openTasks = tasks ?? [];
  const card = (task: Task, completed = false) => (
    <TaskCard
      key={task.id}
      task={task}
      now={now}
      completed={completed}
      busy={busyId === task.id}
      onDone={markDone}
      onReopen={reopen}
      onEdit={(t) => setModal({ mode: t.origin === "manual" ? "edit" : "email", task: t })}
      onDelete={setToDelete}
      onDismiss={dismiss}
      onSubtaskChange={onSubtaskChange}
      onError={(m) => toast.show(m, { kind: "error" })}
    />
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-bold">{title}</h1>
        {manual ? (
          <Button variant="primary" onClick={() => setModal({ mode: "create" })}>
            <PlusIcon width={18} height={18} /> New task
          </Button>
        ) : null}
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {tasks === null ? (
        error ? null : <Spinner />
      ) : openTasks.length === 0 ? (
        <EmptyState title="Nothing to do right now">{empty}</EmptyState>
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
              {items.map((task) => card(task))}
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
            doneTasks.map((task) => card(task, true))
          )
        ) : null}
      </section>

      {modal ? (
        <TaskModal
          mode={modal.mode}
          task={modal.mode === "create" ? undefined : modal.task}
          onClose={() => setModal(null)}
          onSubmit={save}
        />
      ) : null}

      {toDelete ? (
        <ConfirmDialog
          title="Delete this task?"
          message={
            <>
              <strong className="text-fg">{toDelete.title}</strong> will be removed along with its subtasks,
              reminders and Calendar event. This can&apos;t be undone.
            </>
          }
          confirmLabel="Delete"
          danger
          busy={deleting}
          onConfirm={() => void confirmDelete()}
          onCancel={() => setToDelete(null)}
        />
      ) : null}

      {wholeDone ? (
        <ConfirmDialog
          title="Mark the whole task done?"
          message={
            <>
              Every subtask of <strong className="text-fg">{wholeDone.title}</strong> is ticked.
            </>
          }
          confirmLabel="Mark task done"
          busy={finishing}
          onConfirm={() => void finishWhole()}
          onCancel={() => setWholeDone(null)}
        />
      ) : null}
    </div>
  );
}
