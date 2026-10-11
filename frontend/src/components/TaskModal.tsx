"use client";

import { useEffect, useId, useState, type FormEvent } from "react";
import { GripIcon, TrashIcon } from "@/components/icons";
import { Button, ErrorNote, Field, Modal, inputClass } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { moveItem } from "@/lib/tasks";
import { isHttpUrl, isoToIstInput, istInputToIso } from "@/lib/time";
import type { Priority, SubtaskDraft, Task, TaskInput } from "@/lib/types";

/** create / edit: a task of your own. email: an email task (title and deadline only).
 *  review: fix up a needs-review task before confirming it. */
type Mode = "create" | "edit" | "email" | "review";

interface Props {
  mode: Mode;
  task?: Task;
  onClose: () => void;
  onSubmit: (input: TaskInput) => Promise<void>;
}

const TITLES: Record<Mode, string> = {
  create: "New task",
  edit: "Edit task",
  email: "Edit email task",
  review: "Edit and confirm",
};

/** Times are entered and shown in India Standard Time. */
export function TaskModal({ mode, task, onClose, onSubmit }: Props) {
  const [title, setTitle] = useState(task?.title ?? "");
  const [deadline, setDeadline] = useState(isoToIstInput(task?.deadline ?? null));
  const [link, setLink] = useState(task?.link_type === "gmail" ? "" : (task?.link ?? ""));
  const [notes, setNotes] = useState(task?.notes ?? "");
  const [priority, setPriority] = useState<Priority | "">(mode === "edit" ? (task?.priority ?? "") : "");
  const [tag, setTag] = useState(task?.tag ?? "");
  const [reminders, setReminders] = useState(task?.reminders_enabled ?? true);
  const [subtasks, setSubtasks] = useState<SubtaskDraft[]>(
    (task?.subtasks ?? []).map((s) => ({ id: s.id, title: s.title })),
  );
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const tagListId = useId();
  const full = mode === "create" || mode === "edit";

  useEffect(() => {
    if (!full) return;
    let alive = true;
    api<string[]>("/tags")
      .then((tags) => {
        if (alive) setSuggestions(tags);
      })
      .catch(() => undefined); // suggestions are only a convenience
    return () => {
      alive = false;
    };
  }, [full]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const cleanTitle = title.trim();
    const cleanLink = link.trim();
    if (!cleanTitle) return setError("Give the task a title.");
    if (cleanLink && !isHttpUrl(cleanLink)) return setError("The link must start with http:// or https://");
    setError(null);
    setSaving(true);
    try {
      await onSubmit({
        title: cleanTitle,
        deadline: istInputToIso(deadline),
        link: mode === "email" ? (task?.link_type === "gmail" ? null : (task?.link ?? null)) : cleanLink || null,
        notes: mode === "email" ? (task?.notes ?? null) : notes.trim() || null,
        priority: priority || null,
        ...(full
          ? {
              tag: tag.trim() || null,
              reminders_enabled: reminders,
              subtasks: subtasks.filter((s) => s.title.trim()),
            }
          : {}),
      });
    } catch (err) {
      setError(errorMessage(err));
      setSaving(false);
    }
  }

  return (
    <Modal title={TITLES[mode]} onClose={onClose}>
      <form onSubmit={submit} className="space-y-4" noValidate>
        {error ? <ErrorNote>{error}</ErrorNote> : null}
        <Field label="Title">
          <input
            className={inputClass}
            value={title}
            maxLength={500}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="What needs doing?"
            required
          />
        </Field>
        <Field label="Deadline (India time)" hint="Date and time. Leave empty for no deadline.">
          <input
            type="datetime-local"
            className={inputClass}
            value={deadline}
            onChange={(e) => setDeadline(e.target.value)}
          />
        </Field>
        {mode === "create" || mode === "edit" || mode === "review" ? (
          <Field label="Link to complete it" hint="Optional. Must start with http:// or https://">
            <input
              type="url"
              inputMode="url"
              className={inputClass}
              value={link}
              onChange={(e) => setLink(e.target.value)}
              placeholder="https://"
            />
          </Field>
        ) : null}
        {full ? (
          <Field
            label="Priority"
            hint={mode === "create" ? "Automatic makes it urgent when due within 48 hours." : undefined}
          >
            <select
              className={inputClass}
              value={priority}
              onChange={(e) => setPriority(e.target.value as Priority | "")}
            >
              {mode === "create" ? <option value="">Automatic</option> : null}
              <option value="urgent">Urgent</option>
              <option value="important">Important</option>
              <option value="regular">Regular</option>
            </select>
          </Field>
        ) : null}
        {full ? (
          <Field label="Tag" hint="Optional. Pick one you used before or type a new one.">
            <input
              className={inputClass}
              value={tag}
              maxLength={40}
              list={tagListId}
              onChange={(e) => setTag(e.target.value)}
              placeholder="e.g. College"
            />
            <datalist id={tagListId}>
              {suggestions.map((s) => (
                <option key={s} value={s} />
              ))}
            </datalist>
          </Field>
        ) : null}
        {full || mode === "review" ? (
          <Field label="Notes">
            <textarea
              className={`${inputClass} min-h-20`}
              value={notes}
              maxLength={5000}
              onChange={(e) => setNotes(e.target.value)}
            />
          </Field>
        ) : null}
        {full ? (
          <label className="flex items-center gap-3">
            <input
              type="checkbox"
              className="h-5 w-5"
              checked={reminders}
              onChange={(e) => setReminders(e.target.checked)}
            />
            <span className="text-sm font-semibold">Remind me about this task</span>
          </label>
        ) : null}
        {full ? <SubtasksEditor items={subtasks} onChange={setSubtasks} /> : null}
        <div className="flex justify-end gap-2 pt-1">
          <Button onClick={onClose}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={saving}>
            {saving ? "Saving..." : mode === "review" ? "Confirm task" : "Save"}
          </Button>
        </div>
      </form>
    </Modal>
  );
}

