export type Priority = "urgent" | "important" | "regular";
export type Status = "open" | "done" | "needs_review" | "dismissed";
export type Source = "manual" | "nptel" | "internship" | "gform" | "college" | "other";

export interface Task {
  id: number;
  title: string;
  notes: string | null;
  deadline: string | null; // UTC ISO
  deadline_ist: string | null;
  deadline_display: string | null; // e.g. "Tue, 22 Jan 2030, 11:59 PM IST"
  priority: Priority;
  source: Source;
  status: Status;
  link: string | null;
  link_type: "action" | "gmail" | null;
  link_domain: string | null;
  link_confidence: number | null;
  confidence: number | null;
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

export interface SettingsData {
  denylist: string[];
  allowlist: string[];
  digest_hour: number;
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
}
