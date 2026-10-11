"use client";

import { useCallback, useState, type ComponentProps, type ReactNode } from "react";
import { useApp } from "@/components/AppProvider";
import { EmailViewer } from "@/components/EmailViewer";
import { FolderEditor } from "@/components/FolderEditor";
import { PinDialog } from "@/components/PinDialog";
import type { TaskCard } from "@/components/TaskCard";
import { TaskModal } from "@/components/TaskModal";
import { useToast } from "@/components/toast";
import { ConfirmDialog } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { buildCreate, buildPatch, subtasksChanged, syncSubtasks } from "@/lib/tasks";
import type { SubtaskChange, Task, TaskInput } from "@/lib/types";

type ModalState = { mode: "create" } | { mode: "edit" | "email"; task: Task } | null;
type CardProps = ComponentProps<typeof TaskCard>;

interface Options {
  /** Load the lists again. */
  reload: () => void;
  /** Swap in an updated copy of a task (after a subtask change). */
  replace: (task: Task) => void;
  /** Take a task out of the visible lists. */
  remove: (taskId: number) => void;
}

/** Everything a task card can do (done, dismiss, delete, edit, subtasks, view email, folders) and
 *  the dialogs for it, shared by My Tasks, From Email and the folder pages. */
export function useTaskActions({ reload, replace, remove }: Options) {
  const { refreshNotifications, refreshCounts } = useApp();
  const toast = useToast();
  const [busyId, setBusyId] = useState<number | null>(null);
  const [modal, setModal] = useState<ModalState>(null);
  const [toDelete, setToDelete] = useState<Task | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [wholeDone, setWholeDone] = useState<Task | null>(null);
  const [finishing, setFinishing] = useState(false);
  const [viewing, setViewing] = useState<Task | null>(null);
  const [pinning, setPinning] = useState<Task | null>(null);
  const [folderFor, setFolderFor] = useState<Task | null>(null);

  const afterChange = useCallback(() => {
    void refreshNotifications();
    void refreshCounts();
  }, [refreshNotifications, refreshCounts]);

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
      reload();
      afterChange();
    });
  }

  async function markDone(task: Task) {
    await run(task, async () => {
      await api(`/tasks/${task.id}/done`, { method: "POST" });
      remove(task.id);
      reload();
      toast.show("Marked as done", { action: { label: "Undo", onClick: () => void reopen(task) } });
      afterChange();
    });
  }

  async function dismiss(task: Task) {
    await run(task, async () => {
      await api(`/tasks/${task.id}/dismiss`, { method: "POST" });
      remove(task.id);
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
      remove(task.id);
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
    replace(change.task);
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
    reload();
    afterChange();
  }

  /** Props for a TaskCard; folder pages add what they know about pins. */
  function cardProps(task: Task, extra: Partial<CardProps> = {}): Omit<CardProps, "now"> {
    return {
      task,
      busy: busyId === task.id,
      onDone: markDone,
      onReopen: reopen,
      onEdit: (t) => setModal({ mode: t.origin === "manual" ? "edit" : "email", task: t }),
      onDelete: setToDelete,
      onDismiss: dismiss,
      onViewEmail: setViewing,
      onAddToFolder: setPinning,
      onCreateFolder: setFolderFor,
      onSubtaskChange,
      onError: (m: string) => toast.show(m, { kind: "error" }),
      ...extra,
    };
  }

  const overlays: ReactNode = (
    <>
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

      {viewing ? <EmailViewer task={viewing} onClose={() => setViewing(null)} /> : null}

      {pinning ? (
        <PinDialog
          task={pinning}
          onClose={() => {
            setPinning(null);
            reload();
          }}
        />
      ) : null}

      {folderFor ? (
        <FolderEditor
          initial={{
            name: folderFor.source_name ?? "Keyword",
            sources: [folderFor.source === "keyword" ? "hackathon" : (folderFor.source_name ?? "")].filter(Boolean),
          }}
          onClose={() => setFolderFor(null)}
          onSaved={() => setFolderFor(null)}
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
    </>
  );

  const openEdit = useCallback(
    (task: Task) => setModal({ mode: task.origin === "manual" ? "edit" : "email", task }),
    [],
  );

  return {
    busyId,
    cardProps,
    overlays,
    openCreate: () => setModal({ mode: "create" }),
    openEdit,
  };
}