/** Add, rename, delete and drag to reorder (the arrow buttons do the same without a mouse). */
function SubtasksEditor({
  items,
  onChange,
}: {
  items: SubtaskDraft[];
  onChange: (items: SubtaskDraft[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const [dragging, setDragging] = useState<number | null>(null);

  function add() {
    const title = draft.trim();
    if (!title) return;
    onChange([...items, { title }]);
    setDraft("");
  }

  return (
    <div data-testid="subtasks-editor">
      <p className="mb-1 text-sm font-semibold">Subtasks</p>
      {items.length > 0 ? (
        <ul className="mb-2 space-y-1" aria-label="Subtasks">
          {items.map((item, i) => (
            <li
              key={item.id ?? `new-${i}`}
              draggable
              data-testid="subtask-row"
              onDragStart={() => setDragging(i)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => {
                if (dragging !== null) onChange(moveItem(items, dragging, i));
                setDragging(null);
              }}
              onDragEnd={() => setDragging(null)}
              className={`flex items-center gap-1 rounded-lg border border-line bg-surface px-1 py-1 ${dragging === i ? "opacity-50" : ""}`}
            >
              <span className="cursor-grab p-1 text-muted" aria-hidden="true">
                <GripIcon width={16} height={16} />
              </span>
              <input
                className="min-w-0 flex-1 bg-transparent px-1 py-1.5 text-base"
                value={item.title}
                maxLength={300}
                aria-label={`Subtask ${i + 1}`}
                onChange={(e) => onChange(items.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))}
              />
              <button
                type="button"
                aria-label={`Move subtask ${i + 1} up`}
                disabled={i === 0}
                onClick={() => onChange(moveItem(items, i, i - 1))}
                className="rounded p-1.5 text-muted hover:bg-line disabled:opacity-30"
              >
                ↑
              </button>
              <button
                type="button"
                aria-label={`Move subtask ${i + 1} down`}
                disabled={i === items.length - 1}
                onClick={() => onChange(moveItem(items, i, i + 1))}
                className="rounded p-1.5 text-muted hover:bg-line disabled:opacity-30"
              >
                ↓
              </button>
              <button
                type="button"
                aria-label={`Delete subtask ${i + 1}`}
                onClick={() => onChange(items.filter((_, j) => j !== i))}
                className="rounded p-1.5 text-muted hover:bg-line hover:text-fg"
              >
                <TrashIcon width={14} height={14} />
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="flex gap-2">
        <input
          className={inputClass}
          value={draft}
          maxLength={300}
          placeholder="Add a subtask"
          aria-label="New subtask"
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <Button onClick={add} aria-label="Add subtask">
          Add
        </Button>
      </div>
    </div>
  );
}
