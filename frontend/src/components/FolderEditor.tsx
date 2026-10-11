"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useApp } from "@/components/AppProvider";
import { useToast } from "@/components/toast";
import { Button, ErrorNote, Field, Modal, inputClass } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import type { Folder, Priority, SettingsData } from "@/lib/types";

export const FOLDER_COLORS = ["#2563eb", "#7c3aed", "#db2777", "#dc2626", "#ea580c", "#16a34a", "#0d9488", "#64748b"];
const EMOJI = ["📁", "🎓", "💼", "🏠", "🚀", "📚", "⭐", "🔥"];
const PRIORITIES: { key: Priority; label: string }[] = [
  { key: "urgent", label: "Urgent" },
  { key: "important", label: "Important" },
  { key: "regular", label: "Regular" },
];

interface Props {
  /** Editing this folder; leave out to make a new one. */
  folder?: Folder;
  /** Starting values for a new folder (e.g. "Create folder for this source"). */
  initial?: { name?: string; sources?: string[] };
  onClose: () => void;
  onSaved: (folder: Folder) => void;
}

function toggle<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function Chip({ on, children, onClick }: { on: boolean; children: string; onClick: () => void }) {
  return (
    <button
      type="button"
      aria-pressed={on}
      onClick={onClick}
      className={`rounded-full border px-3 py-1.5 text-sm font-semibold ${
        on ? "border-primary bg-primary text-primary-fg" : "border-line bg-surface text-fg hover:bg-surface-2"
      }`}
    >
      {children}
    </button>
  );
}

