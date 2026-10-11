"use client";

import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/AppProvider";
import { FolderEditor } from "@/components/FolderEditor";
import { TaskCard } from "@/components/TaskCard";
import { useToast } from "@/components/toast";
import { Button, ConfirmDialog, EmptyState, ErrorNote, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useLoader, useNow } from "@/lib/hooks";
import type { FolderTasks, Task } from "@/lib/types";
import { useTaskActions } from "@/lib/useTaskActions";

const HEADING: Record<string, string> = { overdue: "text-danger", today: "text-warn" };

export default function FolderPage() {
  const id = Number(useParams<{ id: string }>().id);
  const router = useRouter();
  const toast = useToast();
  const now = useNow();
  const { folders, refreshFolders } = useApp();
  const folder = folders.find((f) => f.id === id);
  const { data, setData, error, reload } = useLoader<FolderTasks>(`/folders/${id}/tasks`, { intervalMs: 60_000 });
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState(false);

  const actions = useTaskActions({
    reload: () => {
      reload();
      void refreshFolders();
    },
    replace: (task) =>
      setData((d) =>
        d && {
          ...d,
          groups: d.groups.map((g) => ({ ...g, tasks: g.tasks.map((t) => (t.id === task.id ? task : t)) })),
        },
      ),
    remove: (taskId) =>
      setData((d) =>
        d && {
          ...d,
          groups: d.groups
            .map((g) => ({ ...g, tasks: g.tasks.filter((t) => t.id !== taskId) }))
            .filter((g) => g.tasks.length > 0),
        },
      ),
  });

  async function unpin(task: Task) {
    try {
      await api(`/folders/${id}/pins/${task.id}`, { method: "DELETE" });
      toast.show("Removed from this folder");
      reload();
      void refreshFolders();
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    }
  }

  async function deleteFolder() {
    setBusy(true);
    try {
      await api(`/folders/${id}`, { method: "DELETE" });
      await refreshFolders();
      toast.show("Folder deleted. Its tasks are untouched.");
      router.replace("/");
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
      setBusy(false);
    }
  }

  if (error && /not found/i.test(error)) {
    return <EmptyState title="That folder does not exist">It may have been deleted.</EmptyState>;
  }

  const rules = folder?.rules;
  const summary = rules
    ? [
        rules.sources.length ? `Source: ${rules.sources.join(" or ")}` : null,
        rules.tags.length ? `Tag: ${rules.tags.join(" or ")}` : null,
        rules.priorities.length ? `Priority: ${rules.priorities.join(" or ")}` : null,
        rules.title_contains ? `Title contains "${rules.title_contains}"` : null,
      ].filter(Boolean)
    : [];

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="flex items-center gap-2 text-xl font-bold">
            <span aria-hidden="true" style={{ color: folder?.color }}>
              {folder?.emoji || "📁"}
            </span>
            <span className="break-words">{folder?.name ?? "Folder"}</span>
          </h1>
          <p className="mt-1 text-sm text-muted" data-testid="folder-rules">
            {summary.length ? summary.join(" · ") : "No rules: only the tasks you pin appear here."}
            {folder?.show_completed ? " · Completed tasks shown" : ""}
          </p>
        </div>
        <div className="flex gap-2">
          <Button onClick={() => setEditing(true)} disabled={!folder}>
            Edit folder
          </Button>
          <Button variant="danger" onClick={() => setDeleting(true)} disabled={!folder}>
            Delete folder
          </Button>
        </div>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {data === null ? (
        error ? null : <Spinner />
      ) : data.groups.length === 0 ? (
        <EmptyState title="Nothing in this folder">
          Tasks that match its rules appear here by themselves. You can also pin any task with &quot;Add to
          folder…&quot;.
        </EmptyState>
      ) : (
        data.groups.map((group) => (
          <section key={group.key} aria-label={group.label} data-testid={`group-${group.key}`} className="space-y-3">
            <h2 className={`flex items-center gap-2 text-sm font-bold uppercase tracking-wide ${HEADING[group.key] ?? "text-muted"}`}>
              {group.label}
              <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted">{group.tasks.length}</span>
            </h2>
            {group.tasks.map((task) => (
              <TaskCard
                key={task.id}
                now={now}
                completed={task.status === "done"}
                {...actions.cardProps(task, {
                  onCreateFolder: undefined,
                  folderInfo: { pinned: data.pinned_ids.includes(task.id), byRule: data.rule_ids.includes(task.id) },
                  onUnpin: unpin,
                })}
              />
            ))}
          </section>
        ))
      )}

      {actions.overlays}

      {editing && folder ? (
        <FolderEditor
          folder={folder}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false);
            reload(); // the rules changed: the contents change at once
          }}
        />
      ) : null}

      {deleting && folder ? (
        <ConfirmDialog
          title="Delete this folder?"
          message={
            <>
              The folder <strong className="text-fg">{folder.name}</strong> will be removed. Your tasks are not
              deleted, only the folder and its pins.
            </>
          }
          confirmLabel="Delete folder"
          danger
          busy={busy}
          onConfirm={() => void deleteFolder()}
          onCancel={() => setDeleting(false)}
        />
      ) : null}
    </div>
  );
}
