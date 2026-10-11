"use client";

import { useEffect, useState } from "react";
import { TaskCard } from "@/components/TaskCard";
import { EmptyState, ErrorNote, Spinner, inputClass } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { useNow } from "@/lib/hooks";
import type { Task } from "@/lib/types";
import { useTaskActions } from "@/lib/useTaskActions";

const MAX_QUERY = 100;

/** Search every task (open and done, mine and from email) by title, notes, tag or source. */
export default function SearchPage() {
  const now = useNow();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Task[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const query = q.trim();

  const actions = useTaskActions({
    reload: () => setTick((t) => t + 1),
    replace: (task) => setResults((list) => list?.map((t) => (t.id === task.id ? task : t)) ?? list),
    remove: (id) => setResults((list) => list?.filter((t) => t.id !== id) ?? list),
  });

  // Search a moment after the last keystroke. State is only set from the fetch callbacks.
  useEffect(() => {
    if (!query) return;
    let alive = true;
    const id = window.setTimeout(() => {
      api<Task[]>(`/search?q=${encodeURIComponent(query)}`)
        .then((list) => {
          if (!alive) return;
          setResults(list);
          setError(null);
        })
        .catch((err) => alive && setError(errorMessage(err)));
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(id);
    };
  }, [query, tick]);

  const shown = query ? results : null;
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Search</h1>
      <input
        type="search"
        className={inputClass}
        value={q}
        maxLength={MAX_QUERY}
        autoFocus
        placeholder="Search title, notes, tag or source"
        aria-label="Search tasks"
        onChange={(e) => setQ(e.target.value)}
      />
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      {!query ? (
        <p className="text-sm text-muted">Type to search all your tasks, finished ones too.</p>
      ) : shown === null ? (
        error ? null : <Spinner />
      ) : shown.length === 0 ? (
        <EmptyState title="No matches">Nothing has &quot;{query}&quot; in its title, notes, tag or source.</EmptyState>
      ) : (
        <ul className="space-y-3" aria-label="Search results" data-testid="search-results">
          {shown.map((task) => (
            <li key={task.id}>
              <TaskCard now={now} completed={task.status === "done"} {...actions.cardProps(task)} />
            </li>
          ))}
        </ul>
      )}
      {actions.overlays}
    </div>
  );
}
