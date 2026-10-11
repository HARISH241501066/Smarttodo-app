"use client";

import { useId, useState, type KeyboardEvent } from "react";
import { Button, inputClass } from "@/components/ui";
import { XIcon } from "@/components/icons";
import { normalizeEntry, validateLooseEntry } from "@/lib/senders";

interface Props {
  label: string;
  hint: string;
  values: string[];
  onChange: (values: string[]) => void;
  placeholder?: string;
  /** Returns an error message for an entry that must not be added, or null if it is fine. */
  validate?: (entry: string) => string | null;
}

export function ChipsEditor({
  label,
  hint,
  values,
  onChange,
  placeholder = "example.com",
  validate = validateLooseEntry,
}: Props) {
  const id = useId();
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  function add() {
    const entry = normalizeEntry(draft);
    if (!entry) return;
    const error = validate(entry);
    if (error) {
      setProblem(error);
      return;
    }
    setProblem(null);
    setDraft("");
    if (!values.includes(entry)) onChange([...values, entry]);
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      add();
    }
  }

  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-sm font-semibold">
        {label}
      </label>
      <p className="mb-2 text-xs text-muted">{hint}</p>
      <div className="flex gap-2">
        <input
          id={id}
          className={inputClass}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          placeholder={placeholder}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          aria-describedby={problem ? `${id}-err` : undefined}
        />
        <Button onClick={add} aria-label={`Add to ${label}`}>
          Add
        </Button>
      </div>
      {problem ? (
        <p id={`${id}-err`} role="alert" className="mt-1 text-xs text-danger">
          {problem}
        </p>
      ) : null}
      {values.length > 0 ? (
        <ul className="mt-2 flex flex-wrap gap-2" aria-label={`${label} entries`}>
          {values.map((value) => (
            <li
              key={value}
              className="flex items-center gap-1 rounded-full border border-line bg-surface-2 py-1 pl-3 pr-1 text-sm"
            >
              <span className="break-all">{value}</span>
              <button
                type="button"
                aria-label={`Remove ${value}`}
                onClick={() => onChange(values.filter((v) => v !== value))}
                className="rounded-full p-1 text-muted hover:bg-line hover:text-fg"
              >
                <XIcon width={14} height={14} />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-xs text-muted">None yet.</p>
      )}
    </div>
  );
}
