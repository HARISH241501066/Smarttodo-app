"use client";

import { useEffect, useState } from "react";
import { Button, ErrorNote, Modal, Spinner } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import type { Task } from "@/lib/types";

interface Email {
  subject: string;
  sender: string;
  received_at: string | null;
  body_text: string;
  truncated: boolean;
  gmail_url: string;
}

/** The whole email behind an email task. It is read from Gmail when you open this window and is
 *  never saved by the app. Shown as plain text, so nothing in the email can run. */
export function EmailViewer({ task, onClose }: { task: Task; onClose: () => void }) {
  const [email, setEmail] = useState<Email | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    api<Email>(`/tasks/${task.id}/email`)
      .then((e) => {
        if (alive) setEmail(e);
      })
      .catch((err) => {
        if (alive) setError(errorMessage(err));
      });
    return () => {
      alive = false;
    };
  }, [task.id]);

  return (
    <Modal title="Full email" onClose={onClose}>
      {error ? <ErrorNote>{error}</ErrorNote> : null}
      {!email && !error ? <Spinner label="Reading the email from Gmail" /> : null}
      {email ? (
        <div className="space-y-3" data-testid="email-view">
          <div className="space-y-0.5 text-sm">
            <p className="break-words text-base font-semibold">{email.subject || "(no subject)"}</p>
            <p className="break-all text-muted">From: {email.sender}</p>
            {email.received_at ? (
              <p className="text-muted">
                {new Date(email.received_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" })} IST
              </p>
            ) : null}
          </div>
          <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-surface-2 p-3 font-sans text-sm">
            {email.body_text || "(this email has no text)"}
          </pre>
          {email.truncated ? <p className="text-xs text-muted">Only the first part is shown.</p> : null}
        </div>
      ) : null}
      <div className="mt-4 flex flex-wrap justify-end gap-2">
        {email ? (
          <a
            href={email.gmail_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex min-h-10 items-center rounded-lg border border-line px-4 text-sm font-semibold text-primary hover:bg-surface-2"
          >
            Open in Gmail
          </a>
        ) : null}
        <Button onClick={onClose}>Close</Button>
      </div>
    </Modal>
  );
}
