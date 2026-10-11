"use client";

import { useEffect, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useApp } from "@/components/AppProvider";
import { ChipsEditor } from "@/components/ChipsEditor";
import { useToast } from "@/components/toast";
import { Button, ConfirmDialog, ErrorNote, Field, Modal, Spinner, inputClass } from "@/components/ui";
import { api, errorMessage } from "@/lib/api";
import { disablePush, enablePush, getPushState, type PushState } from "@/lib/push";
import {
  SOURCE_TYPE_LABEL,
  normalizeKeyword,
  validateKeywordEntry,
  validateSenderEntry,
  validateSourceName,
} from "@/lib/senders";
import type {
  RebuildPreview,
  RebuildResult,
  RebuildStatus,
  SettingsData,
  SourceConfig,
  SourceType,
} from "@/lib/types";

const HOURS = Array.from({ length: 24 }, (_, h) => h);
const POLL_MS = 1500;
const SOURCE_TYPES = Object.keys(SOURCE_TYPE_LABEL) as SourceType[];
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

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

function RebuildSummary({ result }: { result: RebuildResult }) {
  const bySource = Object.entries(result.tasks_by_source);
  return (
    <div className="mt-3 space-y-2 text-sm" data-testid="rebuild-summary">
      <p>
        Removed <strong>{result.removed}</strong>, added <strong>{result.created}</strong>, kept{" "}
        <strong>{result.kept}</strong>.
      </p>
      <p className="text-muted">
        Open tasks by source: {bySource.length ? bySource.map(([name, count]) => `${name} ${count}`).join(", ") : "none"}.
      </p>
      <p className="text-muted">
        Dropped {result.dropped}, ignored {result.ignored}, suspicious {result.suspicious}, needs review{" "}
        {result.review}.
      </p>
      {result.incomplete ? (
        <p className="text-warn">Gmail rate-limited the run, so some mail was not checked. Run it again.</p>
      ) : null}
      {result.failed ? (
        <p className="text-warn">{result.failed} messages could not be read and were left as they were.</p>
      ) : null}
    </div>
  );
}

