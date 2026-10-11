"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useApp } from "@/components/AppProvider";
import { ChipsEditor } from "@/components/ChipsEditor";
import { useToast } from "@/components/toast";
import { Button, ErrorNote, Field, Modal, Spinner, inputClass } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { disablePush, enablePush, getPushState, type PushState } from "@/lib/push";
import type { SettingsData } from "@/lib/types";

const HOURS = Array.from({ length: 24 }, (_, h) => h);

function hourLabel(h: number): string {
  const suffix = h < 12 ? "AM" : "PM";
  return `${h % 12 === 0 ? 12 : h % 12}:00 ${suffix} IST`;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-4 shadow-sm sm:p-5">
      <h2 className="mb-3 text-base font-bold">{title}</h2>
      {children}
    </section>
  );
}

export default function SettingsPage() {
  const router = useRouter();
  const toast = useToast();
  const { me, refreshNotifications } = useApp();

  const [saved, setSaved] = useState<SettingsData | null>(null);
  const [draft, setDraft] = useState<SettingsData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [push, setPush] = useState<PushState | "loading">("loading");
  const [pushBusy, setPushBusy] = useState(false);

  const [deleteOpen, setDeleteOpen] = useState(false);
  const [confirmText, setConfirmText] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  useEffect(() => {
    api<SettingsData>("/settings")
      .then((s) => {
        setSaved(s);
        setDraft(s);
      })
      .catch((err) => setLoadError(errorMessage(err)));
    getPushState()
      .then(setPush)
      .catch(() => setPush("unavailable"));
  }, []);

  const dirty = !!saved && !!draft && JSON.stringify(saved) !== JSON.stringify(draft);

  async function save() {
    if (!draft) return;
    setSaving(true);
    setSaveError(null);
    try {
      const next = await api<SettingsData>("/settings", { method: "PUT", json: draft });
      setSaved(next);
      setDraft(next);
      toast.show("Settings saved");
    } catch (err) {
      setSaveError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  async function togglePush() {
    setPushBusy(true);
    try {
      const next = push === "on" ? await disablePush() : await enablePush();
      setPush(next);
      if (next === "on") toast.show("Push notifications are on for this browser");
      if (next === "denied") toast.show("Notifications are blocked for this site", { kind: "error" });
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setPushBusy(false);
    }
  }

  async function testPush() {
    setPushBusy(true);
    try {
      await api("/push/test", { method: "POST" });
      toast.show("Test notification sent");
      void refreshNotifications();
    } catch (err) {
      toast.show(errorMessage(err), { kind: "error" });
    } finally {
      setPushBusy(false);
    }
  }

  async function signOut() {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      router.replace("/login");
    }
  }

  async function deleteEverything() {
    setDeleting(true);
    setDeleteError(null);
    try {
      await api("/me", { method: "DELETE" });
      router.replace("/login");
    } catch (err) {
      setDeleteError(errorMessage(err));
      setDeleting(false);
    }
  }

  const pushMessage: Record<PushState, string> = {
    unsupported: "This browser doesn't support push notifications.",
    unavailable: "Push couldn't start in this browser just now. Reload the page to try again.",
    unconfigured: "Push notifications aren't set up on the server yet.",
    denied: "Notifications are blocked for this site. Allow them in your browser's site settings, then reload.",
    off: "Get reminders on this device, even when the app is closed. Tapping one opens the task's link.",
    on: "This browser will get reminders. Tapping one opens the task's link.",
  };

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold">Settings</h1>

      <Section title="Account">
        <p className="text-sm">
          Signed in as <strong className="break-all">{me.email}</strong>
        </p>
        <div className="mt-3">
          <Button onClick={() => void signOut()}>Sign out</Button>
        </div>
      </Section>

      <Section title="Push notifications">
        {push === "loading" ? (
          <Spinner label="Checking push support" />
        ) : (
          <>
            <p className="text-sm text-muted" data-testid="push-status" data-state={push}>
              {pushMessage[push]}
            </p>
            {push === "off" || push === "on" ? (
              <div className="mt-3 flex flex-wrap gap-2">
                <Button variant={push === "on" ? "secondary" : "primary"} disabled={pushBusy} onClick={() => void togglePush()}>
                  {push === "on" ? "Turn off push" : "Enable push notifications"}
                </Button>
                {push === "on" ? (
                  <Button disabled={pushBusy} onClick={() => void testPush()}>
                    Send test notification
                  </Button>
                ) : null}
              </div>
            ) : null}
          </>
        )}
      </Section>

      <Section title="Email filters">
        {loadError ? <ErrorNote>{loadError}</ErrorNote> : null}
        {!draft ? (
          loadError ? null : <Spinner />
        ) : (
          <div className="space-y-5">
            <ChipsEditor
              label="Never scan mail from"
              hint="Senders and domains to ignore completely (LinkedIn and Naukri are there by default)."
              values={draft.denylist}
              onChange={(denylist) => setDraft({ ...draft, denylist })}
            />
            <ChipsEditor
              label="My college domains"
              hint="Mail from these domains is labelled College."
              values={draft.allowlist}
              onChange={(allowlist) => setDraft({ ...draft, allowlist })}
            />
            <Field label="Daily digest time" hint="A summary of everything due in the next 72 hours.">
              <select
                className={inputClass}
                value={draft.digest_hour}
                onChange={(e) => setDraft({ ...draft, digest_hour: Number(e.target.value) })}
              >
                {HOURS.map((h) => (
                  <option key={h} value={h}>
                    {hourLabel(h)}
                  </option>
                ))}
              </select>
            </Field>
            {saveError ? <ErrorNote>{saveError}</ErrorNote> : null}
            <div className="flex items-center gap-3">
              <Button variant="primary" disabled={!dirty || saving} onClick={() => void save()}>
                {saving ? "Saving..." : "Save settings"}
              </Button>
              {dirty ? <span className="text-sm text-muted">Unsaved changes</span> : null}
            </div>
          </div>
        )}
      </Section>

      <Section title="Delete my data">
        <p className="text-sm text-muted">
          Removes your account, every task, notification and setting, and stops the connection to your Google
          account. This can&apos;t be undone.
        </p>
        <div className="mt-3">
          <Button variant="danger" onClick={() => setDeleteOpen(true)}>
            Delete my data
          </Button>
        </div>
      </Section>

      {deleteOpen ? (
        <Modal title="Delete everything?" onClose={() => (deleting ? undefined : setDeleteOpen(false))}>
          <p className="text-sm text-muted">
            Type <strong className="text-fg">DELETE</strong> to confirm. You will be signed out.
          </p>
          <input
            className={`${inputClass} mt-3`}
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            aria-label="Type DELETE to confirm"
            autoCapitalize="characters"
          />
          {deleteError ? (
            <div className="mt-3">
              <ErrorNote>{deleteError}</ErrorNote>
            </div>
          ) : null}
          <div className="mt-5 flex justify-end gap-2">
            <Button onClick={() => setDeleteOpen(false)} disabled={deleting}>
              Cancel
            </Button>
            <Button
              variant="danger"
              disabled={confirmText !== "DELETE" || deleting}
              onClick={() => void deleteEverything()}
            >
              {deleting ? "Deleting..." : "Delete everything"}
            </Button>
          </div>
        </Modal>
      ) : null}
    </div>
  );
}
