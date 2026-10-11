"use client";

import { useState } from "react";
import { useApp } from "@/components/AppProvider";
import { ExternalIcon, MailIcon } from "@/components/icons";
import { SourceBadge } from "@/components/TaskCard";
import { useToast } from "@/components/toast";
import { Button, EmptyState, ErrorNote, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useLoader, useNow } from "@/lib/hooks";
import { timeAgo } from "@/lib/time";
import type { Task, UpdateItem } from "@/lib/types";

export default function UpdatesPage() {
  const { refreshCounts } = useApp();
  const toast = useToast();
  const now = useNow(60_000);
  const { data: updates, setData: setUpdates, error } = useLoader<UpdateItem[]>("/updates");
  const [busyId, setBusyId] = useState<number | null>(null);

  function removeLocally(id: number) {
    setUpdates((list) => list?.filter((u) => u.id !== id) ?? list);
    void refreshCounts();
  }

  async function toTask(item: UpdateItem) {
    setBusyId(item.id);
    try {
      await api<Task>(`/updates/${item.id}/task`, { method: "POST" });
      removeLocally(item.id);
      toast.show("Added to your tasks");
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setBusyId(null);
    }
  }

  async function dismiss(item: UpdateItem) {
    setBusyId(item.id);
    try {
      await api(`/updates/${item.id}/dismiss`, { method: "POST" });
      removeLocally(item.id);
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold">Updates</h1>
        <p className="mt-1 text-sm text-muted">
          Results, announcements and other news from your inbox. There is nothing to do for these, so they
          are kept out of your task list. If one is a task after all, turn it into one.
        </p>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {updates === null ? (
        error ? null : <Spinner />
      ) : updates.length === 0 ? (
        <EmptyState title="No updates">
          Quiz scores, certificates, &quot;lecture uploaded&quot; notices and announcements show up here.
        </EmptyState>
      ) : (
        updates.map((item) => {
          const email = item.link_type === "gmail";
          return (
            <article
              key={item.id}
              data-testid="update-card"
              className="rounded-xl border border-line bg-surface p-4 shadow-sm"
            >
              <div className="flex items-start justify-between gap-3">
                <h2 className="min-w-0 break-words font-semibold leading-snug">{item.title}</h2>
                <SourceBadge source={item.source} />
              </div>
              <p className="mt-1 text-sm text-muted">
                {item.received_at ? timeAgo(item.received_at, now) : "Received earlier"}
                {item.received_at ? (
                  <span className="hidden sm:inline">
                    {" "}
                    ·{" "}
                    {new Date(item.received_at).toLocaleDateString("en-IN", {
                      timeZone: "Asia/Kolkata",
                      day: "numeric",
                      month: "short",
                      year: "numeric",
                    })}
                  </span>
                ) : null}
              </p>
              {item.link ? (
                <p className="mt-2 text-sm">
                  <a
                    href={item.link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1.5 font-semibold text-primary underline"
                  >
                    {email ? <MailIcon width={16} height={16} /> : <ExternalIcon width={16} height={16} />}
                    {email ? "Open email" : "Open link"}
                  </a>
                  {item.link_domain ? <span className="text-muted"> · {item.link_domain}</span> : null}
                </p>
              ) : null}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant="primary" size="sm" disabled={busyId === item.id} onClick={() => void toTask(item)}>
                  Turn into task
                </Button>
                <Button variant="ghost" size="sm" disabled={busyId === item.id} onClick={() => void dismiss(item)}>
                  Dismiss
                </Button>
              </div>
            </article>
          );
        })
      )}
    </div>
  );
}
