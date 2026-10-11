"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { PlusIcon } from "@/components/icons";
import { TaskCard } from "@/components/TaskCard";
import { useToast } from "@/components/toast";
import { Button, EmptyState, ErrorNote, Spinner } from "@/components/ui";
import { api } from "@/lib/api";
import { useLoader, useNow } from "@/lib/hooks";
import { GROUPS } from "@/lib/tasks";
import type { Task } from "@/lib/types";
import { useTaskActions } from "@/lib/useTaskActions";

type Origin = "manual" | "email";

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
  const actions = useTaskActions({
    reload: () => {
      load();
      if (showDone) loadDone();
    },
    replace: (task) => {
      const swap = (list: Task[] | null) => list?.map((t) => (t.id === task.id ? task : t)) ?? list;
      open.setData(swap);
      done.setData(swap);
    },
    remove: (id) => {
      const drop = (list: Task[] | null) => list?.filter((t) => t.id !== id) ?? list;
      open.setData(drop);
      done.setData(drop);
    },
  });
  const { openEdit } = actions;

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
        openEdit(task);
        router.replace(PATH[origin]);
      })
      .catch(() => {
        toast.show("That task no longer exists.", { kind: "error" });
        router.replace(PATH[origin]);
      });
    return () => {
      cancelled = true;
    };
  }, [focusId, tasks, router, toast, origin, openEdit]);

  const openTasks = tasks ?? [];
  const card = (task: Task, completed = false) => (
    <TaskCard key={task.id} now={now} completed={completed} {...actions.cardProps(task)} />
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-xl font-bold">{title}</h1>
        {manual ? (
          <Button variant="primary" onClick={actions.openCreate}>
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

      {actions.overlays}
    </div>
  );
}
