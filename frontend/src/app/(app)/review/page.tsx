"use client";

import { useMemo, useState } from "react";
import { useApp } from "@/components/AppProvider";
import { SourceBadge } from "@/components/TaskCard";
import { TaskModal } from "@/components/TaskModal";
import { useToast } from "@/components/toast";
import { Button, ConfirmDialog, EmptyState, ErrorNote, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useLoader } from "@/lib/hooks";
import { buildConfirm, groupBySender, sortSummary } from "@/lib/tasks";
import { domainOf } from "@/lib/time";
import type { ReprocessCounts, Task, TaskInput } from "@/lib/types";

const BATCH = 40; // emails re-read per request, so one request never runs long

export default function ReviewPage() {
  const { refreshCounts, refreshNotifications } = useApp();
  const toast = useToast();
  const { data: tasks, setData: setTasks, error, reload } = useLoader<Task[]>("/review");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [editing, setEditing] = useState<Task | null>(null);
  const [resorting, setResorting] = useState<string | null>(null); // progress text while running
  const [bulk, setBulk] = useState<{ sender: string; count: number } | null>(null);
  const [bulkBusy, setBulkBusy] = useState(false);

  const groups = useMemo(() => groupBySender(tasks ?? []), [tasks]);

  function removeLocally(ids: number[]) {
    const gone = new Set(ids);
    setTasks((list) => list?.filter((t) => !gone.has(t.id)) ?? list);
    void refreshCounts();
    void refreshNotifications();
  }

  async function confirm(task: Task) {
    setBusyId(task.id);
    try {
      await api(`/review/${task.id}/confirm`, { method: "POST" });
      removeLocally([task.id]);
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
      removeLocally([task.id]);
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
    removeLocally([editing.id]);
    setEditing(null);
    toast.show("Confirmed. It is now in your tasks.");
  }

  async function bulkAction(action: "confirm" | "dismiss", sender: string) {
    setBulkBusy(true);
    try {
      const { updated } = await api<{ updated: number }>("/review/bulk", {
        method: "POST",
        json: { action, sender },
      });
      const ids = (tasks ?? []).filter((t) => t.sender_email === sender).map((t) => t.id);
      removeLocally(ids);
      toast.show(
        action === "confirm"
          ? `Confirmed ${updated} from ${sender}. They are now in your tasks.`
          : `Dismissed ${updated} from ${sender}.`,
      );
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setBulkBusy(false);
      setBulk(null);
    }
  }

  async function resort() {
    const totals = { total: 0, task: 0, update: 0, ignore: 0, review: 0, gone: 0 };
    let after = 0;
    setResorting("Re-sorting...");
    try {
      for (;;) {
        const batch = await api<ReprocessCounts>(`/review/reprocess?limit=${BATCH}&after_id=${after}`, {
          method: "POST",
        });
        for (const key of Object.keys(totals) as (keyof typeof totals)[]) totals[key] += batch[key];
        setResorting(`Re-sorting... ${totals.total} checked`);
        if (batch.next_after_id === null) break;
        after = batch.next_after_id;
      }
      toast.show(sortSummary(totals));
    } catch (err) {
      toast.show(
        `${errorMessage(err)}${totals.total ? ` (${totals.total} were already sorted.)` : ""}`,
        { kind: "error" },
      );
    } finally {
      setResorting(null);
      reload();
      void refreshCounts();
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-bold">Needs review</h1>
          <p className="mt-1 text-sm text-muted">
            Emails where the signals conflict, or that give several different deadlines. Confirm the ones
            that are real tasks.
          </p>
        </div>
        <Button onClick={() => void resort()} disabled={resorting !== null || !tasks?.length}>
          {resorting ?? "Re-sort review queue"}
        </Button>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {tasks === null ? (
        error ? null : <Spinner />
      ) : tasks.length === 0 ? (
        <EmptyState title="Nothing to review">Emails we are unsure about will appear here.</EmptyState>
      ) : (
        groups.map((group) => (
          <section
            key={group.sender ?? "unknown"}
            data-testid="review-group"
            aria-label={group.sender ?? "Unknown sender"}
            className="space-y-3"
          >
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="min-w-0 break-all text-sm font-bold text-muted">
                {group.sender ?? "Sender not recorded"}{" "}
                <span className="rounded-full bg-surface-2 px-2 py-0.5 text-xs">{group.tasks.length}</span>
              </h2>
              {group.sender && group.tasks.length > 1 ? (
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={bulkBusy}
                    onClick={() => void bulkAction("confirm", group.sender!)}
                  >
                    Confirm all from this sender
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={bulkBusy}
                    onClick={() => setBulk({ sender: group.sender!, count: group.tasks.length })}
                  >
                    Dismiss all from this sender
                  </Button>
                </div>
              ) : null}
            </div>
            {group.tasks.map((task) => {
              const domain = task.link_domain ?? domainOf(task.link);
              return (
                <article
                  key={task.id}
                  data-testid="review-card"
                  className="rounded-xl border border-line bg-surface p-4 shadow-sm"
                >
                  <div className="flex items-start justify-between gap-3">
                    <h3 className="min-w-0 break-words font-semibold leading-snug">{task.title}</h3>
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
            })}
          </section>
        ))
      )}

      {editing ? (
        <TaskModal mode="review" task={editing} onClose={() => setEditing(null)} onSubmit={submitEdit} />
      ) : null}

      {bulk ? (
        <ConfirmDialog
          title={`Dismiss ${bulk.count} emails?`}
          message={
            <>
              All {bulk.count} items from <strong className="break-all text-fg">{bulk.sender}</strong> will be
              dismissed.
            </>
          }
          confirmLabel={`Dismiss ${bulk.count}`}
          danger
          busy={bulkBusy}
          onConfirm={() => void bulkAction("dismiss", bulk.sender)}
          onCancel={() => setBulk(null)}
        />
      ) : null}
    </div>
  );
}