/** Create or edit a smart folder: name, emoji, colour and the rules that fill it. */
export function FolderEditor({ folder, initial, onClose, onSaved }: Props) {
  const router = useRouter();
  const { refreshFolders } = useApp();
  const toast = useToast();
  const [name, setName] = useState(folder?.name ?? initial?.name ?? "");
  const [emoji, setEmoji] = useState(folder?.emoji ?? "📁");
  const [color, setColor] = useState(folder?.color ?? FOLDER_COLORS[0]);
  const [sources, setSources] = useState<string[]>(folder?.rules.sources ?? initial?.sources ?? []);
  const [tags, setTags] = useState<string[]>(folder?.rules.tags ?? []);
  const [priorities, setPriorities] = useState<Priority[]>(folder?.rules.priorities ?? []);
  const [titleContains, setTitleContains] = useState(folder?.rules.title_contains ?? "");
  const [showCompleted, setShowCompleted] = useState(folder?.show_completed ?? false);

  const [sourceChoices, setSourceChoices] = useState<{ value: string; label: string }[]>([]);
  const [tagChoices, setTagChoices] = useState<string[]>([]);
  const [preview, setPreview] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let alive = true;
    Promise.all([api<SettingsData>("/settings"), api<string[]>("/tags")])
      .then(([settings, allTags]) => {
        if (!alive) return;
        setSourceChoices([
          ...settings.sources.map((s) => ({ value: s.name, label: s.name })),
          { value: "hackathon", label: "Hackathon" },
          { value: "manual", label: "My tasks" },
        ]);
        setTagChoices(allTags);
      })
      .catch((err) => alive && setError(errorMessage(err)));
    return () => {
      alive = false;
    };
  }, []);

  const query = useMemo(() => {
    const q = new URLSearchParams();
    sources.forEach((s) => q.append("sources", s));
    tags.forEach((t) => q.append("tags", t));
    priorities.forEach((p) => q.append("priorities", p));
    if (titleContains.trim()) q.set("title_contains", titleContains.trim());
    if (showCompleted) q.set("show_completed", "true");
    if (folder) q.set("folder_id", String(folder.id));
    return q.toString();
  }, [sources, tags, priorities, titleContains, showCompleted, folder]);

  // The live "N tasks match right now" line. State is only set from the fetch callbacks.
  useEffect(() => {
    let alive = true;
    const id = window.setTimeout(() => {
      api<{ count: number }>(`/folders/preview?${query}`)
        .then((r) => {
          if (alive) setPreview(r.count);
        })
        .catch(() => {
          if (alive) setPreview(null);
        });
    }, 250);
    return () => {
      alive = false;
      window.clearTimeout(id);
    };
  }, [query]);

  const hasSource = (value: string) => sources.some((x) => x.toLowerCase() === value.toLowerCase());
  const toggleSource = (value: string) =>
    setSources(hasSource(value) ? sources.filter((x) => x.toLowerCase() !== value.toLowerCase()) : [...sources, value]);

  const shownTags = [...new Set([...tagChoices, ...tags])];
  const shownSources = [
    ...sourceChoices,
    ...sources.filter((s) => !sourceChoices.some((c) => c.value.toLowerCase() === s.toLowerCase())).map((s) => ({ value: s, label: s })),
  ];

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!name.trim()) return setError("Give the folder a name.");
    setError(null);
    setSaving(true);
    const body = {
      name: name.trim(),
      emoji: emoji.trim(),
      color,
      show_completed: showCompleted,
      rules: { sources, tags, priorities, title_contains: titleContains.trim() || null },
    };
    try {
      const saved = folder
        ? await api<Folder>(`/folders/${folder.id}`, { method: "PATCH", json: body })
        : await api<Folder>("/folders", { method: "POST", json: body });
      await refreshFolders();
      toast.show(folder ? "Folder updated" : "Folder created");
      onSaved(saved);
      if (!folder) router.push(`/folder?id=${saved.id}`); // a new folder opens straight away
    } catch (err) {
      setError(errorMessage(err));
      setSaving(false);
    }
  }

  return (
    <Modal title={folder ? "Edit folder" : "New folder"} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error ? <ErrorNote>{error}</ErrorNote> : null}
        <div className="flex gap-2">
          <div className="w-24">
            <Field label="Emoji">
              <input
                className={`${inputClass} text-center`}
                value={emoji}
                maxLength={8}
                onChange={(e) => setEmoji(e.target.value)}
              />
            </Field>
          </div>
          <div className="min-w-0 flex-1">
            <Field label="Name">
              <input
                className={inputClass}
                value={name}
                maxLength={60}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. College"
              />
            </Field>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5" role="group" aria-label="Pick an emoji">
          {EMOJI.map((e) => (
            <button
              key={e}
              type="button"
              onClick={() => setEmoji(e)}
              aria-label={`Use ${e}`}
              className={`rounded-lg border px-2 py-1 text-lg ${emoji === e ? "border-primary bg-primary/10" : "border-line"}`}
            >
              {e}
            </button>
          ))}
        </div>
        <div role="group" aria-label="Colour" className="flex flex-wrap gap-2">
          {FOLDER_COLORS.map((c) => (
            <button
              key={c}
              type="button"
              aria-label={`Colour ${c}`}
              aria-pressed={color === c}
              onClick={() => setColor(c)}
              style={{ backgroundColor: c }}
              className={`h-8 w-8 rounded-full border-2 ${color === c ? "border-fg" : "border-transparent"}`}
            />
          ))}
        </div>

        <fieldset className="space-y-2">
          <legend className="text-sm font-semibold">Rules</legend>
          <p className="text-xs text-muted">
            Tasks that match every kind of rule you set are collected automatically (any one of the choices
            inside a kind is enough). With no rules, only tasks you pin appear.
          </p>
          <div data-testid="rule-sources">
            <p className="mb-1 text-xs font-semibold text-muted">Source</p>
            <div className="flex flex-wrap gap-2">
              {shownSources.map((s) => (
                <Chip key={s.value} on={hasSource(s.value)} onClick={() => toggleSource(s.value)}>
                  {s.label}
                </Chip>
              ))}
            </div>
          </div>
          <div data-testid="rule-tags">
            <p className="mb-1 text-xs font-semibold text-muted">Tag</p>
            {shownTags.length ? (
              <div className="flex flex-wrap gap-2">
                {shownTags.map((t) => (
                  <Chip key={t} on={tags.includes(t)} onClick={() => setTags(toggle(tags, t))}>
                    {t}
                  </Chip>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted">No tags yet. Tag a task of your own and it appears here.</p>
            )}
          </div>
          <div data-testid="rule-priorities">
            <p className="mb-1 text-xs font-semibold text-muted">Priority</p>
            <div className="flex flex-wrap gap-2">
              {PRIORITIES.map((p) => (
                <Chip key={p.key} on={priorities.includes(p.key)} onClick={() => setPriorities(toggle(priorities, p.key))}>
                  {p.label}
                </Chip>
              ))}
            </div>
          </div>
          <Field label="Title contains">
            <input
              className={inputClass}
              value={titleContains}
              maxLength={100}
              onChange={(e) => setTitleContains(e.target.value)}
              placeholder="e.g. quiz"
            />
          </Field>
        </fieldset>

        <label className="flex items-center gap-3">
          <input
            type="checkbox"
            className="h-5 w-5"
            checked={showCompleted}
            onChange={(e) => setShowCompleted(e.target.checked)}
          />
          <span className="text-sm font-semibold">Show completed tasks in this folder</span>
        </label>

        <p className="rounded-lg bg-surface-2 px-3 py-2 text-sm font-semibold" data-testid="folder-preview" aria-live="polite">
          {preview === null ? "Counting..." : `${preview} ${preview === 1 ? "task matches" : "tasks match"} right now`}
        </p>

        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={saving}>
            {saving ? "Saving..." : folder ? "Save folder" : "Create folder"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}
