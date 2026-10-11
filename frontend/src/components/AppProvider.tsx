"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useRouter } from "next/navigation";
import { api, ApiError, AUTH_EXPIRED_EVENT } from "@/lib/api";
import type { Me, NotificationList, QuarantineItem, Task } from "@/lib/types";
import { Spinner } from "@/components/ui";

const REFRESH_MS = 30_000;

interface AppState {
  me: Me;
  notifications: NotificationList;
  reviewCount: number;
  suspiciousCount: number;
  refreshNotifications: () => Promise<void>;
  refreshCounts: () => Promise<void>;
  markRead: (id: number) => Promise<void>;
  markAllRead: () => Promise<void>;
}

const AppContext = createContext<AppState | null>(null);

export function useApp(): AppState {
  const value = useContext(AppContext);
  if (!value) throw new Error("useApp must be used inside AppProvider");
  return value;
}

/** Loads the signed-in user (sending anyone who is not signed in to /login) and keeps the
 *  notification bell and the tab badges fresh. */
export function AppProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [me, setMe] = useState<Me | null>(null);
  const [notifications, setNotifications] = useState<NotificationList>({ unread_count: 0, items: [] });
  const [reviewCount, setReviewCount] = useState(0);
  const [suspiciousCount, setSuspiciousCount] = useState(0);

  const refreshNotifications = useCallback(async () => {
    try {
      setNotifications(await api<NotificationList>("/notifications?limit=30"));
    } catch {
      /* the bell is best-effort; a failed refresh keeps the last list */
    }
  }, []);

  const refreshCounts = useCallback(async () => {
    const [review, quarantine] = await Promise.allSettled([
      api<Task[]>("/review"),
      api<QuarantineItem[]>("/quarantine"),
    ]);
    if (review.status === "fulfilled") setReviewCount(review.value.length);
    if (quarantine.status === "fulfilled") setSuspiciousCount(quarantine.value.length);
  }, []);

  const markRead = useCallback(
    async (id: number) => {
      setNotifications((n) => ({
        unread_count: Math.max(0, n.unread_count - (n.items.find((i) => i.id === id && !i.read) ? 1 : 0)),
        items: n.items.map((i) => (i.id === id ? { ...i, read: true } : i)),
      }));
      try {
        await api(`/notifications/${id}/read`, { method: "POST" });
      } finally {
        await refreshNotifications();
      }
    },
    [refreshNotifications],
  );

  const markAllRead = useCallback(async () => {
    setNotifications((n) => ({ unread_count: 0, items: n.items.map((i) => ({ ...i, read: true })) }));
    try {
      await api("/notifications/read-all", { method: "POST" });
    } finally {
      await refreshNotifications();
    }
  }, [refreshNotifications]);

  useEffect(() => {
    let cancelled = false;
    api<Me>("/me")
      .then((user) => {
        if (!cancelled) setMe(user);
      })
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) router.replace("/login");
      });
    const onExpired = () => router.replace("/login");
    window.addEventListener(AUTH_EXPIRED_EVENT, onExpired);
    return () => {
      cancelled = true;
      window.removeEventListener(AUTH_EXPIRED_EVENT, onExpired);
    };
  }, [router]);

  useEffect(() => {
    if (!me) return;
    const refresh = () => {
      void refreshNotifications();
      void refreshCounts();
    };
    refresh();
    const id = window.setInterval(refresh, REFRESH_MS);
    window.addEventListener("focus", refresh);
    return () => {
      window.clearInterval(id);
      window.removeEventListener("focus", refresh);
    };
  }, [me, refreshNotifications, refreshCounts]);

  const value = useMemo(
    () =>
      me
        ? { me, notifications, reviewCount, suspiciousCount, refreshNotifications, refreshCounts, markRead, markAllRead }
        : null,
    [me, notifications, reviewCount, suspiciousCount, refreshNotifications, refreshCounts, markRead, markAllRead],
  );

  if (!value) return <Spinner label="Loading your tasks" />;
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}
