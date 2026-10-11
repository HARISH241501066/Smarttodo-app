"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useApp } from "@/components/AppProvider";
import { FolderEditor } from "@/components/FolderEditor";
import { GripIcon, PlusIcon } from "@/components/icons";
import { useToast } from "@/components/toast";
import { api, errorMessage } from "@/lib/api";
import { moveItem } from "@/lib/tasks";

/** The Folders list: emoji, name, open count and a red dot when something is overdue. Drag to
 *  reorder. Used in the desktop sidebar and the phone drawer. */
export function FolderNav({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const openId = useSearchParams().get("id");
  const { folders, setFolders, refreshFolders } = useApp();
  const toast = useToast();
  const [dragging, setDragging] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);

  async function drop(to: number) {
    if (dragging === null || dragging === to) return;
    const next = moveItem(folders, dragging, to);
    setDragging(null);
    setFolders(next);
    try {
      await api("/folders/reorder", { method: "PATCH", json: { ids: next.map((f) => f.id) } });
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
      void refreshFolders();
    }
  }

  return (
    <nav aria-label="Folders" data-testid="folder-nav" className="space-y-1">
      <div className="mb-1 flex items-center justify-between px-2">
        <h2 className="text-xs font-bold uppercase tracking-wide text-muted">Folders</h2>
        <button
          type="button"
          onClick={() => setCreating(true)}
          className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs font-semibold text-primary hover:bg-surface-2"
        >
          <PlusIcon width={14} height={14} /> New folder
        </button>
      </div>
      {folders.length === 0 ? (
        <p className="px-2 text-xs text-muted">No folders yet. Make one to group tasks by rules.</p>
      ) : (
        <ul className="space-y-0.5">
          {folders.map((folder, i) => {
            const active = pathname === "/folder" && openId === String(folder.id);
            return (
              <li
                key={folder.id}
                draggable
                data-testid="folder-item"
                onDragStart={() => setDragging(i)}
                onDragOver={(e) => e.preventDefault()}
                onDrop={() => void drop(i)}
                onDragEnd={() => setDragging(null)}
                className={dragging === i ? "opacity-50" : ""}
              >
                <Link
                  href={`/folder?id=${folder.id}`}
                  onClick={onNavigate}
                  aria-current={active ? "page" : undefined}
                  className={`group flex items-center gap-2 rounded-lg px-2 py-2 text-sm font-semibold ${
                    active ? "bg-primary/10 text-primary" : "text-fg hover:bg-surface-2"
                  }`}
                >
                  <GripIcon width={14} height={14} className="shrink-0 cursor-grab text-muted opacity-60" />
                  <span aria-hidden="true" style={{ color: folder.color }}>
                    {folder.emoji || "📁"}
                  </span>
                  <span className="min-w-0 flex-1 truncate">{folder.name}</span>
                  {folder.has_overdue ? (
                    <span
                      data-testid="folder-overdue"
                      title="Something in this folder is overdue"
                      className="h-2.5 w-2.5 shrink-0 rounded-full bg-danger"
                    >
                      <span className="sr-only">Overdue</span>
                    </span>
                  ) : null}
                  <span
                    data-testid="folder-count"
                    className="shrink-0 rounded-full bg-surface-2 px-2 py-0.5 text-xs text-muted"
                  >
                    {folder.open_count}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      )}
      {creating ? (
        <FolderEditor
          onClose={() => setCreating(false)}
          onSaved={() => setCreating(false)}
        />
      ) : null}
    </nav>
  );
}
