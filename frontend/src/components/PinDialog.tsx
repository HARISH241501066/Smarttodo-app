"use client";

import { useState } from "react";
import { useApp } from "@/components/AppProvider";
import { useToast } from "@/components/toast";
import { Button, EmptyState, ErrorNote, Modal, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useLoader } from "@/lib/hooks";
import type { Folder, Task } from "@/lib/types";

/** "Add to folder...": tick the folders this task should be pinned to. A task can be in several. */
export function PinDialog({ task, onClose }: { task: Task; onClose: () => void }) {
  const { refreshFolders } = useApp();
  const toast = useToast();
  const { data, setData, error } = useLoader<Folder[]>(`/folders?task_id=${task.id}`);
  const [busy, setBusy] = useState<number | null>(null);

  async function toggle(folder: Folder, pinned: boolean) {
    setBusy(folder.id);
    try {
      await api(`/folders/${folder.id}/pins/${task.id}`, { method: pinned ? "POST" : "DELETE" });
      setData((list) => list?.map((f) => (f.id === folder.id ? { ...f, pinned } : f)) ?? list);
      void refreshFolders();
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <Modal title="Add to folder" onClose={onClose}>
      <p className="mb-3 text-sm text-muted">
        Pin <strong className="text-fg">{task.title}</strong> to the folders you pick. Folders also collect tasks
        by their own rules.
      </p>
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      {data === null ? (
        error ? null : <Spinner />
      ) : data.length === 0 ? (
        <EmptyState title="No folders yet">Make one from the Folders list first.</EmptyState>
      ) : (
        <ul className="space-y-1" aria-label="Folders">
          {data.map((folder) => (
            <li key={folder.id}>
              <label className="flex items-center gap-3 rounded-lg px-2 py-2 hover:bg-surface-2">
                <input
                  type="checkbox"
                  className="h-5 w-5"
                  checked={!!folder.pinned}
                  disabled={busy === folder.id}
                  onChange={(e) => void toggle(folder, e.target.checked)}
                />
                <span aria-hidden="true">{folder.emoji}</span>
                <span className="min-w-0 flex-1 truncate font-semibold">{folder.name}</span>
              </label>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 flex justify-end">
        <Button variant="primary" onClick={onClose}>
          Done
        </Button>
      </div>
    </Modal>
  );
}
