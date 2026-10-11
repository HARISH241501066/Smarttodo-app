"use client";

import { useState } from "react";
import Link from "next/link";
import { useApp } from "@/components/AppProvider";
import { useToast } from "@/components/toast";
import { Button, EmptyState, ErrorNote, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useLoader, useNow } from "@/lib/hooks";
import { timeAgo } from "@/lib/time";
import type { QuarantineItem, RescueResult } from "@/lib/types";

export function SuspiciousSection() {
  const { refreshCounts, refreshNotifications } = useApp();
  const toast = useToast();
  const now = useNow(60_000);
  const { data: items, setData: setItems, error } = useLoader<QuarantineItem[]>("/quarantine");
  const [busyId, setBusyId] = useState<number | null>(null);

  function removeLocally(id: number) {
    setItems((list) => list?.filter((i) => i.id !== id) ?? list);
    void refreshCounts();
    void refreshNotifications();
  }

  async function rescue(item: QuarantineItem) {
    setBusyId(item.id);
    try {
      const result = await api<RescueResult>(`/quarantine/${item.id}/rescue`, { method: "POST" });
      removeLocally(item.id);
      toast.show(
        result.task_created
          ? `Task created: ${result.task?.title ?? "from that email"}`
          : "Added to an existing task",
      );
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setBusyId(null);
    }
  }

  async function confirmScam(item: QuarantineItem) {
    setBusyId(item.id);
    try {
      await api(`/quarantine/${item.id}/confirm-scam`, { method: "POST" });
      removeLocally(item.id);
      toast.show("Marked as a scam. It will not become a task.");
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section aria-label="Suspicious" className="space-y-4">
      <div>
        <h2 className="text-lg font-bold">Suspicious</h2>
        <p className="mt-1 text-sm text-muted">
          These emails looked like scams or impersonation, so no task was made. Don&apos;t click anything in
          them. If one is genuine, rescue it.
        </p>
      </div>

      {error ? <ErrorNote>{error}</ErrorNote> : null}

      {items === null ? (
        error ? null : <Spinner />
      ) : items.length === 0 ? (
        <EmptyState title="Nothing suspicious">
          Emails that look like scams will be held here instead of becoming tasks.{" "}
          <Link href="/" className="font-semibold text-primary">
            Back to tasks
          </Link>
        </EmptyState>
      ) : (
        items.map((item) => (
          <article
            key={item.id}
            data-testid="suspicious-card"
            className="rounded-xl border border-l-4 border-line border-l-danger bg-surface p-4 shadow-sm"
          >
            <h3 className="break-words font-semibold leading-snug">{item.subject || "(no subject)"}</h3>
            <p className="mt-0.5 break-all text-sm text-muted">From: {item.sender || "unknown sender"}</p>
            <p className="mt-0.5 text-xs text-muted">
              Risk score {item.score ?? "?"} · {timeAgo(item.created_at, now)}
            </p>
            <div className="mt-2">
              <p className="text-sm font-semibold">Why it was held</p>
              <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm">
                {item.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            </div>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button size="sm" disabled={busyId === item.id} onClick={() => void rescue(item)}>
                Rescue (create task)
              </Button>
              <Button variant="danger" size="sm" disabled={busyId === item.id} onClick={() => void confirmScam(item)}>
                Confirm scam
              </Button>
            </div>
          </article>
        ))
      )}
    </section>
  );
}
