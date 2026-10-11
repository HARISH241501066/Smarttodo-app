"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { BellIcon } from "@/components/icons";
import { useApp } from "@/components/AppProvider";
import { useNow } from "@/lib/hooks";
import { timeAgo } from "@/lib/time";
import type { NotificationItem } from "@/lib/types";

export function NotificationBell() {
  const { notifications, markRead, markAllRead, refreshNotifications } = useApp();
  const router = useRouter();
  const now = useNow(60_000);
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const unread = notifications.unread_count;

  useEffect(() => {
    if (!open) return;
    void refreshNotifications();
    const onDown = (e: MouseEvent) => {
      if (root.current && !root.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, refreshNotifications]);

  async function openItem(item: NotificationItem) {
    setOpen(false);
    if (!item.read) await markRead(item.id);
    if (item.task_id) router.push(`/?task=${item.task_id}`);
  }

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label={unread ? `Notifications, ${unread} unread` : "Notifications"}
        aria-expanded={open}
        aria-haspopup="true"
        className="relative rounded-lg p-2.5 text-muted hover:bg-surface-2 hover:text-fg"
      >
        <BellIcon />
        {unread > 0 ? (
          <span
            data-testid="bell-count"
            className="absolute right-0.5 top-0.5 flex min-h-5 min-w-5 items-center justify-center rounded-full bg-danger px-1 text-[11px] font-bold leading-none text-white"
          >
            {unread > 99 ? "99+" : unread}
          </span>
        ) : null}
      </button>

      {open ? (
        <div
          role="region"
          aria-label="Notifications"
          className="fixed inset-x-2 top-16 z-40 max-h-[70dvh] overflow-y-auto rounded-xl border border-line bg-surface shadow-xl sm:absolute sm:inset-x-auto sm:right-0 sm:top-full sm:mt-2 sm:w-96"
        >
          <div className="sticky top-0 flex items-center justify-between border-b border-line bg-surface px-4 py-3">
            <h2 className="font-bold">Notifications</h2>
            <button
              type="button"
              onClick={() => void markAllRead()}
              disabled={unread === 0}
              className="text-sm font-semibold text-primary disabled:text-muted"
            >
              Mark all read
            </button>
          </div>
          {notifications.items.length === 0 ? (
            <p className="px-4 py-8 text-center text-sm text-muted">You are all caught up.</p>
          ) : (
            <ul>
              {notifications.items.map((item) => (
                <li key={item.id} className="border-b border-line last:border-0">
                  <button
                    type="button"
                    onClick={() => void openItem(item)}
                    className={`flex w-full items-start gap-3 px-4 py-3 text-left hover:bg-surface-2 ${item.read ? "" : "bg-primary/5"}`}
                  >
                    <span
                      aria-label={item.read ? "Read" : "Unread"}
                      className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${item.read ? "bg-transparent" : "bg-primary"}`}
                    />
                    <span className="min-w-0 flex-1">
                      <span className={`block break-words text-sm ${item.read ? "" : "font-semibold"}`}>
                        {item.title}
                      </span>
                      {item.body ? (
                        <span className="mt-0.5 block whitespace-pre-line break-words text-xs text-muted">
                          {item.body}
                        </span>
                      ) : null}
                      <span className="mt-1 block text-xs text-muted">{timeAgo(item.created_at, now)}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}
