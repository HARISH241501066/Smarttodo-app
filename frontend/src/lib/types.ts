export type Priority = "urgent" | "important" | "regular";
export type Status = "open" | "done" | "needs_review" | "dismissed";
export type Source =
  | "manual"
  | "nptel"
  | "digii"
  | "internship"
  | "custom"
  | "keyword"
  | "gform"
  | "college"
  | "other"
  | "tracked";
export type SourceType = "nptel" | "digii" | "internship" | "custom";

export interface Subtask {
  id: number;
  title: string;
  done: boolean;
  position: number;
}

/** Answer to every subtask change. all_subtasks_done is true when every item is ticked: the UI
 *  then asks "Mark the whole task done?" (the task is never completed automatically). */
export interface SubtaskChange {
  subtask: Subtask | null;
  all_subtasks_done: boolean;
  task: Task;
}

export interface Task {
  id: number;
  title: string;
  notes: string | null;
  deadline: string | null; // UTC ISO
  deadline_ist: string | null;
  deadline_display: string | null; // e.g. "Tue, 22 Jan 2030, 11:59 PM IST"
  priority: Priority;
  source: Source;
  source_name: string | null; // the user's name for the source, or the matched keyword
  origin: "manual" | "email";
  tag: string | null;
  reminders_enabled: boolean;
  snoozed_until: string | null;
  subtasks: Subtask[];
  subtasks_done: number;
  subtasks_total: number;
  status: Status;
  link: string | null;
  link_type: "action" | "gmail" | null;
  link_domain: string | null;
  link_confidence: number | null;
  confidence: number | null;
  sender_email: string | null;
  created_at: string;
  updated_at: string;
}

export interface NotificationItem {
  id: number;
  task_id: number | null;
  kind: string;
  title: string;
  body: string | null;
  link: string | null;
  read: boolean;
  created_at: string;
}

export interface NotificationList {
  unread_count: number;
  items: NotificationItem[];
}

export interface QuarantineItem {
  id: number;
  message_id: string;
  subject: string | null;
  sender: string | null;
  score: number | null;
  reasons: string[];
  status: "held" | "rescued" | "confirmed_scam";
  created_at: string;
}

export interface RescueResult {
  quarantine: QuarantineItem;
  task: Task | null;
  task_created: boolean;
}

export interface SourceConfig {
  name: string;
  type: SourceType;
  senders: string[]; // domains (nptel.ac.in) or full addresses
}

export interface SettingsData {
  sources: SourceConfig[];
  watch_keywords: string[];
  block_senders: string[];
  require_deadline: boolean;
  digest_hour: number;
  notices?: string[]; // set in the answer to a save: what it changed elsewhere
}

/** What "Rebuild from sources" would look at, from stored data only. */
export interface RebuildPreview {
  window_days: number;
  rechecked: number;
  will_remove: number;
  review_items: number;
  protected: number;
}

export interface RebuildResult {
  removed: number;
  created: number;
  kept: number;
  tasks_by_source: Record<string, number>;
  dropped: number;
  ignored: number;
  suspicious: number;
  review: number;
  fetched: number;
  failed: number;
  incomplete: boolean;
}

export interface RebuildStatus {
  state: "idle" | "running" | "done" | "error";
  result?: RebuildResult;
  error?: string;
}

export interface Me {
  email: string;
  needs_reauth: boolean;
}

export interface TaskInput {
  title: string;
  deadline: string | null; // ISO with offset, or null
  link: string | null;
  notes: string | null;
  priority: Priority | null; // null = automatic
  // Optional so the review form (which has none of these) can leave them out.
  tag?: string | null;
  reminders_enabled?: boolean;
  subtasks?: SubtaskDraft[];
}

/** A subtask in the form: `id` is set for ones that already exist on the server. */
export interface SubtaskDraft {
  id?: number;
  title: string;
}

export type FolderPriority = Priority;

export interface FolderRules {
  sources: string[]; // your source names, "hackathon" and "manual"
  tags: string[];
  priorities: Priority[];
  title_contains: string | null;
}

export interface Folder {
  id: number;
  name: string;
  emoji: string;
  color: string;
  position: number;
  rules: FolderRules;
  show_completed: boolean;
  open_count: number;
  has_overdue: boolean;
  pinned?: boolean | null;
}

export interface FolderGroup {
  key: "overdue" | "today" | "tomorrow" | "week" | "later" | "none" | "completed";
  label: string;
  tasks: Task[];
}

export interface FolderTasks {
  groups: FolderGroup[];
  pinned_ids: number[];
  rule_ids: number[];
}