export default function SettingsPage() {
  const router = useRouter();
  const toast = useToast();
  const { me, refreshCounts, refreshFolders, refreshNotifications } = useApp();

  const [saved, setSaved] = useState<SettingsData | null>(null);
  const [draft, setDraft] = useState<SettingsData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState<SourceType>("custom");
  const [newError, setNewError] = useState<string | null>(null);

  const [rebuildOpen, setRebuildOpen] = useState(false);
  const [rebuildPreview, setRebuildPreview] = useState<RebuildPreview | null>(null);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildError, setRebuildError] = useState<string | null>(null);
  const [rebuildResult, setRebuildResult] = useState<RebuildResult | null>(null);

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
      const { notices, ...next } = await api<SettingsData>("/settings", { method: "PUT", json: draft });
      setSaved(next);
      setDraft(next);
      toast.show("Settings saved");
      // A deleted source is also taken out of folder rules: say so.
      for (const notice of notices ?? []) toast.show(notice);
      if (notices?.length) void refreshFolders();
    } catch (err) {
      setSaveError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  function updateSource(index: number, patch: Partial<SourceConfig>) {
    if (!draft) return;
    setDraft({ ...draft, sources: draft.sources.map((s, i) => (i === index ? { ...s, ...patch } : s)) });
  }

  function removeSource(index: number) {
    if (!draft) return;
    setDraft({ ...draft, sources: draft.sources.filter((_, i) => i !== index) });
  }

  function addSource() {
    if (!draft) return;
    const problem = validateSourceName(
      newName,
      draft.sources.map((s) => s.name),
    );
    if (problem) {
      setNewError(problem);
      return;
    }
    setNewError(null);
    const name = newName.trim().replace(/\s+/g, " ");
    setDraft({ ...draft, sources: [...draft.sources, { name, type: newType, senders: [] }] });
    setNewName("");
    setNewType("custom");
  }

  async function openRebuild() {
    setRebuildError(null);
    setRebuildResult(null);
    setRebuildPreview(null);
    setRebuildOpen(true);
    try {
      setRebuildPreview(await api<RebuildPreview>("/sources/rebuild/preview"));
    } catch (err) {
      setRebuildError(errorMessage(err));
    }
  }

  async function runRebuild() {
    setRebuilding(true);
    setRebuildError(null);
    try {
      let status = await api<RebuildStatus>("/sources/rebuild", { method: "POST" });
      while (status.state === "running") {
        await sleep(POLL_MS);
        status = await api<RebuildStatus>("/sources/rebuild/status");
      }
      if (status.state === "error") throw new Error(status.error ?? "The rebuild failed.");
      setRebuildResult(status.result ?? null);
      void refreshCounts();
      void refreshNotifications();
    } catch (err) {
      setRebuildError(errorMessage(err));
    } finally {
      setRebuilding(false);
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

      <Section title="Sources">
        {loadError ? <ErrorNote>{loadError}</ErrorNote> : null}
        {!draft ? (
          loadError ? null : <Spinner />
        ) : (
          <div className="space-y-4">
            <p className="text-sm text-muted">
              Only mail from these senders is read. A domain such as nptel.ac.in also covers its subdomains
              (onlinecourses.nptel.ac.in), never look-alikes such as fakenptel.ac.in.
            </p>
            {draft.sources.map((source, index) => (
              <article
                key={index}
                data-testid="source-card"
                aria-label={`Source ${source.name}`}
                className="space-y-3 rounded-lg border border-line bg-surface-2/40 p-3"
              >
                <div className="flex flex-wrap items-end gap-2">
                  <div className="min-w-0 flex-1">
                    <label className="mb-1 block text-xs font-semibold text-muted">Name</label>
                    <input
                      className={inputClass}
                      value={source.name}
                      maxLength={40}
                      aria-label={`Name of source ${source.name}`}
                      onChange={(e) => updateSource(index, { name: e.target.value })}
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-xs font-semibold text-muted">Type</label>
                    <select
                      className={inputClass}
                      value={source.type}
                      aria-label={`Type of source ${source.name}`}
                      onChange={(e) => updateSource(index, { type: e.target.value as SourceType })}
                    >
                      {SOURCE_TYPES.map((type) => (
                        <option key={type} value={type}>
                          {SOURCE_TYPE_LABEL[type]}
                        </option>
                      ))}
                    </select>
                  </div>
                  <Button variant="ghost" onClick={() => removeSource(index)} aria-label={`Remove source ${source.name}`}>
                    Remove source
                  </Button>
                </div>
                <ChipsEditor
                  label={`${source.name} senders`}
                  hint={source.senders.length ? undefined : "No senders yet, so nothing is read for this source."}
                  placeholder="nptel.ac.in or news@example.com"
                  values={source.senders}
                  onChange={(senders) => updateSource(index, { senders })}
                  validate={validateSenderEntry}
                />
              </article>
            ))}
            <div className="rounded-lg border border-dashed border-line p-3">
              <p className="mb-2 text-sm font-semibold">Add source</p>
              <div className="flex flex-wrap items-end gap-2">
                <div className="min-w-0 flex-1">
                  <input
                    className={inputClass}
                    value={newName}
                    maxLength={40}
                    placeholder="Name, e.g. Placement cell"
                    aria-label="New source name"
                    onChange={(e) => setNewName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        addSource();
                      }
                    }}
                  />
                </div>
                <select
                  className={`${inputClass} w-auto`}
                  value={newType}
                  aria-label="New source type"
                  onChange={(e) => setNewType(e.target.value as SourceType)}
                >
                  {SOURCE_TYPES.map((type) => (
                    <option key={type} value={type}>
                      {SOURCE_TYPE_LABEL[type]}
                    </option>
                  ))}
                </select>
                <Button onClick={addSource}>Add source</Button>
              </div>
              {newError ? (
                <p role="alert" className="mt-1 text-xs text-danger">
                  {newError}
                </p>
              ) : null}
            </div>
          </div>
        )}
      </Section>

      {draft ? (
        <Section title="Rules">
          <div className="space-y-5">
            <ChipsEditor
              label="Watch keywords"
              hint="Mail from anyone mentioning one of these becomes a task only if it has a deadline, passes the authenticity checks and does not look like a scam."
              placeholder="hackathon"
              values={draft.watch_keywords}
              onChange={(watch_keywords) => setDraft({ ...draft, watch_keywords })}
              validate={validateKeywordEntry}
              normalize={normalizeKeyword}
              testId="keywords"
            />
            <ChipsEditor
              label="Block senders"
              hint="Never turned into a task through a keyword, even when it mentions one."
              placeholder="promo.example.com"
              values={draft.block_senders}
              onChange={(block_senders) => setDraft({ ...draft, block_senders })}
              validate={validateSenderEntry}
              testId="block-senders"
            />
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                className="mt-1 h-5 w-5"
                checked={draft.require_deadline}
                onChange={(e) => setDraft({ ...draft, require_deadline: e.target.checked })}
              />
              <span>
                <span className="block text-sm font-semibold">Only create tasks that have a deadline</span>
                <span className="block text-xs text-muted">
                  Mail from your sources without a date is then dropped instead of becoming a task with
                  &quot;No deadline&quot;.
                </span>
              </span>
            </label>
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
        </Section>
      ) : null}

      <Section title="Rebuild from sources">
        <p className="text-sm text-muted">
          Settings apply to new mail. To re-check the last 30 days against your current sources and rules, and
          remove automatic tasks that no longer match, rebuild. Tasks you added, confirmed, edited or finished
          are never touched.
        </p>
        {dirty ? <p className="mt-2 text-xs text-warn">Save your changes first.</p> : null}
        <div className="mt-3">
          <Button disabled={!draft || dirty || rebuilding} onClick={() => void openRebuild()}>
            Rebuild from sources
          </Button>
        </div>
        {rebuildResult && !rebuildOpen ? <RebuildSummary result={rebuildResult} /> : null}
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

      {rebuildOpen ? (
        rebuildResult ? (
          <Modal title="Rebuild finished" onClose={() => setRebuildOpen(false)}>
            <RebuildSummary result={rebuildResult} />
            <div className="mt-5 flex justify-end">
              <Button variant="primary" onClick={() => setRebuildOpen(false)}>
                Close
              </Button>
            </div>
          </Modal>
        ) : (
          <ConfirmDialog
            title="Rebuild from sources?"
            message={
              <div className="space-y-2" data-testid="rebuild-preview">
                {rebuildError ? <ErrorNote>{rebuildError}</ErrorNote> : null}
                {rebuildPreview ? (
                  <>
                    <p>
                      The last {rebuildPreview.window_days} days of mail will be checked again against your
                      sources and rules.
                    </p>
                    <ul className="list-disc space-y-1 pl-5">
                      <li>
                        <strong className="text-fg">{rebuildPreview.rechecked}</strong> automatic tasks will be
                        re-checked
                        {rebuildPreview.review_items ? ` (${rebuildPreview.review_items} in Needs review)` : ""}.
                      </li>
                      <li>
                        <strong className="text-fg">{rebuildPreview.will_remove}</strong> of them are from senders
                        that are no longer in any source and will be removed. Others are removed only if the new
                        rules say they are not tasks.
                      </li>
                      <li>
                        <strong className="text-fg">{rebuildPreview.protected}</strong> tasks (added by you, confirmed,
                        edited or finished) will not be touched.
                      </li>
                    </ul>
                    <p>New mail that matches is added. Running it again changes nothing twice.</p>
                  </>
                ) : rebuildError ? null : (
                  <Spinner label="Checking what would change" />
                )}
              </div>
            }
            confirmLabel="Rebuild now"
            danger
            busy={rebuilding || (!rebuildPreview && !rebuildError)}
            onConfirm={() => void runRebuild()}
            onCancel={() => (rebuilding ? undefined : setRebuildOpen(false))}
          />
        )
      ) : null}

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
