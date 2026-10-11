"use client";

import { useState, type FormEvent } from "react";
import { Button, ErrorNote, Field, Modal, inputClass } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { isHttpUrl, isoToIstInput, istInputToIso } from "@/lib/time";
import type { Priority, Task, TaskInput } from "@/lib/types";

type Mode = "create" | "edit" | "review";

interface Props {
  mode: Mode;
  task?: Task;
  onClose: () => void;
  onSubmit: (input: TaskInput) => Promise<void>;
}

const TITLES: Record<Mode, string> = {
  create: "Add a task",
  edit: "Edit task",
  review: "Edit and confirm",
};

/** Used to add a task, edit one, or fix up a needs-review task before confirming it.
 *  Times are entered and shown in India Standard Time. */
export function TaskModal({ mode, task, onClose, onSubmit }: Props) {
  const [title, setTitle] = useState(task?.title ?? "");
  const [deadline, setDeadline] = useState(isoToIstInput(task?.deadline ?? null));
  const [link, setLink] = useState(task?.link_type === "gmail" ? "" : (task?.link ?? ""));
  const [notes, setNotes] = useState(task?.notes ?? "");
  const [priority, setPriority] = useState<Priority | "">(mode === "edit" ? (task?.priority ?? "") : "");
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

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
        link: cleanLink || null,
        notes: notes.trim() || null,
        priority: priority || null,
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
        <Field label="Deadline (India time)" hint="Leave empty for no deadline.">
          <input
            type="datetime-local"
            className={inputClass}
            value={deadline}
            onChange={(e) => setDeadline(e.target.value)}
          />
        </Field>
        <Field label="Link to complete it" hint="Optional. The page where the task gets done.">
          <input
            type="url"
            inputMode="url"
            className={inputClass}
            value={link}
            onChange={(e) => setLink(e.target.value)}
            placeholder="https://"
          />
        </Field>
        {mode !== "review" ? (
          <Field label="Priority" hint={mode === "create" ? "Automatic makes it urgent when due within 48 hours." : undefined}>
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
        <Field label="Notes">
          <textarea
            className={`${inputClass} min-h-20`}
            value={notes}
            maxLength={5000}
            onChange={(e) => setNotes(e.target.value)}
          />
        </Field>
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
