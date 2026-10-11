"use client";

import { useId, useState, type KeyboardEvent } from "react";
import { Button, inputClass } from "@/components/ui";
import { XIcon } from "@/components/icons";

// Same rule as the backend: a plain domain or address, nothing that could act as a search operator.
const ENTRY = /^[a-z0-9][a-z0-9._+@-]*$/;

export function normalizeEntry(raw: string): string {
  return raw.trim().toLowerCase().replace(/^@/, "");
}

interface Props {
  label: string;
  hint: string;
  values: string[];
  onChange: (values: string[]) => void;
}

export function ChipsEditor({ label, hint, values, onChange }: Props) {
  const id = useId();
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string | null>(null);

  function add() {
    const entry = normalizeEntry(draft);
    if (!entry) return;
    if (!ENTRY.test(entry)) {
      setProblem("Enter a plain domain such as example.com, or an address such as news@example.com.");
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
          placeholder="example.com"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          aria-describedby={problem ? `${id}-err` : undefined}
        />
        <Button onClick={add}>Add</Button>
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
